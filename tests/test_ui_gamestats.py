"""The game stats panel in a real browser: every sport, every tab, at the sizes and places people put it
(the corner card, snapped to a side, the smallest it resizes to, a strip along the bottom, made big) on a
720p and a 1080p screen. Nothing may wrap onto a second line (the tabs, the score line, the table headers
and numbers, the score tiles, the key hints) or run off the panel.

Needs Playwright (`pip install playwright`, then `python -m playwright install chromium`; CI does both).
Skipped when it isn't installed. Set TB_UI_SHOTS=<folder> to save a screenshot of every case there.
"""
import json
import mimetypes
import os
from pathlib import Path

import pytest

from tunerbox import bundle

sync_api = pytest.importorskip("playwright.sync_api")

WEB = Path(__file__).resolve().parent.parent / "web"
BASE = "http://tunerbox.test"
SHOTS = os.environ.get("TB_UI_SHOTS")


# ---------------------------------------------------------------- test data: the panel's own shape
def _team(abbr, short, name, score, record, color):
    return {"abbr": abbr, "short": short, "name": name, "score": score, "record": record, "color": color, "logo": ""}


def _rows(rows):
    return [{"num": n, "name": name, "stats": stats} for n, name, stats in rows]


def _game(league, sport, detail, teams, tables, team_stats, plays):
    return {"league": league, "sport": sport, "state": "in", "detail": detail, "updated": 1795140720,
            "teams": teams,
            "players": [{"abbr": t["abbr"], "tables": [{"title": title, "cols": cols, "rows": _rows(rows)}
                                                       for title, cols, rows in tables[i]]}
                        for i, t in enumerate(teams)] if tables else [],
            "team_stats": [{"label": k, "values": v} for k, v in team_stats],
            "plays": [{"when": w, "text": x, "score": sc} for w, x, sc in plays]}


LONG_PLAY = ("Shai Gilgeous-Alexander makes 27-foot three point jumper (Jalen Williams assists), "
             "and the lead is back to six with the shot clock winding down")

