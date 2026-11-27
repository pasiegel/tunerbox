"""The guide's show in On Demand, in a real browser: a show or movie on now that's also in On Demand gets
"from the beginning" and "continue from here" in its guide dialog, in the Menu and on the on-screen controls'
On Demand button, and a server without /api/vod/for-program (an older TunerBox) just leaves them out. The server is mocked.

Needs Playwright (see test_ui_gamestats.py); skipped when it isn't installed."""
import json
import re
import time

import pytest

from test_ui_multiview import BASE, FAKE_MPEGTS, WEB, browser  # noqa: F401  (browser: the shared fixture)
from tunerbox import bundle

sync_api = pytest.importorskip("playwright.sync_api")

CHANNELS = [{"number": 702, "name": "AMC", "label": "AMC", "mapped": True, "alts": 0, "epg": "x:amc", "logo": ""}]
T0 = int(time.time()) - 43 * 60                     # on for 43 minutes of an hour's slot
PROG = {"title": "Dragnet", "subtitle": "The Human Bomb", "episode": "S01E01", "start": T0, "stop": T0 + 3600}
MATCH = {"id": "1:e101", "kind": "episode", "title_id": "1:s7", "title": "Dragnet", "sub": "S1 E1 · The Human Bomb",
         "duration": 42 * 60, "position": 0}
ITEM = {"id": "1:e101", "title": "Dragnet", "sub": "S1 E1", "duration": 42 * 60, "position": 0}


def _bundle():
    js = bundle.tv_js()
    end = js.rindex("})();")
    return js[:end] + "window.__t = { S, tune, programDialog, openMenu, showRemote };\n" + js[end:]


def server(match=True):
    def handle(route):
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
                return route.fulfill(path=str(f))
        if path == "/api/channels":
            return route.fulfill(body=json.dumps(CHANNELS), content_type="application/json")
        if path.startswith("/api/now/"):
            return route.fulfill(body=json.dumps({"now": PROG, "next": None}), content_type="application/json")
        if path.startswith("/api/vod/for-program/"):
            if not match:
                return route.fulfill(status=404, body='{"detail": "Not Found"}', content_type="application/json")
            return route.fulfill(body=json.dumps({"match": MATCH, "slot": {"start": T0, "stop": T0 + 3600}}),
                                 content_type="application/json")
        if path.startswith("/api/vod/play/"):
            return route.fulfill(body=json.dumps(ITEM), content_type="application/json")
        if path.startswith("/api/"):
            return route.fulfill(body="{}", content_type="application/json")
        return route.fulfill(status=404, body="")
    return handle


def open_tv(browser, match=True):  # noqa: F811
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    pg.route("**/*", server(match))
    pg.goto(BASE + "/")
    pg.wait_for_function("window.__t")
    pg.evaluate(f"""async () => {{
        const t = window.__t;
        document.querySelectorAll('#home,#dvr,#guide,#od,.screen,#welcome').forEach((e) => e.classList.add('hidden'));
        t.S.layer = [];
        t.S.channels = {json.dumps(CHANNELS)};
        t.S.byNum = new Map(t.S.channels.map((c, i) => [c.number, i]));
        t.tune(0, 0, true);
        await new Promise((r) => setTimeout(r, 100));
        window.__streams.length = 0;
    }}""")
    return pg


def labels(pg, sel):
    return pg.evaluate(f"[...document.querySelectorAll('{sel} li')].map((li) => li.textContent)")


def assert_seeks_to_the_broadcast(url):
    """ss= is where the broadcast is when it was clicked: 30 minutes in at import (T0), then 42/60 of a
    second per second since, so a long (parallel) run doesn't push it past a fixed bound."""
    want = (time.time() - T0) * 42 / 60
    assert abs(float(url.split("ss=")[1].split("&")[0]) - want) < 15


def near_broadcast(text, pattern):
    """The label shows where the broadcast is now in the episode (min:sec), which moves on 42/60 of a second
    per second after import (T0): a long run reaches these tests well past 30 minutes."""
    m = re.match(pattern, text)
    assert m, text
    want = (time.time() - T0) * 42 / 60 / 60
    assert abs(int(m.group(1)) - want) <= 1.5, text


