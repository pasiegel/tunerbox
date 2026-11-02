"""Fuzzy-matches playlist streams to the local lineup and numbers the leftovers."""
import json
import logging
import re
from collections import defaultdict

import numpy as np
from rapidfuzz import fuzz, process

from . import config, db
from .text import (core, find_callsigns, is_foreign, is_spanish, is_west, norm,
                   parse_callsign, tvg_stem)

log = logging.getLogger("tunerbox.matcher")

# Extra names for lineup channels (keys are normalized lineup names or callsigns)
ALIASES = {
    "cable news network": ["cnn"],
    "home and garden television": ["hgtv"],
    "e entertainment television": ["e", "e entertainment", "e network"],
    "mtv music television": ["mtv"],
    "mtv2 music television": ["mtv2", "mtv 2"],
    "turner classic movies": ["tcm"],
    "the weather channel": ["weather channel", "twc"],
    "fox news channel": ["fox news", "fnc"],
    "fox business": ["fox business network", "fbn"],
    "ms now": ["msnbc", "ms now"],
    "galavision cable network": ["galavision"],
    "big ten network michigan": ["big ten network", "btn", "b1g network"],
    "fs1": ["fox sports 1", "fs1"],
    "fs2": ["fox sports 2", "fs2"],
    "oxygen true crime": ["oxygen"],
    "univision network": ["univision"],
    "bloomberg business television": ["bloomberg", "bloomberg tv", "bloomberg television"],
    "national geographic usa": ["national geographic", "nat geo"],
    "national geographic": ["nat geo"],
    "national geographic wild": ["nat geo wild"],
    "science": ["science channel"],
    "science channel": ["science"],
    "investigation discovery": ["id", "investigation discovery"],
    "a and e": ["ae", "a e", "aande", "a and e network", "aetv"],
    "syfy": ["sci fi", "scifi"],
    "nick jr": ["nickjr"],
    "teen nick": ["teennick"],
    "nickelodeon": ["nick"],
    "disney xd": ["disneyxd"],
    "cspan": ["c span", "c span 1"],
    "5cspan": ["c span", "cspan"],
    "cspan2": ["c span 2"],
    "cspan3": ["c span 3"],
    "espnews": ["espn news"],
    "golf channel": ["golf"],
    "the travel channel": ["travel channel", "travel"],
    "discovery family channel": ["discovery family"],
    "american heroes channel": ["ahc"],
    "we tv": ["wetv"],
    "lmn": ["lifetime movie network", "lifetime movies", "lifetime movie"],
    "game show network": ["gsn"],
    "oprah winfrey network": ["own"],
    "tv one": ["tvone"],
    "starz encore": ["encore"],
    "paramount plus with showtime": ["showtime"],
    "hbo east": ["hbo"],
    "nfl redzone": ["redzone", "nfl red zone"],
    "cbs sports network": ["cbssn"],
    "mlb network": ["mlb"],
    "nhl network": ["nhl"],
    "reelzchannel": ["reelz"],
    "tennis channel": ["tennis"],
    "hallmark channel": ["hallmark"],
    "hallmark mystery": ["hallmark movies and mysteries", "hallmark movies mysteries", "hallmark mysteries"],
    "discovery life channel": ["discovery life"],
    "history": ["history channel"],
    "cartoon network": ["cn"],
    "trutv": ["tru tv"],
    "newsnation": ["news nation"],
    "newsnation sd": ["newsnation", "news nation"],
    "magnolia network": ["magnolia"],
    "fyi": ["fyi channel"],
    "fyi channel": ["fyi"],
    "vice": ["viceland", "vice tv"],
    "hln": ["headline news"],
    "pop": ["pop tv"],
    "uptv": ["up tv", "up"],
    "great american family": ["great american family", "gaf"],
    "nick music": ["nickmusic"],
    "bet her": ["bet her"],
    "cmt": ["country music television"],
    "tv land": ["tvland"],
    "paramount network": ["paramount"],
    "freeform": ["freeform"],
    "hbo2 east": ["hbo 2"],
}

NETWORKS = [("NATIONAL BROADCASTING", "nbc"), ("CBS", "cbs"), ("AMERICAN BROADCASTING", "abc"),
            ("FOX", "fox"), ("PUBLIC BROADCASTING", "pbs"), ("ION", "ion"), ("CW", "cw"),
            ("MYNETWORK", "mynetworktv"), ("TELEMUNDO", "telemundo")]
NET_SUFFIX = ["", " usa", " network", " national", " tv", " television", " feed", " us"]
GENERIC_AFFILIATES = {"INDEPENDENT", "", "NULL"}


