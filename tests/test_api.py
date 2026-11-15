"""HTTP checks. TestClient requests come from host "testclient", i.e. another device on the network
(not this PC), unless _as_pc() is used."""
import pytest
from fastapi.testclient import TestClient

from tunerbox import api, config, db, remote
from tunerbox.bundle import FOOTER, HEADER


@pytest.fixture
def client():
    return TestClient(api.app)        # not as a context manager: no startup jobs or network refreshes


@pytest.fixture
def as_pc(monkeypatch):
    monkeypatch.setattr(remote, "is_pc", lambda request: True)


def test_settings_validation(client):
    assert client.post("/api/settings", json={"nope": 1}).status_code == 400
    assert client.post("/api/settings", json={"hide_unmapped": "x"}).status_code == 400
    r = client.post("/api/settings", json={"hide_unmapped": False, "match_threshold": "90"})
    assert r.status_code == 200 and r.json()["match_threshold"] == 90


def test_other_devices_cannot_change_programs_or_folders(client):
    r = client.post("/api/settings", json={"ffmpeg": r"\\evil\share\x.exe"})
    assert r.status_code == 403
    assert config.get("ffmpeg") == "ffmpeg"


def test_pc_can_change_ffmpeg(client, as_pc):
    assert client.post("/api/settings", json={"ffmpeg": "/opt/ffmpeg"}).status_code == 200
    assert config.get("ffmpeg") == "/opt/ffmpeg"


def test_settings_never_expose_devices(client):
    config.update({"devices": [{"id": "a", "hash": "secret"}]})
    assert "devices" not in client.get("/api/settings").json()


def test_recording_files_need_pairing_with_the_lock_on(client):
    config.update({"require_pairing": True})
    rid = db.run("INSERT INTO recordings(number, title, start, stop, status) VALUES (8, 'S', 1, 2, 'done')")
    assert client.delete(f"/api/recordings/{rid}?delete=1").status_code == 401
    assert db.one("SELECT id FROM recordings WHERE id=?", (rid,))


def test_direct_url_needs_pairing_with_the_lock_on(client):
    config.update({"require_pairing": True})
    assert client.get("/api/direct/8").status_code == 401


def test_hdhomerun_discover(client):
    d = client.get("/discover.json").json()
    assert d["DeviceID"] == config.get("hdhr_device_id") and config.valid_device_id(d["DeviceID"])
    assert d["TunerCount"] == 4


def test_tuner_count_follows_xtream_limits(client):
    a = db.run("INSERT INTO sources(name, type, url) VALUES ('a', 'xtream', 'http://a')")
    b = db.run("INSERT INTO sources(name, type, url) VALUES ('b', 'xtream', 'http://b')")
    db.set_meta(f"xtream_max_{a}", 2)
    db.set_meta(f"xtream_max_{b}", 1)
    assert client.get("/discover.json").json()["TunerCount"] == 3
    db.run("INSERT INTO sources(name, type, url) VALUES ('c', 'm3u', 'http://c')")     # unknown limit
    assert client.get("/discover.json").json()["TunerCount"] == 4
    config.update({"tuner_count": 6})
    assert client.get("/discover.json").json()["TunerCount"] == 6


