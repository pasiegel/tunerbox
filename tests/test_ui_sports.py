"""The Sports & Events screen in a real browser: the list of sports on the left, the big game and score
cards, replays folded away, a sport's page by day, and an older server (no "kind") still working.

Needs Playwright (see test_ui_gamestats.py); skipped when it isn't installed."""
import json
import mimetypes
import time
from pathlib import Path

import pytest

from tunerbox import bundle

sync_api = pytest.importorskip("playwright.sync_api")

WEB = Path(__file__).resolve().parent.parent / "web"
BASE = "http://tunerbox.test"
CHANNELS = [{"number": n, "name": f"C{n}", "label": f"C{n}", "mapped": True, "alts": 0, "epg": None, "logo": ""} for n in (2, 5, 6, 7)]


def _team(name, abbr, score, color):
    return {"name": name, "abbr": abbr, "logo": "", "color": color, "score": score}


def _data(now, kinds=True):
    def row(title, ch, start, stop, sport, kind, live=0, event=None, sub=""):
        r = {"title": title, "subtitle": sub, "start": start, "stop": stop, "sport": sport, "is_live": live,
             "ch": {"number": ch, "name": f"C{ch}", "label": f"C{ch}"} if ch else None, "also": [], "feeds": [], "feed": None,
             "heading": title, "matchup": sub, "event": event, "category": "Sports", "is_new": 0}
        if kinds:
            r["kind"] = kind
        return r
    ev = lambda i, a, b: {"id": f"nba:{i}", "league": "NBA", "league_name": "NBA", "heading": "NBA", "sport": "Basketball",  # noqa: E731
                          "name": f"{a[0]} at {b[0]}", "fav": [], "teams": [_team(*a), _team(*b)], "state": "in",
                          "detail": "2nd 5:00", "score": "", "on": []}
    on_now = [
        row("NBA", 5, now - 3600, now + 3600, "Basketball", "game", 1, ev(1, ("Lakers", "LAL", "50", "552583"), ("Celtics", "BOS", "48", "007a33")), "Lakers at Celtics"),
        row("NBA", 6, now - 1800, now + 5400, "Basketball", "game", 1, ev(2, ("Suns", "PHX", "20", "1d1160"), ("Heat", "MIA", "22", "98002e")), "Suns at Heat"),
        row("NBA League Pass", 7, now - 900, now + 3000, "Basketball", "on"),      # listed before the live one...
        row("Cubs Classics", 7, now - 600, now + 3000, "Baseball", "replay"),
        row("Live: OVW TV", 2, now - 600, now + 3000, "Wrestling", "live", 1),
    ]
    upcoming = [row("WWE NXT", 2, now + 7200, now + 14400, "Wrestling", "live", 1)]
    return {"on_now": on_now, "upcoming": upcoming, "channels": [], "groups": []}


def _js():
    js = bundle.tv_js()
    end = js.rindex("})();")
    return js[:end] + "window.__sp = { S, openSports, sportsKey, SP, RM, loadGameAlerts };\n" + js[end:]