# ------------------------------------------------------------------ helpers
SKIP_LINEUP = re.compile(r"\b(access|educational programming|unknown|lease|leased|ppv|pay per view|"
                         r"on demand|music choice)\b", re.I)


def stream_variants(s):
    """[(text, kind)] kind 'n' = from the channel name, 't' = from its tvg-id."""
    n = s.get("norm") or norm(s["name"])
    out = {(n, "n"), (core(n), "n")}
    t = tvg_stem(s.get("tvg_id", ""))
    if t:
        out.update({(t, "t"), (core(t), "t")})
    return {x for x in out if x[0]}


def _first_token_ok(a, b):
    """The leading word carries the brand: 'cmt music' vs 'mtv music', 'disney' vs 'discovery',
    'cinemax' vs 'newsmax' must not match. Plurals/typos ('classic'/'classics') are fine."""
    fa, fb = a.split()[0], b.split()[0]
    if fa == fb:
        return True
    return len(fa) > 4 and len(fb) > 4 and fuzz.ratio(fa, fb) >= 85


_SOFT = {"and", "the", "of"}


def _brand(n):
    """Name without filler words, for checking the distinctive part agrees."""
    return " ".join(w for w in core(n).split() if w not in _SOFT) or n


def core_score(q, stream_norm):
    """'discovery family channel' vs 'discovery channel' scores high on the full name only
    because of the shared filler word. Compare what's left after dropping filler."""
    return int(fuzz.token_sort_ratio(_brand(q), _brand(stream_norm)))


def stream_calls(s):
    """Callsigns a stream stands for. Antenna tuner channels ('ota:' tvg-id) know theirs exactly,
    so their names (e.g. 3.2 named 'WWMT') don't count."""
    tvg = s["tvg_id"] or ""
    if tvg.startswith("ota:"):
        return find_callsigns(tvg[4:])
    return find_callsigns(s["name"]) | find_callsigns(tvg)


def lineup_info(ch):
    """Names to search for + broadcast details for one lineup channel."""
    name_n = norm(ch["name"])
    names = {name_n, core(name_n)}
    names.update(ALIASES.get(name_n, []))
    strict = set()
    cs = (ch.get("callsign") or "").upper()
    aff = (ch.get("affiliate") or "").upper()
    call = parse_callsign(cs) or (parse_callsign(ch["name"]) if re.fullmatch(r"[KW][A-Z]{2,3}(-?(DT|CD|CA|TV|LD)\d?)?", ch["name"].strip().upper()) else None)
    broadcast = bool(call) and (bool(aff) or ch["number"] < 300 or ch["name"].upper().startswith(call[0]))
    network = None
    if broadcast:
        names = set()  # locals match by callsign, not by fuzzy name
        for key, net in NETWORKS:
            if key in aff:
                network = net
                break
        if aff not in GENERIC_AFFILIATES and not network:
            # digital subchannels carry national diginets (Antenna TV, Charge!, Create...)
            a = norm(aff)
            names.update({a, core(a)})
            strict.update({a, core(a)})
    names = {n for n in names if n}
    if SKIP_LINEUP.search(ch["name"]):
        names = set()
    spanish = is_spanish(ch["name"]) or 600 <= ch["number"] < 700 or "UNIVISION" in aff or "UNIMAS" in aff
    return {"names": names, "strict": strict, "call": call if broadcast else None, "network": network,
            "spanish": spanish, "west": is_west(ch["name"])}


def adjust(score, info, s):
    raw = s["name"]
    if is_foreign(raw):
        score -= 10 if info["spanish"] else 25   # Mexican/Latin feeds suit Spanish channels
    if is_spanish(raw) and not info["spanish"]:
        score -= 20
    if is_west(raw) and not info["west"]:
        score -= 15
    if s.get("status") == "ok":
        score += 2
    if re.search(r"\b(hd|fhd|1080)", raw, re.I):
        score += 1
    return score


def load_streams():
    rows = db.rows("""SELECT s.key, s.name, s.norm, s.tvg_id, s.grp, s.status, s.url, s.logo,
                             s.source_id, src.name AS source_name
                      FROM streams s JOIN sources src ON src.id = s.source_id
                      WHERE src.enabled = 1 ORDER BY src.id, s.rowid""")
    return [r for r in rows if r["status"] != "dead" and r["norm"]]


