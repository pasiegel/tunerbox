"""HTTPS on a second port (8643 by default), next to plain http on 8642, which stays exactly as it was.

Browsers only offer "Install app" for pages served over HTTPS (localhost aside). TunerBox makes its own
little certificate authority, once, and from it a certificate for this computer's names and network
addresses. A phone or PC trusts that authority once (Setup › Settings › HTTPS has the download), then
https://<this PC>:8643 opens without warnings and can be installed as an app.

Everything else - Plex, Emby, Jellyfin, Channels, VLC, the Roku app, /playlist.m3u - keeps using http
on 8642. Off unless switched on (Setup › Settings › HTTPS); changes need a restart of TunerBox.
"""
import datetime
import ipaddress
import logging
import socket
import threading

from . import config

log = logging.getLogger("tunerbox.https")
CA_NAME = "TunerBox local certificate authority"
SERVER_DAYS = 397           # Apple and Chrome refuse server certificates valid for longer than 398 days
RENEW_DAYS = 30             # make a new one when this close to running out
_state = {"running": False, "error": "", "port": 0, "http_port": 0}


def folder():
    return config.DATA / "https"


def ca_file():
    return folder() / "tunerbox-ca.crt"


def _names():
    """DNS names and IP addresses the certificate covers: this computer's, plus any listed in settings
    (e.g. a Docker host's address, which the container can't see)."""
    dns, ips = {"localhost"}, {"127.0.0.1"}
    host = socket.gethostname()
    if host:
        dns.update({host.lower(), f"{host.lower()}.local"})
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:                    # the address used to reach the network (finds it even with odd hostname setups)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    for extra in config.get("https_hosts") or []:
        extra = str(extra).strip().lower()
        if not extra:
            continue
        try:
            ips.add(str(ipaddress.ip_address(extra)))
        except ValueError:
            dns.add(extra)
    return sorted(dns), sorted(ips)


def _load_cert(path):
    from cryptography import x509
    try:
        return x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError):
        return None


def _new_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1())


def _save_key(key, path):
    from cryptography.hazmat.primitives import serialization
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_key(path):
    from cryptography.hazmat.primitives import serialization
    try:
        return serialization.load_pem_private_key(path.read_bytes(), None)
    except (OSError, ValueError, TypeError):
        return None


def _ca():
    """The authority's key and certificate, made on first use (10 years)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID
    key_path, crt_path = folder() / "ca.key", ca_file()
    key, crt = _load_key(key_path), _load_cert(crt_path)
    if key and crt:
        return key, crt
    folder().mkdir(parents=True, exist_ok=True)
    key = _new_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{CA_NAME} ({socket.gethostname() or 'TunerBox'})"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TunerBox")])
    now = datetime.datetime.now(datetime.timezone.utc)
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    crt = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=3650))
           .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
           .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                        key_encipherment=False, data_encipherment=False, key_agreement=False,
                                        encipher_only=False, decipher_only=False), critical=True)
           .add_extension(ski, critical=False)
           .sign(key, hashes.SHA256()))
    _save_key(key, key_path)
    crt_path.write_bytes(crt.public_bytes(serialization.Encoding.PEM))
    log.info("HTTPS: made a new certificate authority")
    return key, crt


def _covers(crt, dns, ips):
    from cryptography import x509
    try:
        san = crt.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    have_dns = set(san.get_values_for_type(x509.DNSName))
    have_ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    return set(dns) <= have_dns and set(ips) <= have_ips


def ensure():
    """Paths of this computer's certificate and key, made or renewed when needed (a new address, or
    close to running out). The authority itself is kept, so phones that trust it keep working."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    ca_key, ca_crt = _ca()
    dns, ips = _names()
    key_path, crt_path = folder() / "server.key", folder() / "server.crt"
    crt, key = _load_cert(crt_path), _load_key(key_path)
    now = datetime.datetime.now(datetime.timezone.utc)
    if (crt and key and crt.issuer == ca_crt.subject and _covers(crt, dns, ips)
            and crt.not_valid_after_utc - now > datetime.timedelta(days=RENEW_DAYS)):
        return crt_path, key_path
    key = _new_key()
    alt = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    crt = (x509.CertificateBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns[1] if len(dns) > 1 else "TunerBox")]))
           .issuer_name(ca_crt.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=SERVER_DAYS))
           .add_extension(x509.SubjectAlternativeName(alt), critical=False)
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                        data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                                        encipher_only=False, decipher_only=False), critical=True)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
           .sign(ca_key, hashes.SHA256()))
    _save_key(key, key_path)
    # the chain (this certificate, then the authority) so clients that only trust the authority can verify
    crt_path.write_bytes(crt.public_bytes(serialization.Encoding.PEM) + ca_crt.public_bytes(serialization.Encoding.PEM))
    log.info("HTTPS: new certificate for %s", ", ".join(dns + ips))
    return crt_path, key_path


def _port_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host if host not in ("", "0.0.0.0") else "0.0.0.0", port))
            return True
        except OSError:
            return False


def http_port():
    """The port plain http really runs on (--port can differ from settings)."""
    return _state["http_port"] or int(config.get("port") or 8642)


def start(app, host, plain_port=None):
    """Start the HTTPS server in the background, if switched on. Never stops plain http from starting:
    any problem (port in use, certificate trouble) is logged and shown in Setup."""
    _state.update(running=False, error="", port=0, http_port=int(plain_port or 0))
    if not config.get("https_enabled"):
        return None
    port = int(config.get("https_port") or 8643)
    _state["port"] = port
    if port == http_port():
        _state["error"] = "The HTTPS port is the same as the http port"
    elif not _port_free(host, port):
        _state["error"] = f"Port {port} is already in use by another program"
    if _state["error"]:
        log.warning("HTTPS not started: %s", _state["error"])
        return None
    try:
        crt, key = ensure()
    except Exception as e:  # noqa: BLE001 - http must still start
        log.exception("HTTPS: certificate")
        _state["error"] = f"Couldn't make the certificate: {e}"
        return None
    import uvicorn
    # lifespan off: the http server already runs the startup jobs (guide refresh, DVR scheduler, ...);
    # a second run would do everything twice
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, ssl_certfile=str(crt), ssl_keyfile=str(key),
                                           lifespan="off", log_level="warning", log_config=None))

    def run():
        _state["running"] = True
        try:
            server.run()
        except (SystemExit, Exception) as e:  # noqa: BLE001 - uvicorn exits on a bind error
            _state["error"] = f"HTTPS stopped: {e}"
            log.warning("HTTPS server stopped: %s", e)
        finally:
            _state["running"] = False
    threading.Thread(target=run, daemon=True, name="https").start()
    log.info("HTTPS on port %s", port)
    return server


def status():
    """For Setup: whether it's on, the port, any problem, and the addresses the certificate covers."""
    on = bool(config.get("https_enabled"))
    dns, ips = _names()
    return {"enabled": on, "running": _state["running"], "port": int(config.get("https_port") or 8643),
            "error": _state["error"], "names": dns, "ips": ips, "ca": ca_file().exists(),
            "http_port": http_port()}
