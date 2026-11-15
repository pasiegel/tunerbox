"""Xtream account details (expiry date, status, trial) shown on the playlist's card."""
import time

from fastapi.testclient import TestClient

from tunerbox import api, connections, db, sources


def _src():
    sid = db.run("INSERT INTO sources(name, type, url, username, password) VALUES ('P', 'xtream', 'http://p:80', 'u', 'secret')")
    return db.one("SELECT * FROM sources WHERE id=?", (sid,))


def test_account_details_are_read_from_the_login_answer():
    s = _src()
    acc = sources.save_account(s, {"user_info": {"auth": 1, "status": "Active", "exp_date": "1801126451", "is_trial": "0",
                                                 "created_at": "1793177651", "max_connections": "2"}})
    assert acc["exp"] == 1801126451 and acc["status"] == "Active" and acc["trial"] is False and acc["created"] == 1793177651
    assert sources.account(s)["exp"] == 1801126451
    assert connections.auto_limit(s) == 2                     # the connection limit still comes from the same answer


def test_no_expiry_trial_and_odd_values():
    s = _src()
    for raw in (None, "", "null", "0"):
        assert sources.save_account(s, {"user_info": {"exp_date": raw, "status": "Active"}})["exp"] == 0     # never
    acc = sources.save_account(s, {"user_info": {"exp_date": 1801126451.0, "is_trial": "1", "status": "Expired"}})
    assert acc["exp"] == 1801126451 and acc["trial"] and acc["status"] == "Expired"
    assert sources.save_account(s, {"nope": 1}) is None and sources.save_account(s, ["x"]) is None


def test_scan_keeps_the_account(monkeypatch):
    s = _src()
    answers = {None: {"user_info": {"auth": 1, "exp_date": "1801126451", "status": "Active", "max_connections": "1"}},
               "get_live_categories": [], "get_live_streams": []}
    monkeypatch.setattr(sources, "xtream_api", lambda src, action=None, **k: answers[action])
    sources.fetch_xtream(s)
    assert sources.account(s)["exp"] == 1801126451


def test_playlist_list_shows_it_without_the_password(monkeypatch):
    s = _src()
    sources.save_account(s, {"user_info": {"exp_date": "1801126451", "status": "Active"}})
    started = []
    monkeypatch.setattr(sources, "refresh_account_later", started.append)
    out = TestClient(api.app).get("/api/sources").json()[0]
    assert out["account"]["exp"] == 1801126451 and out["password"] != "secret"
    assert started == []                                          # just read: no new check
    db.set_meta(f"xtream_account_{s['id']}", '{"exp": 1, "checked": %d}' % (time.time() - sources.ACCOUNT_TTL - 5))
    TestClient(api.app).get("/api/sources")
    assert [x["id"] for x in started] == [s["id"]] and started[0]["password"] == "secret"   # the check needs the login


def test_failed_check_keeps_what_was_known(monkeypatch):
    s = _src()
    sources.save_account(s, {"user_info": {"exp_date": "1801126451"}})

    def down(*a, **k):
        raise OSError("provider down")
    monkeypatch.setattr(sources, "xtream_api", down)
    assert sources.refresh_account(s) is None and sources.account(s)["exp"] == 1801126451