# ------------------------------------------------------------------ main pass
def match_all(progress=None):
    threshold = int(config.get("match_threshold", 86))
    streams = load_streams()
    lineup = db.rows("SELECT * FROM lineup ORDER BY number")
    existing = {m["number"]: m for m in db.rows("SELECT * FROM mappings")}
    by_key = {s["key"]: s for s in streams}

    # choice string -> stream indexes
    choice_idx = defaultdict(list)
    for i, s in enumerate(streams):
        for v, kind in stream_variants(s):
            choice_idx[v].append((i, kind))
    choices = list(choice_idx)

    # callsign index for local stations
    call_idx = defaultdict(list)
    for i, s in enumerate(streams):
        for c in stream_calls(s):
            call_idx[c].append(i)
    norm_idx = defaultdict(list)
    for i, s in enumerate(streams):
        norm_idx[s["norm"]].append(i)

    infos = {ch["number"]: lineup_info(ch) for ch in lineup}
    queries = sorted({n for inf in infos.values() for n in inf["names"]})
    qpos = {q: i for i, q in enumerate(queries)}

    # fuzzy scores: query -> {stream index: best raw score}
    cutoff = max(0, threshold - 20)
    qhits = defaultdict(dict)
    chunk = 200
    for start in range(0, len(queries), chunk):
        qs = queries[start:start + chunk]
        if not choices:
            break
        mat = process.cdist(qs, choices, scorer=fuzz.token_sort_ratio, score_cutoff=cutoff,
                            dtype=np.uint8, workers=-1)
        rr, cc = np.nonzero(mat)
        for r, c in zip(rr.tolist(), cc.tolist()):
            q, ch = qs[r], choices[c]
            sc = int(mat[r, c])
            # very short names (cnn, tbs, e) must match exactly
            if min(len(q), len(ch)) <= 4 and q != ch:
                continue
            if not _first_token_ok(q, ch):
                continue
            for si, kind in choice_idx[ch]:
                if qhits[q].get((si, kind), 0) < sc:
                    qhits[q][(si, kind)] = sc
        if progress:
            progress(min(start + chunk, len(queries)), len(queries))

    results = {}
    for ch in lineup:
        num = ch["number"]
        inf = infos[num]
        name_sc, tvg_sc = {}, {}
        for q in inf["names"]:
            pen = 6 if q in inf["strict"] else 0
            for (si, kind), sc in qhits.get(q, {}).items():
                if kind == "n" and sc >= cutoff:
                    sc = min(sc, core_score(q, streams[si]["norm"]))
                d = name_sc if kind == "n" else tvg_sc
                d[si] = max(d.get(si, 0), sc - pen)
        cands = {}
        for si in set(name_sc) | set(tvg_sc):
            ns, ts = name_sc.get(si, 0), tvg_sc.get(si, 0)
            if ns >= threshold:
                cands[si] = ns + (2 if ts >= threshold else 0)
            elif ts >= 95 and ns >= 65:
                cands[si] = threshold + 1  # tvg-id agrees, name is close-ish: weak match
            else:
                cands[si] = ns
        reason = "name"
        if inf["call"]:
            for si in call_idx.get(inf["call"], []):
                cands[si] = max(cands.get(si, 0), 99)
            reason = "callsign"
        scored = sorted(((adjust(sc, inf, streams[si]), si) for si, sc in cands.items()), reverse=True)
        good = [(sc, si) for sc, si in scored if sc >= threshold]
        if not good and inf["network"] and config.get("network_fallback", True) and inf["call"] and inf["call"][1] == 1:
            for suf in NET_SUFFIX:
                for si in norm_idx.get(inf["network"] + suf, []):
                    sc = adjust(80, inf, streams[si])
                    if sc >= 60:
                        good.append((sc, si))
            good.sort(reverse=True)
            reason = f"{inf['network'].upper()} network feed"
        # distinct URLs only
        picked, urls = [], set()
        for sc, si in good:
            u = streams[si]["url"]
            if u in urls:
                continue
            urls.add(u)
            picked.append((sc, si))
            if len(picked) >= 5:
                break
        results[num] = (picked, reason)

    with db.conn() as c:
        for num, (picked, reason) in results.items():
            m = existing.get(num)
            if m and m["mode"] in ("manual", "none"):
                if m["mode"] == "manual" and m["stream_key"] not in by_key:
                    pass  # keep the manual pick even if the source is temporarily gone
                continue
            if picked:
                key = streams[picked[0][1]]["key"]
                alts = [streams[si]["key"] for _, si in picked[1:]]
                c.execute("""INSERT OR REPLACE INTO mappings(number, stream_key, alt_keys, mode, score, reason)
                             VALUES (?,?,?,?,?,?)""",
                          (num, key, json.dumps(alts), "auto", min(100, picked[0][0]), reason))
            else:
                c.execute("DELETE FROM mappings WHERE number=? AND mode='auto'", (num,))
    mapped = sum(1 for p, _ in results.values() if p)
    log.info("Matched %d / %d lineup channels", mapped, len(lineup))
    rebuild_extras(streams)
    return mapped, len(lineup)


