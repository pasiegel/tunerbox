"""Multiview: one TV screen watching up to four live channels at once. The main picture keeps the
screen's own client id; every other tile plays as "<screen>~<slot>", so each tile only ever replaces
its own stream and the playlist limits in connections.py count every tile.

plan() decides which feed (alternate) each tile plays from. A channel is often on more than one
playlist, so when the channel you add is only on a playlist whose connections are all in use by your
own tiles, a tile that has a copy elsewhere is moved there to make room. When nothing can be moved,
it says what is using the connections so the screen can offer to replace a tile instead."""
import itertools

from . import config, connections, db, guide, hls, streaming, tuner

SEP = "~"
MAX_TILES = 4
MAX_OPTIONS = 6         # feeds per tile looked at: enough for any real channel, keeps the search tiny
MOVE_COST = 100         # moving a tile that's already playing restarts it: only to make room


def capacity():
    """How many channels a TV screen may show at once, 1-4 (1: no multiview): the connections the
    enabled playlists allow, added up. A playlist with no limit (none set, and none reported by the
    provider or tuner) has room for all four, and so do your own videos, which need no provider
    connection: My Channels, and My Library (its movies and shows play in a small picture)."""
    if not config.get("multiview_enabled", False):
        return 1
    n = 0
    for s in db.rows("SELECT * FROM sources WHERE enabled=1"):
        n += connections.limit_of(s) or MAX_TILES
    for own in ("SELECT 1 FROM media_channels WHERE enabled=1 AND item_count>0 LIMIT 1",     # My Channels
                "SELECT 1 FROM lib_shares WHERE enabled=1 AND count>0 LIMIT 1"):              # My Library
        if n >= MAX_TILES:
            break
        try:
            if db.one(own):
                n += MAX_TILES
        except Exception:  # noqa: BLE001 - that table isn't there yet
            pass
    return max(1, min(MAX_TILES, n))


def tile_client(screen, slot):
    return screen if slot == 1 else f"{screen}{SEP}{slot}"


def split(client):
    """("<screen>", slot) for a reader name (hls: prefix allowed); slot 1 for the main picture."""
    name = (client or "").removeprefix("hls:")
    base, _, n = name.partition(SEP)
    return base, int(n) if n.isdigit() else 1


def _mine(screen):
    """Is this reader name one of `screen`'s tiles?"""
    return lambda name: bool(screen) and split(name)[0] == screen


def options(number=None, key=None):
    """The feeds a channel (or an event feed) can play from, in the order the screen tries them:
    [{"alt", "url", "source_id", "mode", "dead"}]."""
    if key:
        rows = guide.stream_rows([key])
    else:
        c = guide.channel(number) if number is not None else None
        rows = guide.stream_rows(c["streams"]) if c and c["streams"] else []
    default = config.get("video_mode", "copy")
    return [{"alt": i, "url": r["url"], "source_id": r.get("source_id") or 0,
             "mode": r.get("video_mode") or default, "dead": r.get("status") == "dead"}
            for i, r in enumerate(rows[:MAX_OPTIONS])]


def _others(screen, slot=None):
    """What everyone except this screen holds: ({source_id: connections}, {url: mode} of tuners they keep running).
    This screen's own On Demand pictures count too (they can't move to another playlist), except the
    one in `slot`, which is being replaced."""
    mine = _mine(screen)
    used, running = {}, {}
    for t in tuner.running():
        if t["viewers"] and all(mine(v) for v in t["viewers"]):
            continue                    # only this screen's tiles on it: it goes when they move
        sid = t.get("source_id") or 0
        used[sid] = used.get(sid, 0) + 1
        running[t["url"]] = t.get("mode")
    for p in streaming.playing() + hls.playing():
        c = p.get("client") or ""
        if not mine(c) or split(c)[1] != slot:
            sid = p.get("source_id") or 0
            used[sid] = used.get(sid, 0) + 1
    return used, running


def _limits(source_ids):
    out = {}
    for sid in source_ids:
        src = connections._source(sid)
        out[sid] = connections.limit_of(src) if src else 0
    return out


def _fits(choice, room, running):
    """Does this choice of one feed per tile fit in the connections left? Tiles on the same feed share it."""
    need = {}
    for o in {(o["url"], o["mode"]): o for o in choice}.values():
        if running.get(o["url"]) == o["mode"]:
            continue                    # someone else keeps it running: joining is free
        need[o["source_id"]] = need.get(o["source_id"], 0) + 1
    return all(room.get(sid) is None or n <= room[sid] for sid, n in need.items())


