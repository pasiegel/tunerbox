"""The live-stream pipeline, with a small Python script standing in for ffmpeg."""
import asyncio
import sys
import time

from tunerbox import streaming

FAST = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes(range(256)) * 4000)"]   # ~1 MB then exit
SLOW = [sys.executable, "-c", "import sys, time\nwhile True:\n sys.stdout.buffer.write(b'x' * 1000); sys.stdout.flush(); time.sleep(0.05)"]
SILENT = [sys.executable, "-c", "import time; time.sleep(60)"]


async def _collect(gen, limit=None):
    out = b""
    async for chunk in gen:
        out += chunk
        if limit and len(out) >= limit:
            break
    await gen.aclose()
    return out


def test_all_bytes_arrive_in_order():
    data = asyncio.run(_collect(streaming.ffmpeg_stream("", cmd=FAST)))
    assert data == bytes(range(256)) * 4000


def test_closing_the_stream_stops_the_process():
    procs = []
    real_popen = streaming.subprocess.Popen

    def spy(*a, **k):
        procs.append(real_popen(*a, **k))
        return procs[-1]
    streaming.subprocess.Popen = spy
    try:
        asyncio.run(_collect(streaming.ffmpeg_stream("", "viewer1", cmd=SLOW), limit=5000))
    finally:
        streaming.subprocess.Popen = real_popen
    procs[0].wait(timeout=5)                         # killed by the cleanup thread
    assert procs[0].poll() is not None
    assert "viewer1" not in streaming._sessions


class _GoneViewer:
    async def is_disconnected(self):
        return True


def test_viewer_leaving_a_silent_feed_is_noticed():
    t = time.monotonic()
    data = asyncio.run(_collect(streaming.ffmpeg_stream("", request=_GoneViewer(), cmd=SILENT)))
    assert data == b""
    assert time.monotonic() - t < 6                  # checked every 2 s even with no data at all


def test_waiting_viewers_hold_no_worker_threads():
    """Many idle streams at once must not use up anyio's thread pool (40 by default)."""
    import anyio.to_thread

    async def main():
        gens = [streaming.ffmpeg_stream("", cmd=SILENT) for _ in range(45)]
        tasks = [asyncio.ensure_future(g.__anext__()) for g in gens]
        await asyncio.sleep(1.0)
        # the pool must still be free for ordinary requests
        assert await asyncio.wait_for(anyio.to_thread.run_sync(lambda: 42), 3) == 42
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for g in gens:
            await g.aclose()
    asyncio.run(main())
