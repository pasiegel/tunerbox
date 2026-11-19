import logging

from fastapi.testclient import TestClient

from tunerbox import api, config, logview, sources


def _write(*lines, mode="a"):
    with open(config.DATA / logview.LOG_NAME, mode, encoding="utf-8") as f:
        f.writelines(x + "\n" for x in lines)


def test_logins_passwords_and_keys_are_hidden():
    sources.add_source("IPTV", "xtream", "http://prov.example:8080", "kevin99", "s3cretPass")
    logview.forget()
    line = "Stream start for tv-1: http://prov.example:8080/movie/kevin99/s3cretPass/123.mkv"
    assert logview.redact(line) == "Stream start for tv-1: http://prov.example:8080/movie/***/***/123.mkv"
    assert logview.redact("GET /player_api.php?username=a&password=b&action=x") == \
        "GET /player_api.php?username=***&password=***&action=x"
    assert logview.redact("tuned to tv") == "tuned to tv"                 # short words are left alone


def test_lines_are_written_with_the_secrets_already_hidden():
    sources.add_source("IPTV", "xtream", "http://prov.example", "kevin99", "s3cretPass")
    logview.forget()
    rec = logging.LogRecord("tunerbox.x", logging.INFO, "", 0, "url %s", ("http://h/live/kevin99/s3cretPass/1.ts",), None)
    out = logview.Formatter("%(levelname)s %(message)s").format(rec)
    assert out == "INFO url http://h/live/***/***/1.ts"


def test_the_tail_then_only_whats_new_then_a_fresh_file():
    _write("2026-11-19 10:00:00,000 tunerbox.api INFO one", "2026-11-19 10:00:01,000 tunerbox.api INFO two", mode="w")
    r = logview.tail()
    assert [x.split()[-1] for x in r["lines"]] == ["one", "two"] and not r["restarted"]
    assert logview.tail(r["pos"])["lines"] == []
    with open(config.DATA / logview.LOG_NAME, "a", encoding="utf-8") as f:
        f.write("2026-11-19 10:00:02,000 tunerbox.api INFO three\n2026-11-19 10:00:03,000 tunerbox.api INFO hal")
    r2 = logview.tail(r["pos"])
    assert [x.split()[-1] for x in r2["lines"]] == ["three"]               # a line still being written waits
    _write("x", mode="w")                                                  # the log was started over
    r3 = logview.tail(r2["pos"])
    assert r3["restarted"] and r3["lines"] == ["x"]


def test_the_api_follows_the_log_and_respects_lock_setup(monkeypatch):
    _write("2026-11-19 10:00:00,000 tunerbox.api INFO hello", mode="w")
    c = TestClient(api.app)
    assert c.get("/api/log").json()["lines"][-1].endswith("hello")
    config.update({"require_pairing": True})
    assert c.get("/api/log").status_code == 403                            # another device on the network


def test_playing_a_movie_is_logged_by_name(caplog):
    it = {"source_id": -1, "title": "Heat", "sub": "4K"}
    with caplog.at_level(logging.INFO, logger="tunerbox.api"):
        api._log_play(it, 3725, "tv-livingroom", "streamed as HLS")
    assert "On Demand: playing Heat · 4K (My Library) from 1:02:05 for tv-livingroom, streamed as HLS" in caplog.text


def test_the_nas_clients_routine_lines_are_not_shown_but_its_problems_are():
    _write("2026-11-19 11:15:57,001 smbprotocol.open INFO Session: guest, Tree Connect: \\\\nas\\Media - receiving SMB2 Close Response",
           "2026-11-19 11:15:57,002 smbprotocol.connection WARNING Connection to nas lost",
           "2026-11-19 11:15:58,000 tunerbox.api INFO On Demand: playing Heat (My Library) for tv, streamed as HLS", mode="w")
    lines = logview.tail()["lines"]
    assert len(lines) == 2 and "Connection to nas lost" in lines[0] and "playing Heat" in lines[1]


def test_the_first_look_goes_further_back_past_the_nas_clients_lines():
    smb = "2026-11-19 11:15:57,001 smbprotocol.open INFO Session: guest - receiving SMB2 Close Response"
    _write("2026-11-19 11:00:00,000 tunerbox.library INFO My Library: NAS Movies: 5 movies", mode="w")
    _write(*[smb] * 6000)                                    # ~600 KB of them: more than the first look reads
    lines = logview.tail()["lines"]
    assert len(lines) == 1 and lines[0].endswith("NAS Movies: 5 movies")


def test_a_device_name_cant_add_lines_to_the_log():
    from tunerbox import connections
    connections.name_client("roku-1", "Den\n2026-11-19 12:00:00,000 tunerbox.api INFO fake")
    assert "\n" not in connections.client_name("roku-1") and len(connections.client_name("roku-1")) <= 60


def test_a_log_being_started_over_right_then_is_read_next_time(monkeypatch):
    _write("2026-11-19 10:00:00,000 tunerbox.api INFO one", mode="w")
    def busy(path):
        raise PermissionError("in use")
    with monkeypatch.context() as m:
        m.setattr(logview, "_open_shared", busy)
        assert logview.tail() == {"pos": -1, "lines": [], "restarted": False}     # still the first look next time
        assert logview.tail(12)["pos"] == 12
    assert [x.split()[-1] for x in logview.tail()["lines"]] == ["one"]
