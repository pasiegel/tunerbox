"""Background jobs (scan sources, refresh guide) + the housekeeping loop."""
import logging
import threading
import time
import traceback
from datetime import datetime, timedelta

from . import config, db, dvr, epg, guide, library, libtasks, lineup, matcher, media, probe, scoreboard, sources, sports, vod

log = logging.getLogger("tunerbox.jobs")
_lock = threading.Lock()
# Three lanes, each running one job at a time: everything else (playlists, On Demand from your provider, the guide),
# My Library (reading its folders on your NAS, TMDB, its tasks) and grabbing My Library's video details (reading each
# file, which can take hours). So a long Full rescan doesn't hold up your channels, a scan of the playlists doesn't
# hold up the library, and new videos are found and shown while older ones are still being read. Two jobs of the
# same lane still wait for each other.
_NEW = {"running": False, "name": "", "stage": "", "done": 0, "total": 0, "message": "", "finished": 0}
state = dict(_NEW)          # the main lane (/api/jobs: its fields at the top, for the pages that only know one job)
lib_state = dict(_NEW)      # the My Library lane (/api/jobs: "library")
_queue = []                 # (name, fn) waiting for the main lane's job: started straight after it
_lib_queue = []             # the same for My Library
vid_state = dict(_NEW)      # the video details lane (/api/jobs: "videos")
_vid_queue = []             # the same for video details


def _lane(name):
    """-> (state, queue) of the lane a job of this name runs in."""
    if name.startswith("library:videos"):
        return vid_state, _vid_queue
    return (lib_state, _lib_queue) if name.startswith("library:") else (state, _queue)


def _progress(stage, done=0, total=0):
    state.update(stage=stage, done=done, total=total)


def _lib_progress(stage, done=0, total=0):
    lib_state.update(stage=stage, done=done, total=total)


def _vid_progress(stage, done=0, total=0):
    vid_state.update(stage=stage, done=done, total=total)


_WHAT = {"scan": "the channel scan", "vod": "reading On Demand", "library": "another My Library job", "epg": "the guide update",
         "refresh": "the playlist refresh", "match": "channel matching", "media": "reading My Channels"}


def running_what(library=False, videos=False):
    """What the job running in that lane is, in words: "the guide update"."""
    st = vid_state if videos else lib_state if library else state
    if not st["running"]:
        return ""
    return "grabbing video details" if videos else _WHAT.get(st["name"].split(":")[0], "another job")


def _run(name, fn, queue=False):
    """Start a job in the background, in its lane. If that lane is busy: False, or with `queue` it runs after the
    job there (a setting changed in Setup isn't lost because a guide refresh happened to be running) and "queued"."""
    st, q = _lane(name)
    with _lock:
        if st["running"]:
            if queue:
                if name not in [n for n, _ in q]:      # already waiting: once is enough
                    q.append((name, fn))
                return "queued"
            return False
        st.update(running=True, name=name, stage="Starting", done=0, total=0, message="")

    def wrap(name, fn):
        while True:
            try:
                st["message"] = fn() or "Done"
            except Exception as e:  # noqa: BLE001
                log.error("Job %s failed: %s", name, traceback.format_exc())
                st["message"] = f"Failed: {e}"
            finally:
                guide.invalidate()
            with _lock:                 # the next queued job keeps "running" on, so pages keep following it
                if not q:
                    st.update(running=False, finished=int(time.time()))
                    return
                name, fn = q.pop(0)
                st.update(name=name, stage="Starting", done=0, total=0)
    threading.Thread(target=wrap, args=(name, fn), daemon=True).start()
    return True