def rebuild_extras(streams=None):
    """Number every playlist channel that isn't on the lineup, from extra_channel_start up."""
    streams = streams if streams is not None else load_streams()
    start = int(config.get("extra_channel_start", 20000))
    if db.get_meta("extra_start") != str(start):
        db.run("DELETE FROM extras")  # numbering base changed: renumber everything
        db.set_meta("extra_start", start)
    used_keys, used_norms = set(), set()
    by_key = {s["key"]: s for s in streams}
    for m in db.rows("SELECT stream_key, alt_keys FROM mappings WHERE stream_key IS NOT NULL"):
        for k in [m["stream_key"]] + json.loads(m["alt_keys"] or "[]"):
            used_keys.add(k)
            if k in by_key:
                used_norms.add(by_key[k]["norm"])
    for m in db.rows("SELECT stream_key FROM mappings WHERE mode='manual'"):
        if m["stream_key"] in by_key:
            used_norms.add(by_key[m["stream_key"]]["norm"])

    # one entry per distinct channel name, best status first
    order = {"ok": 0, "unknown": 1}
    best = {}
    for s in streams:
        if s["key"] in used_keys or s["norm"] in used_norms:
            continue
        cur = best.get(s["norm"])
        if cur is None or order.get(s["status"], 2) < order.get(cur["status"], 2):
            best[s["norm"]] = s
    wanted = {s["key"]: s for s in best.values()}

    existing = {e["stream_key"]: e for e in db.rows("SELECT * FROM extras")}
    taken = {e["number"] for e in existing.values()}
    lineup_nums = {r["number"] for r in db.rows("SELECT number FROM lineup")}
    nxt = max([start - 1] + [n for n in taken if n >= start]) + 1
    new = sorted((s for k, s in wanted.items() if k not in existing),
                 key=lambda s: (s["source_id"], (s["grp"] or "~").lower(), s["norm"]))
    with db.conn() as c:
        c.execute("UPDATE extras SET active=0")
        for k in wanted:
            if k in existing:
                c.execute("UPDATE extras SET active=1 WHERE stream_key=?", (k,))
        for s in new:
            while nxt in taken or nxt in lineup_nums:
                nxt += 1
            c.execute("INSERT INTO extras(stream_key, number, enabled, active) VALUES (?,?,1,1)", (s["key"], nxt))
            taken.add(nxt)
            nxt += 1
    log.info("Extras: %d channels (%d new)", len(wanted), len(new))
    return len(wanted)


# ------------------------------------------------------------------ manual picker
def suggest(number=None, q="", limit=60):
    streams = load_streams_all()
    if not streams:
        return []
    names = [s["norm"] or norm(s["name"]) for s in streams]
    scores = {}
    if q:
        qn = norm(q) or q.lower()
        for _, sc, i in process.extract(qn, names, scorer=fuzz.WRatio, limit=limit * 3):
            scores[i] = sc
        ql = q.lower()
        for i, s in enumerate(streams):
            if ql in s["name"].lower() or ql in (s["tvg_id"] or "").lower():
                scores[i] = max(scores.get(i, 0), 90 + (5 if s["norm"] == qn else 0))
    elif number is not None:
        ch = db.one("SELECT * FROM lineup WHERE number=?", (number,))
        if not ch:
            return []
        inf = lineup_info(ch)
        qs = set(inf["names"]) | {norm(ch["name"])}
        if inf["network"]:
            qs.add(inf["network"])
        for qn in filter(None, qs):
            for _, sc, i in process.extract(qn, names, scorer=fuzz.token_sort_ratio, limit=limit):
                scores[i] = max(scores.get(i, 0), adjust(sc, inf, streams[i]))
        if inf["call"]:
            for i, s in enumerate(streams):
                if inf["call"] in stream_calls(s):
                    scores[i] = 99
                elif inf["call"][0] in {c[0] for c in stream_calls(s)}:
                    scores[i] = max(scores.get(i, 0), 85)
        if inf["network"]:
            for i, s in enumerate(streams):
                if s["norm"] in {inf["network"] + x for x in NET_SUFFIX}:
                    scores[i] = max(scores.get(i, 0), 78)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], streams[kv[0]]["status"] != "ok"))[:limit]
    out = []
    for i, sc in ranked:
        s = dict(streams[i])
        s["score"] = max(0, min(100, int(sc)))
        s.pop("url", None)
        out.append(s)
    return out


def load_streams_all():
    return db.rows("""SELECT s.key, s.name, s.norm, s.tvg_id, s.grp, s.status, s.logo, s.source_id,
                             src.name AS source_name
                      FROM streams s JOIN sources src ON src.id = s.source_id
                      WHERE src.enabled = 1 ORDER BY src.id, s.rowid""")