GAMES = {
    # the longest short names, three-figure scores, made-attempted columns
    "nba": _game("NBA", "Basketball", "End of 3rd Quarter",
                 [_team("OKC", "Thunder", "Oklahoma City Thunder", "118", "61-21", "007ac1"),
                  _team("MIN", "Timberwolves", "Minnesota Timberwolves", "109", "49-33", "0c2340")],
                 [[("", ["PTS", "REB", "AST", "FG"], [("2", "S. Gilgeous-Alexander", ["38", "6", "9", "13-24"]),
                                                       ("8", "J. Williams", ["21", "5", "4", "8-17"]),
                                                       ("11", "Isaiah Hartenstein", ["12", "14", "3", "6-8"])])],
                  [("", ["PTS", "REB", "AST", "FG"], [("5", "A. Edwards", ["35", "7", "5", "12-27"]),
                                                       ("32", "Karl-Anthony Towns", ["22", "11", "2", "9-18"])])]],
                 [("FG", ["45-88", "41-90"]), ("3PT", ["15-38", "12-35"]), ("FT", ["13-15", "15-21"]),
                  ("Rebounds", ["44", "47"]), ("Fast Break Points", ["18", "9"]), ("Points in Paint", ["52", "44"])],
                 [("Q3 · 0:02", LONG_PLAY, True), ("Q3 · 0:31", "Edwards defensive rebound", False)]),
    # the panel from the first screenshot: a long player name, C/ATT
    "ncaaf": _game("NCAAF", "Football", "13:18 - 2nd",
                   [_team("RICE", "Owls", "Rice Owls", "0", "1-9", "003d7d"),
                    _team("TEM", "Owls", "Temple Owls", "7", "2-8", "9d2235")],
                   [[("Passing", ["C/ATT", "YDS", "TD", "INT"], [("4", "Gael Ochoa", ["22/31", "319", "3", "0"])]),
                     ("Rushing", ["CAR", "YDS", "TD"], [("28", "D'Andre Hardeman Jr.", ["23", "115", "1"]),
                                                        ("8", "Colin Leahey", ["2", "10", "0"])]),
                     ("Receiving", ["REC", "YDS", "TD"], [("19", "Jordan Clark", ["12", "142", "2"])])],
                    [("Passing", ["C/ATT", "YDS", "TD", "INT"], [("11", "Evan Simon", ["18/29", "211", "1", "1"])]),
                     ("Rushing", ["CAR", "YDS", "TD"], [("2", "Antwain Littleton II", ["17", "88", "1"])])]],
                   [("Total Yards", ["434", "302"]), ("3rd down efficiency", ["7-13", "4-11"]),
                    ("Possession", ["32:15", "27:45"]), ("Penalties", ["8-75", "5-40"])],
                   [("Q2 · 13:18", "Jordan Clark 12 Yd pass from Gael Ochoa (Conner Weigman Kick)", True)]),
    "nhl": _game("NHL", "Hockey", "14:22 - 2nd",
                 [_team("CBJ", "Blue Jackets", "Columbus Blue Jackets", "3", "30-37-15", "002654"),
                  _team("VGK", "Golden Knights", "Vegas Golden Knights", "4", "50-22-10", "b4975a")],
                 [[("Skaters", ["G", "A", "SOG", "+/-"], [("13", "Johnny Gaudreau", ["1", "2", "5", "+2"])]),
                   ("Goalies", ["SA", "SV", "SV%"], [("70", "Elvis Merzlikins", ["31", "27", ".871"])])],
                  [("Skaters", ["G", "A", "SOG", "+/-"], [("61", "Mark Stone", ["2", "0", "4", "+1"])]),
                   ("Goalies", ["SA", "SV", "SV%"], [("33", "Adin Hill", ["24", "21", ".875"])])]],
                 [("Power Play Opportunities", ["3", "4"]), ("Faceoffs Won", ["28", "31"]), ("Penalty Minutes", ["12", "8"])],
                 [("P2 · 14:22", "Goal - Mark Stone wrist shot (assisted by Jack Eichel, Shea Theodore)", True)]),
    "mlb": _game("MLB", "Baseball", "Bottom 7th",
                 [_team("LAD", "Dodgers", "Los Angeles Dodgers", "10", "98-64", "005a9c"),
                  _team("NYY", "Yankees", "New York Yankees", "11", "94-68", "003087")],
                 [[("Batting", ["H-AB", "R", "HR", "RBI"], [("17", "Shohei Ohtani", ["3-5", "2", "1", "4"]),
                                                            ("50", "Mookie Betts", ["2-4", "1", "0", "1"])]),
                   ("Pitching", ["IP", "H", "ER", "K"], [("18", "Yoshinobu Yamamoto", ["6.2", "4", "2", "10"])])],
                  [("Batting", ["H-AB", "R", "HR", "RBI"], [("99", "Aaron Judge", ["2-3", "2", "2", "5"])]),
                   ("Pitching", ["IP", "H", "ER", "K"], [("45", "Gerrit Cole", ["5.0", "8", "6", "7"])])]],
                 [("Left On Base", ["9", "6"]), ("Strikeouts", ["12", "14"])],
                 [("Bottom 7th", "Judge homered to center (412 feet), Soto scored", True)]),
    # soccer: team stats and plays only, home side first
    "soccer": _game("Premier League", "Soccer", "90'+6'",
                    [_team("WOL", "Wolverhampton", "Wolverhampton Wanderers", "2", "", "fdb913"),
                     _team("NFO", "Nottm Forest", "Nottingham Forest", "2", "", "dd0000")],
                    None,
                    [("Possession", ["58.3%", "41.7%"]), ("SHOTS", ["17", "9"]), ("Yellow Cards", ["3", "4"])],
                    [("90'+4'", "Goal! Wolverhampton Wanderers 2, Nottingham Forest 2. Matheus Cunha header", True)]),
}


def _other(i, league, sport, a, b, sa, sb):
    return {"id": f"o{i}", "name": "", "league": league, "sport": sport, "state": "in", "detail": "6:05 - 1st", "start": 0,
            "teams": [{**_team(*a, sa, "", "0021a5")}, {**_team(*b, sb, "", "eaaa00")}],
            "away": {**_team(*a, sa, "", "0021a5")}, "home": {**_team(*b, sb, "", "eaaa00")},
            "ch": {"number": 1205}, "also": [{"number": 2}, {"number": 3}], "feeds": []}


