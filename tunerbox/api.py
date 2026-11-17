"""HTTP API + web UI + M3U/XMLTV/HDHomeRun outputs."""
import hashlib
import ipaddress
import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

import anyio
import httpx
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from . import bundle, config, connections, https, db, dvr, epg, gamestats, guide, hls, home, jobs, library, lineup, matcher, media, remote, scoreboard, sources, sports, vod
from . import streaming, tuner
from .streaming import ffmpeg_stream, stop_client
from . import reminders  # noqa: I001
from . import catchup  # noqa: I001

log = logging.getLogger("tunerbox.api")
ART_W = 480                     # guide show pictures: sharp in the guide panel, still small


def _startup():
    db.init()
    if not db.one("SELECT 1 FROM lineup LIMIT 1"):
        lineup.load_csv()
    dvr.recover()
    hls.cleanup_stale()
    threading.Thread(target=jobs.housekeeping, daemon=True).start()


@asynccontextmanager
async def _lifespan(_app):
    _startup()
    try:
        yield
    finally:
        hls.stop_all()


app = FastAPI(title="TunerBox", lifespan=_lifespan)


class LiveResponse(StreamingResponse):
    """Closes the ffmpeg generator as soon as the viewer disconnects. `cleanup`: frees what the
    endpoint opened before responding (a generator that never started doesn't run its own finally)."""
    def __init__(self, *a, cleanup=None, **kw):
        super().__init__(*a, **kw)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose:
                await aclose()
            if self.cleanup:
                await anyio.to_thread.run_sync(self.cleanup)


# ------------------------------------------------------------------ channels & guide
@app.get("/api/channels")
def api_channels(all: int = 0):
    return [{k: v for k, v in c.items() if k != "streams"} | {"alts": max(0, len(c["streams"]) - 1)}
            for c in guide.channels(bool(all) or None)]


@app.get("/api/favorites")
def api_favorites():
    return sorted({int(n) for n in config.get("favorites") or []})


@app.post("/api/favorites")
def api_set_favorite(number: int = Body(...), on: bool = Body(True)):
    """Star / unstar a channel. Open to every screen on the network, like watching (not a setup change)."""
    favs = {int(n) for n in config.get("favorites") or []}
    (favs.add if on else favs.discard)(number)
    config.update({"favorites": sorted(favs)})
    return sorted(favs)


@app.get("/api/guide")
def api_guide(start: int, stop: int, numbers: str = ""):
    nums = {int(n) for n in numbers.split(",") if n.strip().lstrip("-").isdigit()}
    chans = [c for c in guide.channels(True) if not nums or c["number"] in nums]
    keys = {c["epg"] for c in chans if c["epg"]}
    progs = epg.programs(keys, start, stop)
    recs = {(r["number"], r["start"]): r["status"] for r in db.rows(
        "SELECT number, start, status FROM recordings WHERE stop > ? AND start < ? AND status IN ('scheduled','recording')",
        (start, stop))}
    rules = {(r["title"].lower(), r["number"]) for r in db.rows("SELECT title, number FROM rules")}
    reminded = reminders.keys(start, stop)
    out = {}
    for c in chans:
        lst = []
        for p in progs.get(c["epg"], []):
            p = dict(p)
            p["rec"] = recs.get((c["number"], p["start"]), "")
            p["remind"] = (c["number"], p["start"]) in reminded
            p["series"] = (p["title"].lower(), c["number"]) in rules or (p["title"].lower(), None) in rules
            p["genre"] = epg.genre(p["category"])
            p.pop("channel", None)
            lst.append(p)
        out[c["number"]] = lst
    return out


@app.get("/api/art/{number}/{start}")
async def api_art(number: int, start: int):
    """A show's picture from the guide, shrunk and kept on disk like On Demand posters."""
    def find():
        c = guide.channel(number)
        return epg.image(c["epg"], start) if c and c["epg"] else ""
    url = await anyio.to_thread.run_sync(find)
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(404)
    path = await anyio.to_thread.run_sync(vod.small_image, url, ART_W)
    if path:
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
    return RedirectResponse(url, status_code=302)


@app.get("/api/guide/genre")
def api_guide_genre(genre: str, start: int, stop: int):
    """Channel numbers with a show of that genre (movies, sports, news, kids) on in [start, stop)."""
    if genre not in epg.GENRES:
        raise HTTPException(400, "Unknown genre")
    chans = [c for c in guide.channels(True) if c["epg"]]
    keys = epg.genre_channels(genre, {c["epg"] for c in chans}, start, stop)
    return sorted(c["number"] for c in chans if c["epg"] in keys)


@app.get("/api/now/{number}")
def api_now(number: int):
    c = guide.channel(number)
    if not c:
        raise HTTPException(404)
    now = int(time.time())
    progs = epg.programs([c["epg"]], now - 60, now + 6 * 3600).get(c["epg"], []) if c["epg"] else []
    cur = [p for p in progs if p["start"] <= now < p["stop"]]
    nxt = [p for p in progs if p["start"] >= now]
    return {"channel": {k: v for k, v in c.items() if k != "streams"},
            "now": cur[0] if cur else None, "next": nxt[0] if nxt else None}


# ------------------------------------------------------------------ playback
def _full(e):
    """503 for a playlist with no connection left, as a real HDHomeRun answers Plex when all its tuners are busy.
    The TV then asks /api/busy what is using them."""
    return HTTPException(503, str(e), headers={"X-HDHomeRun-Error": "805 All Tuners In Use"})


async def _started(fn):
    """Run what opens a stream (off the event loop) before responding: a full playlist is a 503."""
    try:
        return await anyio.to_thread.run_sync(fn)
    except connections.Full as e:
        raise _full(e) from None
    except OSError as e:            # ffmpeg missing
        raise HTTPException(502, str(e)) from None


async def _live(request, row, client, mode, audio, buf, name=""):
    mode = mode or row.get("video_mode") or None
    connections.name_client(client, name)
    who = "" if client else connections.app_reader(request.headers.get("user-agent", ""),
                                                   request.client.host if request.client else "")
    sub = await _started(lambda: tuner.open_for(row["url"], client or None, mode, audio, buf, row.get("source_id"), who))
    return LiveResponse(ffmpeg_stream(row["url"], client or None, mode, audio, request, buf, sub=sub),
                        media_type="video/mp2t", headers={"Cache-Control": "no-store"},
                        cleanup=lambda: sub.close(wait=False))


async def _hls(row, client, mode, buf, gone="No signal"):
    sid = await _started(lambda: hls.start(row["url"], client or None, mode or row.get("video_mode") or None,
                                           buf, source_id=row.get("source_id")))
    if not await hls.wait_ready(sid):
        raise HTTPException(502, gone)
    return RedirectResponse(f"/api/hls/s/{sid}/index.m3u8", status_code=302)


@app.get("/api/stream/{number}")
async def api_stream(request: Request, number: int, alt: int = 0, client: str = "", mode: str = "", audio: str = "aac",
                     buf: int = -1, name: str = ""):
    # channel list rebuilds can take a second with big playlists: keep them off the event loop
    c = await anyio.to_thread.run_sync(guide.channel, number)
    if not c or not c["streams"]:
        raise HTTPException(404, "No signal")
    rows = await anyio.to_thread.run_sync(guide.stream_rows, c["streams"])
    if alt >= len(rows):
        raise HTTPException(404, "No more alternates")
    return await _live(request, rows[alt], client, mode, audio, buf, name)


@app.get("/api/hls/{number}")
async def api_hls(number: int, alt: int = 0, client: str = "", mode: str = "", buf: int = -1):
    """Native-HLS players (iPad/iPhone Safari): start a session, then redirect to its playlist."""
    c = await anyio.to_thread.run_sync(guide.channel, number)
    if not c or not c["streams"]:
        raise HTTPException(404, "No signal")
    rows = await anyio.to_thread.run_sync(guide.stream_rows, c["streams"])
    if alt >= len(rows):
        raise HTTPException(404, "No more alternates")
    return await _hls(rows[alt], client, mode, buf)


