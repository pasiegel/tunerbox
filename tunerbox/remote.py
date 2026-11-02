"""Phone pairing and the phone remote.

  * This PC (loopback or one of its own addresses) can always do everything.
  * A phone pairs with a 6-digit code (or the QR code) shown on the PC. The code lasts 5 minutes and
    five wrong tries cancel it. The phone then keeps a random device token in an HttpOnly cookie;
    the server stores only its SHA-256 hash (settings.json "devices").
  * Paired phones (and this PC) can send remote-control commands to any TunerBox TV screen.
  * Optional lock ("require_pairing"): only this PC and paired phones can change setup. Watching TV
    stays open to every device on the network, so the iPad, Android app and Plex keep working.
"""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
import uuid

from . import config

COOKIE = "tb_device"
PAIR_SECONDS = 300

# ------------------------------------------------------------------ who is asking
_own = {"t": 0.0, "ips": set()}


def local_ips():
    """This PC's own addresses (refreshed every minute)."""
    if time.monotonic() - _own["t"] > 60:
        ips = set()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.0.2.1", 9))         # no packets are sent
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ips.add(info[4][0])
        except OSError:
            pass
        _own.update(t=time.monotonic(), ips=ips)
    return _own["ips"]


def lan_ip():
    """Best address for a phone on the same network."""
    good = []
    for ip in local_ips():
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if a.is_private and not a.is_loopback and not a.is_link_local:
            good.append(ip)
    # prefer the usual home ranges over VPN / virtual adapters
    good.sort(key=lambda ip: (not ip.startswith("192.168."), not ip.startswith("10."), ip))
    return good[0] if good else "127.0.0.1"


def is_pc(request):
    host = request.client.host if request.client else ""
    try:
        a = ipaddress.ip_address(host.split("%")[0])
        if a.version == 6 and a.ipv4_mapped:
            a = a.ipv4_mapped
    except ValueError:
        return False
    return a.is_loopback or str(a) in local_ips()


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def device(request):
    """The paired phone making this request, or None."""
    tok = request.cookies.get(COOKIE)
    if not tok:
        return None
    h = _hash(tok)
    devices = config.get("devices") or []
    for d in devices:
        if hmac.compare_digest(d.get("hash", ""), h):
            if time.time() - d.get("last_seen", 0) > 3600:
                d["last_seen"] = time.time()
                config.update({"devices": devices})
            return d
    return None


def trusted(request):
    return is_pc(request) or device(request) is not None


# ------------------------------------------------------------------ pairing
_pair = {"code": None, "key": None, "expires": 0.0, "fails": 0}
_plock = threading.Lock()


def start_pairing():
    with _plock:
        _pair.update(code=f"{secrets.randbelow(10 ** 6):06d}", key=secrets.token_urlsafe(18),
                     expires=time.time() + PAIR_SECONDS, fails=0)
        return dict(_pair)


def pairing_state():
    with _plock:
        live = bool(_pair["code"]) and time.time() < _pair["expires"]
        return {"active": live, "expires": _pair["expires"] if live else 0}


def cancel_pairing():
    with _plock:
        _pair.update(code=None, key=None, expires=0.0)


def complete_pairing(code, name, key=""):
    """-> device token. Raises ValueError with a message for the phone."""
    with _plock:
        if not _pair["code"] or time.time() > _pair["expires"]:
            raise ValueError("No pairing in progress. On the PC open Setup › Phones › Pair a phone.")
        ok = hmac.compare_digest((code or "").strip(), _pair["code"])
        if key:   # the QR code carries a long one-time key as well
            ok = ok and hmac.compare_digest(key.strip(), _pair["key"] or "")
        if not ok:
            _pair["fails"] += 1
            if _pair["fails"] >= 5:
                _pair.update(code=None, key=None, expires=0.0)
                raise ValueError("Too many wrong codes. Start pairing again on the PC.")
            raise ValueError("Wrong code")
        _pair.update(code=None, key=None, expires=0.0)
    token = secrets.token_urlsafe(32)
    devices = list(config.get("devices") or [])
    devices.append({"id": uuid.uuid4().hex[:12], "name": (name or "Phone").strip()[:40] or "Phone",
                    "hash": _hash(token), "created": time.time(), "last_seen": time.time()})
    config.update({"devices": devices})
    return token


def devices():
    return [{k: d.get(k) for k in ("id", "name", "created", "last_seen")} for d in config.get("devices") or []]


def rename_device(did, name):
    ds = config.get("devices") or []
    for d in ds:
        if d["id"] == did:
            d["name"] = (name or "Phone").strip()[:40] or "Phone"
    config.update({"devices": ds})


def remove_device(did):
    config.update({"devices": [d for d in config.get("devices") or [] if d["id"] != did]})


def qr_svg(text):
    """QR code as an SVG data URL (None if the qrcode package is missing)."""
    try:
        import base64
        import io
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return None
    buf = io.BytesIO()
    qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2).save(buf)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ open phone remotes
_phones = {}        # device id -> last time its remote page checked in (it polls every 2 s while open)


def phone_seen(request):
    d = device(request)
    _phones[d["id"] if d else "pc:" + (request.client.host if request.client else "")] = time.time()


def phones_open(window=20):
    now = time.time()
    return sum(1 for t in _phones.values() if now - t < window)


# ------------------------------------------------------------------ TV screens
class Screen:
    def __init__(self, sid, name):
        self.id, self.name = sid, name
        self.queue = asyncio.Queue(maxsize=50)
        self.state = {}
        self.seen = time.time()
        self.conns = 0


_screens = {}


def screens():
    now = time.time()
    for sid in [k for k, s in _screens.items() if s.conns == 0 and now - s.seen > 90]:
        _screens.pop(sid, None)
    out = [{"id": s.id, "name": s.name, "state": s.state, "online": s.conns > 0, "seen": s.seen}
           for s in _screens.values()]
    return sorted(out, key=lambda s: (not s["online"], -s["seen"]))


def screen(sid, name=""):
    s = _screens.get(sid)
    if not s:
        s = _screens[sid] = Screen(sid, name or "TV")
    if name:
        s.name = name[:40]
    s.seen = time.time()
    return s


def set_state(sid, state):
    s = _screens.get(sid)
    if s:
        s.state = {k: state.get(k) for k in ("power", "mode", "number", "name", "title", "layer",
                                             "volume", "muted", "feed", "buffer")}
        s.seen = time.time()


def send(sid, cmd):
    s = _screens.get(sid)
    if not s or s.conns == 0:
        return False
    try:
        s.queue.put_nowait(cmd)
    except asyncio.QueueFull:
        return False
    return True


async def events(request, sid, name):
    """Server-sent events to one TV screen: the commands phones send it."""
    s = screen(sid, name)
    s.conns += 1
    try:
        yield "retry: 3000\n\n"
        while True:
            try:
                cmd = await asyncio.wait_for(s.queue.get(), 15)
                yield f"data: {json.dumps(cmd)}\n\n"
            except asyncio.TimeoutError:
                yield ": ping\n\n"
            s.seen = time.time()
            if await request.is_disconnected():
                break
    finally:
        s.conns -= 1
        s.seen = time.time()