OTHERS = [_other(i, *g) for i, g in enumerate([
    ("NCAAM", "Basketball", ("FLA", "Gators", "Florida Gators"), ("WVU", "Mountaineers", "West Virginia Mountaineers"), "129", "116"),
    ("NFL", "Football", ("NE", "Patriots", "New England Patriots"), ("BUF", "Bills", "Buffalo Bills"), "24", "27"),
    ("NHL", "Hockey", ("CBJ", "Blue Jackets", "Columbus Blue Jackets"), ("VGK", "Golden Knights", "Vegas Golden Knights"), "3", "4"),
    ("Premier League", "Soccer", ("WOL", "Wolves", "Wolverhampton Wanderers"), ("NFO", "Forest", "Nottingham Forest"), "2", "2"),
    ("MLB", "Baseball", ("LAD", "Dodgers", "Los Angeles Dodgers"), ("NYY", "Yankees", "New York Yankees"), "10", "11"),
    ("WNBA", "Basketball", ("LV", "Aces", "Las Vegas Aces"), ("NY", "Liberty", "New York Liberty"), "88", "91"),
])]

# where the panel is and how big: (name, pos, size) as the panel stores them (size in px, or None)
PLACES = [
    ("corner", "r", None),                          # as it opens
    ("left", "l", None),
    ("smallest", "br", "min"),                      # the smallest the handle allows
    ("narrow-side", "r", "narrow"),                 # a little narrower, on a side
    ("bottom-strip", "bl", "strip"),                # wide and short, along the bottom
    ("big", {"x": 0.5, "y": 0.5}, "big"),           # made big: bigger text, two columns
]
SCREENS = [(1280, 720), (1920, 1080)]


# ---------------------------------------------------------------- the browser
def _bundle():
    js = bundle.tv_js()
    end = js.rindex("})();")
    # a way in for the test: the panel's state and drawing
    return js[:end] + "window.__gs = { GS, gsRender, gsPlace };\n" + js[end:]