def plan(screen, tiles, want, slot):
    """Where to play `want` ({"number"} or {"key"}) in tile `slot` of `screen`, given what its tiles play
    now (`tiles`: [{"slot", "number" | "key", "alt"}]). The tile in `slot`, if any, is replaced.

    {"ok": True, "alt": n, "moves": [{"slot", "alt", "source"}], "note": str}: play feed `alt` there,
    after retuning each tile in `moves` to its new feed (on another playlist) to free a connection.
    {"ok": False, "reason": str, "full": [{"source", "limit", "using": [... "slot" when it's a tile]}]}."""
    new = options(want.get("number"), want.get("key"))
    if not new:
        return {"ok": False, "reason": "That channel has no feed to play", "full": []}
    keep = [t for t in tiles if t.get("slot") != slot][:MAX_TILES - 1]
    opts = []
    for t in keep:
        o = options(t.get("number"), t.get("key"))
        cur = t.get("alt") or 0
        opts.append(o or [])
        if o and not any(x["alt"] == cur for x in o):
            t["alt"] = o[0]["alt"]
    keep = [t for t, o in zip(keep, opts) if o]
    opts = [o for o in opts if o]

    used, running = _others(screen, slot)
    sids = {o["source_id"] for o in new} | {o["source_id"] for os_ in opts for o in os_}
    lim = _limits(sids)
    room = {sid: (None if not lim[sid] else max(0, lim[sid] - used.get(sid, 0))) for sid in sids}

    def cost(choice):
        c = 0
        for t, o in zip(keep, choice):
            if o["alt"] != (t.get("alt") or 0):
                c += MOVE_COST
            c += o["alt"] + (5 if o["dead"] else 0)
        o = choice[-1]
        return c + o["alt"] + (5 if o["dead"] else 0)

    best = None
    for choice in itertools.product(*opts, new):
        if _fits(choice, room, running):
            c = cost(choice)
            if best is None or c < best[0]:
                best = (c, choice)
    if best is None:
        return _no_room(screen, new, lim, keep)

    choice = best[1]
    names = {sid: (connections._source(sid) or {}).get("name", "") for sid in sids}
    moves, freed = [], set()
    for t, os_, o in zip(keep, opts, choice):
        cur = t.get("alt") or 0
        if o["alt"] != cur:
            moves.append({"slot": t["slot"], "alt": o["alt"], "source": names.get(o["source_id"], "")})
            freed |= {names.get(x["source_id"], "") for x in os_ if x["alt"] == cur}
    note = ""
    if moves:
        to = ", ".join(sorted({m["source"] for m in moves if m["source"]})) or "another playlist"
        was = ", ".join(sorted(n for n in freed if n))
        note = (f"Moved {'a tile' if len(moves) == 1 else f'{len(moves)} tiles'} to {to}"
                + (f" to free a {was} connection" if was else " to make room"))
    return {"ok": True, "alt": choice[-1]["alt"], "moves": moves, "note": note}


def _no_room(screen, new, lim, keep):
    """Nothing fits: for each playlist the new channel could play from, what is using its connections
    (this screen's tiles marked with their slot, so the screen can offer to replace one)."""
    mine = _mine(screen)
    entries = connections._entries()
    full = []
    for sid in dict.fromkeys(o["source_id"] for o in new):
        src = connections._source(sid)
        if not src or not lim.get(sid):
            continue
        using = []
        for x in entries:
            if x["source_id"] != sid:
                continue
            u = {k: x[k] for k in ("kind", "title", "number", "feed", "viewers")}
            tiles = sorted(split(n)[1] for n in x["_names"] if mine(n))
            if tiles and all(mine(n) for n in x["_names"]):
                u["slot"] = tiles[0]
            using.append(u)
        full.append({"source": src["name"], "limit": lim[sid], "using": using})
    names = [f["source"] for f in full]
    reason = (f"{' and '.join(names)} {'has' if len(names) == 1 else 'have'} no connection left"
              if names else "No connection is free for that channel")
    return {"ok": False, "reason": reason, "full": full}


def room():
    """Connections in use on each playlist that has a limit (for the "+ Add" tile): [{"source", "used", "limit"}]."""
    return [{"source": s["name"], "used": s["used"], "limit": s["limit"]}
            for s in connections.in_use()["sources"] if s["limit"]]