def scan(force_probe=False, queue=False, source_id=None):
    """Read every playlist, On Demand, check streams, match channels, the online guides. `queue`: after the
    job that's running, if there is one (a playlist just added or changed), instead of not at all.
    `source_id` (a playlist just added): only that one is read, with its On Demand and its streams checked;
    the others are left as they are. Matching and the guides still run for all, so its channels fit in."""
    def job():
        lineup.load_csv()
        if epg.gracenote_ready() and db.get_meta("gn_lineup") != config.get("gracenote")["lineup_id"]:
            _progress("Loading your provider's channel numbers")
            epg.fetch_gracenote_channels()
        srcs = [s for s in sources.enabled_sources() if source_id is None or s["id"] == source_id]
        for i, s in enumerate(srcs):
            _progress(f"Loading {s['name']}", i, len(srcs))
            sources.refresh_source(s)
        on_demand = vod.refresh_all(_progress, source_id) if vod.enabled() else ""
        probed = ""
        if config.get("probe_streams", True):
            cutoff = 0 if force_probe else int(time.time()) - 12 * 3600
            types = ("m3u", "xtream") if config.get("probe_xtream") else ("m3u",)
            q = ",".join("?" * len(types))
            one = "AND src.id=?" if source_id else ""
            todo = db.rows(f"""SELECT s.key, s.url FROM streams s JOIN sources src ON src.id=s.source_id
                               WHERE src.enabled=1 AND src.type IN ({q}) AND s.checked_at < ? {one}""",
                           (*types, cutoff, *([source_id] if source_id else [])))
            if todo:
                ok, n = probe.probe_streams(todo, lambda d, t: _progress("Checking streams", d, t))
                probed = f", {ok}/{n} streams working"
        _progress("Matching channels")
        mapped, total = matcher.match_all(lambda d, t: _progress("Matching channels", d, t), force=False)
        guide.invalidate()
        if not db.get_meta("epg_updated"):
            epg.refresh_all(_progress)
        else:
            _progress("Online guides (XMLTV)")
            epg.fetch_xmltv(lambda d, t: _progress("Online guides (XMLTV)", d, t))
        guide.invalidate()
        dvr.apply_rules()
        if source_id is None:               # one new playlist isn't the full scan: that still runs on schedule
            db.set_meta("scan_updated", int(time.time()))
        n_extra = db.one("SELECT count(*) n FROM extras WHERE active=1")["n"]
        return f"{mapped}/{total} lineup channels mapped, {n_extra} extra channels{probed}" + (f". {on_demand}" if on_demand else "")
    return _run(f"scan:{source_id}" if source_id else "scan", job, queue)


def vod_scan(source_id=None, kinds=vod.KINDS):
    """Re-read the On Demand lists (movies and/or shows) of the Xtream logins that have them turned on.
    Queued if another job is running."""
    return _run(f"vod:{source_id}:{','.join(kinds)}", lambda: vod.refresh_all(_progress, source_id, kinds), queue=True)


def library_scan(share_id=None, full_refresh=False):
    """Read your own movie and TV folders (Setup › My Library) and download details from TMDB.
    Queued if another My Library job is running. All of them (Scan now): a quick scan, which counts as the "scan" task's
    run. One library (its Full rescan button, or just added or moved): every folder is read again."""
    if share_id:
        return _run(f"library:{share_id}", lambda: library.scan_all(_lib_progress, share_id, full_refresh), queue=True)
    return _run("library:", lambda: libtasks.run("scan", _lib_progress,
                                                  lambda p: library.scan_all(p, None, full_refresh, quick=True)),
                queue=True)


def library_task(key):
    """One of My Library's automatic tasks (libtasks.py), now. Queued if another My Library job is running."""
    if key not in libtasks.KEYS:
        raise KeyError(key)
    return _run(f"library:task:{key}", lambda: libtasks.run(key, _lib_progress), queue=True)


def library_tasks_all():
    """Every automatic task that's on, one after another (Setup › My Library › Run all now)."""
    return _run("library:task:all", lambda: libtasks.run_all(_lib_progress), queue=True)


def library_measure(**kw):
    """Grab the details of the videos that still need them, in their own lane: after a scan found new videos
    (library.scan_all), or again after the NAS stopped answering (`kw`: measure_lengths' scope, e.g. a Full
    rescan's library). Queued behind a run that's going; a run of the same scope already waiting is enough."""
    scope = f":{kw['share_id']}" if kw.get("share_id") else ":new" if kw.get("retry_days") == 0 else ""
    return _run(f"library:videos{scope}", lambda: library.measure_lengths(_vid_progress, **kw), queue=True)


library.resume = library_measure
library.measure_later = library_measure


def library_details():
    """Only the TMDB step: after a key is added, or to retry misses."""
    return _run("library:details", lambda: library.fetch_details(_lib_progress) or "Nothing to look up", queue=True)


def refresh_epg():
    def job():
        lineup.load_csv()
        n1, n2 = epg.refresh_all(_progress)
        guide.invalidate()
        made = dvr.apply_rules()
        if not n1 + n2 and (epg.gracenote_ready() or epg.xmltv_urls()):
            return "Guide update failed: nothing downloaded, keeping the old listings (will retry)"
        return f"Guide updated: {n1} local + {n2} online programs, {made} new recordings scheduled"
    return _run("epg", job)