def _serve(route):
    path = route.request.url[len(BASE):].split("?")[0]
    if path in ("/", "/index.html"):
        return route.fulfill(path=str(WEB / "index.html"), content_type="text/html")
    if path == "/static/tv.js":
        return route.fulfill(body=_bundle(), content_type="text/javascript")
    if path.startswith("/static/"):
        f = WEB / path[len("/static/"):]
        if f.is_file():
            return route.fulfill(path=str(f), content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream")
    if path.startswith("/api/"):
        return route.fulfill(body="{}", content_type="application/json")
    return route.fulfill(status=404, body="")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = None
        for kw in ({}, {"channel": "chrome"}):            # Playwright's Chromium, else an installed Chrome
            try:
                b = p.chromium.launch(**kw)
                break
            except Exception:  # noqa: BLE001
                continue
        if not b:
            pytest.skip("no browser for Playwright (python -m playwright install chromium)")
        yield b
        b.close()


@pytest.fixture(scope="module", params=SCREENS, ids=[f"{w}x{h}" for w, h in SCREENS])
def page(browser, request):
    w, h = request.param
    pg = browser.new_page(viewport={"width": w, "height": h})
    pg.route("**/*", _serve)
    pg.goto(BASE + "/")
    pg.wait_for_function("window.__gs")
    pg.evaluate("""() => {
        document.querySelectorAll('#home,#dvr,#guide,#od,.screen,#welcome').forEach((e) => e.classList.add('hidden'));
        document.getElementById('gstats').classList.remove('hidden');
    }""")
    pg.screen = (w, h)
    yield pg
    pg.close()


# what's wrong with the panel as drawn: text on more than one line, numbers cut off, anything off the panel
CHECK = """() => {
  const box = document.getElementById('gstats'), bad = [];
  const lines = (el) => {                  // how many lines the element's own text takes
    const tops = new Set();
    for (const n of el.childNodes) {
      if (n.nodeType !== 3 || !n.textContent.trim()) continue;
      const r = document.createRange(); r.selectNodeContents(n);
      for (const q of r.getClientRects()) if (q.width > 0) tops.add(Math.round(q.top / 4));
    }
    return tops.size;
  };
  const one = '.gs-head span, #gs-move, .gs-team b, .gs-team small, .gs-team em, .gs-dash, #gs-tabs .tab, '
            + '.gs-tbl th, .gs-tbl td, .gs-tbl td small, .gs-colh, .gs-gx b, .gs-gx span, #gs-keys, #gs-keys b, #gs-upd, .gs-empty';
  for (const el of box.querySelectorAll(one)) {
    if (!el.offsetParent) continue;
    if (lines(el) > 1) bad.push(`wraps: ${el.className || el.id || el.tagName} "${el.textContent.trim().slice(0, 40)}"`);
  }
  // numbers are never cut short (names may end in "…")
  for (const td of box.querySelectorAll('.gs-tbl td:not(:first-child), .gs-tbl th, .gs-team em, #gs-clock, #gs-keys, #gs-upd, #gs-tabs .tab'))
    if (td.offsetParent && td.scrollWidth > td.clientWidth + 1) bad.push(`cut off: "${td.textContent.trim()}"`);
  // the tabs fit on their one row, the key hints on theirs
  const tabs = document.getElementById('gs-tabs');
  if (tabs.scrollWidth > tabs.clientWidth + 1) bad.push(`tabs don't fit: ${tabs.scrollWidth} > ${tabs.clientWidth}`);
  const foot = box.querySelector('.gs-foot');
  if (foot.getBoundingClientRect().height > 2 * parseFloat(getComputedStyle(foot).fontSize) * 1.25)
    bad.push('the key hints take two lines');
  // nothing sideways off the panel or the panel off the screen
  const body = document.getElementById('gs-body');
  if (body.scrollWidth > body.clientWidth + 1) bad.push(`stats wider than the panel: ${body.scrollWidth} > ${body.clientWidth}`);
  // and, when there's more than fits, room to show some of it: at least two rows' worth
  const fs = parseFloat(getComputedStyle(body).fontSize);
  if (body.scrollHeight > body.clientHeight + 1 && body.clientHeight < 3.5 * fs) bad.push(`no room for the stats: ${body.clientHeight}px`);
  const r = box.getBoundingClientRect();
  if (r.left < 0 || r.top < 0 || r.right > innerWidth + 1 || r.bottom > innerHeight + 1)
    bad.push(`panel off the screen: ${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)}`);
  return bad;
}"""

SETUP = """([game, others, pos, size, tab]) => {
  const g = window.__gs, GS = g.GS, em = parseFloat(getComputedStyle(document.getElementById('tv')).fontSize) * 0.92;
  const sizes = { min: { w: 19 * em, h: 15 * em, sh: 0 }, narrow: { w: 20 * em, h: 0, sh: 0 },
                  strip: { w: innerWidth - 3.2 * em, h: Math.round(innerHeight * 0.36), sh: 0 },
                  big: { w: Math.min(innerWidth * 0.9, 62 * em), h: Math.round(innerHeight * 0.85), sh: 0 } };
  GS.data = game; GS.others = others; GS.sel = 0;
  GS.pos = pos; GS.size = size ? sizes[size] : null; GS.wide = false;
  GS.tab = 0; g.gsRender();                    // drawn once to settle the layout (one tab per team, or both)
  GS.tab = tab; g.gsRender();
  return document.querySelectorAll('#gs-tabs .tab').length;
}"""


def _cases():
    for sport, game in GAMES.items():
        for place in PLACES:
            yield pytest.param(sport, game, OTHERS, place, id=f"{sport}-{place[0]}")
    for n in (1, 6):                                         # nothing on this channel: just the scores
        for place in PLACES:
            yield pytest.param(f"scores{n}", None, OTHERS[:n], place, id=f"scores{n}-{place[0]}")


@pytest.mark.parametrize("sport,game,others,place", list(_cases()))
def test_game_stats_never_wraps(page, sport, game, others, place):
    name, pos, size = place
    n = page.evaluate(SETUP, [game, others, pos, size, 0])
    problems = {}
    for tab in range(max(n, 1)):
        page.evaluate(SETUP, [game, others, pos, size, tab])
        label = page.evaluate("() => (document.querySelector('#gs-tabs .tab.sel') || {}).textContent || ''")
        bad = page.evaluate(CHECK)
        if bad:
            problems[label] = bad
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            w, h = page.screen
            page.locator("#gstats").screenshot(path=str(Path(SHOTS) / f"{w}x{h}-{sport}-{name}-{tab}-{label or 'none'}.png"))
    assert not problems, json.dumps(problems, indent=1)


def test_bigger_panel_has_bigger_text_and_two_columns(page):
    """Made bigger, the text grows and both teams' players go side by side on one tab."""
    page.evaluate(SETUP, [GAMES["nba"], OTHERS, "r", None, 0])
    small = page.evaluate("() => parseFloat(getComputedStyle(document.getElementById('gstats')).fontSize)")
    assert page.evaluate("() => [...document.querySelectorAll('#gs-tabs .tab')].map((t) => t.textContent)")[:2] == ["OKC", "MIN"]
    page.evaluate(SETUP, [GAMES["nba"], OTHERS, {"x": 0.5, "y": 0.5}, "big", 0])
    big = page.evaluate("() => parseFloat(getComputedStyle(document.getElementById('gstats')).fontSize)")
    assert big > small * 1.2
    assert page.evaluate("() => document.getElementById('gstats').classList.contains('wide')")
    assert page.evaluate("() => document.querySelector('#gs-tabs .tab').textContent") == "Players"
    assert page.evaluate("() => [...document.querySelectorAll('.gs-colh')].map((e) => e.textContent)") == ["OKC", "MIN"]


# ---------------------------------------------------------------- phones
# a touch screen (pointer: coarse) upright and sideways: an iPhone and a small Android phone
PHONES = [(393, 852), (852, 393), (360, 740), (740, 360)]


@pytest.fixture(scope="module", params=PHONES, ids=[f"phone-{w}x{h}" for w, h in PHONES])
def phone(browser, request):
    w, h = request.param
    pg = browser.new_page(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True, device_scale_factor=2)
    pg.route("**/*", _serve)
    pg.goto(BASE + "/")
    pg.wait_for_function("window.__gs")
    assert pg.evaluate("() => document.documentElement.classList.contains('phone')")
    pg.evaluate("""() => {
        document.querySelectorAll('#home,#dvr,#guide,#od,.screen,#welcome').forEach((e) => e.classList.add('hidden'));
        document.getElementById('gstats').classList.remove('hidden');
        document.getElementById('tv').classList.add('gs-open');
    }""")
    pg.screen = (w, h)
    yield pg
    pg.close()


# upright: under the picture (which keeps its shape), clear of the Back button; sideways: a column on the
# right beside the picture, covering none of it, its stats ending above the Back button at its foot
WHERE = """() => {
  const r = document.getElementById('gstats').getBoundingClientRect(), v = document.getElementById('video').getBoundingClientRect();
  const body = document.getElementById('gs-body').getBoundingClientRect(), bad = [];
  if (innerHeight > innerWidth) {
    if (r.top < v.bottom - 1) bad.push(`over the picture: panel top ${r.top}, picture bottom ${v.bottom}`);
    if (Math.abs(v.height - v.width * 9 / 16) > 2) bad.push(`picture not 16:9: ${v.width}x${v.height}`);
    if (r.bottom > innerHeight - 60) bad.push(`reaches the Back button: bottom ${r.bottom} of ${innerHeight}`);
  } else {
    if (r.left < v.right - 1) bad.push(`over the picture: panel left ${r.left}, picture right ${v.right}`);
    if (r.width > innerWidth * 0.41) bad.push(`too wide sideways: ${r.width} of ${innerWidth}`);
    if (v.height < innerHeight - 1) bad.push(`picture not full height: ${v.height}`);
    if (body.bottom > innerHeight - 60) bad.push(`stats reach the Back button: bottom ${body.bottom} of ${innerHeight}`);
  }
  return bad;
}"""


@pytest.mark.parametrize("sport", list(GAMES) + ["scores6"])
def test_game_stats_on_a_phone(phone, sport):
    game, others = (None, OTHERS) if sport == "scores6" else (GAMES[sport], OTHERS)
    n = phone.evaluate(SETUP, [game, others, "r", None, 0])
    problems = {}
    for tab in range(max(n, 1)):
        phone.evaluate(SETUP, [game, others, "r", None, tab])
        label = phone.evaluate("() => (document.querySelector('#gs-tabs .tab.sel') || {}).textContent || ''")
        bad = phone.evaluate(CHECK) + phone.evaluate(WHERE)
        if bad:
            problems[label] = bad
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            w, h = phone.screen
            phone.screenshot(path=str(Path(SHOTS) / f"phone-{w}x{h}-{sport}-{tab}-{label or 'none'}.png"))
    assert not problems, json.dumps(problems, indent=1)


def test_phone_panel_is_only_as_tall_as_the_tab(phone):
    """Upright, a short tab makes a short panel; the scores list a longer one (up to the room there is).
    (Sideways it's a column the height of the screen, beside the picture.)"""
    if phone.screen[0] > phone.screen[1]:
        return
    phone.evaluate(SETUP, [GAMES["soccer"], OTHERS, "r", None, 0])           # Team: three rows
    short = phone.evaluate("() => document.getElementById('gstats').getBoundingClientRect().height")
    phone.evaluate(SETUP, [GAMES["soccer"], OTHERS, "r", None, 2])           # Scores: six games
    tall = phone.evaluate("() => document.getElementById('gstats').getBoundingClientRect().height")
    assert short < tall
    assert short < phone.screen[1] * 0.4