class Server:
    def __init__(self, data, layout="enhanced"):
        self.data, self.layout = data, layout

    def __call__(self, route):
        url = route.request.url
        path = url[len(BASE):].split("?")[0]
        q = url.split("?", 1)[1] if "?" in url else ""
        J = lambda o: route.fulfill(body=json.dumps(o), content_type="application/json")  # noqa: E731
        if path in ("/", "/index.html"):
            return route.fulfill(path=str(WEB / "index.html"), content_type="text/html")
        if path == "/static/tv.js":
            return route.fulfill(body=_js(), content_type="text/javascript")
        if path.startswith("/static/"):
            f = WEB / path[len("/static/"):]
            if f.is_file():
                return route.fulfill(path=str(f), content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        if path == "/api/channels":
            return J(CHANNELS)
        if path == "/api/sports":
            tab = q.partition("=")[2]
            keys = {"now": ["on_now"], "up": ["upcoming"], "ch": ["channels"], "ev": ["groups"]}.get(tab, list(self.data))
            return J({k: self.data[k] for k in keys})
        if path == "/api/settings":
            st = {"sports_layout": self.layout} if self.layout else {}         # None: an older server
            return J({**st, **getattr(self, "extra", {})})
        if path == "/api/sports/alerts":
            return J(getattr(self, "alerts", []))
        if path.startswith("/api/now/"):
            return J({"now": None, "next": None})
        if path.startswith("/api/"):
            return J({})
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


def _open(browser, srv):
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    pg.route("**/*", srv)
    pg.goto(BASE + "/")
    pg.wait_for_function("window.__sp")
    pg.evaluate(f"""() => {{
        document.querySelectorAll('#home,#dvr,#guide,#od,.screen,#welcome').forEach((e) => e.classList.add('hidden'));
        const m = window.__sp; m.S.layer = []; m.S.channels = {json.dumps(CHANNELS)};
        m.S.byNum = new Map(m.S.channels.map((c, i) => [c.number, i])); m.openSports(); }}""")
    if srv.layout == "enhanced":
        pg.wait_for_function("document.querySelectorAll('#sp-rail .sp-ri').length > 4")
    return pg


def rail(pg):
    return pg.evaluate("[...document.querySelectorAll('#sp-rail .sp-ri .sp-rn')].map((e) => e.textContent)")


def test_live_now_has_the_big_game_cards_and_folds_replays_away(browser):
    pg = _open(browser, Server(_data(int(time.time()))))
    assert rail(pg)[:2] == ["Live now", "Coming up"] and {"Basketball", "Wrestling"} <= set(rail(pg))
    assert "Baseball" not in rail(pg)                                  # only a replay: folded away
    assert pg.inner_text(".sp-hero .sp-hs").replace("\n", "") == "50–48"       # the first game, big
    assert pg.locator(".sp-card").count() == 1                          # the other game as a score card
    rows = pg.evaluate("[...document.querySelectorAll('#sp-main .sp-row .sp-t')].map((e) => e.textContent)")
    assert "Cubs Classics" not in rows and any("OVW" in r for r in rows)
    order = [r for r in rows if "OVW" in r or "League Pass" in r]
    assert order[0].endswith("Live: OVW TV") and order[1].endswith("NBA League Pass")     # ...shown after it
    more = pg.locator(".sp-more")
    assert more.inner_text() == "Show 1 replay and show"
    more.click()
    assert any("Cubs Classics" in t for t in pg.evaluate("[...document.querySelectorAll('#sp-main .sp-row .sp-t')].map((e) => e.textContent)"))
    # the keys: into the right side, down through the cards, back to the list
    pg.evaluate("() => { const m = window.__sp; m.sportsKey('ArrowLeft'); m.sportsKey('ArrowRight'); }")   # the click went in
    assert pg.evaluate("document.querySelector('#sp-main .f.sel').classList.contains('sp-hero')")
    pg.evaluate("() => { const m = window.__sp; m.sportsKey('ArrowDown'); }")
    assert pg.evaluate("document.querySelector('#sp-main .f.sel').classList.contains('sp-card')")
    pg.evaluate("() => { const m = window.__sp; m.sportsKey('ArrowLeft'); }")
    assert pg.evaluate("window.__sp.SP.zone") == "rail"
    pg.close()


def test_a_sport_shows_what_is_on_big_then_the_rest_by_day(browser):
    pg = _open(browser, Server(_data(int(time.time()))))
    i = rail(pg).index("Wrestling")
    pg.locator("#sp-rail .sp-ri").nth(i).click()
    pg.wait_for_function("document.querySelector('.sp-hero')")
    assert "OVW" in pg.inner_text(".sp-hero")                           # on now beats tonight's show
    heads = pg.evaluate("[...document.querySelectorAll('#sp-main .sp-h')].map((e) => e.textContent)")
    assert heads and heads[0] in ("Later today", "Tomorrow")            # tonight's NXT, under its day
    assert "WWE NXT" in pg.inner_text("#sp-main .sp-row")
    pg.close()


def test_an_older_server_folds_nothing_away(browser):
    pg = _open(browser, Server(_data(int(time.time()), kinds=False)))
    assert "Baseball" in rail(pg) and pg.locator(".sp-more").count() == 0
    i = rail(pg).index("Wrestling")
    pg.locator("#sp-rail .sp-ri").nth(i).click()
    pg.wait_for_timeout(300)
    assert pg.locator(".sp-hero").count() == 1
    pg.close()


def test_a_close_game_goes_big_and_extra_games_fold_into_a_more_card(browser):
    now = int(time.time())
    d = _data(now)
    game = d["on_now"][0]
    more = []
    for n in range(3, 11):           # eight more games; the sixth is close late on
        ev = json.loads(json.dumps(game["event"]))
        ev.update(id=f"nba:{n}", name=f"Game {n}", close=(n == 8))
        more.append({**game, "event": ev, "matchup": f"Game {n}", "ch": {"number": 5, "name": "C5", "label": "C5"}})
    d["on_now"] = d["on_now"][:2] + more + d["on_now"][2:]
    pg = _open(browser, Server(d))
    assert "CLOSE GAME" in pg.inner_text(".sp-hero")                 # the close one is the big one
    assert pg.locator(".sp-card:not(.sp-mcard)").count() == 5
    assert pg.inner_text(".sp-mcard").startswith("+4 more games")   # 9 other games: 5 cards, 4 folded
    pg.locator(".sp-mcard").click()
    assert pg.evaluate("window.__sp.SP.sec") == "s:Basketball"
    pg.close()


@pytest.mark.parametrize("layout", ["basic", None])          # the default, and an older server (no setting)
def test_basic_layout_is_the_tabs_and_one_list(browser, layout):
    pg = _open(browser, Server(_data(int(time.time())), layout))
    pg.wait_for_function("document.querySelectorAll('#spb-list li').length > 1")
    assert pg.is_visible("#sports-basic") and not pg.is_visible("#sports")
    assert pg.inner_text("#spb-tabs").split()[0:2] == ["On", "Now"]
    pg.evaluate("() => window.__sp.sportsKey('Back')")                # the keys reach the open one
    assert not pg.is_visible("#sports-basic")
    pg.close()


def test_the_layout_follows_the_setting_the_next_time_sports_opens(browser):
    srv = Server(_data(int(time.time())), "basic")
    pg = _open(browser, srv)
    pg.wait_for_function("document.querySelectorAll('#spb-list li').length > 1")
    pg.evaluate("() => window.__sp.sportsKey('Back')")
    srv.layout = "enhanced"
    pg.evaluate("() => window.__sp.openSports()")
    pg.wait_for_function("document.querySelectorAll('#sp-rail .sp-ri').length > 4")
    assert pg.is_visible("#sports") and not pg.is_visible("#sports-basic")
    pg.close()


@pytest.mark.parametrize("on", [True, False])
def test_close_game_popup_can_be_turned_off(browser, on):
    now = int(time.time())
    game = _data(now)["on_now"][0]
    game["event"].update(fav=["Los Angeles Lakers"], close=True)
    srv = Server(_data(now))
    srv.extra, srv.alerts = {"sports_close_alerts": on}, [game]
    pg = _open(browser, srv)
    pg.evaluate("async () => { const m = window.__sp; m.S.settings = { ...m.S.settings, sports_close_alerts: %s }; await m.loadGameAlerts(); }" % ("true" if on else "false"))
    keys = pg.evaluate("window.__sp.RM.games.map((r) => r.key || 'start')")
    assert keys == (["start", "close:nba:1"] if on else ["start"])       # the game-start pop-up stays either way
    pg.close()
