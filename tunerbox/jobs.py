"""Background jobs (scan sources, refresh guide) + the housekeeping loop."""
import logging
import threading
import time
import traceback
from datetime import datetime, timedelta

from . import config, db, dvr, epg, guide, lineup, matcher, media, probe, sources

log = logging.getLogger("tunerbox.jobs")
_lock = threading.Lock()
state = {"running": False, "name": "", "stage": "", "done": 0, "total": 0, "message": "", "finished": 0}


def _progress(stage, done=0, total=0):
    state.update(stage=stage, done=done, total=total)


def _run(name, fn):
    with _lock:
        if state["running"]:
            return False
        state.update(running=True, name=name, stage="Starting", done=0, total=0, message="")

    def wrap():
        try:
            state["message"] = fn() or "Done"
        except Exception as e:  # noqa: BLE001
            log.error("Job %s failed: %s", name, traceback.format_exc())
            state["message"] = f"Failed: {e}"
        finally:
            guide.invalidate()
            state.update(running=False, finished=int(time.time()))
    threading.Thread(target=wrap, daemon=True).start()
    return True


def scan(force_probe=False):
    def job():
        lineup.load_csv()
        if epg.gracenote_ready() and db.get_meta("gn_lineup") != config.get("gracenote")["lineup_id"]:
            _progress("Loading your provider's channel numbers")
            epg.fetch_gracenote_channels()
        srcs = sources.enabled_sources()
        for i, s in enumerate(srcs):
            _progress(f"Loading {s['name']}", i, len(srcs))
            sources.refresh_source(s)
        probed = ""
        if config.get("probe_streams", True):
            cutoff = 0 if force_probe else int(time.time()) - 12 * 3600
            types = ("m3u", "xtream") if config.get("probe_xtream") else ("m3u",)
            q = ",".join("?" * len(types))
            todo = db.rows(f"""SELECT s.key, s.url FROM streams s JOIN sources src ON src.id=s.source_id
                               WHERE src.enabled=1 AND src.type IN ({q}) AND s.checked_at < ?""", (*types, cutoff))
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
        db.set_meta("scan_updated", int(time.time()))
        n_extra = db.one("SELECT count(*) n FROM extras WHERE active=1")["n"]
        return f"{mapped}/{total} lineup channels mapped, {n_extra} extra channels{probed}"
    return _run("scan", job)


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


def quick_refresh():
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
    return _run("refresh", job)


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
        return "; ".join(msgs) or "No local channels"
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


def housekeeping():
    """Runs forever: DVR ticks every 15s, periodic scans, guide refreshes and quick playlist refreshes."""
    while True:
        try:
            dvr.tick()
            now = int(time.time())
            if not state["running"]:
                has_sources = db.one("SELECT count(*) n FROM sources WHERE enabled=1")["n"] > 0
                epg_age = now - int(db.get_meta("epg_updated", 0) or 0)
                if has_sources and scan_due(now, int(db.get_meta("scan_updated", 0) or 0)):
                    scan()
                elif (epg_age > float(config.get("epg_refresh_hours", 12)) * 3600
                      and now - int(db.get_meta("epg_tried", 0) or 0) > EPG_RETRY):
                    refresh_epg()
                elif has_sources and quick_due(now):
                    quick_refresh()
            # local channels: keep two days of listings ahead
            if now - int(db.get_meta("media_epg", 0) or 0) > 3600:
                media.write_epg()
                guide.invalidate()
        except Exception:  # noqa: BLE001
            log.exception("housekeeping")
        time.sleep(15)
