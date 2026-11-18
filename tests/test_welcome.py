"""First-time setup (/welcome): a new install's first screen goes there once; finishing or skipping ends it,
and installs set up before the wizard existed never see it."""
import json

from fastapi.testclient import TestClient

from tunerbox import api, config


def _client():
    return TestClient(api.app, follow_redirects=False)


def test_new_install_opens_the_wizard_first():
    assert config.get("welcome_done") is False
    c = _client()
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/welcome"
    r = c.get("/?on")                       # the screen that was opened is where the wizard ends
    assert r.headers["location"] == "/welcome?next=%2F%3Fon"
    page = c.get("/welcome")
    assert page.status_code == 200 and "/static/welcome.js?v=" in page.text
    assert c.get("/setup").status_code == 200       # Setup itself is never redirected


def test_finishing_or_skipping_ends_it_for_good():
    c = _client()
    assert c.post("/api/settings", json={"welcome_done": True}).json()["welcome_done"] is True
    assert c.get("/").status_code == 200
    config._cache = None                    # still done after a restart
    assert config.get("welcome_done") is True and c.get("/").status_code == 200


def test_installs_from_before_the_wizard_skip_it():
    config.SETTINGS_FILE.write_text(json.dumps({"dvr_enabled": False}), encoding="utf-8")
    config._cache = None
    assert config.get("welcome_done") is True
    assert _client().get("/").status_code == 200


def test_a_wizard_left_half_way_comes_back():
    config.load()                           # first start writes welcome_done: false
    config._cache = None
    assert config.get("welcome_done") is False
    assert _client().get("/").status_code == 303


def test_wizard_steps_use_setup_settings():
    c = _client()
    s = c.post("/api/settings", json={"dvr_enabled": False, "gracenote": {"enabled": False}}).json()
    assert s["dvr_enabled"] is False and s["gracenote"]["enabled"] is False


def test_home_screen_icon_at_the_addresses_ios_tries():
    c = _client()
    for name in ("apple-touch-icon.png", "apple-touch-icon-precomposed.png", "favicon.ico"):
        r = c.get("/" + name)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert c.get("/setup").status_code == 200 and c.get("/discover.json").status_code == 200
    config.update({"welcome_done": True})
    assert 'rel="apple-touch-icon" sizes="180x180"' in c.get("/").text