def test_guide_dialog_offers_on_demand_from_the_start_or_from_here(browser):  # noqa: F811
    pg = open_tv(browser)
    pg.evaluate(f"window.__t.programDialog(window.__t.S.channels[0], {json.dumps(PROG)})")
    pg.wait_for_function("[...document.querySelectorAll('#dlg-list li')].some((li) => li.textContent.includes('On Demand'))")
    got = labels(pg, "#dlg-list")
    assert got[0] == "Watch now"
    assert got[1] == "▶ Watch this episode (S1 E1) On Demand from the beginning"
    # 43 minutes into an hour's slot is about 30 minutes into the 42-minute episode
    near_broadcast(got[2], r"▶ Continue On Demand from here \(about (\d+):")
    pg.click("#dlg-list li:nth-child(3)")
    pg.wait_for_function("window.__streams.some((u) => u.includes('/api/vod/stream/'))")
    url = next(u for u in pg.evaluate("window.__streams") if "/api/vod/stream/" in u)
    assert url.startswith("/api/vod/stream/1%3Ae101?ss=")
    assert_seeks_to_the_broadcast(url)
    assert pg.evaluate("window.__t.S.mode") == "vod" and pg.evaluate("window.__t.S.vod.back") is False
    pg.close()


def test_menu_offers_it_for_the_show_on_now(browser):  # noqa: F811
    pg = open_tv(browser)
    pg.evaluate("window.__t.openMenu()")
    pg.wait_for_function("[...document.querySelectorAll('#menu-list li')].some((li) => li.textContent.includes('On Demand from'))")
    got = labels(pg, "#menu-list")
    i = got.index("This channel")
    assert got[i + 1] == "▶ Watch this episode (S1 E1) On Demand from the beginning"
    assert got[i + 2].startswith("▶ Continue On Demand from here")
    pg.close()


def test_an_older_server_just_has_no_on_demand_entries(browser):  # noqa: F811
    pg = open_tv(browser, match=False)
    pg.evaluate(f"window.__t.programDialog(window.__t.S.channels[0], {json.dumps(PROG)})")
    pg.evaluate("window.__t.openMenu()")
    pg.wait_for_timeout(300)
    assert not any("On Demand" in x for x in labels(pg, "#dlg-list") + labels(pg, "#menu-list") if "▶" in x)
    pg.close()


# ---------------------------------------------------------------- the On Demand button on the on-screen controls
def controls(pg):
    pg.mouse.move(640, 300)                             # the mouse brings up the on-screen controls
    pg.evaluate("window.__t.showRemote()")


def test_controls_get_an_on_demand_button_with_its_choices(browser):  # noqa: F811
    pg = open_tv(browser)
    controls(pg)
    pg.wait_for_selector("#rm-od:not(.hidden)", timeout=5000)
    pg.click("#rm-od")
    assert pg.is_visible("#rm-odmenu")
    assert pg.text_content("#rm-odmenu .rm-odhead") == "This episode is in On Demand"
    got = pg.evaluate("[...document.querySelectorAll('#rm-odmenu button')].map((b) => b.textContent)")
    assert got[0] == "Start from the beginning"
    near_broadcast(got[1], r"Continue · (\d+):")
    pg.click("#rm-odmenu button:nth-of-type(2)")       # Continue: from where the broadcast is
    pg.wait_for_function("window.__streams.some((u) => u.includes('/api/vod/stream/'))")
    url = next(u for u in pg.evaluate("window.__streams") if "/api/vod/stream/" in u)
    assert_seeks_to_the_broadcast(url)
    assert pg.evaluate("window.__t.S.mode") == "vod" and pg.is_hidden("#remote")
    pg.close()


def test_controls_on_demand_button_toggles_its_menu(browser):  # noqa: F811
    pg = open_tv(browser)
    controls(pg)
    pg.wait_for_selector("#rm-od:not(.hidden)", timeout=5000)
    pg.click("#rm-od")
    assert pg.is_visible("#rm-odmenu")
    pg.click("#rm-od")
    assert pg.is_hidden("#rm-odmenu")
    pg.close()


def test_an_older_server_has_no_on_demand_button(browser):  # noqa: F811
    pg = open_tv(browser, match=False)
    controls(pg)
    pg.wait_for_timeout(800)
    assert pg.is_hidden("#rm-od")
    pg.close()
