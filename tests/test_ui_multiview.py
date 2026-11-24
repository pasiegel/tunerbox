"""Multiview in a real browser: tiles get their own client ids, the planner's moves run before the new
tile starts (so the connection it frees is free), the highlight carries the sound, and leaving keeps the
highlighted channel. The video player is a stand-in that "plays" at once; the server is mocked.

Needs Playwright (see test_ui_gamestats.py); skipped when it isn't installed."""
import json
import mimetypes
from pathlib import Path

import pytest

from tunerbox import bundle

sync_api = pytest.importorskip("playwright.sync_api")

WEB = Path(__file__).resolve().parent.parent / "web"
BASE = "http://tunerbox.test"

CHANNELS = [
    {"number": 702, "name": "ESPN", "label": "ESPN", "mapped": True, "alts": 1, "epg": None, "logo": ""},
    {"number": 705, "name": "FS1", "label": "FS1", "mapped": True, "alts": 0, "epg": None, "logo": ""},
    {"number": 710, "name": "NBC", "label": "NBC", "mapped": True, "alts": 0, "epg": None, "logo": ""},
    {"number": 720, "name": "CNN", "label": "CNN", "mapped": True, "alts": 0, "epg": None, "logo": ""},
]

MOVIE = {"id": "m1", "title": "The Long Road", "duration": 6000, "position": 0}

# a stand-in for mpegts.js: records each stream address and "plays" straight away
FAKE_MPEGTS = """
window.__streams = [];
window.mpegts = {
  Events: { ERROR: "error", MEDIA_INFO: "media_info", LOADING_COMPLETE: "loading_complete" },
  ErrorTypes: { MEDIA_ERROR: "MediaError" },
  getFeatureList: () => ({ mseLivePlayback: true }),
  createPlayer(cfg) {
    window.__streams.push(cfg.url.replace(location.origin, ""));
    const p = { url: cfg.url, h: {}, on(ev, fn) { (this.h[ev] = this.h[ev] || []).push(fn); },
             emit(ev, ...a) { (this.h[ev] || []).forEach((fn) => fn(...a)); },
             attachMediaElement(v) { this.v = v; }, load() {},
             play() { const v = this.v; setTimeout(() => v.dispatchEvent(new Event("playing")), 20); return Promise.resolve(); },
             pause() {}, unload() {}, detachMediaElement() {}, destroy() {} };
    (window.__players = window.__players || []).push(p);
    return p;
  },
};
"""


def _bundle():
    js = bundle.tv_js()
    end = js.rindex("})();")
    return js[:end] + ("window.__mv = { S, tune, failOver, playVod, mvAddPrompt, programDialog, handleKey, mvAddNow, mvEnd, mvRemove, mvNextLayout, soundEl,"
                       " get video() { return video; }, CID };\n") + js[end:]


