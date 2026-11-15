"""HTTPS on a second port: certificates, the server itself, and that http answers stay exactly as before."""
import http.server
import socket
import ssl
import threading
import time

import httpx
import pytest
from cryptography import x509
from fastapi.testclient import TestClient

from tunerbox import api, config, https, remote


@pytest.fixture(autouse=True)
def fresh_https_state():
    https._state.update(running=False, error="", port=0, http_port=0)
    yield
    https._state.update(running=False, error="", port=0, http_port=0)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _san(path):
    crt = x509.load_pem_x509_certificate(path.read_bytes())
    san = crt.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return crt, set(san.get_values_for_type(x509.DNSName)), {str(i) for i in san.get_values_for_type(x509.IPAddress)}


def test_certificate_is_made_once_and_signed_by_tunerbox():
    crt_path, key_path = https.ensure()
    ca = x509.load_pem_x509_certificate(https.ca_file().read_bytes())
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    crt, dns, ips = _san(crt_path)
    crt.verify_directly_issued_by(ca)
    assert "localhost" in dns and "127.0.0.1" in ips
    assert (crt.not_valid_after_utc - crt.not_valid_before_utc).days <= 398      # phones refuse longer
    assert https.ensure() == (crt_path, key_path)
    assert _san(crt_path)[0].serial_number == crt.serial_number                   # kept, not remade


def test_new_address_remakes_the_certificate_but_keeps_the_authority():
    https.ensure()
    ca_before = https.ca_file().read_bytes()
    config.update({"https_hosts": ["192.168.50.7", "tunerbox.lan"]})
    crt_path, _ = https.ensure()
    _, dns, ips = _san(crt_path)
    assert "192.168.50.7" in ips and "tunerbox.lan" in dns
    assert https.ca_file().read_bytes() == ca_before        # devices that trust it keep working


def test_settings_are_checked():
    out = config.clean_changes({"https_hosts": [" 192.168.1.9 ", "bad host!", "Box.LAN"], "https_port": 8643}, True)
    assert out["https_hosts"] == ["192.168.1.9", "box.lan"]
    with pytest.raises(ValueError):
        config.clean_changes({"https_port": 70000}, True)
    with pytest.raises(PermissionError):                    # PC only
        config.clean_changes({"https_enabled": True}, False)


def test_off_by_default_starts_nothing():
    assert config.get("https_enabled") is False and config.get("https_port") == 8643
    assert https.start(api.app, "127.0.0.1") is None
    assert https.status()["running"] is False


def test_port_in_use_is_reported_and_never_raises():
    with socket.socket() as busy:
        busy.bind(("0.0.0.0", 0))
        busy.listen()
        config.update({"https_enabled": True, "https_port": busy.getsockname()[1]})
        assert https.start(api.app, "0.0.0.0") is None
        assert "in use" in https.status()["error"]


def test_same_port_as_http_is_refused():
    config.update({"https_enabled": True, "https_port": config.get("port")})
    assert https.start(api.app, "127.0.0.1") is None
    assert https.status()["error"]


def test_https_server_answers_and_hands_out_http_addresses():
    port = _free_port()
    config.update({"https_enabled": True, "https_port": port})
    server = https.start(api.app, "127.0.0.1")
    assert server is not None
    try:
        ctx = ssl.create_default_context(cafile=str(https.ca_file()))       # trusting only TunerBox's authority
        for _ in range(50):
            try:
                r = httpx.get(f"https://127.0.0.1:{port}/api/me", verify=ctx, timeout=3)
                break
            except httpx.ConnectError:
                time.sleep(0.1)
        assert r.status_code == 200
        assert r.json()["http_base"] == f"http://127.0.0.1:{config.get('port')}"
        d = httpx.get(f"https://127.0.0.1:{port}/discover.json", verify=ctx, timeout=3).json()
        assert d["BaseURL"].startswith("http://127.0.0.1:")                 # Plex is never given https
        assert https.status()["running"]
        assert server.config.lifespan == "off"      # startup jobs run once, on the http server
    finally:
        server.should_exit = True
        time.sleep(0.5)


def test_http_answers_are_unchanged():
    c = TestClient(api.app)
    assert c.get("/discover.json").json()["BaseURL"] == "http://testserver"
    assert c.get("/api/me").json()["http_base"] == "http://testserver"
    # opened over HTTPS: the same host, on the http port
    s = TestClient(api.app, base_url="https://box.lan:8643")
    assert s.get("/discover.json").json()["BaseURL"] == f"http://box.lan:{config.get('port')}"
    assert s.get("/playlist.m3u").status_code == 200


def test_pairing_link_is_http_even_from_https(monkeypatch):
    monkeypatch.setattr(remote, "is_pc", lambda request: True)
    s = TestClient(api.app, base_url="https://box.lan:8643")
    link = s.post("/api/pair/start").json()["url"]
    assert link.startswith("http://") and f":{config.get('port')}/pair#" in link


def test_ca_download_is_only_the_public_certificate():
    r = TestClient(api.app).get("/tunerbox-ca.crt")
    assert r.status_code == 200 and b"BEGIN CERTIFICATE" in r.content and b"PRIVATE" not in r.content


@pytest.fixture
def pictures():
    """A little http server with a picture and a web page."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body, kind = (b"\x89PNG\r\n\x1a\nfake", "image/png") if self.path == "/logo.png" else (b"<html>", "text/html")
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_picture_pass_through(pictures):
    c = TestClient(api.app)
    r = c.get("/api/img", params={"u": f"{pictures}/logo.png"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(b"\x89PNG")
    assert c.get("/api/img", params={"u": f"{pictures}/page"}).status_code == 404       # not a picture
    assert c.get("/api/img", params={"u": "https://example.com/x.png"}).status_code == 400
    assert c.get("/api/img", params={"u": "file:///etc/passwd"}).status_code == 400


def test_real_http_port_is_used_for_addresses(monkeypatch):
    """--port can differ from settings: addresses handed out over HTTPS use the port http really runs on."""
    config.update({"https_enabled": True, "https_port": 8655})
    assert https.start(api.app, "127.0.0.1", 8655) is None            # same as http: refused, nothing started
    assert "same as the http port" in https.status()["error"] and https.http_port() == 8655
    s = TestClient(api.app, base_url="https://box.lan:8643")
    assert s.get("/api/me").json()["http_base"] == "http://box.lan:8655"
    https._state["http_port"] = 0
    assert https.http_port() == config.get("port")                      # not started: the setting