@app.get("/api/hls/s/{sid}/{name}")
def api_hls_file(sid: str, name: str):
    p = hls.file(sid, name)
    if not p:
        raise HTTPException(404)
    if name.endswith(".m3u8"):
        return Response(hls.playlist(sid, p), media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-cache"})
    return FileResponse(p, media_type="video/mp2t", headers={"Cache-Control": "no-cache"})


# event / PPV feeds that have no channel number: played by stream key
def _feed_row(key):
    rows = guide.stream_rows([key])
    if not rows:
        raise HTTPException(404, "Unknown feed")
    return rows[0]


@app.get("/api/feed/stream")
async def api_feed_stream(request: Request, key: str, client: str = "", mode: str = "", audio: str = "aac",
                          buf: int = -1, name: str = ""):
    r = await anyio.to_thread.run_sync(_feed_row, key)
    return await _live(request, r, client, mode, audio, buf, name)


@app.get("/api/feed/hls")
async def api_feed_hls(key: str, client: str = "", mode: str = "", buf: int = -1):
    r = await anyio.to_thread.run_sync(_feed_row, key)
    return await _hls(r, client, mode, buf, "Not live")


@app.get("/api/busy")
def api_busy(number: int | None = None, key: str = "", vod_id: str = "", client: str = "", mode: str = "",
             audio: str = "aac"):
    """After a channel, feed or On Demand title wouldn't start: if that's because every connection its
    playlists allow is in use, what is using them (so the screen can offer to watch that instead)."""
    if number is not None:
        c = guide.channel(number)
        rows = guide.stream_rows(c["streams"]) if c else []
    elif key:
        rows = guide.stream_rows([key])
    elif vod_id:
        it = vod.item(vod_id)
        rows = [{"source_id": it["source_id"]}] if it else []
    else:
        rows = []
    return {"full": connections.busy(rows, client, mode or None, audio)}


# ------------------------------------------------------------------ search, sports & PPV
@app.get("/api/search")
def api_search(q: str = ""):
    return {**sports.search(q), "vod": vod.search(q)}


@app.get("/api/sports")
def api_sports(tab: str = ""):
    if tab not in ("", "now", "up", "ch", "ev"):
        raise HTTPException(400, "Unknown tab")
    return sports.overview(tab)


@app.get("/api/sports/leagues")
def api_sports_leagues():
    return scoreboard.league_list()


@app.get("/api/sports/teams")
def api_sports_teams(league: str):
    """Every team of a league, for Settings › Favorite teams."""
    if league not in scoreboard.LEAGUES:
        raise HTTPException(404, "Unknown league")
    try:
        return scoreboard.teams(league)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Couldn't get the teams from ESPN: {e}")


@app.get("/api/sports/stats")
def api_sports_stats(ch: str = "", feed: str = "", id: str = ""):
    """Box score of the game on channel `ch` or event feed `feed` (or game `id`), for the stats panel
    while watching, and the other live games. "game" is null when the scoreboard doesn't know a game
    there; "others" still lists every live game, so the panel can open on its Live scores tab.
    "enabled" is false when no leagues or favorite teams are picked in Setup › Settings › Sports."""
    if not scoreboard.selected():
        return {"game": None, "others": [], "enabled": False}
    gid = id or gamestats.game_for(ch=ch, feed=feed)
    game = gamestats.summary(gid) if gid else None
    return {"game": game, "others": gamestats.others(gid if game else None), "enabled": True}


@app.get("/api/sports/alerts")
def api_sports_alerts():
    """Favorite teams' games on now or starting soon; each TV screen pops one up when it starts."""
    return sports.favorite_alerts()


@app.get("/api/sports/group")
def api_sports_group(name: str):
    g = sports.group_feeds(name)
    if not g:
        raise HTTPException(404)
    return g


@app.post("/api/sports/check")
def api_sports_check(group: str = Body("", embed=True)):
    return {"started": sports.start_check(group or None), **sports.check_state}


@app.get("/api/sports/check")
def api_sports_check_state():
    return sports.check_state


@app.get("/api/preview/{key:path}")
async def api_preview(request: Request, key: str, client: str = ""):
    rows = await anyio.to_thread.run_sync(guide.stream_rows, [key])
    if not rows:
        raise HTTPException(404)
    return await _live(request, rows[0], client, "", "aac", -1)


@app.post("/api/clientlog")
async def api_clientlog(request: Request):
    """Errors from the TV page (sent with sendBeacon), so lock-ups show up in tunerbox.log."""
    try:
        d = json.loads((await request.body())[:2000] or b"{}")
    except ValueError:
        d = {}
    log.warning("Page: %s [%s]", str(d.get("msg", ""))[:500], str(d.get("ua", ""))[:120])
    return {"ok": True}


@app.post("/api/stop")
def api_stop(client: str = Body(..., embed=True)):
    stop_client(client)
    hls.stop_client(client)
    return {"ok": True}


@app.get("/api/direct/{number}")
def api_direct(request: Request, number: int, alt: int = 0):
    """Original stream URL (for 'open in VLC/mpv'). It can hold the provider login, so with the setup
    lock on only this PC and paired phones get it."""
    if config.get("require_pairing"):
        _trusted(request)
    c = guide.channel(number)
    rows = guide.stream_rows(c["streams"]) if c else []
    if alt >= len(rows):
        raise HTTPException(404)
    return {"url": rows[alt]["url"].split("|")[0]}


# ------------------------------------------------------------------ lineup mapping (setup)
@app.get("/api/lineup")
def api_lineup():
    maps = {m["number"]: m for m in db.rows("SELECT * FROM mappings")}
    streams = {s["key"]: s for s in db.rows(
        """SELECT s.key, s.name, s.grp, s.status, s.tvg_id, s.logo, src.name AS source_name
           FROM streams s JOIN sources src ON src.id=s.source_id""")}
    out = []
    for ch in lineup.all_channels():
        m = maps.get(ch["number"]) or {}
        s = streams.get(m.get("stream_key"))
        out.append({**ch, "mode": m.get("mode", ""), "score": m.get("score", 0), "reason": m.get("reason", ""),
                    "stream": s, "alts": len(json.loads(m.get("alt_keys") or "[]"))})
    return out


@app.get("/api/suggest")
def api_suggest(number: int | None = None, q: str = "", limit: int = 60, source_id: int | None = None):
    return matcher.suggest(number, q, limit, source_id)


@app.post("/api/mapping")
def api_mapping(number: int = Body(...), stream_key: str | None = Body(None), mode: str = Body("manual")):
    if mode == "auto":
        db.run("DELETE FROM mappings WHERE number=?", (number,))
        jobs.rematch()
    elif mode == "none":
        db.run("INSERT OR REPLACE INTO mappings(number, stream_key, alt_keys, mode, score, reason) VALUES (?,?,?,?,?,?)",
               (number, None, "[]", "none", 0, "turned off"))
    else:
        if not db.one("SELECT key FROM streams WHERE key=?", (stream_key,)):
            raise HTTPException(400, "Unknown stream")
        db.run("INSERT OR REPLACE INTO mappings(number, stream_key, alt_keys, mode, score, reason) VALUES (?,?,?,?,?,?)",
               (number, stream_key, "[]", "manual", 100, "manual"))
    if mode != "auto":
        matcher.rebuild_extras()
    guide.invalidate()
    return {"ok": True}


@app.get("/api/extras")
def api_extras():
    return db.rows("""SELECT e.number, e.enabled, e.stream_key, s.name, s.grp, s.status, s.logo, s.tvg_id,
                             src.name AS source_name
                      FROM extras e JOIN streams s ON s.key=e.stream_key JOIN sources src ON src.id=s.source_id
                      WHERE e.active=1 ORDER BY e.number""")


@app.post("/api/extras")
def api_extras_update(stream_key: str = Body(...), number: int | None = Body(None), enabled: bool | None = Body(None)):
    if number is not None:
        clash = db.one("SELECT stream_key FROM extras WHERE number=? AND stream_key!=?", (number, stream_key))
        if clash or db.one("SELECT 1 FROM lineup WHERE number=?", (number,)):
            raise HTTPException(400, f"Channel {number} is already taken")
        db.run("UPDATE extras SET number=? WHERE stream_key=?", (number, stream_key))
    if enabled is not None:
        db.run("UPDATE extras SET enabled=? WHERE stream_key=?", (int(enabled), stream_key))
    guide.invalidate()
    return {"ok": True}


@app.post("/api/extras/bulk")
def api_extras_bulk(group: str = Body(...), enabled: bool = Body(...)):
    db.run("""UPDATE extras SET enabled=? WHERE stream_key IN (SELECT key FROM streams WHERE grp=?)""",
           (int(enabled), group))
    guide.invalidate()
    return {"ok": True}


# ------------------------------------------------------------------ sources
@app.get("/api/sources")
def api_sources():
    out = db.rows("SELECT * FROM sources ORDER BY id")
    for s in out:
        s["password"] = "••••" if s["password"] else ""
        s["count"] = db.one("SELECT count(*) n FROM streams WHERE source_id=?", (s["id"],))["n"]
        s["hidden"] = len(sources.hidden_groups(s))
        s["vod"] = vod.source_stats(s)
        s["conn_auto"] = connections.auto_limit(s)       # what the provider / device says, shown as "Automatic (2)"
        if s["type"] == "xtream":
            s["account"] = sources.account(s)            # expiry date, status, trial (read at each scan)
            if s["enabled"] and time.time() - ((s["account"] or {}).get("checked") or 0) > sources.ACCOUNT_TTL:
                full = db.one("SELECT * FROM sources WHERE id=?", (s["id"],))     # with the real password
                sources.refresh_account_later(full)
        s.pop("hidden_groups", None)
        s.pop("vod_hidden", None)
    return out


def _disk_path_ok(request, type_, url):
    """A playlist that's a file on this computer (not a web address) is read from its disk: only this PC
    or a paired device may point TunerBox at files."""
    url = (url or "").strip()
    if type_ == "m3u" and url and not re.match(r"^https?://", url, re.I) and not remote.can_pair(request):
        raise HTTPException(403, "Playlist files on disk can only be added on the TunerBox PC or a paired device "
                                 "while Setup is locked. Use a web address (http://…), or pair this device in Setup › Phones.")


@app.post("/api/sources")
def api_add_source(request: Request, data: dict = Body(...)):
    if not data.get("url"):
        raise HTTPException(400, "URL / server is required")
    _disk_path_ok(request, data.get("type", "m3u"), data["url"])
    try:
        sid = sources.add_source(data.get("name", ""), data.get("type", "m3u"), data["url"].strip(),
                                 data.get("username", "").strip(), data.get("password", "").strip(),
                                 data.get("exclude_groups", ""), data.get("xtream_format", "ts"),
                                 data.get("video_mode") or None)
    except Exception as e:  # noqa: BLE001
        if data.get("type") == "hdhomerun":
            raise HTTPException(400, f"Can't reach an HDHomeRun at {data['url'].strip()}: {e}")
        raise
    return {"id": sid}


@app.get("/api/sources/export")
def api_export_sources(request: Request):
    """Download every playlist (logins included) as a JSON file. Only this PC or a paired phone
    may: the file holds passwords."""
    if not remote.trusted(request):
        raise HTTPException(403, "Export from the TunerBox PC or a paired phone (Setup › Phones)")
    body = json.dumps(sources.export_sources(), indent=2, ensure_ascii=False)
    name = f"tunerbox-playlists-{time.strftime('%Y-%m-%d')}.json"
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@app.post("/api/sources/import")
def api_import_sources(request: Request, data: dict = Body(...)):
    for pl in (data.get("playlists") or []) if isinstance(data, dict) else []:
        if isinstance(pl, dict):
            _disk_path_ok(request, pl.get("type", "m3u"), str(pl.get("url") or ""))
    try:
        r = sources.import_sources(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    guide.invalidate()
    if r["added"] or r["updated"]:
        r["job"] = "scan" if jobs.scan() else ""        # read the new / changed playlists
    return r


@app.get("/api/hdhomerun/discover")
def api_hdhr_discover():
    return sources.discover_hdhomerun()


@app.patch("/api/sources/{sid}")
def api_edit_source(request: Request, sid: int, data: dict = Body(...)):
    before = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    if not before:
        raise HTTPException(404)
    if isinstance(data.get("url"), str) and data["url"].strip() != before["url"]:
        _disk_path_ok(request, before["type"], data["url"])
    for k in ("name", "url", "username", "password"):
        if isinstance(data.get(k), str):
            data[k] = data[k].strip()
    if "password" in data and data["password"] in ("", "••••"):
        data.pop("password")                    # left blank in the Edit form: keep the saved one
    if "name" in data and not data["name"]:
        raise HTTPException(400, "Give the playlist a name")
    if "url" in data:
        if not data["url"]:
            raise HTTPException(400, "URL / server is required")
        if before["type"] == "hdhomerun" and data["url"] != before["url"]:
            try:
                sources.hdhr_info(data["url"])      # fail now, with a clear message, if it isn't a tuner
            except Exception as e:  # noqa: BLE001
                raise HTTPException(400, f"Can't reach an HDHomeRun at {data['url']}: {e}") from None
            data["url"] = sources.hdhr_base(data["url"])
    if "max_conn" in data:
        try:
            data["max_conn"] = max(0, min(int(data["max_conn"] or 0), 99))
        except (TypeError, ValueError):
            raise HTTPException(400, "Connections must be a number") from None
    for k in ("name", "url", "username", "password", "exclude_groups", "xtream_format", "enabled", "video_mode", "max_conn"):
        if k in data:
            db.run(f"UPDATE sources SET {k}=? WHERE id=?", (data[k], sid))
    job = ""
    src = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    if any(k in data and data[k] != before[k] for k in ("url", "username", "password", "xtream_format")):
        job = "scan" if jobs.scan() else ""      # a new address or login: read the playlist again
    turned = {k: int(bool(data[k])) for k in ("vod_movies", "vod_series") if src and k in data and int(bool(data[k])) != src[k]}
    if turned:              # On Demand switched on or off for this playlist
        for k, on in turned.items():
            db.run(f"UPDATE sources SET {k}=? WHERE id=?", (on, sid))
            if not on:
                vod.clear(sid, vod.KINDS if src["type"] != "xtream" else ("movie",) if k == "vod_movies" else ("series",))
        if src["type"] != "xtream":
            job = "refresh" if jobs.quick_refresh(queue=True) else ""     # its video files move between channels and On Demand
        elif any(turned.values()):
            kinds = tuple(k for k, key in (("movie", "vod_movies"), ("series", "vod_series")) if turned.get(key))
            job = "vod" if jobs.vod_scan(sid, kinds) else ""
    guide.invalidate()
    return {"ok": True, "job": job}


def _kind(kind):
    if kind not in vod.KINDS:
        raise HTTPException(400, "Unknown kind")
    return kind


@app.get("/api/sources/{sid}/vod-cats")
def api_source_vod_cats(sid: int, kind: str = "movie"):
    g = vod.cats(sid, _kind(kind))
    if not g:
        raise HTTPException(404)
    return g


@app.put("/api/sources/{sid}/vod-cats")
def api_source_vod_cats_save(sid: int, hidden: list[str] = Body(..., embed=True), kind: str = "movie"):
    r = vod.set_hidden(sid, hidden, _kind(kind))
    if r is None:
        raise HTTPException(404)
    if r.pop("missing"):    # shown again, but never read (hidden since before titles were kept): read them
        src = db.one("SELECT type FROM sources WHERE id=?", (sid,))
        r["job"] = ("vod" if jobs.vod_scan(sid, (kind,)) else "") if src["type"] == "xtream" \
            else ("refresh" if jobs.quick_refresh(queue=True) else "")
    return r


@app.get("/api/sources/{sid}/vod-tags")
def api_source_vod_tags(sid: int):
    g = vod.tags(sid)
    if not g:
        raise HTTPException(404)
    return g


@app.put("/api/sources/{sid}/vod-tags")
def api_source_vod_tags_save(sid: int, hidden: list[str] = Body(..., embed=True)):
    r = vod.set_tags(sid, hidden)
    if r is None:
        raise HTTPException(404)
    return r


@app.get("/api/sources/{sid}/groups")
def api_source_groups(sid: int):
    g = sources.groups(sid)
    if not g:
        raise HTTPException(404)
    return g


@app.put("/api/sources/{sid}/groups")
def api_source_groups_save(sid: int, hidden: list[str] = Body(..., embed=True)):
    r = sources.set_hidden_groups(sid, hidden)
    if r is None:
        raise HTTPException(404)
    guide.invalidate()
    if r["shown_again"]:
        r["job"] = "scan" if jobs.scan() else ""        # the playlist has to be read again
    elif r["newly_hidden"]:
        r["job"] = "match" if jobs.rematch() else ""     # re-match + renumber without those groups
    return r


@app.delete("/api/sources/{sid}")
def api_del_source(sid: int):
    db.run("DELETE FROM streams WHERE source_id=?", (sid,))
    db.run("DELETE FROM sources WHERE id=?", (sid,))
    vod.clear(sid)
    guide.invalidate()
    return {"ok": True}


# ------------------------------------------------------------------ jobs & settings
@app.post("/api/jobs/{name}")
def api_job(name: str, force: int = 0):
    fn = {"scan": lambda: jobs.scan(bool(force)), "epg": jobs.refresh_epg, "match": jobs.rematch, "refresh": jobs.quick_refresh,
          "vod": jobs.vod_scan}.get(name)
    if not fn:
        raise HTTPException(404)
    return {"started": fn()}


@app.get("/api/jobs")
def api_jobs():
    return {**jobs.state, "epg_updated": int(db.get_meta("epg_updated", 0) or 0),
            "scan_updated": int(db.get_meta("scan_updated", 0) or 0)}


def _public_settings():
    # vod_available: some playlist has On Demand turned on (screens leave On Demand out otherwise). Read-only.
    return {**{k: v for k, v in config.load().items() if k != "devices"}, "vod_available": vod.enabled()}


def _dvr_on():
    if not dvr.enabled():
        raise HTTPException(409, "Recordings are turned off. Turn them on in Setup › Settings › DVR.")


@app.get("/api/settings")
def api_settings():
    return _public_settings()


@app.post("/api/settings")
def api_save_settings(request: Request, data: dict = Body(...)):
    try:
        # a NAS has no "PC": there the home network (until locked) and paired devices stand in for it.
        # The lock itself only from the PC or a paired device, so whoever turns it on can still get in.
        if ("require_pairing" in data and bool(data["require_pairing"]) != bool(config.get("require_pairing"))
                and not remote.trusted(request)):
            raise PermissionError("Pair this browser first (Setup › Phones), so it can still change things "
                                  "once Setup is locked")
        data = config.clean_changes(data, from_pc=remote.acts_as_pc(request))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    before = (config.get("gracenote") or {}).get("lineup_id") or ""
    leagues = scoreboard.selected()
    fav_teams = config.get("sports_teams")
    adult = config.get("vod_show_adult")
    config.update(data)
    if config.get("vod_show_adult") != adult:
        jobs.vod_scan()             # adult titles are left out (or kept) while the lists are read
    if scoreboard.selected() != leagues or config.get("sports_teams") != fav_teams:
        scoreboard.tick(force=True)     # fetch the newly picked leagues (and teams' games) now, not at the next refresh
    after = (config.get("gracenote") or {}).get("lineup_id") or ""
    if before and after != before:
        # a different provider has different channel numbers: drop the old ones (your
        # channels.csv, if any, is read again); the next scan loads the new provider's
        with db.conn() as c:
            c.execute("DELETE FROM lineup")
            c.execute("DELETE FROM programs WHERE channel LIKE 'gn:%'")
        db.set_meta("gn_lineup", "")
        lineup.load_csv()
    guide.invalidate()
    return _public_settings()


# ------------------------------------------------------------------ phone pairing + remote
def _pc_only(request: Request):
    if not remote.is_pc(request):
        raise HTTPException(403, "Only on the TunerBox PC")


def _manager(request: Request):
    """Pairing and the paired-phone list (see remote.can_pair)."""
    if not remote.can_pair(request):
        raise HTTPException(403, "Only on the TunerBox PC or a paired phone")


def _trusted(request: Request):
    if not remote.trusted(request):
        raise HTTPException(401, "Pair this phone first")


@app.get("/api/me")
def api_me(request: Request):
    d = remote.device(request)
    return {"pc": remote.is_pc(request), "paired": bool(d), "name": d["name"] if d else None,
            "manage": remote.can_pair(request), "admin": remote.acts_as_pc(request), "server": remote.trust_lan(),
            "require_pairing": bool(config.get("require_pairing")), "http_base": _http_base(request)}


@app.get("/api/https")
def api_https():
    """Setup › Settings › HTTPS: on or off, the port, any problem, what the certificate covers."""
    return https.status()


@app.get("/tunerbox-ca.crt")
def api_https_ca():
    """TunerBox's certificate authority, for a phone or PC to trust once (only the public certificate)."""
    path = https.ca_file()
    if not path.exists():
        try:
            https.ensure()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"Couldn't make the certificate: {e}") from e
    return FileResponse(path, media_type="application/x-x509-ca-cert", filename="tunerbox-ca.crt",
                        headers={"Cache-Control": "no-store"})


IMG_MAX = 5_000_000
_img_lock = threading.Lock()


def _img_fetch(u):
    """A picture from a plain-http site, kept on disk a day (logos, posters): pages on the HTTPS port
    can't load http pictures themselves. Only images, at most 5 MB."""
    key = hashlib.sha1(u.encode("utf-8")).hexdigest()
    folder = config.DATA / "cache" / "img"
    path, kind = folder / key, folder / f"{key}.type"
    if path.exists() and kind.exists() and time.time() - path.stat().st_mtime < 86400:
        return path, kind.read_text(encoding="utf-8")
    try:
        with httpx.stream("GET", u, timeout=10, follow_redirects=True,
                          headers={"User-Agent": config.get("user_agent") or "TunerBox"}) as r:
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if r.status_code != 200 or not ctype.startswith("image/") or ctype == "image/svg+xml":
                return None, ""
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > IMG_MAX:
                    return None, ""
    except (httpx.HTTPError, OSError):
        return None, ""
    with _img_lock:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{key}.part"
        tmp.write_bytes(data)
        tmp.replace(path)
        kind.write_text(ctype, encoding="utf-8")
    return path, ctype


@app.get("/api/img")
async def api_img(u: str):
    if not u.lower().startswith("http://") or len(u) > 2000:
        raise HTTPException(400, "Only http:// pictures")
    path, ctype = await anyio.to_thread.run_sync(_img_fetch, u)
    if not path:
        raise HTTPException(404)
    return FileResponse(path, media_type=ctype, headers={"Cache-Control": "public, max-age=86400",
                                                         "X-Content-Type-Options": "nosniff"})


@app.post("/api/pair/start")
def api_pair_start(request: Request):
    _manager(request)
    p = remote.start_pairing()
    # always plain http (a phone scanning it needs no certificate), even when Setup is open over HTTPS
    port = https.http_port() if request.url.scheme == "https" else (request.url.port or config.get("port", 8642))
    # the address this browser used (a NAS's own address, not the container's), else this PC's
    host = request.url.hostname or ""
    if not host or host in ("localhost", "127.0.0.1", "::1"):
        host = remote.lan_ip()
    if ":" in host:                 # IPv6 literal
        host = f"[{host}]"
    base = f"http://{host}:{port}"
    link = f"{base}/pair#{p['code']}.{p['key']}"
    return {"code": p["code"], "expires": p["expires"], "url": link, "address": f"{base}/pair",
            "qr": remote.qr_svg(link)}


@app.get("/api/pair/state")
def api_pair_state(request: Request):
    _manager(request)
    return {**remote.pairing_state(), "devices": remote.devices()}


@app.post("/api/pair/cancel")
def api_pair_cancel(request: Request):
    _manager(request)
    remote.cancel_pairing()
    return {"ok": True}


@app.post("/api/pair/complete")
def api_pair_complete(request: Request, code: str = Body(...), name: str = Body("Phone"), key: str = Body("")):
    try:
        token = remote.complete_pairing(code, name, key)
    except ValueError as e:
        raise HTTPException(403, str(e))
    log.info("Phone paired: %s from %s", name[:40], request.client.host if request.client else "?")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(remote.COOKIE, token, max_age=60 * 60 * 24 * 365 * 5, httponly=True, samesite="lax", path="/")
    return resp


@app.post("/api/pair/forget")
def api_pair_forget(request: Request):
    """A phone un-pairing itself."""
    d = remote.device(request)
    if d:
        remote.remove_device(d["id"])
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(remote.COOKIE, path="/")
    return resp


@app.get("/api/devices")
def api_devices(request: Request):
    _manager(request)
    return remote.devices()


@app.patch("/api/devices/{did}")
def api_device_rename(request: Request, did: str, name: str = Body(..., embed=True)):
    _manager(request)
    remote.rename_device(did, name)
    return remote.devices()


@app.delete("/api/devices/{did}")
def api_device_remove(request: Request, did: str):
    _manager(request)
    remote.remove_device(did)
    return remote.devices()


@app.get("/api/remote/events")
async def api_remote_events(request: Request, screen: str, name: str = ""):
    """Each TV screen listens here for the buttons a phone presses."""
    return LiveResponse(remote.events(request, screen[:40], name), media_type="text/event-stream",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/remote/state")
async def api_remote_state(screen: str = Body(...), state: dict = Body(...)):
    remote.set_state(screen[:40], state)
    return {"ok": True}


@app.get("/api/remote/screens")
async def api_remote_screens(request: Request):
    _trusted(request)
    remote.phone_seen(request)
    return remote.screens()


@app.get("/api/idle")
def api_idle():
    """What keeps a TV screen from sleeping or starting its screensaver."""
    rec = len(dvr._active)
    phones = remote.phones_open()
    return {"recording": rec, "phones": phones, "busy": bool(rec or phones)}


@app.post("/api/remote/send")
async def api_remote_send(request: Request, screen: str = Body(...), cmd: dict = Body(...)):
    _trusted(request)
    remote.phone_seen(request)
    if cmd.get("type") not in ("key", "tune", "feed", "volume", "power"):
        raise HTTPException(400, "Unknown command")
    if not remote.send(screen, cmd):
        raise HTTPException(404, "That TV isn't on. Turn it on or pick another one.")
    return {"ok": True}


@app.get("/api/gracenote/providers")
def api_providers(zip: str, country: str = "USA"):
    return epg.gracenote_providers(zip, country)


@app.get("/api/stats")
def api_stats():
    return {
        "lineup": db.one("SELECT count(*) n FROM lineup")["n"],
        "mapped": db.one("SELECT count(*) n FROM mappings WHERE stream_key IS NOT NULL")["n"],
        "manual": db.one("SELECT count(*) n FROM mappings WHERE mode='manual'")["n"],
        "streams": db.one("SELECT count(*) n FROM streams")["n"],
        "working": db.one("SELECT count(*) n FROM streams WHERE status='ok'")["n"],
        "dead": db.one("SELECT count(*) n FROM streams WHERE status='dead'")["n"],
        "extras": db.one("SELECT count(*) n FROM extras WHERE active=1")["n"],
        "programs": db.one("SELECT count(*) n FROM programs")["n"],
    }


@app.get("/api/overview")
def api_overview():
    """Setup › Overview: is everything working? One request for the health cards."""
    maps = db.rows("SELECT mode, score, reason, stream_key FROM mappings")
    weak = sum(1 for m in maps if m["stream_key"] and m["mode"] == "auto" and (m["score"] < 92 or "network" in (m["reason"] or "").lower()))
    srcs = db.rows("SELECT id, name, type, enabled, status, updated_at FROM sources ORDER BY id")
    for s in srcs:
        s["count"] = db.one("SELECT count(*) n FROM streams WHERE source_id=?", (s["id"],))["n"]
    gn = config.get("gracenote") or {}
    recs = {r["status"]: r["n"] for r in db.rows("SELECT status, count(*) n FROM recordings WHERE status IN ('scheduled','recording') GROUP BY status")}
    vc = vod.counts()
    return {
        "lineup": db.one("SELECT count(*) n FROM lineup")["n"],
        "mapped": sum(1 for m in maps if m["stream_key"]), "manual": sum(1 for m in maps if m["mode"] == "manual"), "weak": weak,
        "extras": db.one("SELECT count(*) n FROM extras WHERE active=1")["n"],
        "playlists": srcs,
        "guide": {"programs": db.one("SELECT count(*) n FROM programs WHERE stop > ?", (int(time.time()),))["n"],
                  "updated": int(db.get_meta("epg_updated", 0) or 0), "gracenote": bool(gn.get("enabled")),
                  "postal_code": gn.get("postal_code", ""), "lineup_id": gn.get("lineup_id", "")},
        "scanned": int(db.get_meta("scan_updated", 0) or 0),
        "dvr": {"scheduled": recs.get("scheduled", 0), "recording": recs.get("recording", 0), "free": dvr._free_bytes()},
        "library": library.counts(), "libraries": len(library.shares()),
        "vod": {"movies": vc["movie"]["total"], "shows": vc["series"]["total"]},
        "local_channels": db.one("SELECT count(*) n FROM media_channels WHERE enabled=1")["n"],
    }


# ------------------------------------------------------------------ On Demand (movies and shows from the playlists)
@app.get("/api/vod")
def api_vod():
    return vod.counts()


@app.get("/api/vod/categories")
def api_vod_categories(kind: str = "movie"):
    return vod.categories(_kind(kind))


@app.get("/api/vod/titles")
def api_vod_titles(kind: str = "movie", cat: str | None = None, tab: str = "", offset: int = 0, limit: int = 120):
    if tab not in ("", "continue", "list"):
        raise HTTPException(400, "Unknown list")
    return vod.titles(_kind(kind), cat, tab, max(0, offset), max(1, min(limit, 500)))


@app.get("/api/vod/search")
def api_vod_search(q: str = "", limit: int = 60):
    return vod.search(q[:100], max(1, min(limit, 200)))


@app.get("/api/vod/title/{title_id}")
def api_vod_title(title_id: str):
    t = vod.title(title_id)
    if not t:
        raise HTTPException(404, "That title is no longer in the playlist")
    return t


@app.get("/api/vod/poster/{title_id}")
async def api_vod_poster(title_id: str):
    """A small copy of a title's poster for TV grids (made once, then served from disk).
    Falls back to the provider's own image when it can't be shrunk."""
    path = await anyio.to_thread.run_sync(vod.poster_file, title_id)
    if path:
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=2592000"})
    url = await anyio.to_thread.run_sync(vod.poster_url, title_id)
    if url.lower().startswith(("http://", "https://")):
        return RedirectResponse(url, status_code=302)
    raise HTTPException(404)


def _vod_item(item_id, prepare=False):
    if catchup.is_item(item_id):                    # a show from a channel's catch-up archive
        it = catchup.item(item_id)
        if not it:
            raise HTTPException(404, "That show is no longer in the channel's archive")
        return it
    it = (vod.prepare if prepare else vod.item)(item_id)
    if not it:
        raise HTTPException(404, "That title is no longer in the playlist")
    return it


def _vod_source(it, ss):
    """(address, start in it) to play an On Demand title or a catch-up show from ss seconds in."""
    if it.get("catchup"):
        return catchup.url_at(it, ss)
    return it["url"], ss


def _vod_label(it):
    return " · ".join(str(x) for x in (it.get("title"), it.get("sub")) if x)


@app.get("/api/vod/play/{item_id}")
def api_vod_play(item_id: str):
    """What the player needs before it starts: title, length, where to resume, the next episode."""
    it = _vod_item(item_id, prepare=True)
    return {k: v for k, v in it.items() if k not in ("url", "video_mode", "row")}     # the address holds the provider login


@app.get("/api/vod/stream/{item_id}")
async def api_vod_stream(request: Request, item_id: str, ss: float = 0.0, client: str = "", mode: str = "",
                         name: str = ""):
    connections.name_client(client, name)
    it = await anyio.to_thread.run_sync(_vod_item, item_id)
    url, ss = await anyio.to_thread.run_sync(_vod_source, it, max(0.0, ss))
    if not url:
        raise HTTPException(404, "Can't play this")
    cmd = vod.stream_cmd(url, ss, mode or it["video_mode"] or None, catchup.pace(ss) if it.get("catchup") else None)
    proc = await _started(lambda: streaming.start_cmd(cmd, client or None, url, _vod_label(it), it["source_id"]))
    return LiveResponse(ffmpeg_stream(url, client or None, request=request, buffer=0, proc=proc),
                        media_type="video/mp2t", headers={"Cache-Control": "no-store"},
                        cleanup=lambda: streaming.release(proc, client or None))


@app.get("/api/vod/hls/{item_id}")
async def api_vod_hls(item_id: str, ss: float = 0.0, client: str = "", mode: str = ""):
    it = await anyio.to_thread.run_sync(_vod_item, item_id)
    url, ss = await anyio.to_thread.run_sync(_vod_source, it, max(0.0, ss))
    if not url:
        raise HTTPException(404, "Can't play this")
    sid = await _started(lambda: hls.start(url, client or None, mode or it["video_mode"] or None, 0,
                                           vod.hls_pre(ss, catchup.pace(ss) if it.get("catchup") else None),
                                           _vod_label(it), it["source_id"]))
    if not await hls.wait_ready(sid):
        raise HTTPException(502, "Can't play this title")
    return RedirectResponse(f"/api/hls/s/{sid}/index.m3u8", status_code=302)


@app.post("/api/vod/progress")
def api_vod_progress(id: str = Body(...), position: float = Body(...), duration: float = Body(0)):
    if catchup.is_item(id):
        return {"watched": False}                   # catch-up shows aren't kept in Continue Watching
    r = vod.set_progress(id, position, duration)
    if r is None:
        raise HTTPException(404)
    return r


@app.post("/api/vod/watched")
def api_vod_watched(id: str = Body(...), watched: bool = Body(True)):
    if catchup.is_item(id):
        return {}
    return vod.set_watched(id, watched)


@app.post("/api/vod/list")
def api_vod_list(id: str = Body(...), on: bool = Body(True)):
    r = vod.set_listed(id, on)
    if r is None:
        raise HTTPException(404)
    return r


# ------------------------------------------------------------------ reminders
@app.get("/api/reminders")
def api_reminders():
    """Reminders for programs that haven't ended; each TV screen pops one up when its program starts."""
    return reminders.upcoming()


@app.post("/api/reminders")
def api_remind(number: int = Body(...), start: int = Body(...)):
    r = reminders.add(number, start)
    if not r:
        raise HTTPException(400, "No guide data for that program")
    return r


@app.delete("/api/reminders/{rid}")
def api_remind_cancel(rid: int):
    reminders.remove(rid)
    return {"ok": True}


@app.get("/api/tuners")
def api_tuners():
    """Setup › Tuners: each running tuner and On Demand stream, who's on it, and each playlist's connection limit."""
    return connections.in_use()


@app.get("/api/stopped")
def api_stopped(client: str = ""):
    """A TV screen whose stream ended asks this before reconnecting or trying a backup feed: true when
    Setup › Tuners › Stop ended it, so it says so instead."""
    return {"stopped": connections.was_stopped(client)}


@app.post("/api/tuners/stop")
def api_tuner_stop(request: Request, id: str = Body(..., embed=True)):
    """Setup › Tuners › Stop: force-stop a stuck or unwanted stream and free its connection. Every screen
    on it gets the end of the stream; a recording on it is stopped too (else the DVR would reopen it)."""
    if config.get("require_pairing"):
        _trusted(request)
    recs = connections.stop(id, cancel=dvr.cancel)
    if recs is None:
        raise HTTPException(404, "That stream has already stopped")
    log.info("Setup stopped stream %s (recordings stopped: %s)", id, recs or "none")
    return {"ok": True, "recordings": recs}


# ------------------------------------------------------------------ DVR
@app.get("/api/recordings")
def api_recordings():
    """Every recording; `conflict` says why an upcoming one won't get a connection ("" when it will)."""
    rows = db.rows("SELECT * FROM recordings ORDER BY CASE status WHEN 'recording' THEN 0 WHEN 'scheduled' THEN 1 ELSE 2 END, "
                   "CASE WHEN status IN ('scheduled','recording') THEN start ELSE -start END")
    clash = dvr.conflicts()
    for r in rows:
        r["conflict"] = clash.get(r["id"], "") if r["status"] == "scheduled" else ""
    return rows


def _clashes(ids):
    """Conflict texts for the given recording ids, for the message after scheduling."""
    clash = dvr.conflicts()
    return [clash[i] for i in ids if i in clash]


@app.post("/api/recordings")
def api_record(number: int = Body(...), start: int = Body(...), series: bool = Body(False),
               new_only: bool = Body(False), any_channel: bool = Body(False)):
    _dvr_on()
    if series:
        c = guide.channel(number)
        p = db.one("SELECT * FROM programs WHERE channel=? AND start<=? AND stop>?", (c["epg"], start, start)) if c and c["epg"] else None
        if not p:
            raise HTTPException(400, "No guide data for that program")
        rid = dvr.add_rule(p["title"], None if any_channel else number, new_only)
        dvr.schedule_program(number, start)
        ids = [r["id"] for r in db.rows("SELECT id FROM recordings WHERE status='scheduled' AND (rule_id=? OR (number=? AND start=?))",
                                        (rid, number, p["start"]))]
        return {"ok": True, "conflicts": _clashes(ids)}
    rec = dvr.schedule_program(number, start)
    if not rec:
        raise HTTPException(400, "No guide data for that program")
    dvr.tick()
    return {**rec, "conflicts": _clashes([rec["id"]])}


@app.post("/api/recordings/manual")
def api_record_manual(number: int = Body(...), start: int = Body(...), minutes: int = Body(...), title: str = Body("")):
    _dvr_on()
    return dvr.schedule(number, start, start + minutes * 60, title)


@app.delete("/api/recordings/{rid}")
def api_cancel(request: Request, rid: int, delete: int = 0):
    if delete and config.get("require_pairing"):
        _trusted(request)        # deleting a recorded file is a setup change; cancelling isn't
    dvr.cancel(rid, bool(delete))
    return {"ok": True}


@app.post("/api/recordings/{rid}/progress")
def api_rec_progress(rid: int, position: float = Body(...), duration: float = Body(0)):
    """Where playback of a recording got to, so any screen can resume it (Home › Continue Watching)."""
    dvr.progress(rid, position, duration)
    return {"ok": True}


@app.get("/api/recordings/{rid}/art")
async def api_rec_art(rid: int):
    r = await anyio.to_thread.run_sync(db.one, "SELECT image FROM recordings WHERE id=?", (rid,))
    url = epg.image_url((r or {}).get("image") or "")
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(404)
    path = await anyio.to_thread.run_sync(vod.small_image, url, ART_W)
    if path:
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
    return RedirectResponse(url, status_code=302)


@app.get("/api/home")
def api_home(recent: str = ""):
    """Everything the Home screen shows. `recent`: this screen's last channels, newest first."""
    nums = [int(n) for n in recent.split(",")[:20] if n.strip().isdigit()]
    return home.overview(nums)


@app.get("/api/recordings/{rid}/file")
def api_rec_file(rid: int):
    r = db.one("SELECT file FROM recordings WHERE id=?", (rid,))
    if not r or not r["file"] or not Path(r["file"]).exists():
        raise HTTPException(404)
    media = "video/mp4" if r["file"].endswith(".mp4") else "video/mp2t"
    return FileResponse(r["file"], media_type=media)


@app.get("/api/rules")
def api_rules():
    return db.rows("SELECT * FROM rules ORDER BY title")


@app.delete("/api/rules/{rid}")
def api_del_rule(rid: int):
    db.run("DELETE FROM rules WHERE id=?", (rid,))
    db.run("DELETE FROM recordings WHERE rule_id=? AND status='scheduled'", (rid,))
    return {"ok": True}


# ------------------------------------------------------------------ outputs for other apps
def _http_base(request: Request):
    """This server's plain-http address as the caller sees it. Opened over HTTPS (https.py), it's the
    same host on the http port instead: Plex, VLC and phones are given http addresses, which need no
    certificate. Over http it's exactly the address that was used (Docker port mappings included)."""
    if request.url.scheme != "https":
        return str(request.base_url).rstrip("/")
    host = request.url.hostname or remote.lan_ip()
    if ":" in host:                 # IPv6 literal
        host = f"[{host}]"
    return f"http://{host}:{https.http_port()}"


def _base(request: Request):
    return _http_base(request)


@app.get("/playlist.m3u")
def out_m3u(request: Request):
    base = _base(request)
    lines = [f'#EXTM3U x-tvg-url="{base}/epg.xml"']
    for c in guide.channels():
        lines.append(f'#EXTINF:-1 tvg-chno="{c["number"]}" tvg-id="ch{c["number"]}" tvg-name="{c["name"]}" '
                     f'tvg-logo="{c["logo"]}" group-title="{"Local Lineup" if c["kind"] == "lineup" else (c.get("group") or "Extra")}",'
                     f'{c["number"]} {c["name"]}')
        lines.append(f"{base}/api/stream/{c['number']}")
    return PlainTextResponse("\n".join(lines) + "\n", media_type="audio/x-mpegurl")


@app.get("/epg.xml")
def out_xmltv():
    def fmt(t):
        return time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(t))
    now = int(time.time())
    chans = guide.channels()
    progs = epg.programs({c["epg"] for c in chans if c["epg"]}, now - 3600, now + 3 * 86400)
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="TunerBox">']
    for c in chans:
        out.append(f'<channel id="ch{c["number"]}"><display-name>{escape(c["name"])}</display-name>'
                   f'<display-name>{c["number"]}</display-name>'
                   + (f'<icon src="{escape(c["logo"])}"/>' if c["logo"] else "") + "</channel>")
    for c in chans:
        for p in progs.get(c["epg"], []):
            out.append(f'<programme start="{fmt(p["start"])}" stop="{fmt(p["stop"])}" channel="ch{c["number"]}">'
                       f'<title>{escape(p["title"] or "")}</title>'
                       + (f'<sub-title>{escape(p["subtitle"])}</sub-title>' if p["subtitle"] else "")
                       + (f'<desc>{escape(p["descr"])}</desc>' if p["descr"] else "")
                       + (f'<category>{escape(p["category"])}</category>' if p["category"] else "")
                       + (f'<episode-num system="onscreen">{escape(p["episode"])}</episode-num>' if p["episode"] else "")
                       + (f'<icon src="{escape(epg.image_url(p["image"]))}"/>' if p["image"] else "")
                       + ("<new/>" if p["is_new"] else "") + "</programme>")
    out.append("</tv>")
    return Response("\n".join(out), media_type="application/xml")


# HDHomeRun emulation so Plex / Emby / Jellyfin can add TunerBox as a tuner
@app.get("/discover.json")
def hdhr_discover(request: Request):
    base = _base(request)
    return {"FriendlyName": config.box_name(), "Manufacturer": "Silicondust", "ModelNumber": "HDTC-2US",
            "FirmwareName": "hdhomeruntc_atsc", "FirmwareVersion": "20200101",
            "DeviceID": config.get("hdhr_device_id") or config.LEGACY_DEVICE_ID,
            "DeviceAuth": "tunerbox", "BaseURL": base, "LineupURL": f"{base}/lineup.json",
            "TunerCount": sources.tuner_count()}


@app.get("/lineup_status.json")
def hdhr_status():
    return {"ScanInProgress": 0, "ScanPossible": 1, "Source": "Cable", "SourceList": ["Cable"]}


@app.get("/lineup.json")
def hdhr_lineup(request: Request):
    base = _base(request)
    return [{"GuideNumber": str(c["number"]), "GuideName": c["name"],
             "URL": f"{base}/api/stream/{c['number']}?audio=copy"} for c in guide.channels()]


@app.post("/lineup.post")
def hdhr_post():
    return {}


# ------------------------------------------------------------------ local channels (folders of videos)
@app.get("/api/media/channels")
def api_media_channels():
    out = media.channels()
    for ch in out:
        np_ = media.now_playing(ch["id"]) if ch["item_count"] else None
        ch["now"] = {"title": np_[0]["title"], "subtitle": np_[0]["subtitle"], "episode": np_[0]["episode"]} if np_ else None
    return out


@app.get("/api/media/next-number")
def api_media_next():
    return {"number": media.next_number()}


@app.post("/api/media/channels")
def api_media_create(data: dict = Body(...)):
    try:
        cid = media.create(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    guide.invalidate()
    return {"id": cid, "job": jobs.media_scan(cid)}


@app.put("/api/media/channels/{cid}")
def api_media_update(cid: int, data: dict = Body(...)):
    try:
        rescan = media.update(cid, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not rescan:
        media.write_epg(cid)
    guide.invalidate()
    return {"ok": True, "job": jobs.media_scan(cid) if rescan else False}


@app.post("/api/media/channels/{cid}/scan")
def api_media_rescan(cid: int):
    return {"job": jobs.media_scan(cid)}


@app.delete("/api/media/channels/{cid}")
def api_media_delete(cid: int):
    media.delete(cid)
    guide.invalidate()
    return {"ok": True}


@app.get("/api/media/folder")
def api_media_folder(request: Request, path: str):
    """Quick check that TunerBox can open a folder (UNC shares need the PC to have access). It tells
    whether paths exist, so with the setup lock on only this PC and paired phones may ask."""
    if config.get("require_pairing"):
        _trusted(request)
    import os
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
    p = path.strip().strip('"')

    def count():
        if not os.path.isdir(p):
            return None
        n, end = 0, time.time() + 8
        for dp, _, fns in os.walk(p):
            n += sum(1 for f in fns if Path(f).suffix.lower() in media.VIDEO_EXT)
            if n > 2000 or time.time() > end:
                return -n
        return n
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        n = ex.submit(count).result(timeout=10)      # an offline \server can hang for half a minute
    except FutTimeout:
        return {"ok": False, "message": "no answer from that location (is the server/share online?)"}
    finally:
        ex.shutdown(wait=False)
    if n is None:
        return {"ok": False, "message": "TunerBox can't open this folder from the PC"}
    return {"ok": True, "videos": abs(n), "message": f"{abs(n)}{'+' if n < 0 else ''} videos found"}


@app.get("/api/media/piece/{cid}/{idx}")
async def api_media_piece(request: Request, cid: int, idx: int, ss: float = 0.0):
    """One file of a local channel in the channel's common format (read by TunerBox's own ffmpeg)."""
    if not (request.client and request.client.host in ("127.0.0.1", "::1")):
        raise HTTPException(403)
    cmd = await anyio.to_thread.run_sync(media.piece_cmd, cid, idx, ss)
    if not cmd:
        raise HTTPException(404)
    return LiveResponse(ffmpeg_stream("", None, request=request, buffer=0, cmd=cmd), media_type="video/mp2t")


# ------------------------------------------------------------------ local library (your own movies and shows)
def _lib_err(fn, *a):
    try:
        return fn(*a)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "Not found")
    except library.LibraryError as e:
        raise HTTPException(400, str(e))


@app.get("/api/library")
def api_library():
    return {"settings": library.public_settings(), "shares": library.shares(), "counts": library.counts()}


@app.post("/api/library/settings")
def api_library_settings(request: Request, data: dict = Body(...)):
    key_added = bool(data.get("tmdb_key")) and not library.settings()["tmdb_key"]
    if "tmdb_key" in data and not data["tmdb_key"] and not data.get("clear_key"):
        data.pop("tmdb_key")                 # a blank box keeps the saved key
    data.pop("clear_key", None)
    out = _lib_err(library.save_settings, data)
    if key_added:
        jobs.library_details()
    return out


@app.post("/api/library/test-key")
def api_library_test_key(key: str = Body("", embed=True)):
    return library.test_key(key or library.settings()["tmdb_key"])


def _lib_conn(data):
    """The library being added or edited, from what Setup sent (a blank password uses the saved one)."""
    old = library.share(int(data["id"])) if data.get("id") else None
    d = {**data}
    d.setdefault("kind", (old or {}).get("kind") or "movie")
    return library.clean(d, old)


def _lib_folder_ok(request, sh):
    if sh["type"] == "folder" and not remote.can_pair(request):
        raise HTTPException(403, "Folders on the TunerBox computer can only be added on the TunerBox PC or a paired "
                                 r"device while Setup is locked. Use a network location (\\nas\share), or pair this "
                                 "device in Setup › Phones.")


@app.post("/api/library/browse")
def api_library_browse(request: Request, data: dict = Body(...)):
    """Connect and list the folders in `path` (inside the location), to check a library and pick its folder."""
    sh = _lib_err(_lib_conn, data)
    _lib_folder_ok(request, sh)
    return library.check(sh, str(data.get("path") or ""))


@app.post("/api/library/shares")
def api_library_add(request: Request, data: dict = Body(...)):
    _lib_folder_ok(request, _lib_err(library.clean, data))
    sid = _lib_err(library.add, data)
    return {"id": sid, "job": jobs.library_scan(sid)}


@app.put("/api/library/shares/{sid}")
def api_library_update(request: Request, sid: int, data: dict = Body(...)):
    old = library.share(sid)
    if old and data.get("location") is not None:
        _lib_folder_ok(request, _lib_err(library.clean, data, old))
    moved = _lib_err(library.update, sid, data)
    return {"ok": True, "job": jobs.library_scan(sid) if moved and library.share(sid)["enabled"] else False}


@app.delete("/api/library/shares/{sid}")
def api_library_delete(sid: int):
    library.delete(sid)
    return {"ok": True}


@app.post("/api/library/scan")
def api_library_scan(share_id: int = Body(0, embed=True), refresh: bool = Body(False, embed=True)):
    return {"job": jobs.library_scan(share_id or None, refresh)}


@app.get("/api/library/titles")
def api_library_titles(share_id: int = 0, filter: str = "review", q: str = ""):
    return library.review(share_id or None, filter, q[:100])


@app.get("/api/library/tmdb")
def api_library_tmdb(kind: str, q: str, year: str = ""):
    if kind not in library.KINDS or not q.strip():
        raise HTTPException(400, "kind and q are needed")
    return _lib_err(library.search_tmdb, kind, q.strip()[:120], year.strip()[:4])


@app.post("/api/library/titles/{title_id}/match")
def api_library_match(title_id: str, tmdb_id: int = Body(..., embed=True)):
    return _lib_err(library.pin, title_id, tmdb_id)


@app.post("/api/library/titles/{title_id}/unmatch")
def api_library_unmatch(title_id: str):
    return _lib_err(library.unmatch, title_id)


@app.post("/api/library/titles/{title_id}/lookup")
def api_library_lookup(title_id: str):
    return _lib_err(library.relookup, title_id)


@app.post("/api/library/titles/{title_id}/ignore")
def api_library_ignore(title_id: str, on: bool = Body(True, embed=True)):
    return _lib_err(library.ignore, title_id, on)


@app.api_route("/api/library/file/{item_id}", methods=["GET", "HEAD"])
async def api_library_file(request: Request, item_id: str):
    """A library video's bytes, with ranges, for TunerBox's own ffmpeg (this computer only: no logins needed)."""
    if not (request.client and request.client.host in ("127.0.0.1", "::1")):
        raise HTTPException(403)
    try:
        status, headers, body = await anyio.to_thread.run_sync(library.open_range, item_id, request.headers.get("range", ""))
    except KeyError:
        raise HTTPException(404)
    except library.LibraryError as e:
        raise HTTPException(502, str(e))
    if request.method == "HEAD":
        getattr(body, "close", lambda: None)()      # closes the file it opened
        return Response(status_code=status, headers=headers, media_type="application/octet-stream")
    return StreamingResponse(body, status_code=status, headers=headers, media_type="application/octet-stream")


# ------------------------------------------------------------------ web UI
@app.get("/")
def index():
    return _page("index.html")


@app.get("/setup")
def setup_page(request: Request):
    if config.get("require_pairing") and not remote.trusted(request):
        return RedirectResponse("/pair?next=/setup", 303)
    return _page("setup.html")


@app.get("/pair")
def pair_page():
    return _page("pair.html")


@app.get("/remote")
def remote_page(request: Request):
    if not remote.trusted(request):
        return RedirectResponse("/pair", 303)
    return _page("remote.html")


# setup changes need this PC or a paired phone once "require_pairing" is on (watching TV never does)
_LOCKED = ("/api/sources", "/api/mapping", "/api/extras", "/api/settings", "/api/jobs", "/api/rules",
           "/api/lineup", "/api/gracenote", "/api/media/channels", "/api/library")


@app.middleware("http")
async def _setup_lock(request: Request, call_next):
    media.set_port(request.scope.get("server", (None, None))[1])
    if (request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith(_LOCKED)
            and config.get("require_pairing") and not remote.trusted(request)):
        return JSONResponse({"detail": "Pair this device first (Setup › Phones on the TunerBox PC)"}, 403)
    return await call_next(request)


def _named(html):
    """Setup's header shows the network name, so you can tell which box you're setting up. Titles and
    home-screen apps stay "TunerBox"."""
    name = config.box_name()
    if name == "TunerBox":
        return html
    return html.replace("<small>Setup</small>", f"<small>Setup · {escape(name)}</small>")


_ASSET = re.compile(r'((?:src|href)="/static/([^"?]+\.(?:js|css)))"')


def _page(name):
    """Serve a page with ?v=<file time> on its scripts/styles, so an updated TunerBox never runs
    with a script the browser or app window cached from the old version."""
    html = _named((config.WEB / name).read_text(encoding="utf-8"))

    def stamp(m):
        f = config.WEB / m.group(2)
        if m.group(2) == "tv.js":
            v = bundle.version()
        else:
            v = int(f.stat().st_mtime) if f.exists() else 0
        return f'{m.group(1)}?v={v}"'
    return HTMLResponse(_ASSET.sub(stamp, html), headers={"Cache-Control": "no-cache"})


@app.api_route("/static/tv.js", methods=["GET", "HEAD"])
def tv_script():
    """The TV page's script, joined from web/tv/*.js (see bundle.py)."""
    return Response(bundle.tv_js(), media_type="text/javascript; charset=utf-8")


@app.middleware("http")
async def _revalidate_static(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"      # always check with the server (cheap: ETag)
    return resp


# ------------------------------------------------------------------ other websites
# TunerBox has no logins on the home network, so two browser tricks need closing:
#  * cross-site requests: a page on another site making your browser POST here (start scans, change setup).
#    Browsers say where a request comes from (Origin, Sec-Fetch-Site); changes from other sites are refused.
#  * DNS rebinding: another site pointing its own name at TunerBox's address so its pages can read the
#    answers. TunerBox only answers to IP addresses, local names (nas, nas.local, tunerbox.lan…) and names
#    added under Settings › Advanced › Allowed host names.
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain", ".localhost")


def _host_ok(host):
    host = (host or "").strip().lower()
    if host.startswith("["):                              # [IPv6]:port
        host = host[1:host.find("]")] if "]" in host else host
    elif host.count(":") == 1:
        host = host.split(":")[0]
    host = host.rstrip(".")
    if not host:
        return True                                       # HTTP/1.0 without Host: nothing to rebind
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host == "localhost" or "." not in host or host.endswith(LOCAL_SUFFIXES):
        return True
    extra = [h.lower() for h in (config.get("allowed_hosts") or [])]
    extra += [h.strip().lower() for h in os.environ.get("TUNERBOX_ALLOWED_HOSTS", "").split(",") if h.strip()]
    extra += [h.lower() for h in (config.get("https_hosts") or [])]     # names on the HTTPS certificate
    return host in extra or any(h.startswith("*.") and host.endswith(h[1:]) for h in extra)


def _same_origin(request):
    """False when a browser says this change comes from another website."""
    origin = request.headers.get("origin")
    if origin:
        if origin == "null":
            return False
        try:
            o = urlsplit(origin)
        except ValueError:
            return False
        return (o.netloc or "").lower() == (request.headers.get("host") or "").lower()
    return request.headers.get("sec-fetch-site", "") not in ("cross-site", "same-site")


@app.middleware("http")
async def _other_sites(request: Request, call_next):
    if not _host_ok(request.headers.get("host")):
        return PlainTextResponse("TunerBox doesn't answer to this address. Use its IP address, or add this name "
                                 "under Setup › Settings › Advanced › Allowed host names.", 403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request):
        return JSONResponse({"detail": "Refused: this request came from another website"}, 403)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=config.WEB), name="static")