class Server:
    def __init__(self):
        self.plans = []           # bodies of /api/mv/plan
        self.answers = []         # what the next /api/mv/plan calls answer (default: ok, feed 0, no moves)
        self.progress = []        # bodies of /api/vod/progress

    def __call__(self, route):
        path = route.request.url[len(BASE):].split("?")[0]
        if path in ("/", "/index.html"):
            return route.fulfill(path=str(WEB / "index.html"), content_type="text/html")
        if path == "/static/tv.js":
            return route.fulfill(body=_bundle(), content_type="text/javascript")
        if path == "/static/vendor/mpegts.js":
            return route.fulfill(body=FAKE_MPEGTS, content_type="text/javascript")
        if path.startswith("/static/"):
            f = WEB / path[len("/static/"):]
            if f.is_file():
                return route.fulfill(path=str(f), content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        if path == "/api/channels":
            return route.fulfill(body=json.dumps(CHANNELS), content_type="application/json")
        if path == "/api/mv/plan":
            self.plans.append(json.loads(route.request.post_data))
            a = self.answers.pop(0) if self.answers else {"ok": True, "alt": 0, "moves": [], "note": ""}
            return route.fulfill(body=json.dumps(a), content_type="application/json")
        if path.startswith("/api/vod/play/"):
            return route.fulfill(body=json.dumps(MOVIE), content_type="application/json")
        if path == "/api/vod/progress":
            self.progress.append(json.loads(route.request.post_data))
            return route.fulfill(body="{}", content_type="application/json")
        if path == "/api/mv/room":
            return route.fulfill(body="[]", content_type="application/json")
        if path.startswith("/api/now/"):
            return route.fulfill(body='{"now": null, "next": null}', content_type="application/json")
        if path.startswith("/api/"):
            return route.fulfill(body="{}", content_type="application/json")
        return route.fulfill(status=404, body="")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = None
        for kw in ({}, {"channel": "chrome"}):
            try:
                b = p.chromium.launch(**kw)
                break
            except Exception:  # noqa: BLE001
                continue
        if not b:
            pytest.skip("no browser for Playwright (python -m playwright install chromium)")
        yield b
        b.close()


@pytest.fixture
def tv(browser):
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    srv = Server()
    pg.route("**/*", srv)
    pg.goto(BASE + "/")
    pg.wait_for_function("window.__mv")
    pg.evaluate(f"""async () => {{
        const m = window.__mv;
        document.querySelectorAll('#home,#dvr,#guide,#od,.screen,#welcome').forEach((e) => e.classList.add('hidden'));
        m.S.layer = [];
        m.S.channels = {json.dumps(CHANNELS)};
        m.S.byNum = new Map(m.S.channels.map((c, i) => [c.number, i]));
        m.tune(0, 0, true);
        await new Promise((r) => setTimeout(r, 100));
        window.__streams.length = 0;
    }}""")
    pg.srv = srv
    yield pg
    pg.close()


def streams(pg):
    return pg.evaluate("window.__streams")


def frames(pg):
    """[(slot, pos, focused, rect)] of the pictures on screen."""
    return pg.evaluate("""() => [...document.querySelectorAll('#mv .mv-f:not(.hidden):not(.mv-add)')].map((f) => {
        const r = f.getBoundingClientRect();
        return [+f.dataset.slot, +f.dataset.pos, f.classList.contains('focus'), [r.left, r.top, r.width, r.height].map(Math.round)];
    })""")


def add(pg, idx):
    pg.evaluate(f"async () => {{ const m = window.__mv; m.mvAddNow(m.S.channels[{idx}]); await new Promise((r) => setTimeout(r, 200)); }}")


def test_moves_run_before_the_new_tile_starts(tv):
    cid = tv.evaluate("window.__mv.CID")
    # ESPN (main, Strong) moves to its backup on Other, so FS1 (only on Strong) can have Strong
    tv.srv.answers.append({"ok": True, "alt": 0, "moves": [{"slot": 1, "alt": 1, "source": "Other"}],
                           "note": "Moved a tile to Other to free a Strong connection"})
    add(tv, 1)
    (plan,) = tv.srv.plans
    assert plan["client"] == cid and plan["slot"] == 2 and plan["want"] == {"number": 705}
    assert plan["tiles"] == [{"slot": 1, "number": 702, "key": None, "alt": 0}]
    assert streams(tv) == [f"/api/stream/702?alt=1&client={cid}&buf=0", f"/api/stream/705?alt=0&client={cid}~2&buf=0"]
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "pip"          # two pictures: PiP first
    tv.evaluate("window.__mv.mvNextLayout()")
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "split"
    tv.wait_for_timeout(400)                                            # the pictures slide into place
    (s1, p1, f1, r1), (s2, p2, f2, r2) = frames(tv)
    assert (s1, p1, f1, s2, p2, f2) == (1, 1, True, 2, 2, False)
    assert r1[2] == r2[2] == 640 and r2[0] == 640                       # side by side, half each
    # the main picture fills its half too (once it has slid there)
    tv.wait_for_timeout(400)
    assert tv.evaluate("Math.round(document.getElementById('video').getBoundingClientRect().width)") == 640


def test_highlight_carries_the_sound_and_the_channel_keys(tv):
    add(tv, 1)
    tv.evaluate("window.__mv.handleKey('ArrowRight')")
    assert tv.evaluate("window.__mv.S.mv.focus") == 2
    assert tv.evaluate("window.__mv.video.muted") is True
    assert tv.evaluate("window.__mv.soundEl().muted") is False
    assert tv.evaluate("window.__mv.soundEl() !== window.__mv.video")
    # CH+ changes the highlighted tile, through the planner, in its own slot
    tv.evaluate("window.__mv.handleKey('ArrowUp')")         # ▲ moves the highlight: nothing above
    tv.evaluate("window.__mv.handleKey('ChannelUp')")
    tv.wait_for_timeout(700)
    assert tv.srv.plans[-1]["slot"] == 2 and tv.srv.plans[-1]["want"] == {"number": 710}
    assert streams(tv)[-1].startswith("/api/stream/710?alt=0&client=") and "~2&" in streams(tv)[-1]


def test_no_room_offers_to_swap_a_tile(tv):
    add(tv, 1)
    tv.srv.answers.append({"ok": False, "reason": "Strong has no connection left", "full": [
        {"source": "Strong", "limit": 2, "using": [
            {"kind": "channel", "title": "705 FS1", "number": 705, "feed": None, "viewers": [], "slot": 2},
            {"kind": "channel", "title": "702 ESPN", "number": 702, "feed": None, "viewers": [{"kind": "tv", "name": "Kitchen"}]}]}]})
    add(tv, 2)
    assert not tv.evaluate("document.getElementById('dialog').classList.contains('hidden')")
    items = tv.evaluate("[...document.querySelectorAll('#dlg-list li')].map((l) => l.textContent)")
    assert items[0].startswith("Swap 705 FS1 for 710 NBC")
    assert any("702 ESPN" in i and "Kitchen" in i for i in items)
    tv.evaluate("document.querySelector('#dlg-list li').click()")
    tv.wait_for_timeout(200)
    assert tv.srv.plans[-1]["slot"] == 2 and tv.srv.plans[-1]["want"] == {"number": 710}
    assert tv.evaluate("window.__mv.S.mv.tiles.length") == 1


def test_four_squares_and_leaving_keeps_the_highlighted_one(tv):
    add(tv, 1)
    add(tv, 2)
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "big"
    tv.evaluate("window.__mv.mvNextLayout()")
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "big3"
    tv.evaluate("window.__mv.mvNextLayout()")
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "grid"
    assert tv.evaluate("!document.querySelector('#mv .mv-add').classList.contains('hidden')")   # the spare square
    add(tv, 3)
    fr = frames(tv)
    assert sorted(p for _, p, _, _ in fr) == [1, 2, 3, 4]
    assert {tuple(r) for *_, r in fr} == {(0, 0, 640, 360), (640, 0, 640, 360), (0, 360, 640, 360), (640, 360, 640, 360)}
    assert tv.evaluate("document.querySelector('#mv .mv-add').classList.contains('hidden')")
    # highlight the bottom right one (CNN) and leave: CNN in the main picture, every tile gone
    tv.evaluate("window.__mv.handleKey('ArrowDown'); window.__mv.handleKey('ArrowRight')")
    assert tv.evaluate("window.__mv.S.mv.focus") == 4
    tv.evaluate("window.__mv.handleKey('Enter')")
    tv.wait_for_timeout(200)
    cid = tv.evaluate("window.__mv.CID")
    assert streams(tv)[-1] == f"/api/stream/720?alt=0&client={cid}&buf=0"
    assert tv.evaluate("window.__mv.S.mv.tiles.length") == 0
    assert tv.evaluate("document.getElementById('tv').dataset.mv") is None
    assert tv.evaluate("document.getElementById('mv').classList.contains('hidden')")
    assert tv.evaluate("window.__mv.video.muted") is False
    tv.wait_for_timeout(400)
    assert tv.evaluate("Math.round(document.getElementById('video').getBoundingClientRect().width)") == 1280


def test_removing_the_main_picture_promotes_a_tile(tv):
    add(tv, 1)
    tv.evaluate("window.__mv.mvRemove(1)")
    tv.wait_for_timeout(200)
    cid = tv.evaluate("window.__mv.CID")
    assert streams(tv)[-1] == f"/api/stream/705?alt=0&client={cid}&buf=0"
    assert tv.evaluate("window.__mv.S.mv.tiles.length") == 0


def test_main_picture_backup_feed_stays_in_the_main_picture(tv):
    # the main picture fails over to its backup while a tile has the highlight: the backup plays in the
    # main picture (not the highlighted tile), without asking the planner (which would pick feed 0 again)
    add(tv, 1)
    tv.evaluate("window.__mv.handleKey('ArrowRight')")
    n = len(tv.srv.plans)
    tv.evaluate("window.__mv.failOver(0, 0)")
    tv.wait_for_timeout(200)
    cid = tv.evaluate("window.__mv.CID")
    assert len(tv.srv.plans) == n
    assert streams(tv)[-1] == f"/api/stream/702?alt=1&client={cid}&buf=0"
    assert tv.evaluate("window.__mv.S.mv.tiles[0].ch.number") == 705


def test_removing_the_highlighted_tile_gives_the_sound_back(tv):
    add(tv, 1)
    tv.evaluate("window.__mv.handleKey('ArrowRight')")
    assert tv.evaluate("window.__mv.video.muted") is True
    tv.evaluate("window.__mv.mvRemove(2)")
    assert tv.evaluate("window.__mv.S.mv.focus") == 1
    assert tv.evaluate("window.__mv.video.muted") is False


def test_replacing_a_tile_stops_its_stream_before_the_moves(tv):
    # replacing tile 2 frees its connection for the main picture's move: stop it on the server first
    add(tv, 1)
    tv.evaluate("""() => { const f = window.fetch; window.fetch = (u, o) => {
        if (String(u).includes('/api/stop')) window.__streams.push('STOP ' + JSON.parse(o.body).client);
        return f(u, o); }; }""")
    tv.evaluate("window.__mv.handleKey('ArrowRight')")
    tv.srv.answers.append({"ok": True, "alt": 0, "moves": [{"slot": 1, "alt": 1, "source": "Other"}],
                           "note": "Moved a tile to Other to free a Strong connection"})
    tv.evaluate("window.__mv.handleKey('ChannelUp')")
    tv.wait_for_timeout(700)
    cid = tv.evaluate("window.__mv.CID")
    s = streams(tv)
    i = s.index(f"STOP {cid}~2")
    assert s[i + 1] == f"/api/stream/702?alt=1&client={cid}&buf=0"
    assert s[i + 2] == f"/api/stream/710?alt=0&client={cid}~2&buf=0"


def _top_slot(pg, x, y):
    """The picture a click at (x, y) lands on: the slot of the frame on top there."""
    return pg.evaluate(f"(() => {{ const f = document.elementFromPoint({x}, {y}).closest('.mv-f'); return f ? +f.dataset.slot : 0; }})()")


def _rect(pg, sel):
    return pg.evaluate(f"(() => {{ const r = document.querySelector('{sel}').getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map(Math.round); }})()")


def test_ok_swaps_the_big_and_small_pictures_in_pip(tv):
    add(tv, 1)
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "pip"
    n = len(streams(tv))
    tv.evaluate("window.__mv.handleKey('Enter')")               # the small one (FS1) becomes big
    tv.wait_for_timeout(400)
    assert tv.evaluate("window.__mv.S.mv.tiles.length") == 1     # still in multiview
    assert tv.evaluate("window.__mv.S.mv.focus") == 2           # the highlight and the sound go with it
    assert tv.evaluate("window.__mv.video.muted") is True and tv.evaluate("window.__mv.soundEl().muted") is False
    assert _rect(tv, '#mv .mv-f[data-slot="2"]') == [0, 0, 1280, 720]
    small = _rect(tv, "#video")
    assert small[2] == 384 and small[0] > 800                    # the main picture is in the corner now
    assert _top_slot(tv, small[0] + 50, small[1] + 50) == 1     # ... on top of the big one
    assert _top_slot(tv, 300, 400) == 2
    assert len(streams(tv)) == n                                 # nothing restarted
    tv.evaluate("window.__mv.handleKey('Enter')")               # and back
    tv.wait_for_timeout(400)
    assert tv.evaluate("window.__mv.S.mv.focus") == 1
    assert tv.evaluate("window.__mv.video.muted") is False
    assert _rect(tv, "#video") == [0, 0, 1280, 720]
    assert _top_slot(tv, 1000, 100) == 2
    assert len(streams(tv)) == n


def test_ok_makes_a_small_picture_big_in_one_big_two_small(tv):
    add(tv, 1)
    add(tv, 2)
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "big"
    tv.evaluate("window.__mv.handleKey('ArrowRight'); window.__mv.handleKey('ArrowDown')")
    assert tv.evaluate("window.__mv.S.mv.focus") == 3
    tv.evaluate("window.__mv.handleKey('Enter')")
    assert tv.evaluate("window.__mv.S.mv.order") == [3, 2, 1]
    assert tv.evaluate("document.getElementById('video').dataset.pos") == "3"
    tv.evaluate("window.__mv.handleKey('Enter')")               # OK on the big one: full screen, as before
    tv.wait_for_timeout(200)
    assert tv.evaluate("window.__mv.S.mv.tiles.length") == 0
    assert tv.evaluate("window.__mv.S.mv.order") == []
    assert tv.evaluate("'pos' in document.getElementById('video').dataset") is False


def _dialog(pg):
    return pg.evaluate("""() => ({ shown: !document.getElementById('dialog').classList.contains('hidden'),
        title: document.getElementById('dlg-title').textContent,
        items: [...document.querySelectorAll('#dlg-list li')].map((l) => l.textContent.trim()) })""")


def _click_item(pg, text):
    pg.evaluate(f"""() => [...document.querySelectorAll('#dlg-list li')].find((l) => l.textContent.trim().startsWith({json.dumps(text)})).click()""")
    pg.wait_for_timeout(250)


def _show_controls(pg):
    pg.mouse.move(400, 300)
    pg.mouse.move(420, 320)
    pg.wait_for_timeout(100)


def test_multiview_button_picks_a_layout_then_a_channel(tv):
    tv.evaluate("window.__mv.S.favs = new Set([705, 710])")
    _show_controls(tv)
    assert tv.evaluate("!document.getElementById('rm-mv').classList.contains('hidden')")
    assert tv.evaluate("document.querySelector('#rm-mv span').textContent") == "Multiview"
    tv.evaluate("document.getElementById('rm-mv').click()")
    d = _dialog(tv)
    assert d["shown"] and d["title"] == "Watch two or more channels"
    assert d["items"] == ["Picture in picture", "Side by side", "One big, two small", "One big, three small", "Four squares"]
    assert tv.evaluate("document.getElementById('dialog').classList.contains('mv-dlg')")
    # Right moves along the row of layouts
    tv.evaluate("window.__mv.handleKey('ArrowRight'); window.__mv.handleKey('ArrowRight')")
    assert tv.evaluate("document.querySelector('#dlg-list li.sel').textContent") == "One big, two small"
    tv.evaluate("window.__mv.handleKey('Enter')")              # the three-picture layout
    d = _dialog(tv)
    assert d["title"] == "Pick the channel to add" and "One big, two small" in tv.evaluate("document.getElementById('dlg-sub').textContent")
    assert d["items"][:3] == ["Favorites", "705FS1", "710NBC"]
    assert "From the TV Guide…" in d["items"]
    tv.evaluate("window.__mv.handleKey('Back')")                # Back: to the layouts
    assert _dialog(tv)["title"] == "Watch two or more channels"
    tv.evaluate("window.__mv.handleKey('Enter')")
    _click_item(tv, "705")
    assert tv.srv.plans[-1]["slot"] == 2 and tv.srv.plans[-1]["want"] == {"number": 705}
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "big"
    # the third place waits with "+ Add a channel"
    spare = tv.evaluate("[...document.querySelectorAll('#mv .mv-add:not(.hidden)')].map((e) => e.dataset.pos)")
    assert spare == ["3"]
    tv.evaluate("document.querySelector('#mv .mv-add:not(.hidden)').click()")
    d = _dialog(tv)
    assert d["items"][:2] == ["Favorites", "710NBC"]             # 705 is on screen already
    _click_item(tv, "710")
    assert tv.evaluate("window.__mv.S.mv.tiles.length") == 2
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "big"
    assert tv.evaluate("document.querySelectorAll('#mv .mv-add:not(.hidden)').length") == 0
    # the button now changes the layout, or leaves
    _show_controls(tv)
    assert tv.evaluate("document.querySelector('#rm-mv span').textContent") == "Multiview on"
    tv.evaluate("document.getElementById('rm-mv').click()")
    d = _dialog(tv)
    # every layout: the two-picture ones say what they'd stop (the highlighted big one stays)
    assert d["title"] == "Multiview" and d["items"] == [
        "Picture in pictureStops 710 NBC", "Side by sideStops 710 NBC", "One big, two small", "One big, three small",
        "Four squares", "Leave multiview (keep the highlighted channel)"]


def test_one_connection_means_no_multiview(tv):
    tv.evaluate("window.__mv.S.settings.multiview_max = 1")
    _show_controls(tv)
    assert tv.evaluate("document.getElementById('rm-mv').classList.contains('hidden')")
    tv.evaluate("window.__mv.handleKey('m')")
    menu = tv.evaluate("[...document.querySelectorAll('#menu-list li')].map((l) => l.textContent)")
    assert not any("Multiview" in m or "two or more" in m for m in menu)


def test_two_connections_leave_out_the_bigger_layouts(tv):
    tv.evaluate("window.__mv.S.settings.multiview_max = 2")
    _show_controls(tv)
    tv.evaluate("document.getElementById('rm-mv').click()")
    assert _dialog(tv)["items"] == ["Picture in picture", "Side by side"]


def test_setup_can_turn_it_off(tv):
    tv.evaluate("window.__mv.S.settings.multiview_enabled = false")
    _show_controls(tv)
    assert tv.evaluate("document.getElementById('rm-mv').classList.contains('hidden')")


def test_one_big_three_small(tv):
    add(tv, 1)
    add(tv, 2)
    add(tv, 3)
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "big3"     # four pictures: one big first
    tv.wait_for_timeout(400)
    fr = {s: r for s, _, _, r in frames(tv)}
    assert fr[1] == [0, 90, 960, 540]                                          # 16:9, three quarters of the screen
    assert [fr[s] for s in (2, 3, 4)] == [[960, 90, 320, 180], [960, 270, 320, 180], [960, 450, 320, 180]]
    # OK on a small one makes it the big one
    tv.evaluate("window.__mv.handleKey('ArrowRight')")                 # the small one level with the big one's middle
    assert tv.evaluate("window.__mv.S.mv.focus") == 3
    tv.evaluate("window.__mv.handleKey('ArrowUp')")
    assert tv.evaluate("window.__mv.S.mv.focus") == 2
    tv.evaluate("window.__mv.handleKey('Enter')")
    assert tv.evaluate("window.__mv.S.mv.order[0]") == 2


def test_on_demand_plays_in_the_highlighted_small_picture(tv):
    cid = tv.evaluate("window.__mv.CID")
    add(tv, 1)
    tv.evaluate("window.__mv.handleKey('ArrowRight')")                        # highlight the small one
    n = len(streams(tv))
    tv.evaluate("async () => { await window.__mv.playVod('m1', 120); }")
    tv.wait_for_timeout(200)
    assert streams(tv)[n:] == [f"/api/vod/stream/m1?ss=120.0&client={cid}~2"]   # only the small picture changed
    assert tv.evaluate("window.__mv.S.mode") == "live"
    assert tv.evaluate("window.__mv.S.mv.tiles.length") == 1 and tv.evaluate("window.__mv.S.mv.focus") == 2
    assert tv.evaluate("document.querySelector('#mv .mv-f[data-slot=\"2\"] .mv-ch').textContent") == "▶ The Long Road"
    # Play/Pause pauses it there (the stand-in player never starts the video itself)
    tv.evaluate("(() => { window.__mv.S.mv.tiles[0].v.play().catch(() => {}); })()")
    tv.evaluate("window.__mv.handleKey(' ')")
    assert tv.evaluate("window.__mv.S.mv.tiles[0].v.paused") is True
    assert "Paused" in tv.evaluate("document.querySelector('#mv .mv-f[data-slot=\"2\"] .mv-msg').textContent")
    tv.evaluate("window.__mv.handleKey(' ')")
    assert tv.evaluate("window.__mv.S.mv.tiles[0].v.paused") is False
    # it holds a connection the planner can't move: it isn't offered as a picture to move
    tv.evaluate("window.__mv.handleKey('ArrowLeft')")
    add(tv, 2)
    assert [t["slot"] for t in tv.srv.plans[-1]["tiles"]] == [1]
    # Back: full screen in the On Demand player, from where it was; the rest stop
    tv.evaluate("window.__mv.handleKey('ArrowRight')")
    tv.evaluate("(() => { const m = window.__mv; m.S.mv.focus = m.S.mv.tiles.find((t) => t.vod).slot; })()")
    tv.evaluate("window.__mv.handleKey('Back')")
    tv.wait_for_timeout(300)
    assert tv.evaluate("window.__mv.S.mode") == "vod"
    assert tv.evaluate("window.__mv.S.mv.tiles.length") == 0
    assert streams(tv)[-1].startswith(f"/api/vod/stream/m1?ss=120") and f"client={cid}" in streams(tv)[-1] and "~" not in streams(tv)[-1]


def test_on_demand_with_the_main_picture_highlighted_uses_the_small_one(tv):
    cid = tv.evaluate("window.__mv.CID")
    add(tv, 1)
    assert tv.evaluate("window.__mv.S.mv.focus") == 1
    tv.evaluate("async () => { await window.__mv.playVod('m1', 0); }")
    tv.wait_for_timeout(200)
    assert streams(tv)[-1] == f"/api/vod/stream/m1?ss=0.0&client={cid}~2"
    assert tv.evaluate("window.__mv.S.mv.focus") == 2                      # and it has the sound


def test_on_demand_without_multiview_is_full_screen(tv):
    tv.evaluate("async () => { await window.__mv.playVod('m1', 0); }")
    tv.wait_for_timeout(200)
    assert tv.evaluate("window.__mv.S.mode") == "vod"
    assert "~" not in streams(tv)[-1]


def test_layout_cards_fill_the_row(tv):
    # three pictures at most, multiview on: three cards and the Leave row, no empty columns beside the cards
    tv.evaluate("window.__mv.S.settings.multiview_max = 3")
    add(tv, 1)
    _show_controls(tv)
    tv.evaluate("document.getElementById('rm-mv').click()")
    assert _dialog(tv)["items"] == ["Picture in picture", "Side by side", "One big, two small", "Leave multiview (keep the highlighted channel)"]
    list_r = _rect(tv, "#dlg-list")
    cards = tv.evaluate("[...document.querySelectorAll('#dlg-list li.mv-lay')].map((l) => { const r = l.getBoundingClientRect(); return [r.left, r.right]; })")
    assert abs(cards[-1][1] - (list_r[0] + list_r[2])) < 4          # the last card reaches the right edge
    assert abs(cards[0][0] - list_r[0]) < 4


def test_small_picture_stays_16_9_in_a_wide_window(tv):
    tv.set_viewport_size({"width": 1680, "height": 820})
    add(tv, 1)
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "pip"
    tv.wait_for_timeout(400)
    x, y, w, h = _rect(tv, '#mv .mv-f[data-slot="2"]')
    assert w == round(1680 * .3) and abs(w / h - 16 / 9) < .02


def _watch_stops(pg):
    pg.evaluate("""() => { const f = window.fetch; window.fetch = (u, o) => {
        if (String(u).includes('/api/stop')) window.__streams.push('STOP ' + JSON.parse(o.body).client);
        return f(u, o); }; }""")


def test_a_smaller_layout_stops_what_doesnt_fit(tv):
    cid = tv.evaluate("window.__mv.CID")
    add(tv, 1)
    add(tv, 2)                                                   # 702 big, 705 and 710 small
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "big"
    tv.evaluate("window.__mv.handleKey('ArrowRight')")
    tv.evaluate("window.__mv.handleKey('ArrowDown')")           # highlight 710
    assert tv.evaluate("window.__mv.S.mv.focus") == 3
    _watch_stops(tv)
    _show_controls(tv)
    tv.evaluate("document.getElementById('rm-mv').click()")
    d = _dialog(tv)
    assert d["title"] == "Multiview"
    assert d["items"][:2] == ["Picture in pictureStops 705 FS1", "Side by sideStops 705 FS1"]     # every layout, smaller ones too
    _click_item(tv, "Picture in picture")
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "pip"
    assert [t["slot"] for t in tv.evaluate("window.__mv.S.mv.tiles.map((t) => ({ slot: t.slot }))")] == [3]
    assert f"STOP {cid}~2" in streams(tv)                       # its connection is let go at once
    assert tv.evaluate("window.__mv.S.mv.focus") == 3 and tv.evaluate("window.__mv.soundEl().muted") is False


def test_a_smaller_layout_can_stop_the_main_picture(tv):
    cid = tv.evaluate("window.__mv.CID")
    add(tv, 1)
    add(tv, 2)
    tv.evaluate("window.__mv.handleKey('ArrowRight'); window.__mv.handleKey('ArrowDown')")
    tv.evaluate("window.__mv.handleKey('Enter')")              # 710 big; 705 and 702 small
    assert tv.evaluate("window.__mv.S.mv.order") == [3, 2, 1]
    _show_controls(tv)
    tv.evaluate("document.getElementById('rm-mv').click()")
    _click_item(tv, "Picture in picture")                      # keeps 710 (big, highlighted) and 705
    tv.wait_for_timeout(200)
    assert streams(tv)[-1] == f"/api/stream/705?alt=0&client={cid}&buf=0"   # 705 took the main picture's place
    assert [t.get("slot") for t in tv.evaluate("window.__mv.S.mv.tiles.map((t) => ({ slot: t.slot }))")] == [3]
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "pip"


def test_a_pictures_corner_buttons_change_or_remove_it(tv):
    tv.evaluate("window.__mv.S.favs = new Set([705, 710, 720])")
    add(tv, 1)
    add(tv, 2)                                                   # 702 big; 705 and 710 small
    tv.wait_for_timeout(400)
    sel = '#mv .mv-f[data-slot="2"]'
    x, y, w, h = _rect(tv, sel)
    assert tv.evaluate(f"getComputedStyle(document.querySelector('{sel} .mv-btns')).opacity") == "0"
    tv.mouse.move(x + w / 2, y + h / 2)                         # over the picture: its buttons show
    tv.wait_for_timeout(250)
    assert tv.evaluate(f"getComputedStyle(document.querySelector('{sel} .mv-btns')).opacity") == "1"
    tv.evaluate(f"document.querySelector('{sel} .mv-chg').click()")
    d = _dialog(tv)
    assert d["title"] == "Change 705 FS1"
    assert d["items"][:2] == ["Favorites", "720CNN"]             # what's on screen already is left out
    assert "From On Demand…" in d["items"]
    assert tv.evaluate("window.__mv.S.mv.focus") == 2           # it has the highlight (and the sound)
    _click_item(tv, "720")
    assert tv.srv.plans[-1]["slot"] == 2 and tv.srv.plans[-1]["want"] == {"number": 720}
    assert tv.evaluate("window.__mv.S.mv.tiles.find((t) => t.slot === 2).ch.number") == 720
    assert tv.evaluate("window.__mv.S.mv.tiles.length") == 2     # changed in place, nothing added
    # the x takes it off the screen
    tv.evaluate(f"document.querySelector('{sel} .mv-x').click()")
    assert tv.evaluate("window.__mv.S.mv.tiles.map((t) => t.slot)") == [3]


def test_the_multiview_key_opens_it(tv):
    add(tv, 1)
    tv.evaluate("window.__mv.handleKey('w')")                    # keyboards, and a remote button set up for it
    d = _dialog(tv)
    assert d["title"] == "Multiview" and d["items"][-1] == "Leave multiview (keep the highlighted channel)"
    assert not any(i.startswith(("Change", "Take")) for i in d["items"])


def test_the_big_pictures_buttons_are_clear_of_the_small_one(tv):
    add(tv, 1)
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "pip"
    tv.wait_for_timeout(400)
    tv.mouse.move(300, 400)                                      # over the big picture (the top bar comes up too)
    tv.wait_for_timeout(300)
    bx, by, bw, bh = _rect(tv, '#mv .mv-f[data-slot="1"] .mv-btns')
    px, py, pw, ph = _rect(tv, '#mv .mv-f[data-slot="2"]')
    assert bx + bw < px                                          # left of the small picture, not under it
    top = tv.evaluate("(() => { const r = document.querySelector('#remote .rm-top').getBoundingClientRect(); return r.bottom; })()")
    assert by > top                                              # below the on-screen controls' top bar
    assert tv.evaluate("document.elementFromPoint(%d, %d).closest('.mv-chg') !== null" % (bx + 20, by + bh // 2))


def test_multiview_is_not_in_the_menu(tv):
    add(tv, 1)
    tv.evaluate("window.__mv.handleKey('m')")
    menu = tv.evaluate("[...document.querySelectorAll('#menu-list li')].map((l) => l.textContent)")
    assert menu and not any("ultiview" in m or "two or more" in m or "the screen" in m for m in menu)


def test_the_small_pictures_buttons_are_clear_of_the_full_screen_button(tv):
    add(tv, 1)
    tv.wait_for_timeout(400)
    px, py, pw, ph = _rect(tv, '#mv .mv-f[data-slot="2"]')
    tv.mouse.move(px + pw / 2, py + ph / 2)
    tv.wait_for_timeout(300)
    bx, by, bw, bh = _rect(tv, '#mv .mv-f[data-slot="2"] .mv-btns')
    assert by > py + ph / 2                                      # along its bottom
    assert tv.evaluate("document.elementFromPoint(%d, %d).closest('.mv-chg') !== null" % (bx + 20, by + bh // 2))



def test_start_multiview_with_something_from_on_demand(tv):
    cid = tv.evaluate("window.__mv.CID")
    _show_controls(tv)
    tv.evaluate("document.getElementById('rm-mv').click()")
    _click_item(tv, "Picture in picture")
    d = _dialog(tv)
    assert d["title"] == "Pick the channel to add" and "From On Demand…" in d["items"]
    _click_item(tv, "From On Demand…")
    # On Demand opens to pick a title (the stand-in server has no catalogue), marked to add a picture
    assert tv.evaluate("window.__mv.S.mv.vodAdd > 0") and not _dialog(tv)["shown"]
    n = len(streams(tv))
    tv.evaluate("async () => { await window.__mv.playVod('m1', 0); }")
    tv.wait_for_timeout(200)
    assert streams(tv)[n:] == [f"/api/vod/stream/m1?ss=0.0&client={cid}~2"]   # a new small picture; the channel stays
    assert tv.evaluate("window.__mv.S.mode") == "live"
    assert tv.evaluate("window.__mv.S.mv.tiles.map((t) => t.slot)") == [2]
    assert tv.evaluate("document.getElementById('tv').dataset.mv") == "pip"
    # the next title picked plays where On Demand usually goes (the highlighted small picture), not another new one
    tv.evaluate("async () => { await window.__mv.playVod('m1', 0); }")
    assert tv.evaluate("window.__mv.S.mv.tiles.length") == 1


def test_add_from_on_demand_while_in_multiview(tv):
    cid = tv.evaluate("window.__mv.CID")
    add(tv, 1)
    tv.evaluate("window.__mv.mvAddPrompt()")
    _click_item(tv, "From On Demand…")
    tv.evaluate("async () => { await window.__mv.playVod('m1', 0); }")
    tv.wait_for_timeout(200)
    assert streams(tv)[-1] == f"/api/vod/stream/m1?ss=0.0&client={cid}~3"
    assert tv.evaluate("window.__mv.S.mv.tiles.map((t) => [t.slot, !!t.vod])") == [[2, False], [3, True]]


def test_no_on_demand_option_without_on_demand(tv):
    tv.evaluate("window.__mv.S.settings.vod_available = false")
    add(tv, 1)
    tv.evaluate("window.__mv.mvAddPrompt()")
    assert "From On Demand…" not in _dialog(tv)["items"]


def test_on_demand_comes_before_search(tv):
    add(tv, 1)
    tv.evaluate("window.__mv.mvAddPrompt()")
    items = _dialog(tv)["items"]
    i = items.index("Find a channel")
    assert items[i + 1:] == ["From the TV Guide…", "From Sports & Events…", "From On Demand…", "Search…"]



def _player_for(pg, client_end):
    """Index in window.__players of the newest player whose address ends with `client_end`."""
    return pg.evaluate(f"""(() => {{ const ps = window.__players; for (let i = ps.length - 1; i >= 0; i--)
        if (ps[i].url.includes({json.dumps(client_end)})) return i; return -1; }})()""")


def test_a_small_picture_this_browser_cant_show_is_converted(tv):
    cid = tv.evaluate("window.__mv.CID")
    add(tv, 1)
    i = _player_for(tv, f"client={cid}~2")
    tv.evaluate("MediaSource.isTypeSupported = () => false")      # no HEVC here
    tv.evaluate(f"window.__players[{i}].emit('media_info', {{ videoCodec: 'hvc1.2.4.L120' }})")
    tv.wait_for_timeout(200)
    assert streams(tv)[-1] == f"/api/stream/705?alt=0&client={cid}~2&buf=0&mode=h264"


def test_a_stuttering_small_picture_is_converted(tv):
    # HEVC the browser says it can play, but decodes too slowly in the small picture: frames drop
    cid = tv.evaluate("window.__mv.CID")
    add(tv, 1)
    tv.evaluate("""(() => {
        localStorage.removeItem('tb.slowHevc');
        MediaSource.isTypeSupported = () => true;
        const v = window.__mv.S.mv.tiles[0].v; let n = 0;
        Object.defineProperty(v, 'paused', { get: () => false });
        Object.defineProperty(v, 'readyState', { get: () => 4 });
        Object.defineProperty(v, 'buffered', { get: () => ({ length: 1, start: () => 0, end: () => 30 }) });
        v.getVideoPlaybackQuality = () => { n += 30; return { totalVideoFrames: n, droppedVideoFrames: n / 3 }; };
    })()""")
    i = _player_for(tv, f"client={cid}~2")
    tv.evaluate(f"window.__players[{i}].emit('media_info', {{ videoCodec: 'hvc1.2.4.L120' }})")
    tv.wait_for_timeout(3500)                                    # two bad seconds in a row
    assert streams(tv)[-1] == f"/api/stream/705?alt=0&client={cid}~2&buf=0&mode=h264"
    assert "hvc1.2" in tv.evaluate("localStorage.getItem('tb.slowHevc')")      # remembered for next time


def test_a_stuttering_movie_in_a_small_picture_is_converted_where_it_is(tv):
    cid = tv.evaluate("window.__mv.CID")
    add(tv, 1)
    tv.evaluate("window.__mv.handleKey('ArrowRight')")
    tv.evaluate("async () => { await window.__mv.playVod('m1', 600); }")
    tv.evaluate("MediaSource.isTypeSupported = () => false")
    i = _player_for(tv, f"client={cid}~2")
    tv.evaluate(f"window.__players[{i}].emit('media_info', {{ videoCodec: 'hev1.1.6.L93' }})")
    tv.wait_for_timeout(200)
    last = streams(tv)[-1]
    assert last.startswith("/api/vod/stream/m1?ss=600") and f"client={cid}~2" in last and last.endswith("&mode=h264")



def test_the_guides_show_dialog_has_no_multiview_entry(tv):
    # multiview starts from the Multiview button (or W), not from a show in the Guide
    tv.evaluate("""(() => { const m = window.__mv, now = Math.floor(Date.now() / 1000);
        m.programDialog(m.S.channels[1], { title: "Coastal League", start: now - 600, stop: now + 3000 }); })()""")
    items = _dialog(tv)["items"]
    assert "Watch now" in items
    assert not any("screen" in i or "ultiview" in i for i in items)
