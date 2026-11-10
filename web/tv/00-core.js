const $ = (id) => document.getElementById(id);
const video = $("video");
const CID = Math.random().toString(36).slice(2);
const HALF = 1800, CHUNK = 21600;

const S = {
  channels: [], byNum: new Map(), idx: -1, lastIdx: -1, alt: 0, player: null,
  mode: "off",            // off | live | playback
  layer: [],              // overlay stack: guide, menu, dvr, dialog
  digits: "", digitTimer: null, bannerTimer: null, watchdog: null,
  nowCache: new Map(), settings: {},
  favs: new Set(),        // favorite channel numbers (shared by every screen, stored on the server)
};

// ---------------------------------------------------------------- helpers
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
  return r.json();
};
const now = () => Math.floor(Date.now() / 1000);
let reported = 0;
function report(msg) {
  if (++reported > 30) return;
  try { navigator.sendBeacon("/api/clientlog", new Blob([JSON.stringify({ msg: String(msg).slice(0, 500), ua: navigator.userAgent.slice(0, 120) })], { type: "application/json" })); } catch (_) {}
}
window.addEventListener("error", (e) => report(`error: ${e.message} @ ${e.filename}:${e.lineno}`));
window.addEventListener("unhandledrejection", (e) => report(`unhandled: ${e.reason && (e.reason.stack || e.reason.message || e.reason)}`));
const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const fmtDay = (t) => new Date(t * 1000).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const top = () => S.layer[S.layer.length - 1];
// phones (not iPads/tablets): touch screen and small in at least one direction. Rechecked on rotate.
const phoneMQ = window.matchMedia && matchMedia("(pointer: coarse) and (max-width: 700px), (pointer: coarse) and (max-height: 500px)");
const isPhone = () => !!(phoneMQ && phoneMQ.matches);
function applyPhone() { document.documentElement.classList.toggle("phone", isPhone()); }
applyPhone();
if (phoneMQ) (phoneMQ.addEventListener ? phoneMQ.addEventListener("change", applyPhone) : phoneMQ.addListener(applyPhone));
let toastTimer;
function toast(msg, ms = 2500) {
  const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), ms);
}
function flags(p) {
  if (!p) return "";
  let f = "";
  if (p.is_live) f += '<span class="flag live">LIVE</span>';
  if (p.is_new) f += '<span class="flag">NEW</span>';
  if (p.rating) f += `<span class="flag cc">${esc(p.rating)}</span>`;
  if (p.rec) f += '<span class="flag rec">&#9679; REC</span>';
  if (p.remind) f += '<span class="flag remind">&#128276;</span>';
  return f;
}

// ---------------------------------------------------------------- channels
async function loadChannels() {
  S.channels = await api("/api/channels");
  S.byNum = new Map(S.channels.map((c, i) => [c.number, i]));
}
async function loadFavs() {
  try { S.favs = new Set(await api("/api/favorites")); } catch (_) {}
}
// star / unstar a channel; the guide's Favorites view updates straight away
async function toggleFav(ch) {
  if (!ch || ch.feed || ch.number === "" || ch.number === undefined) { toast("Only numbered channels can be favorites"); return; }
  const on = !S.favs.has(ch.number);
  try { S.favs = new Set(await api("/api/favorites", { method: "POST", body: { number: ch.number, on } })); }
  catch (e) { toast(`Can't change favorites: ${e.message}`); return; }
  toast(on ? `★ ${ch.number} ${ch.name} added to Favorites` : `${ch.number} ${ch.name} removed from Favorites`);
  if (top() === "guide") G.refresh();
}
function nearestIdx(num) {
  if (S.byNum.has(num)) return S.byNum.get(num);
  const i = S.channels.findIndex((c) => c.number >= num);
  return i;
}