def test_tv_script_is_joined_from_parts(client):
    r = client.get("/static/tv.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.text.startswith(HEADER) and r.text.endswith(FOOTER)
    assert "function powerOn" in r.text and r.headers["cache-control"] == "no-cache"


def test_page_stamps_tv_script_with_parts_version(client):
    from tunerbox import bundle
    assert f'/static/tv.js?v={bundle.version()}"' in client.get("/").text


def test_network_name_is_for_tuner_apps_not_home_screen_apps():
    from fastapi.testclient import TestClient
    from tunerbox import api, config
    c = TestClient(api.app)
    assert c.get("/discover.json").json()["FriendlyName"] == "TunerBox"
    assert c.get("/static/manifest.webmanifest").json()["name"] == "TunerBox"
    config.update(config.clean_changes({"box_name": "  TunerBox  -  Private "}, from_pc=False))
    assert config.box_name() == "TunerBox - Private"
    assert c.get("/discover.json").json()["FriendlyName"] == "TunerBox - Private"
    assert c.get("/static/manifest.webmanifest").json()["name"] == "TunerBox"          # home-screen apps keep the name
    assert c.get("/static/remote.webmanifest").json()["name"] == "TunerBox Remote"
    assert "<title>TunerBox</title>" in c.get("/").text
    assert "<small>Setup · TunerBox - Private</small>" in c.get("/setup").text
    config.update(config.clean_changes({"box_name": "   "}, from_pc=False))          # blank goes back to the default
    assert config.box_name() == "TunerBox"
    assert config.clean_changes({"box_name": "x" * 99}, from_pc=False)["box_name"] == "x" * 40


def test_show_pictures_come_from_the_guide_shrunk(client, monkeypatch, tmp_path):
    from tunerbox import guide, vod
    monkeypatch.setattr(guide, "channel", lambda n: {"number": n, "epg": "gn:7"} if n == 7 else None)
    db.run("INSERT INTO programs(channel, start, stop, title, image) VALUES ('gn:7', 1000, 2800, 'News', ?)",
           ("https://img.example/p1.jpg",))
    db.run("INSERT INTO programs(channel, start, stop, title, image) VALUES ('gn:7', 2800, 4600, 'Late', '')")
    small = tmp_path / "s.jpg"
    small.write_bytes(b"\xff\xd8 tiny")
    asked = []
    monkeypatch.setattr(vod, "small_image", lambda url, w: asked.append((url, w)) or small)
    r = client.get("/api/art/7/1500")                       # any time during the show
    assert r.status_code == 200 and r.content == b"\xff\xd8 tiny"
    assert asked == [("https://img.example/p1.jpg", api.ART_W)]
    assert client.get("/api/art/7/3000").status_code == 404     # no picture
    assert client.get("/api/art/9/1500").status_code == 404     # no such channel
    monkeypatch.setattr(vod, "small_image", lambda url, w: None)
    r = client.get("/api/art/7/1500", follow_redirects=False)  # can't shrink it: the original
    assert r.status_code == 302 and r.headers["location"] == "https://img.example/p1.jpg"


def test_nas_server_home_network_is_the_pc_until_locked(monkeypatch, tmp_path):
    """Docker on a NAS (TUNERBOX_TRUST_LAN): no browser is ever "this PC", so the home network counts as
    the PC until the lock is on: pairing, PC-only settings and folders on the server. Exporting logins
    and the lock itself need a paired device. The pairing link uses the address the browser used."""
    from fastapi.testclient import TestClient
    from tunerbox import jobs
    monkeypatch.setattr(jobs, "library_scan", lambda *a, **k: True)
    lan = TestClient(api.app, client=("192.168.10.50", 5000), base_url="http://192.168.10.9:8642")
    folder = {"location": str(tmp_path), "kind": "movie"}
    assert lan.post("/api/pair/start").status_code == 403           # a desktop install: only the PC itself
    assert lan.post("/api/settings", json={"ffmpeg": "/usr/bin/ffmpeg"}).status_code == 403
    assert lan.post("/api/library/browse", json=folder).status_code == 403
    monkeypatch.setenv("TUNERBOX_TRUST_LAN", "1")
    me = lan.get("/api/me").json()
    assert me["manage"] and me["admin"] and me["server"] and not me["pc"]
    assert lan.post("/api/settings", json={"ffmpeg": "/usr/bin/ffmpeg"}).status_code == 200   # settings from the web UI
    assert lan.post("/api/settings", json={"https_port": 8647}).status_code == 200
    assert lan.post("/api/library/browse", json=folder).json()["ok"]                     # folders on the server
    assert lan.get("/api/sources/export").status_code == 403                            # logins stay put
    assert lan.post("/api/settings", json={"require_pairing": True}).status_code == 403  # would lock itself out
    r = lan.post("/api/pair/start")
    assert r.status_code == 200 and r.json()["address"] == "http://192.168.10.9:8642/pair"
    phone = TestClient(api.app, client=("192.168.10.77", 5000), base_url="http://192.168.10.9:8642")
    assert phone.post("/api/pair/complete", json={"code": r.json()["code"], "name": "Pixel"}).status_code == 200
    assert phone.post("/api/settings", json={"require_pairing": True}).status_code == 200   # paired: may lock
    me = lan.get("/api/me").json()
    assert not me["manage"] and not me["admin"]                     # locked: the home network is just a viewer
    assert lan.post("/api/pair/start").status_code == 403
    assert lan.post("/api/settings", json={"ffmpeg": "/bin/sh"}).status_code == 403
    assert lan.post("/api/library/browse", json=folder).status_code == 403
    assert phone.post("/api/pair/start").status_code == 200         # the paired phone still can
    assert phone.post("/api/settings", json={"ffmpeg": "ffmpeg"}).status_code == 200
    assert phone.post("/api/library/browse", json=folder).json()["ok"]
    assert phone.post("/api/settings", json={"require_pairing": False}).status_code == 200
    outside = TestClient(api.app, client=("8.8.8.8", 5000))
    assert not outside.get("/api/me").json()["manage"]              # only private addresses count
    assert outside.post("/api/settings", json={"ffmpeg": "/bin/sh"}).status_code == 403


def test_other_websites_cant_change_things(monkeypatch):
    """Cross-site requests (another site's page making your browser POST here) are refused."""
    from tunerbox import jobs
    monkeypatch.setattr(jobs, "scan", lambda *a, **k: True)
    c = TestClient(api.app, base_url="http://192.168.10.9:8642")
    assert c.post("/api/jobs/scan", headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.post("/api/jobs/scan", headers={"Origin": "null"}).status_code == 403
    assert c.post("/api/jobs/scan", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/api/jobs/scan", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403   # another port on the NAS
    assert c.post("/api/jobs/scan", headers={"Origin": "http://192.168.10.9:8642"}).status_code == 200
    assert c.post("/api/jobs/scan", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert c.post("/api/jobs/scan").status_code == 200              # apps and scripts send neither
    assert c.get("/api/jobs", headers={"Origin": "http://evil.example"}).status_code == 200   # reading is GET: not a change


def test_only_answers_to_local_names(monkeypatch):
    """DNS rebinding: a site pointing its own name at TunerBox gets nothing."""
    def get(host):
        return TestClient(api.app, base_url=f"http://{host}").get("/api/jobs").status_code
    for ok in ("192.168.10.9:8642", "127.0.0.1:8642", "[::1]:8642", "nas:8642", "nas.local", "tunerbox.lan:8642", "localhost"):
        assert get(ok) == 200, ok
    for bad in ("evil.example", "rebind.evil.example:8642", "192.168.10.9.nip.io:8642"):
        assert get(bad) == 403, bad
    config.update({"allowed_hosts": ["tv.example.com", "*.tail1234.ts.net"]})
    assert get("tv.example.com:8642") == 200 and get("nas.tail1234.ts.net") == 200 and get("other.ts.net") == 403
    monkeypatch.setenv("TUNERBOX_ALLOWED_HOSTS", "box.home.example")
    assert get("box.home.example") == 200


def test_files_on_disk_need_the_pc_or_a_paired_phone(monkeypatch):
    """Playlist files and folders on the TunerBox computer: a phone on the Wi-Fi can't point TunerBox at them."""
    from tunerbox import sources
    monkeypatch.setattr(sources, "add_source", lambda *a, **k: 1)
    c = TestClient(api.app)
    r = c.post("/api/sources", json={"type": "m3u", "url": "/data/settings.json"})
    assert r.status_code == 403 and "web address" in r.json()["detail"]
    assert c.post("/api/sources", json={"type": "m3u", "url": "http://host/list.m3u"}).status_code == 200
    assert c.post("/api/sources/import", json={"kind": "tunerbox-playlists", "playlists": [
        {"name": "x", "type": "m3u", "url": r"C:\\Users\\me\\list.m3u"}]}).status_code == 403
    config.update({"require_pairing": True})
    assert c.get("/api/media/folder?path=/etc").status_code == 401             # probing paths: locked too
    monkeypatch.setattr(remote, "is_pc", lambda request: True)
    assert c.post("/api/sources", json={"type": "m3u", "url": "/srv/list.m3u"}).status_code == 200


def test_ffmpeg_only_takes_stream_protocols():
    from tunerbox import streaming
    args = streaming.input_args("http://host/live/1.ts")
    assert args[args.index("-protocol_whitelist") + 1] == "http,https,tcp,tls,crypto"
    assert "udp" in streaming.protocols_for("udp://239.0.0.1:1234")
    assert streaming.protocols_for("/srv/video.mkv") == "file,crypto"
    assert streaming.protocols_for("concat:/etc/passwd|x") == "file,crypto"     # can't reach concat:
