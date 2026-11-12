// ---------------------------------------------------------------- phone remote
function screenName() {
  const own = localStorage.getItem("tb.screen");
  if (own) return own;
  const ua = navigator.userAgent;
  if (window.pywebview) return "TunerBox on the PC";
  if (/iPad|Macintosh/.test(ua) && "ontouchend" in document) return "iPad";
  if (window.TunerBoxApp) return /Android/.test(ua) ? "Android TV box" : "TunerBox app";
  if (/iPhone/.test(ua)) return "iPhone";
  if (/Android/.test(ua)) return "Android";
  return `Browser (${/Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "web"})`;
}
function closeOverlays() {
  if (S.digits) { clearTimeout(S.digitTimer); S.digits = ""; $("digits").classList.add("hidden"); }
  if (top() === "guide") G.close();
  closePanels(); closeMenu(); closeDvr(); closeDialog();
}
async function remoteCmd(c) {
  wake(false);
  if (c.type === "key") {
    if (c.key === "Power" && S.mode === "off") { powerOn(); return; }
    handleKey(c.key);
  } else if (c.type === "power") {
    if (c.on && S.mode === "off") powerOn(); else if (!c.on && S.mode !== "off") powerOff();
  } else if (c.type === "tune") {
    if (S.mode === "off") { await powerOn(); }
    closeOverlays();
    if (!tuneNum(+c.number)) return;
    // a game from the phone's Sports list: the event feed that has it too, in case of no signal
    if (c.feed && c.feed.key) S.gameFeed = { idx: S.idx, feed: c.feed, until: +c.until || 0 };
  } else if (c.type === "feed" && c.feed && c.feed.key) {
    if (S.mode === "off") { await powerOn(); }
    closeOverlays(); playFeed(c.feed);
  } else if (c.type === "volume") setVolume(+c.value);
  setTimeout(() => sendState(), 300);
}
let lastState = "", lastStateAt = 0;
function sendState(force) {
  const ch = curCh();
  const live = S.mode === "live" && ch;
  const nn = live && !ch.feed && S.nowCache.get(ch.number);
  const st = { power: S.mode !== "off", mode: S.mode, number: live ? ch.number : "", name: live ? (ch.label || ch.name) : "",
               title: nn && nn.d && nn.d.now ? nn.d.now.title : "", layer: top() || "", volume: Math.round(video.volume * 100) / 100,
               muted: video.muted, feed: !!(live && ch.feed), buffer: bufSecs() };
  const js = JSON.stringify(st);
  if (!force && js === lastState && Date.now() - lastStateAt < 30000) return;
  lastState = js; lastStateAt = Date.now();
  fetch("/api/remote/state", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ screen: CID, state: st }) }).catch(() => {});
}
(function listenForPhones() {
  if (!window.EventSource) return;
  let es = null;
  const open = () => {
    es = new EventSource(`/api/remote/events?screen=${CID}&name=${encodeURIComponent(screenName())}`);
    es.onopen = () => sendState(true);
    es.onmessage = (e) => { try { remoteCmd(JSON.parse(e.data)); } catch (err) { report(`remote: ${err && (err.stack || err.message)}`); } };
  };
  open();
  setInterval(() => sendState(), 2000);
  // Android app in the background: the system may drop the connection; EventSource reconnects by itself
})();

// keep the guide's "now" line and banner fresh
setInterval(() => { if (top() === "guide") G.render(); }, 60000);
// start straight into TV if launched with ?on
if (new URLSearchParams(location.search).has("on") || new URLSearchParams(location.search).has("ch")) powerOn();
