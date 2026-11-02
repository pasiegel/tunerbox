"""Checks whether streams actually deliver video."""
import asyncio
import logging
import time
from urllib.parse import urljoin

import httpx

from . import config, db

log = logging.getLogger("tunerbox.probe")


def split_url(url: str):
    """Kodi-style 'http://x/y.m3u8|User-Agent=foo&Referer=bar' -> (url, headers)."""
    headers = {"User-Agent": config.get("user_agent")}
    if "|" in url:
        url, _, extra = url.partition("|")
        for part in extra.split("&"):
            k, _, v = part.partition("=")
            if k and v:
                headers[k.strip()] = v.strip()
    return url, headers


async def _read_some(client, url, headers, n=2048):
    async with client.stream("GET", url, headers=headers) as r:
        if r.status_code >= 400:
            return r, b""
        buf = b""
        async for chunk in r.aiter_bytes():
            buf += chunk
            if len(buf) >= n:
                break
        return r, buf


async def probe_one(client, raw_url):
    url, headers = split_url(raw_url)
    r, data = await _read_some(client, url, headers, 65536)
    if r.status_code >= 400 or not data:
        return False
    text_start = data[:16].lstrip()
    if text_start.startswith(b"#EXTM3U"):
        body = data.decode("utf-8", "ignore")
        base = str(r.url)
        lines = [l.strip() for l in body.splitlines()]
        if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
            variant = next((l for i, l in enumerate(lines)
                            if l and not l.startswith("#") and i > 0 and lines[i - 1].startswith("#EXT-X-STREAM-INF")), None)
            if not variant:
                return False
            r, data = await _read_some(client, urljoin(base, variant), headers, 65536)
            if r.status_code >= 400 or not data:
                return False
            base = str(r.url)
            lines = [l.strip() for l in data.decode("utf-8", "ignore").splitlines()]
        seg = next((l for i, l in enumerate(lines)
                    if l and not l.startswith("#") and i > 0 and lines[i - 1].startswith("#EXTINF")), None)
        if not seg:
            return False
        r, data = await _read_some(client, urljoin(base, seg), headers, 1024)
        return r.status_code < 400 and len(data) > 0
    return True


async def probe_many(streams, progress=None, conc=None, tmo=None):
    conc = int(conc or config.get("probe_concurrency", 16))
    tmo = float(tmo or config.get("probe_timeout", 10))
    sem = asyncio.Semaphore(conc)
    done = 0
    results = {}
    async with httpx.AsyncClient(timeout=tmo, follow_redirects=True, verify=False,
                                 limits=httpx.Limits(max_connections=conc * 2)) as client:
        async def worker(s):
            nonlocal done
            async with sem:
                try:
                    ok = await asyncio.wait_for(probe_one(client, s["url"]), tmo + 5)
                except Exception:  # noqa: BLE001
                    ok = False
                results[s["key"]] = ok
                done += 1
                if progress and done % 10 == 0:
                    progress(done, len(streams))
        await asyncio.gather(*(worker(s) for s in streams))
    now = int(time.time())
    with db.conn() as c:
        for k, ok in results.items():
            c.execute("UPDATE streams SET status=?, checked_at=? WHERE key=?", ("ok" if ok else "dead", now, k))
    return sum(results.values()), len(results)


def probe_streams(streams, progress=None):
    return asyncio.run(probe_many(streams, progress))
