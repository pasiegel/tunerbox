"""HTTP API + web UI + M3U/XMLTV/HDHomeRun outputs."""
import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from xml.sax.saxutils import escape

import anyio
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from . import bundle, config, db, dvr, epg, guide, hls, jobs, lineup, matcher, media, remote, scoreboard, sources, sports, vod
from .streaming import ffmpeg_stream, stop_client
from . import reminders  # noqa: I001

log = logging.getLogger("tunerbox.api")


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
    """Closes the ffmpeg generator as soon as the viewer disconnects."""
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose:
                await aclose()


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
            p.pop("channel", None)
            lst.append(p)
        out[c["number"]] = lst
    return out


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
@app.get("/api/stream/{number}")
async def api_stream(request: Request, number: int, alt: int = 0, client: str = "", mode: str = "", audio: str = "aac",
                     buf: int = -1):
    # channel list rebuilds can take a second with big playlists: keep them off the event loop
    c = await anyio.to_thread.run_sync(guide.channel, number)
    if not c or not c["streams"]:
        raise HTTPException(404, "No signal")
    rows = await anyio.to_thread.run_sync(guide.stream_rows, c["streams"])
    if alt >= len(rows):
        raise HTTPException(404, "No more alternates")
    return LiveResponse(ffmpeg_stream(rows[alt]["url"], client or None, mode or rows[alt].get("video_mode") or None,
                                      audio, request, buf),
                             media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@app.get("/api/hls/{number}")
async def api_hls(number: int, alt: int = 0, client: str = "", mode: str = "", buf: int = -1):
    """Native-HLS players (iPad/iPhone Safari): start a session, then redirect to its playlist."""
    c = await anyio.to_thread.run_sync(guide.channel, number)
    if not c or not c["streams"]:
        raise HTTPException(404, "No signal")
    rows = await anyio.to_thread.run_sync(guide.stream_rows, c["streams"])
    if alt >= len(rows):
        raise HTTPException(404, "No more alternates")
    sid = await anyio.to_thread.run_sync(hls.start, rows[alt]["url"], client or None,
                                         mode or rows[alt].get("video_mode") or None, buf)
    if not await hls.wait_ready(sid):
        raise HTTPException(502, "No signal")
    return RedirectResponse(f"/api/hls/s/{sid}/index.m3u8", status_code=302)


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
                          buf: int = -1):
    r = await anyio.to_thread.run_sync(_feed_row, key)
    return LiveResponse(ffmpeg_stream(r["url"], client or None, mode or r.get("video_mode") or None, audio, request, buf),
                        media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@app.get("/api/feed/hls")
async def api_feed_hls(key: str, client: str = "", mode: str = "", buf: int = -1):
    r = await anyio.to_thread.run_sync(_feed_row, key)
    sid = await anyio.to_thread.run_sync(hls.start, r["url"], client or None, mode or r.get("video_mode") or None, buf)
    if not await hls.wait_ready(sid):
        raise HTTPException(502, "Not live")
    return RedirectResponse(f"/api/hls/s/{sid}/index.m3u8", status_code=302)


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
    return LiveResponse(ffmpeg_stream(rows[0]["url"], client or None, rows[0].get("video_mode") or None, request=request),
                        media_type="video/mp2t")


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
def api_suggest(number: int | None = None, q: str = "", limit: int = 60):
    return matcher.suggest(number, q, limit)


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
        s.pop("hidden_groups", None)
        s.pop("vod_hidden", None)
    return out


@app.post("/api/sources")
def api_add_source(data: dict = Body(...)):
    if not data.get("url"):
        raise HTTPException(400, "URL / server is required")
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
def api_import_sources(data: dict = Body(...)):
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
def api_edit_source(sid: int, data: dict = Body(...)):
    for k in ("name", "url", "username", "password", "exclude_groups", "xtream_format", "enabled", "video_mode"):
        if k in data and not (k == "password" and data[k] == "••••"):
            db.run(f"UPDATE sources SET {k}=? WHERE id=?", (data[k], sid))
    job = ""
    src = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    turned = {k: int(bool(data[k])) for k in ("vod_movies", "vod_series") if src and k in data and int(bool(data[k])) != src[k]}
    if turned:              # On Demand switched on or off for this playlist
        for k, on in turned.items():
            db.run(f"UPDATE sources SET {k}=? WHERE id=?", (on, sid))
            if not on:
                vod.clear(sid, vod.KINDS if src["type"] != "xtream" else ("movie",) if k == "vod_movies" else ("series",))
        if src["type"] != "xtream":
            job = "refresh" if jobs.quick_refresh() else ""     # its video files move between channels and On Demand
        elif any(turned.values()):
            job = "vod" if jobs.vod_scan(sid) else ""
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
    if r["shown_again"]:    # those titles have to be read again
        src = db.one("SELECT type FROM sources WHERE id=?", (sid,))
        r["job"] = ("vod" if jobs.vod_scan(sid) else "") if src["type"] == "xtream" else ("refresh" if jobs.quick_refresh() else "")
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
    return {k: v for k, v in config.load().items() if k != "devices"}


@app.get("/api/settings")
def api_settings():
    return _public_settings()


@app.post("/api/settings")
def api_save_settings(request: Request, data: dict = Body(...)):
    try:
        data = config.clean_changes(data, from_pc=remote.is_pc(request))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    before = (config.get("gracenote") or {}).get("lineup_id") or ""
    leagues = scoreboard.selected()
    config.update(data)
    if scoreboard.selected() != leagues:
        scoreboard.tick(force=True)     # fetch the newly picked leagues now, not at the next refresh
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


def _trusted(request: Request):
    if not remote.trusted(request):
        raise HTTPException(401, "Pair this phone first")


@app.get("/api/me")
def api_me(request: Request):
    d = remote.device(request)
    return {"pc": remote.is_pc(request), "paired": bool(d), "name": d["name"] if d else None,
            "require_pairing": bool(config.get("require_pairing"))}


@app.post("/api/pair/start")
def api_pair_start(request: Request):
    _pc_only(request)
    p = remote.start_pairing()
    port = request.url.port or config.get("port", 8642)
    base = f"http://{remote.lan_ip()}:{port}"
    link = f"{base}/pair#{p['code']}.{p['key']}"
    return {"code": p["code"], "expires": p["expires"], "url": link, "address": f"{base}/pair",
            "qr": remote.qr_svg(link)}


@app.get("/api/pair/state")
def api_pair_state(request: Request):
    _pc_only(request)
    return {**remote.pairing_state(), "devices": remote.devices()}


@app.post("/api/pair/cancel")
def api_pair_cancel(request: Request):
    _pc_only(request)
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
    _pc_only(request)
    return remote.devices()


@app.patch("/api/devices/{did}")
def api_device_rename(request: Request, did: str, name: str = Body(..., embed=True)):
    _pc_only(request)
    remote.rename_device(did, name)
    return remote.devices()


@app.delete("/api/devices/{did}")
def api_device_remove(request: Request, did: str):
    _pc_only(request)
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


def _vod_item(item_id, prepare=False):
    it = (vod.prepare if prepare else vod.item)(item_id)
    if not it:
        raise HTTPException(404, "That title is no longer in the playlist")
    return it


@app.get("/api/vod/play/{item_id}")
def api_vod_play(item_id: str):
    """What the player needs before it starts: title, length, where to resume, the next episode."""
    it = _vod_item(item_id, prepare=True)
    return {k: v for k, v in it.items() if k not in ("url", "video_mode")}     # the address holds the provider login


@app.get("/api/vod/stream/{item_id}")
async def api_vod_stream(request: Request, item_id: str, ss: float = 0.0, client: str = "", mode: str = ""):
    it = await anyio.to_thread.run_sync(_vod_item, item_id)
    cmd = vod.stream_cmd(it["url"], max(0.0, ss), mode or it["video_mode"] or None)
    return LiveResponse(ffmpeg_stream(it["url"], client or None, request=request, buffer=0, cmd=cmd),
                        media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@app.get("/api/vod/hls/{item_id}")
async def api_vod_hls(item_id: str, ss: float = 0.0, client: str = "", mode: str = ""):
    it = await anyio.to_thread.run_sync(_vod_item, item_id)
    sid = await anyio.to_thread.run_sync(hls.start, it["url"], client or None, mode or it["video_mode"] or None, 0,
                                         vod.hls_pre(max(0.0, ss)))
    if not await hls.wait_ready(sid):
        raise HTTPException(502, "Can't play this title")
    return RedirectResponse(f"/api/hls/s/{sid}/index.m3u8", status_code=302)


@app.post("/api/vod/progress")
def api_vod_progress(id: str = Body(...), position: float = Body(...), duration: float = Body(0)):
    r = vod.set_progress(id, position, duration)
    if r is None:
        raise HTTPException(404)
    return r


@app.post("/api/vod/watched")
def api_vod_watched(id: str = Body(...), watched: bool = Body(True)):
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


# ------------------------------------------------------------------ DVR
@app.get("/api/recordings")
def api_recordings():
    return db.rows("SELECT * FROM recordings ORDER BY CASE status WHEN 'recording' THEN 0 WHEN 'scheduled' THEN 1 ELSE 2 END, "
                   "CASE WHEN status IN ('scheduled','recording') THEN start ELSE -start END")


@app.post("/api/recordings")
def api_record(number: int = Body(...), start: int = Body(...), series: bool = Body(False),
               new_only: bool = Body(False), any_channel: bool = Body(False)):
    if series:
        c = guide.channel(number)
        p = db.one("SELECT * FROM programs WHERE channel=? AND start<=? AND stop>?", (c["epg"], start, start)) if c and c["epg"] else None
        if not p:
            raise HTTPException(400, "No guide data for that program")
        dvr.add_rule(p["title"], None if any_channel else number, new_only)
        dvr.schedule_program(number, start)
        return {"ok": True}
    rec = dvr.schedule_program(number, start)
    if not rec:
        raise HTTPException(400, "No guide data for that program")
    dvr.tick()
    return rec


@app.post("/api/recordings/manual")
def api_record_manual(number: int = Body(...), start: int = Body(...), minutes: int = Body(...), title: str = Body("")):
    return dvr.schedule(number, start, start + minutes * 60, title)


@app.delete("/api/recordings/{rid}")
def api_cancel(request: Request, rid: int, delete: int = 0):
    if delete and config.get("require_pairing"):
        _trusted(request)        # deleting a recorded file is a setup change; cancelling isn't
    dvr.cancel(rid, bool(delete))
    return {"ok": True}


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
def _base(request: Request):
    return str(request.base_url).rstrip("/")


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
                       + (f'<icon src="{escape(p["image"])}"/>' if p["image"] else "")
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
def api_media_folder(path: str):
    """Quick check that TunerBox can open a folder (UNC shares need the PC to have access)."""
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
           "/api/lineup", "/api/gracenote", "/api/media/channels")


@app.middleware("http")
async def _setup_lock(request: Request, call_next):
    media.set_port(request.scope.get("server", (None, None))[1])
    if (request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith(_LOCKED)
            and config.get("require_pairing") and not remote.trusted(request)):
        return JSONResponse({"detail": "Pair this device first (Setup › Phones on the TunerBox PC)"}, 403)
    return await call_next(request)


def _named(html):
    """The page with this box's name (Settings › Other apps) in its title, home-screen name and header."""
    name = config.box_name()
    if name == "TunerBox":
        return html
    n = escape(name, {'"': "&quot;"})
    html = html.replace("<title>TunerBox", f"<title>{n}").replace(" · TunerBox</title>", f" · {n}</title>")
    html = html.replace('name="apple-mobile-web-app-title" content="TunerBox"', f'name="apple-mobile-web-app-title" content="{n}"')
    html = html.replace('name="apple-mobile-web-app-title" content="TB Remote"', f'name="apple-mobile-web-app-title" content="{n} Remote"')
    return html.replace("<small>Setup</small>", f"<small>Setup · {n}</small>")


@app.api_route("/static/manifest.webmanifest", methods=["GET", "HEAD"])
@app.api_route("/static/remote.webmanifest", methods=["GET", "HEAD"])
def web_manifest(request: Request):
    """The home-screen app's manifest with this box's name, so two TunerBoxes get two names."""
    remote_app = request.url.path.endswith("remote.webmanifest")
    m = json.loads((config.WEB / ("remote.webmanifest" if remote_app else "manifest.webmanifest")).read_text(encoding="utf-8"))
    name = config.box_name()
    if name != "TunerBox":
        m["name"] = f"{name} Remote" if remote_app else name
        m["short_name"] = (f"{name} Remote" if remote_app else name)[:24]
    return JSONResponse(m, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


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


app.mount("/static", StaticFiles(directory=config.WEB), name="static")