def quick_refresh(queue=False):
    """Re-read the playlists between full scans, so event/PPV feeds pick up tonight's names and the
    guide picks up their listings. No stream checks; matching and guide parsing only run if something
    they use changed."""
    def job():
        db.set_meta("quick_updated", int(time.time()))
        srcs = [s for s in sources.enabled_sources() if s["type"] != "hdhomerun"]   # antenna lineups don't change
        for i, s in enumerate(srcs):
            _progress(f"Loading {s['name']}", i, len(srcs))
            sources.refresh_source(s)
        _progress("Matching channels")
        mapped, total = matcher.match_all(lambda d, t: _progress("Matching channels", d, t), force=False)
        guide.invalidate()
        _progress("Online guides (XMLTV)")
        epg.fetch_xmltv(lambda d, t: _progress("Online guides (XMLTV)", d, t))
        guide.invalidate()
        made = dvr.apply_rules()
        n_extra = db.one("SELECT count(*) n FROM extras WHERE active=1")["n"]
        return f"Playlists refreshed: {mapped}/{total} lineup channels mapped, {n_extra} extra channels, {made} new recordings scheduled"
    return _run("refresh", job, queue)


def rematch():
    def job():
        mapped, total = matcher.match_all(lambda d, t: _progress("Matching channels", d, t))
        return f"{mapped}/{total} lineup channels mapped"
    return _run("match", job)


def media_scan(cid=None):
    """Read the folders of one local channel (or all of them)."""
    def job():
        chans = [media.channel(cid)] if cid else media.channels()
        msgs = []
        for i, ch in enumerate(c for c in chans if c):
            _progress(f"Reading {ch['name']}", 0, 0)
            msgs.append(f"{ch['name']}: " + media.scan(ch["id"], lambda d, t, n=ch["name"]: _progress(f"Reading {n}", d, t)))
        guide.invalidate()
        return "; ".join(msgs) or "No channels in My Channels"
    return _run("media", job)


EPG_RETRY = 30 * 60   # after a guide refresh that downloaded nothing, wait this long before trying again


def scan_due(now, last):
    """Full scan wanted? Daily at scan_hour (local time) if set, else every source_refresh_hours."""
    hour = int(config.get("scan_hour", -1))
    if 0 <= hour <= 23:
        t = datetime.fromtimestamp(now).replace(hour=hour, minute=0, second=0, microsecond=0)
        if t.timestamp() > now:
            t -= timedelta(days=1)
        return last < t.timestamp()
    return now - last > float(config.get("source_refresh_hours", 24)) * 3600


def quick_due(now):
    hours = float(config.get("event_refresh_hours", 2) or 0)
    if hours <= 0:
        return False
    last = max(int(db.get_meta("scan_updated", 0) or 0), int(db.get_meta("quick_updated", 0) or 0))
    return now - last > hours * 3600


EPG_ON_START = 30 * 60   # at startup, refresh a guide older than this (a quick restart doesn't download it again)


def housekeeping():
    """Runs forever: DVR ticks every 15s, periodic scans, guide refreshes and quick playlist refreshes."""
    startup = True     # TunerBox just started: refresh the guide once (after a due scan) unless it's fresh
    while True:
        try:
            dvr.tick()
            now = int(time.time())
            if not state["running"]:
                has_sources = db.one("SELECT count(*) n FROM sources WHERE enabled=1")["n"] > 0
                epg_age = now - int(db.get_meta("epg_updated", 0) or 0)
                if has_sources and scan_due(now, int(db.get_meta("scan_updated", 0) or 0)):
                    scan()
                elif startup:
                    startup = False
                    if epg_age > EPG_ON_START:
                        refresh_epg()
                elif (epg_age > float(config.get("epg_refresh_hours", 12)) * 3600
                      and now - int(db.get_meta("epg_tried", 0) or 0) > EPG_RETRY):
                    refresh_epg()
                elif has_sources and quick_due(now):
                    quick_refresh()
            if not lib_state["running"] and (task := libtasks.due(now)):      # My Library: its own lane
                library_task(task)
            scoreboard.tick(now)       # games and scores, in its own thread
            sources.hdhr_tick(now)     # antenna tuners: one with a new address is found (its own thread)
            sports.warm(now)           # the Sports screen's game matching, ready before it's opened
            # local channels: keep two days of listings ahead
            if now - int(db.get_meta("media_epg", 0) or 0) > 3600:
                media.write_epg()
                guide.invalidate()
        except Exception:  # noqa: BLE001
            log.exception("housekeeping")
        time.sleep(15)
