/* TunerBox set-top box UI */
(() => {
"use strict";
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

// ---------------------------------------------------------------- playback
function destroyPlayer() {
  clearTimeout(S.watchdog); hideBuffering();
  $("tapplay").classList.add("hidden");
  if (S.player) {
    const p = S.player; S.player = null;
    for (const step of ["pause", "unload", "detachMediaElement", "destroy"]) {
      try { if (typeof p[step] === "function") p[step](); } catch (e) { report(`destroy ${step}: ${e && e.message}`); }
    }
  }
  video.onerror = video.onplaying = video.onwaiting = null;
  video.removeAttribute("src");
  try { video.load(); } catch (_) {}
}

function showStatic(num, msg, sub) {
  $("static").classList.remove("hidden");
  $("nosig-num").textContent = num ?? "";
  $("nosig-msg").textContent = msg || "No Signal";
  $("nosig-sub").textContent = sub || "";
}
function hideStatic() { $("static").classList.add("hidden"); }
(function noise() {
  const c = $("noise"), ctx = c.getContext("2d"), img = ctx.createImageData(c.width, c.height);
  const loop = () => {
    if (!$("static").classList.contains("hidden")) {
      for (let i = 0; i < img.data.length; i += 4) {
        const v = Math.random() * 255 | 0; img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = 255;
      }
      ctx.putImageData(img, 0, 0);
    }
    setTimeout(() => requestAnimationFrame(loop), 50);
  };
  loop();
})();

// what's on screen: a numbered channel, or an event/PPV feed picked from Sports or Search
const curCh = () => S.feedCh || S.channels[S.idx];
// extra stream cache (seconds): this device's choice from the Menu, else the server-wide setting
const BUFFERS = [0, 5, 10, 20, 30, 60];
function bufSecs() {
  const own = localStorage.getItem("tb.buffer");
  const v = own !== null && own !== "" ? +own : +(S.settings.stream_buffer || 0);
  return isNaN(v) ? 0 : v;
}
function streamUrl(ch, alt, hls) {
  const b = `&buf=${bufSecs()}`;
  if (ch.feed) return `${location.origin}/api/feed/${hls ? "hls" : "stream"}?key=${encodeURIComponent(ch.feed)}&client=${CID}${b}`;
  return `${location.origin}/api/${hls ? "hls" : "stream"}/${ch.number}?alt=${alt}&client=${CID}${b}`;
}
const bufferedAhead = () => {
  const b = video.buffered;
  return b.length ? b.end(b.length - 1) - Math.max(video.currentTime, b.start(b.length - 1)) : 0;
};
function hideBuffering() { clearInterval(S.bufTimer); S.bufTimer = null; $("buffering").classList.add("hidden"); }
// hold the picture until `secs` of video are cached, then play (a stall later rebuilds part of it)
function fillCache(p, secs, then) {
  hideBuffering();
  const t0 = Date.now();
  $("buf-text").textContent = "Buffering…"; $("buffering").classList.remove("hidden");
  S.bufTimer = setInterval(() => {
    if (S.player !== p) { hideBuffering(); return; }
    const have = bufferedAhead();
    $("buf-text").textContent = `Buffering ${Math.min(100, Math.round(have / secs * 100))}%`;
    // a live source only arrives in real time: never wait much longer than the cache size
    if (have >= secs || (have > 1 && Date.now() - t0 > (secs + 8) * 1000)) { hideBuffering(); then(); }
  }, 250);
}

function tune(idx, alt = 0) {
  if (idx < 0 || idx >= S.channels.length) return;
  clearTimeout(S.stepTimer); S.pendingIdx = undefined;
  const ch = S.channels[idx];
  if (S.idx !== idx && S.idx >= 0 && alt === 0 && !S.feedCh) S.lastIdx = S.idx;
  S.feedCh = null;
  S.idx = idx; S.alt = alt; S.mode = "live";
  localStorage.setItem("tb.last", ch.number);
  $("playback").classList.add("hidden");
  destroyPlayer();
  hideStatic();
  if (alt === 0) showBanner();
  if (!ch.mapped) { showStatic(ch.number, "Channel Not Available", ch.name); return; }
  play(ch, alt, () => failOver(idx, alt), () => tune(idx, alt));
}

// event / PPV feed: no channel number, no backups; Last/Back returns to the channel you were on
function playFeed(f) {
  if (S.idx >= 0 && !S.feedCh) S.lastIdx = S.idx;
  const ch = { number: "", badge: f.kind === "ppv" ? "PPV" : "LIVE", name: f.name, label: f.name, logo: f.logo || "",
               feed: f.key, group: f.group, mapped: true, alts: 0, epg: null };
  S.feedCh = ch; S.alt = 0; S.mode = "live";
  $("playback").classList.add("hidden");
  destroyPlayer(); hideStatic(); showBanner();
  const fail = () => {
    if (S.feedCh !== ch) return;
    destroyPlayer();
    showStatic(ch.badge, "Event not on right now", `${ch.name}: this feed only carries video while the event is live. Press Last to go back.`);
  };
  play(ch, 0, fail, () => S.feedCh === ch && playFeed(f));
}

function play(ch, alt, onFail, again) {
  if (useHls()) { playHls(ch, alt, onFail); return; }
  if (!window.mpegts || !mpegts.getFeatureList().mseLivePlayback) {
    showStatic(ch.number, "Playback not supported", "This browser can't play live video"); return;
  }
  const p = mpegts.createPlayer({ type: "mpegts", isLive: true, url: streamUrl(ch, alt, false) },
    { enableWorker: true, lazyLoad: false, enableStashBuffer: true, stashInitialSize: 256 * 1024,
      autoCleanupSourceBuffer: true, liveBufferLatencyChasing: false });
  S.player = p;
  p.attachMediaElement(video);
  p.on(mpegts.Events.ERROR, (type, detail) => {
    if (S.player !== p) return;
    console.warn("player error", type, detail);
    onFail();
  });
  let played = false;
  const onPlay = () => { played = true; S.retries = 0; };
  video.addEventListener("playing", onPlay, { once: true });
  p.on(mpegts.Events.LOADING_COMPLETE, () => {
    if (S.player !== p) return;
    if (!played) { onFail(); return; }
    // live feed dropped mid-show: reconnect like a cable box re-acquiring signal
    S.retries = (S.retries || 0) + 1;
    if (S.retries <= 3) { toast("Reacquiring signal…"); setTimeout(() => S.player === p && again(), 1500); }
    else onFail();
  });
  p.load();
  const start = () => {
    const pr = p.play();
    if (pr && pr.catch) pr.catch((e) => { if (e && e.name === "NotAllowedError" && S.player === p) showTapPlay(); });
  };
  const secs = bufSecs();
  if (secs > 0) {
    fillCache(p, secs, start);
    // ran dry (provider hiccup): wait for a part of the cache to come back instead of stuttering
    video.onwaiting = () => {
      if (S.player !== p || !played || S.bufTimer) return;
      video.pause(); fillCache(p, Math.max(2, secs / 2), start);
    };
  } else start();
  S.watchdog = setTimeout(() => { if (S.player === p && video.readyState < 3) onFail(); }, 20000 + secs * 1000);
}

// iPad/iPhone Safari (and anything else without Media Source Extensions) plays the
// server's HLS output natively. ?hls in the address forces it, for testing.
function useHls() {
  if (S.hls === undefined) {
    const mse = !!(window.mpegts && mpegts.getFeatureList().mseLivePlayback);
    const native = !!video.canPlayType("application/vnd.apple.mpegurl");
    S.hls = native && (!mse || new URLSearchParams(location.search).has("hls"));
  }
  return S.hls;
}
function playHls(ch, alt, onFail) {
  const token = {};
  S.player = { destroy() {}, token };
  let played = false;
  video.onplaying = () => { played = true; S.retries = 0; };
  video.onerror = () => { if (S.player && S.player.token === token) onFail(); };
  video.src = streamUrl(ch, alt, true);
  const pr = video.play();
  if (pr && pr.catch) pr.catch((e) => { if (e && e.name === "NotAllowedError" && S.player && S.player.token === token) showTapPlay(); });
  S.watchdog = setTimeout(() => { if (S.player && S.player.token === token && !played && video.readyState < 3) onFail(); }, 35000 + bufSecs() * 1000);
}

function failOver(idx, alt) {
  if (S.feedCh || S.idx !== idx || S.alt !== alt) return;
  const ch = S.channels[idx];
  if (alt < (ch.alts || 0)) { toast(`Trying backup feed ${alt + 2}…`); tune(idx, alt + 1); }
  else { destroyPlayer(); showStatic(ch.number, "No Signal", ch.name); }
}
video.addEventListener("playing", () => { clearTimeout(S.watchdog); hideStatic(); });

function chStep(d) {
  if (!S.channels.length) return;
  const n = S.channels.length;
  const from = S.pendingIdx !== undefined ? S.pendingIdx : (S.feedCh || S.idx < 0 ? Math.max(0, S.idx) : S.idx);
  const to = (((from + d) % n) + n) % n;
  S.pendingIdx = to;
  peekBanner(S.channels[to]);
  clearTimeout(S.stepTimer);
  // like a real cable box: flicking through channels doesn't start (and kill) a stream per press
  S.stepTimer = setTimeout(() => { const i = S.pendingIdx; S.pendingIdx = undefined; tune(i); }, 350);
}
function peekBanner(ch) {
  $("b-num").textContent = ch.number; $("b-name").textContent = ch.name;
  const logo = $("b-logo");
  if (ch.logo) { logo.src = ch.logo; logo.style.display = ""; } else logo.style.display = "none";
  $("b-title").textContent = ch.name; $("b-sub").textContent = ""; $("b-flags").innerHTML = "";
  $("b-time").textContent = ""; $("b-next-l").textContent = "Next:"; $("b-next").textContent = "—"; $("b-desc").textContent = ""; $("b-progress").style.width = "0";
  $("banner").classList.remove("full"); $("banner").classList.remove("hidden");
  clearTimeout(S.bannerTimer);
  S.bannerTimer = setTimeout(hideBanner, 6000);
}

// ---------------------------------------------------------------- banner
async function nowNext(num, force) {
  const c = S.nowCache.get(num);
  if (c && !force && Date.now() - c.t < 60000 && (!c.d.now || c.d.now.stop > now())) return c.d;
  const d = await api(`/api/now/${num}`);
  S.nowCache.set(num, { t: Date.now(), d });
  return d;
}
async function showBanner(full = false) {
  const ch = curCh(); if (!ch) return;
  const b = $("banner");
  $("b-num").textContent = ch.badge || ch.number;
  $("b-name").textContent = ch.name;
  const logo = $("b-logo");
  if (ch.logo) { logo.src = ch.logo; logo.style.display = ""; } else logo.style.display = "none";
  logo.onerror = () => (logo.style.display = "none");
  $("b-title").textContent = ch.epg ? "…" : ch.name; $("b-sub").textContent = ""; $("b-flags").innerHTML = "";
  $("b-time").textContent = ""; $("b-next-l").textContent = "Next:"; $("b-next").textContent = "—"; $("b-desc").textContent = ""; $("b-progress").style.width = "0";
  b.classList.toggle("full", full); b.classList.remove("hidden");
  clearTimeout(S.bannerTimer);
  S.bannerTimer = setTimeout(() => b.classList.add("hidden"), full ? 12000 : 6000);
  if (ch.feed) { $("b-sub").textContent = ch.group || "Live event"; $("b-next").textContent = "—"; return; }
  try {
    const d = await nowNext(ch.number);
    if (curCh() !== ch) return;
    const p = d.now;
    if (p) {
      $("b-title").textContent = p.title || ch.name;
      $("b-flags").innerHTML = flags(p);
      $("b-sub").textContent = [p.episode, p.subtitle].filter(Boolean).join("  ·  ");
      $("b-time").textContent = `${fmtTime(p.start)} – ${fmtTime(p.stop)}`;
      $("b-progress").style.width = `${Math.min(100, (now() - p.start) / (p.stop - p.start) * 100)}%`;
      $("b-desc").textContent = p.descr || "";
    } else {
      $("b-title").textContent = ch.name; $("b-sub").textContent = ch.epg ? "No information available" : "";
    }
    if (d.next) $("b-next").textContent = `${fmtTime(d.next.start)}  ${d.next.title}`;
  } catch (_) {}
}
function hideBanner() { $("banner").classList.add("hidden"); }

function tickClock() {
  const d = new Date();
  const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const day = d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  $("b-clock").textContent = t; $("g-clock").textContent = t; $("b-date").textContent = day; $("g-date").textContent = day;
}
setInterval(tickClock, 1000); tickClock();

// ---------------------------------------------------------------- digit entry
function digit(d) {
  S.digits = (S.digits + d).slice(-5);
  $("digits").textContent = S.digits;
  $("digits").classList.remove("hidden");
  clearTimeout(S.digitTimer);
  S.digitTimer = setTimeout(commitDigits, S.digits.length >= 5 ? 300 : 1800);
}
function commitDigits() {
  clearTimeout(S.digitTimer);
  const num = parseInt(S.digits, 10); S.digits = "";
  $("digits").classList.add("hidden");
  if (isNaN(num)) return;
  if (top() === "guide") { G.jump(num); return; }
  if (S.byNum.has(num)) tune(S.byNum.get(num));
  else { toast(`Channel ${num} is not available`); }
}

// ---------------------------------------------------------------- guide
const G = {
  rows: 8, top: 0, row: 0, win: 0, span: 7200, focusT: 0,
  cache: new Map(), cacheT: 0, loading: new Set(),
  favOnly: false, list: [],       // rows = indexes into S.channels (every channel, or favorites only)

  build() {
    this.list = [];
    S.channels.forEach((c, i) => { if (!this.favOnly || S.favs.has(c.number)) this.list.push(i); });
  },
  ch(row) { return S.channels[this.list[row]]; },
  rowOf(idx) { return this.list.indexOf(idx); },

  open(favs = false) {
    if (!S.channels.length) { toast("No channels yet - open Menu › Channel Setup"); return; }
    if (favs && !S.channels.some((c) => S.favs.has(c.number))) {
      toast("No favorites yet: highlight a channel in the guide and press * (or Menu › Add to Favorites)", 5000);
      favs = false;
    }
    if (Date.now() - this.cacheT > 5 * 60000) { this.cache.clear(); this.cacheT = Date.now(); }
    if (top() !== "guide") S.layer.push("guide");
    hideBanner(); $("playback").classList.add("hidden");
    $("guide").classList.remove("hidden");
    video.classList.add("pip");
    this.rows = window.innerHeight < window.innerWidth ? 8 : 9;
    this.span = isPhone() && window.innerHeight > window.innerWidth ? 5400 : 7200;   // phone portrait: 90 min, wider cells
    this.win = Math.floor(now() / HALF) * HALF;
    this.focusT = now();
    this.favOnly = favs; this.build();
    // a phone may have changed the favorites: pick that up
    loadFavs().then(() => top() === "guide" && this.refresh());
    this.row = Math.max(0, this.rowOf(S.idx));
    this.top = Math.max(0, Math.min(this.row - 2, this.list.length - this.rows));
    this.render();
  },
  // switch between every channel and favorites only, staying on the same channel if it's in both
  toggleView() {
    const cur = this.list[this.row];
    if (!this.favOnly && !S.channels.some((c) => S.favs.has(c.number))) {
      toast("No favorites yet: highlight a channel and press * to add it", 4000); return;
    }
    this.favOnly = !this.favOnly; this.build();
    const r = this.rowOf(cur);
    this.row = r >= 0 ? r : 0;
    this.top = Math.max(0, Math.min(this.row - 2, this.list.length - this.rows));
    toast(this.favOnly ? "★ Favorites only" : "All channels");
    this.render();
  },
  // favorites changed while the guide is open
  refresh() {
    const cur = this.list[this.row];
    this.build();
    if (!this.list.length) { this.favOnly = false; this.build(); toast("No favorites left - showing all channels"); }
    const r = this.rowOf(cur);
    this.row = r >= 0 ? r : Math.min(this.row, this.list.length - 1);
    this.top = Math.max(0, Math.min(this.top, this.list.length - this.rows));
    if (this.row < this.top) this.top = this.row;
    if (this.row >= this.top + this.rows) this.top = this.row - this.rows + 1;
    this.render();
  },
  close() {
    S.layer = S.layer.filter((l) => l !== "guide");
    $("guide").classList.add("hidden");
    video.classList.remove("pip");
  },
  minWin() { return Math.floor(now() / HALF) * HALF; },

  progsFor(num) {
    const e = this.cache.get(num);
    return e ? [...e.progs.values()].sort((a, b) => a.start - b.start) : [];
  },
  async ensure(nums, from, to) {
    const need = new Map();
    for (let c = Math.floor(from / CHUNK) * CHUNK; c < to; c += CHUNK) {
      for (const n of nums) {
        const e = this.cache.get(n);
        if (!e || !e.chunks.has(c)) { if (!need.has(c)) need.set(c, []); need.get(c).push(n); }
      }
    }
    if (!need.size) return false;
    // Only report "changed" when this call actually downloaded something. Requests already in
    // flight are skipped; the call that started them redraws when they land. (Reporting true for
    // a skipped request made render -> ensure -> render spin forever and froze the whole page
    // whenever the server was slow to answer.)
    let fetched = false;
    await Promise.all([...need].map(async ([c, list]) => {
      const key = c + ":" + list.join(",");
      if (this.loading.has(key)) return;
      this.loading.add(key);
      try {
        const data = await api(`/api/guide?start=${c}&stop=${c + CHUNK}&numbers=${list.join(",")}`);
        for (const n of list) {
          if (!this.cache.has(n)) this.cache.set(n, { chunks: new Set(), progs: new Map() });
          const e = this.cache.get(n); e.chunks.add(c);
          for (const p of data[n] || []) e.progs.set(p.start, p);
        }
        fetched = true;
      } finally { this.loading.delete(key); }
    }));
    return fetched;
  },
  forget(num) { this.cache.delete(num); },

  // programs + filler cells covering [win, win+span)
  cells(num) {
    const a = this.win, b = this.win + this.span, out = [];
    const hasData = this.cache.has(num);
    let t = a;
    const fill = (until) => {
      while (t < until) {
        const nx = Math.min(until, (Math.floor(t / HALF) + 1) * HALF);
        out.push({ start: t, stop: nx, title: hasData ? "No Information" : "Loading…", empty: true });
        t = nx;
      }
    };
    for (const p of this.progsFor(num)) {
      if (p.stop <= a || p.start >= b) continue;
      if (p.start > t) fill(p.start);
      out.push(p); t = Math.max(t, p.stop);
    }
    fill(b);
    return out;
  },
  // full program list for navigation (includes outside window)
  rowProgs(num) {
    const ps = this.progsFor(num);
    if (ps.length) return ps;
    return [];
  },
  selected() {
    const ch = this.ch(this.row);
    const t = Math.max(this.focusT, this.win);
    const cells = this.cells(ch.number);
    return cells.find((c) => c.start <= t && t < c.stop) || cells[0];
  },

  render() {
    const rowsEl = $("g-rows");
    const n = this.list.length;
    // prefetch this page + next page
    const pre = [];
    for (let i = Math.max(0, this.top - this.rows); i < Math.min(n, this.top + this.rows * 2); i++) pre.push(this.ch(i).number);
    this.ensure(pre, this.win, this.win + this.span).then((changed) => changed && top() === "guide" && this.render()).catch(() => {});

    $("g-day").textContent = (this.favOnly ? "★ " : "") + fmtDay(this.win);
    const tEl = $("g-times"); tEl.innerHTML = "";
    for (let i = 0; i < this.span / HALF; i++) {
      const d = document.createElement("div");
      d.style.left = `${(i * HALF) / this.span * 100}%`; d.style.width = `${HALF / this.span * 100}%`;
      d.textContent = fmtTime(this.win + i * HALF);
      tEl.appendChild(d);
    }
    const sel = this.selected();
    const n0 = now();
    let html = "";
    for (let i = this.top; i < Math.min(n, this.top + this.rows); i++) {
      const ch = this.ch(i), fav = S.favs.has(ch.number);
      const cls = ["g-row", i === this.row ? "sel" : "", this.list[i] === S.idx ? "playing" : ""].join(" ");
      html += `<div class="${cls}" data-i="${i}"><div class="g-ch"><span class="g-fav${fav ? " on" : ""}" title="${fav ? "Remove from" : "Add to"} Favorites">${fav ? "★" : "☆"}</span><span class="n">${ch.number}</span>`
        + (ch.logo ? `<img src="${esc(ch.logo)}" onerror="this.remove()" alt="">` : "")
        + `<span class="l">${esc(ch.label || ch.name)}</span></div><div class="g-progs">`;
      for (const p of this.cells(ch.number)) {
        const l = Math.max(p.start, this.win), r = Math.min(p.stop, this.win + this.span);
        const isSel = i === this.row && sel && p.start === sel.start;
        const c = ["g-p", p.empty ? "empty" : (p.category || ""), (p.start <= n0 && n0 < p.stop) ? "now" : "", isSel ? "sel" : ""].join(" ");
        const pre = p.start < this.win ? "&#9664; " : "";
        const post = p.stop > this.win + this.span ? " &#9654;" : "";
        const title = p.empty && !ch.epg ? ch.name : p.title;
        const dot = p.rec ? '<span class="dot">&#9679;</span>' : (p.series ? '<span class="dot">&#9679;&#9679;</span>' : "");
        html += `<div class="${c}" data-i="${i}" data-s="${p.start}" style="left:${(l - this.win) / this.span * 100}%;width:${(r - l) / this.span * 100}%">`
          + `${dot}<span class="t">${pre}${esc(title)}${post}</span></div>`;
      }
      if (n0 >= this.win && n0 < this.win + this.span) html += `<div class="g-nowline" style="left:${(n0 - this.win) / this.span * 100}%"></div>`;
      html += "</div></div>";
    }
    rowsEl.innerHTML = html;
    this.info(sel);
  },
  info(p) {
    const ch = this.ch(this.row);
    if (!p || p.empty) {
      $("gi-title").textContent = ch.name; $("gi-sub").textContent = p && p.title === "Loading…" ? "Loading…" : "No information available";
      $("gi-meta").textContent = `Channel ${ch.number}`; $("gi-desc").textContent = ""; return;
    }
    $("gi-title").innerHTML = esc(p.title) + " " + flags(p);
    $("gi-sub").textContent = [p.episode, p.subtitle].filter(Boolean).join("  ·  ");
    $("gi-meta").textContent = `${fmtDay(p.start)}  ${fmtTime(p.start)} – ${fmtTime(p.stop)}   ·   ${ch.number} ${ch.label || ch.name}` + (p.category ? `   ·   ${p.category}` : "");
    $("gi-desc").textContent = p.descr || "";
  },
  move(dr) {
    const n = this.list.length;
    this.row = (this.row + dr + n) % n;
    if (this.row < this.top) this.top = this.row;
    if (this.row >= this.top + this.rows) this.top = this.row - this.rows + 1;
    if (Math.abs(dr) > 1) this.top = Math.max(0, Math.min(this.row, n - this.rows));
    this.render();
  },
  page(d) {
    const n = this.list.length;
    if (d > 0 && this.top + this.rows >= n) { this.top = 0; this.row = 0; }
    else if (d < 0 && this.top === 0) { this.top = Math.max(0, n - this.rows); this.row = n - 1; }
    else {
      this.top = Math.max(0, Math.min(n - this.rows, this.top + d * this.rows));
      this.row = Math.max(0, Math.min(n - 1, this.row + d * this.rows));
      if (this.row < this.top || this.row >= this.top + this.rows) this.row = this.top;
    }
    this.render();
  },
  horiz(d) {
    const sel = this.selected(); if (!sel) return;
    if (d > 0) {
      const target = sel.stop;
      if (target >= now() + 14 * 86400) return;
      this.focusT = target;
      while (this.focusT >= this.win + this.span) this.win += HALF;
    } else {
      const target = sel.start - 1;
      if (target < this.minWin() && sel.start <= this.minWin()) return;
      this.focusT = Math.max(target, this.minWin());
      while (this.focusT < this.win && this.win > this.minWin()) this.win -= HALF;
      if (this.focusT < this.win) this.focusT = this.win;
    }
    this.render();
  },
  // touch scrolling: move the visible page / time window, keeping the highlight on screen
  scrollTo(t) {
    t = Math.max(0, Math.min(t, this.list.length - this.rows));
    if (t === this.top) return;
    this.top = t;
    this.row = Math.max(t, Math.min(this.row, t + this.rows - 1));
    this.render();
  },
  timeTo(w) {
    w = Math.max(this.minWin(), Math.min(w, Math.floor((now() + 14 * 86400) / HALF) * HALF - this.span));
    if (w === this.win) return;
    this.win = w;
    this.focusT = Math.max(w, Math.min(this.focusT, w + this.span - 1));
    this.render();
  },
  jump(num) {
    let i = this.list.findIndex((ci) => S.channels[ci].number >= num);
    if (i < 0 && this.favOnly) { this.favOnly = false; this.build(); i = this.list.findIndex((ci) => S.channels[ci].number >= num); }
    if (i < 0) { toast(`Channel ${num} is not available`); return; }
    this.row = i; this.top = Math.max(0, Math.min(i, this.list.length - this.rows)); this.render();
  },
  ok() {
    const p = this.selected(); const ch = this.ch(this.row);
    const n0 = now();
    if (!p || p.empty || (p.start <= n0 && n0 < p.stop)) {
      if (this.list[this.row] !== S.idx || S.mode !== "live") tune(this.list[this.row]);
      this.close();
      return;
    }
    programDialog(ch, p);
  },
  fav() { toggleFav(this.ch(this.row)); },
  record() {
    const p = this.selected(); const ch = this.ch(this.row);
    if (!p || p.empty) { toast("No guide information to record"); return; }
    programDialog(ch, p);
  },
};

// ---------------------------------------------------------------- dialogs & lists
function listNav(listEl, key, onPick) {
  const items = [...listEl.querySelectorAll("li")];
  if (!items.length) return;
  let i = items.findIndex((li) => li.classList.contains("sel"));
  if (key === "ArrowDown") i = (i + 1) % items.length;
  else if (key === "ArrowUp") i = (i - 1 + items.length) % items.length;
  else if (key === "ChannelDown") i = Math.min(items.length - 1, Math.max(0, i) + 8);
  else if (key === "ChannelUp") i = Math.max(0, i - 8);
  else if (key === "Enter") { if (i >= 0) onPick(items[i], i); return; }
  else return;
  items.forEach((li) => li.classList.remove("sel"));
  items[i].classList.add("sel"); items[i].scrollIntoView({ block: "nearest" });
}
function fillList(listEl, entries, sel = 0) {
  listEl.innerHTML = entries.map((e, i) => `<li class="${i === sel ? "sel" : ""}" data-i="${i}">${e.html ?? esc(e.label)}${e.right ? `<span class="r">${e.right}</span>` : ""}</li>`).join("");
}

let dlg = { entries: [] };
function openDialog(title, sub, entries) {
  dlg.entries = entries; $("dlg-img").classList.add("hidden");
  $("dlg-title").textContent = title; $("dlg-sub").textContent = sub || "";
  fillList($("dlg-list"), entries);
  if (top() !== "dialog") S.layer.push("dialog");
  $("dialog").classList.remove("hidden");
}
function closeDialog() { S.layer = S.layer.filter((l) => l !== "dialog"); $("dialog").classList.add("hidden"); $("dlg-img").classList.add("hidden"); clearInterval(S.pairPoll); }

function bufferDialog() {
  const cur = localStorage.getItem("tb.buffer");
  const set = (v) => {
    if (v === null) localStorage.removeItem("tb.buffer"); else localStorage.setItem("tb.buffer", v);
    closeDialog();
    toast(`Stream cache: ${bufSecs() ? bufSecs() + " seconds" : "Off"}`);
    if (S.mode === "live") { if (S.feedCh) { const f = S.feedCh; playFeed({ key: f.feed, name: f.name, group: f.group, logo: f.logo, kind: f.badge === "PPV" ? "ppv" : "events" }); } else if (S.idx >= 0) tune(S.idx, S.alt); }
    sendState(true);
  };
  const def = +(S.settings.stream_buffer || 0);
  openDialog("Stream cache", "Keeps more video in memory before and while playing, so a slow or hiccuping stream doesn't freeze. Bigger = smoother, but each channel change takes that long to start.", [
    { label: `Use the default (${def ? def + " seconds" : "Off"})${cur === null ? "  ✓" : ""}`, act: () => set(null) },
    ...BUFFERS.map((b) => ({ label: `${b ? b + " seconds" : "Off: fastest channel changes"}${cur !== null && +cur === b ? "  ✓" : ""}`, act: () => set(String(b)) })),
  ]);
}

async function pairDialog() {
  let p;
  try { p = await api("/api/pair/start", { method: "POST" }); }
  catch (e) {
    openDialog("Use a phone as the remote", "Pairing starts on the TunerBox PC: open TunerBox there, then Menu › Use a phone as the remote (or Setup › Phones).", [{ label: "OK", act: closeDialog }]);
    return;
  }
  const before = (await api("/api/pair/state").catch(() => ({ devices: [] }))).devices.length;
  openDialog(`Use a phone as the remote · code ${p.code.slice(0, 3)} ${p.code.slice(3)}`, `Scan with the phone's camera, or open ${p.address} and enter ${p.code.slice(0, 3)} ${p.code.slice(3)}`,
             [{ label: "Done", act: () => { api("/api/pair/cancel", { method: "POST" }).catch(() => {}); closeDialog(); } }]);
  if (p.qr) { $("dlg-img").src = p.qr; $("dlg-img").classList.remove("hidden"); }
  clearInterval(S.pairPoll);
  S.pairPoll = setInterval(async () => {
    const st = await api("/api/pair/state").catch(() => null);
    if (!st) return;
    if (st.devices.length > before) { closeDialog(); toast(`${st.devices[st.devices.length - 1].name} paired as a remote`, 4000); }
    else if (!st.active) { closeDialog(); toast("Pairing code expired"); }
  }, 1500);
}
$("dlg-list").addEventListener("click", (e) => { const li = e.target.closest("li"); if (li) dlg.entries[+li.dataset.i].act(); });

function programDialog(ch, p) {
  const sub = `${ch.number} ${ch.name}\n${fmtDay(p.start)} ${fmtTime(p.start)} – ${fmtTime(p.stop)}` + (p.subtitle ? `\n${p.subtitle}` : "");
  const n0 = now();
  const entries = [];
  if (p.start <= n0 && n0 < p.stop) entries.push({ label: "Watch now", act: () => { closeDialog(); G.close(); tune(S.byNum.get(ch.number)); } });
  const rec = async (body, msg) => {
    try { await api("/api/recordings", { method: "POST", body: { number: ch.number, start: p.start, ...body } }); toast(msg); }
    catch (e) { toast(`Can't record: ${e.message}`); }
    closeDialog(); G.forget(ch.number); if (top() === "guide") G.render();
  };
  if (p.rec) {
    entries.push({ label: "Cancel this recording", act: async () => {
      const recs = await api("/api/recordings");
      const r = recs.find((x) => x.number === ch.number && x.start === p.start);
      if (r) await api(`/api/recordings/${r.id}`, { method: "DELETE" });
      toast("Recording cancelled"); closeDialog(); G.forget(ch.number); if (top() === "guide") G.render();
    } });
  } else {
    entries.push({ label: "Record this episode", act: () => rec({}, `Recording scheduled: ${p.title}`) });
  }
  entries.push({ label: "Record series (this channel)", act: () => rec({ series: true }, `Series recording set: ${p.title}`) });
  entries.push({ label: "Record series (new episodes, any channel)", act: () => rec({ series: true, new_only: true, any_channel: true }, `Series recording set: ${p.title}`) });
  entries.push({ label: S.favs.has(ch.number) ? `Remove ${ch.number} ${ch.name} from Favorites` : `★ Add ${ch.number} ${ch.name} to Favorites`,
                 act: () => { closeDialog(); toggleFav(ch); } });
  entries.push({ label: "Close", act: closeDialog });
  openDialog(p.title, sub, entries);
}

// menu
const MENU = [
  { label: "TV Guide", act: () => { closeMenu(); G.open(); } },
  { label: "★ Favorites guide", act: () => { closeMenu(); G.open(true); } },
  { get label() {
      const ch = S.mode === "live" && curCh();
      if (!ch || ch.feed) return "Add to Favorites (tune to a channel first)";
      return S.favs.has(ch.number) ? `Remove ${ch.number} ${ch.name} from Favorites` : `★ Add ${ch.number} ${ch.name} to Favorites`;
    },
    act: () => { const ch = S.mode === "live" && curCh(); closeMenu(); if (ch) toggleFav(ch); else toast("Tune to a channel first"); } },
  { label: "Search", act: () => { closeMenu(); openSearch(); } },
  { label: "Sports & PPV", act: () => { closeMenu(); openSports(); } },
  { label: "My Recordings (DVR)", act: () => { closeMenu(); openDvr(); } },
  { label: "Channel Setup (playlists, lineup, guide)", act: openSetup },
  { label: "Rescan channels", act: async () => { await api("/api/jobs/scan", { method: "POST" }); toast("Scanning channels in the background…"); closeMenu(); watchJob(); } },
  { label: "Refresh TV guide", act: async () => { await api("/api/jobs/epg", { method: "POST" }); toast("Updating guide in the background…"); closeMenu(); watchJob(); } },
  { label: "Open current channel in VLC / mpv", notPhone: true, act: async () => {
    const ch = curCh(); if (!ch) return;
    const url = ch.feed ? `${location.origin}/api/feed/stream?key=${encodeURIComponent(ch.feed)}` : `${location.origin}/api/stream/${ch.number}`;
    try { await navigator.clipboard.writeText(url); } catch (_) {}
    toast(`Stream URL copied: ${url}`, 6000); closeMenu();
  } },
  { label: "Remote control setup", notPhone: true, act: () => { closeMenu(); openRemote(); } },
  { label: "Use a phone as the remote…", notPhone: true, act: () => { closeMenu(); pairDialog(); } },
  { get label() { return `Sleep timer & screensaver: ${sleepOn() ? plural(sleepH(), "hour") : "sleep off"} · ${saverOn() ? plural(saverM(), "min") : "saver off"}`; },
    act: () => { closeMenu(); sleepDialog(); } },
  { get label() { const b = bufSecs(); return `Stream cache: ${b ? b + " seconds" : "Off"}${localStorage.getItem("tb.buffer") === null ? " (default)" : " (this screen)"}`; },
    act: () => { closeMenu(); bufferDialog(); } },
  { label: "Full screen", act: () => { toggleFull(); closeMenu(); } },
  { label: "Turn off", act: () => { closeMenu(); powerOff(); } },
];
// Android app: switch to another TunerBox PC
if (window.TunerBoxApp) MENU.splice(MENU.length - 2, 1, { label: "Change TunerBox server", act: () => window.TunerBoxApp.changeServer() });
// the entries shown right now (phones hide the ones that only make sense on a TV / PC)
let menuShown = MENU;
function openMenu() {
  hideBanner();
  menuShown = isPhone() ? MENU.filter((e) => !e.notPhone) : MENU;
  fillList($("menu-list"), menuShown);
  S.layer.push("menu"); $("menu").classList.remove("hidden");
}
function closeMenu() { S.layer = S.layer.filter((l) => l !== "menu"); $("menu").classList.add("hidden"); }
$("menu-list").addEventListener("click", (e) => { const li = e.target.closest("li"); if (li) menuShown[+li.dataset.i].act(); });

async function watchJob() {
  for (;;) {
    await new Promise((r) => setTimeout(r, 3000));
    const j = await api("/api/jobs");
    if (!j.running) {
      toast(j.message || "Done", 6000);
      await loadChannels(); G.cache.clear(); S.nowCache.clear();
      if (S.mode === "empty" && S.channels.length) tune(0);
      if (top() === "guide") G.render();
      return;
    }
  }
}

// dvr
let dvrRecs = [];
async function openDvr() {
  dvrRecs = await api("/api/recordings");
  const entries = dvrRecs.length ? dvrRecs.map((r) => ({
    html: `<span class="badge ${r.status}">${r.status.toUpperCase()}</span> <span>${esc(r.title)}${r.subtitle ? ` <small>· ${esc(r.subtitle)}</small>` : ""}</span>`,
    right: `${r.number} · ${fmtDay(r.start)} ${fmtTime(r.start)}`,
  })) : [{ label: "No recordings yet. Press R on a program in the guide to record it." }];
  fillList($("dvr-list"), entries);
  if (top() !== "dvr") S.layer.push("dvr");
  $("dvr").classList.remove("hidden");
}
function closeDvr() { S.layer = S.layer.filter((l) => l !== "dvr"); $("dvr").classList.add("hidden"); }
function dvrPick(i) {
  const r = dvrRecs[i]; if (!r) return;
  const entries = [];
  if (r.status === "done" || r.status === "cancelled") {
    entries.push({ label: "Play", act: () => { closeDialog(); closeDvr(); playRecording(r); } });
    entries.push({ label: "Delete recording", act: async () => { await api(`/api/recordings/${r.id}?delete=1`, { method: "DELETE" }); closeDialog(); openDvr(); } });
  } else if (r.status === "recording") {
    entries.push({ label: "Watch channel live", act: () => { closeDialog(); closeDvr(); tune(S.byNum.get(r.number)); } });
    entries.push({ label: "Stop recording", act: async () => { await api(`/api/recordings/${r.id}`, { method: "DELETE" }); closeDialog(); openDvr(); } });
  } else if (r.status === "scheduled") {
    entries.push({ label: "Cancel recording", act: async () => { await api(`/api/recordings/${r.id}`, { method: "DELETE" }); closeDialog(); openDvr(); } });
  } else {
    entries.push({ label: "Remove from list", act: async () => { await api(`/api/recordings/${r.id}?delete=1`, { method: "DELETE" }); closeDialog(); openDvr(); } });
  }
  entries.push({ label: "Back", act: closeDialog });
  openDialog(r.title, `${r.number} ${r.channel_name || ""}\n${fmtDay(r.start)} ${fmtTime(r.start)} – ${fmtTime(r.stop)}${r.error ? "\n" + r.error : ""}`, entries);
}
$("dvr-list").addEventListener("click", (e) => { const li = e.target.closest("li"); if (li) dvrPick(+li.dataset.i); });

function playRecording(r) {
  destroyPlayer(); hideStatic(); hideBanner();
  S.mode = "playback";
  if (r.file.endsWith(".ts") && window.mpegts) {
    S.player = mpegts.createPlayer({ type: "mpegts", isLive: false, url: `${location.origin}/api/recordings/${r.id}/file` });
    S.player.attachMediaElement(video); S.player.load(); S.player.play();
  } else {
    video.src = `/api/recordings/${r.id}/file`; video.play().catch(() => {});
  }
  $("pb-title").textContent = r.title + (r.subtitle ? ` · ${r.subtitle}` : "");
  S.rec = r;
  showPlaybackBar();
}
// Info on a recording: the banner, filled from the recording instead of the live guide
function recInfo(full = false) {
  const r = S.rec; if (!r) return;
  const ch = S.channels[S.byNum.get(r.number)] || {};
  $("b-num").textContent = r.number; $("b-name").textContent = r.channel_name || ch.name || "";
  const logo = $("b-logo");
  if (ch.logo) { logo.src = ch.logo; logo.style.display = ""; } else logo.style.display = "none";
  $("b-title").textContent = r.title || "Recording";
  $("b-flags").innerHTML = '<span class="flag rec">RECORDING</span>';
  $("b-sub").textContent = [r.episode, r.subtitle].filter(Boolean).join("  ·  ");
  $("b-time").textContent = `Recorded ${fmtDay(r.start)}  ${fmtTime(r.start)} – ${fmtTime(r.stop)}`;
  const d = video.duration || 0;
  $("b-progress").style.width = d ? `${video.currentTime / d * 100}%` : "0";
  $("b-desc").textContent = r.descr || "";
  $("b-next-l").textContent = "Watched:";
  $("b-next").textContent = d ? `${fmtDur(video.currentTime)} of ${fmtDur(d)}` : "—";
  const b = $("banner"); b.classList.toggle("full", full); b.classList.remove("hidden");
  clearTimeout(S.bannerTimer); S.bannerTimer = setTimeout(hideBanner, full ? 12000 : 6000);
}
let pbTimer;
function showPlaybackBar() {
  hideBanner();                 // the bar and the info banner share the bottom of the screen
  $("playback").classList.remove("hidden");
  clearTimeout(pbTimer); pbTimer = setTimeout(() => $("playback").classList.add("hidden"), 4000);
}
const fmtDur = (s) => { s = Math.max(0, s | 0); const h = s / 3600 | 0, m = (s % 3600) / 60 | 0, x = s % 60; return (h ? h + ":" : "") + String(m).padStart(h ? 2 : 1, "0") + ":" + String(x).padStart(2, "0"); };
video.addEventListener("timeupdate", () => {
  if (S.mode !== "playback") return;
  const d = video.duration || 0;
  $("pb-progress").style.width = d ? `${video.currentTime / d * 100}%` : "0";
  $("pb-time").textContent = `${fmtDur(video.currentTime)} / ${fmtDur(d)}${video.paused ? "   ❚❚ Paused" : ""}`;
});
video.addEventListener("ended", () => { if (S.mode === "playback") backToLive(); });
function backToLive() { $("playback").classList.add("hidden"); hideBanner(); S.rec = null; destroyPlayer(); if (S.idx >= 0) tune(S.idx); }

// ---------------------------------------------------------------- shared by search + sports
function fmtWhen(t) {
  const d = new Date(t * 1000), today = new Date();
  if (d.toDateString() === today.toDateString()) return fmtTime(t);
  return d.toLocaleDateString([], { weekday: "short" }) + " " + fmtTime(t);
}
function tuneNum(num) {
  const i = S.byNum.get(num);
  if (i === undefined) { toast(`Channel ${num} is not available`); return false; }
  tune(i); return true;
}
function closePanels() { closeSearch(); closeSports(); }
// a guide listing from search/sports: on now -> watch, later -> record options
function pickProgram(p) {
  const i = S.byNum.get(p.ch.number);
  if (i === undefined) { toast(`Channel ${p.ch.number} is not available`); return; }
  const n0 = now();
  if (p.start <= n0 && n0 < p.stop) { closePanels(); tune(i); return; }
  programDialog(S.channels[i], p);
}
const progHtml = (p, badge) => `${badge} <span>${esc(p.title)}${p.subtitle ? ` <small>· ${esc(p.subtitle)}</small>` : ""}</span>`;
function feedBadge(f) {
  if (f.status === "ok") return '<span class="badge live">LIVE</span>';
  if (f.ended) return '<span class="badge off">ENDED</span>';
  if (f.slot) return '<span class="badge off">EMPTY</span>';
  if (f.status === "dead") return '<span class="badge off">OFF AIR</span>';
  return `<span class="badge ${f.kind === "ppv" ? "ppv" : ""}">${f.kind === "ppv" ? "PPV" : "EVENT"}</span>`;
}
const feedItem = (f) => ({ html: `${feedBadge(f)} <span>${esc(f.name)}</span>`, right: esc(f.group || ""),
                           act: () => { closePanels(); playFeed(f); } });
function selIndex(listEl) { return [...listEl.querySelectorAll("li")].findIndex((li) => li.classList.contains("sel")); }

// ---------------------------------------------------------------- search
// On-screen keyboard for remotes; a real keyboard (or the iPad/phone keyboard) types into the box.
const KB_ROWS = [[..."ABCDEFG"], [..."HIJKLMN"], [..."OPQRSTU"], [..."VWXYZ12"], [..."3456789"], ["0", "Space", "⌫", "Clear"]];
const SR = { q: "", zone: "kb", r: 0, c: 0, sel: 0, items: [], timer: null, seq: 0 };
const sInput = $("s-input");
const finePointer = !!(window.matchMedia && matchMedia("(pointer: fine)").matches);

function openSearch() {
  hideBanner(); closeMenu(); closeSports();
  if (top() !== "search") S.layer.push("search");
  $("search").classList.remove("hidden");
  SR.zone = "kb"; renderKb(); renderSearch();
  if (finePointer && !window.TunerBoxApp) sInput.focus();
}
function closeSearch() {
  S.layer = S.layer.filter((l) => l !== "search");
  $("search").classList.add("hidden");
  sInput.blur();
}
function renderKb() {
  $("s-kb").innerHTML = KB_ROWS.map((row, r) => `<div class="kb-row">${row.map((k, c) =>
    `<button class="kb-k${k.length > 1 ? " wide" : ""}${SR.zone === "kb" && SR.r === r && SR.c === c ? " sel" : ""}" data-r="${r}" data-c="${c}">${esc(k)}</button>`).join("")}</div>`).join("");
}
function setQuery(q) {
  SR.q = q; if (sInput.value !== q) sInput.value = q;
  clearTimeout(SR.timer); SR.timer = setTimeout(runSearch, 250);
}
function kbPress(k) {
  if (k === "Space") setQuery(SR.q + " ");
  else if (k === "⌫") setQuery(SR.q.slice(0, -1));
  else if (k === "Clear") setQuery("");
  else setQuery(SR.q + k.toLowerCase());
}
async function runSearch() {
  const seq = ++SR.seq, q = SR.q.trim();
  if (!q) { SR.items = []; renderSearch(); return; }
  $("s-count").textContent = "Searching…";
  let d;
  try { d = await api(`/api/search?q=${encodeURIComponent(q)}`); } catch (e) { $("s-count").textContent = e.message; return; }
  if (seq !== SR.seq) return;
  const n0 = now();
  SR.items = [
    ...d.channels.map((c) => ({ html: `<span class="badge ch">CH</span><b class="num">${c.number}</b> <span>${esc(c.name)}</span>`,
                                right: esc(c.now || ""), act: () => { closePanels(); tuneNum(c.number); } })),
    ...d.programs.map((p) => ({ html: progHtml(p, p.start <= n0 ? '<span class="badge live">ON NOW</span>' : `<span class="badge">${esc(fmtWhen(p.start))}</span>`),
                                right: `${p.ch.number} ${esc(p.ch.label || p.ch.name)}`, act: () => pickProgram(p) })),
    ...d.feeds.map(feedItem),
  ];
  SR.sel = Math.min(SR.sel, Math.max(0, SR.items.length - 1));
  renderSearch();
}
function renderSearch() {
  const q = SR.q.trim();
  $("s-count").textContent = q ? `${SR.items.length || "No"} result${SR.items.length === 1 ? "" : "s"}` : "";
  const entries = SR.items.length ? SR.items
    : [{ label: q ? "Nothing found. Try fewer letters, a team name or a channel number." : "Type a channel name or number, a show, a team or an event." }];
  fillList($("s-list"), entries, SR.zone === "res" && SR.items.length ? SR.sel : -1);
  const li = $("s-list").querySelector("li.sel"); if (li) li.scrollIntoView({ block: "nearest" });
}
// keys typed on a real keyboard (before remote mapping): letters go into the search box
function searchRawKey(ev) {
  if (ev.target === sInput) return !["ArrowDown", "ArrowUp", "Enter", "Escape", "PageDown", "PageUp", "F11"].includes(ev.key);
  if (ev.key === "Backspace" && SR.q) { setQuery(SR.q.slice(0, -1)); ev.preventDefault(); return true; }
  if (ev.key && ev.key.length === 1 && /[\p{L}\p{N} &'+.:!-]/u.test(ev.key) && !/^[0-9]$/.test(ev.key)) {
    setQuery(SR.q + ev.key.toLowerCase()); ev.preventDefault(); return true;
  }
  return false;
}
function searchKey(k) {
  if (k === "Back") { closeSearch(); return; }
  if (/^[0-9]$/.test(k)) { setQuery(SR.q + k); return; }
  if (document.activeElement === sInput && SR.zone === "kb") SR.zone = "res";
  if (SR.zone === "kb") {
    const row = KB_ROWS[SR.r];
    if (k === "ArrowUp") SR.r = Math.max(0, SR.r - 1);
    else if (k === "ArrowDown") SR.r = Math.min(KB_ROWS.length - 1, SR.r + 1);
    else if (k === "ArrowLeft") SR.c = Math.max(0, SR.c - 1);
    else if (k === "ArrowRight") {
      if (SR.c < row.length - 1) SR.c++;
      else if (SR.items.length) { SR.zone = "res"; renderKb(); renderSearch(); return; }
    } else if (k === "Enter") { kbPress(row[SR.c]); return; }
    else return;
    SR.c = Math.min(SR.c, KB_ROWS[SR.r].length - 1);
    renderKb(); return;
  }
  const n = SR.items.length;
  if (k === "ArrowLeft") { if (document.activeElement !== sInput) { SR.zone = "kb"; renderKb(); renderSearch(); } return; }
  if (k === "ArrowDown") SR.sel = Math.min(n - 1, SR.sel + 1);
  else if (k === "ArrowUp") SR.sel = Math.max(0, SR.sel - 1);
  else if (k === "ChannelDown") SR.sel = Math.min(n - 1, SR.sel + 8);
  else if (k === "ChannelUp") SR.sel = Math.max(0, SR.sel - 8);
  else if (k === "Enter") { if (SR.items[SR.sel]) SR.items[SR.sel].act(); return; }
  else return;
  renderSearch();
}
sInput.addEventListener("input", () => setQuery(sInput.value));
sInput.addEventListener("focus", () => { SR.zone = "res"; renderKb(); });
$("s-kb").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  SR.zone = "kb"; SR.r = +b.dataset.r; SR.c = +b.dataset.c;
  kbPress(KB_ROWS[SR.r][SR.c]); renderKb();
});
$("s-list").addEventListener("click", (e) => {
  const li = e.target.closest("li"); if (!li || !SR.items.length) return;
  SR.sel = +li.dataset.i; SR.items[SR.sel].act();
});

// ---------------------------------------------------------------- sports & PPV
const SP_TABS = [["now", "On Now"], ["up", "Coming Up"], ["ch", "Sports Channels"], ["ev", "Events & PPV"]];
const SP = { tab: 0, data: null, group: null, items: [], poll: null };

async function openSports() {
  hideBanner(); closeMenu(); closeSearch();
  if (top() !== "sports") S.layer.push("sports");
  $("sports").classList.remove("hidden");
  SP.group = null;
  $("sp-list").innerHTML = "<li>Loading…</li>";
  try { SP.data = await api("/api/sports"); } catch (e) { $("sp-list").innerHTML = `<li>${esc(e.message)}</li>`; return; }
  const counts = spCounts();
  if (!counts[SP.tab]) { const t = counts.findIndex((n) => n > 0); if (t >= 0) SP.tab = t; }
  renderSports(0);
}
function closeSports() {
  S.layer = S.layer.filter((l) => l !== "sports");
  $("sports").classList.add("hidden");
  clearInterval(SP.poll); SP.poll = null;
}
const spCounts = () => SP.data ? [SP.data.on_now.length, SP.data.upcoming.length, SP.data.channels.length, SP.data.groups.length] : [0, 0, 0, 0];
const ago = (t) => { const m = Math.round((now() - t) / 60); return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };

function spItems() {
  const d = SP.data, n0 = now(), id = SP_TABS[SP.tab][0];
  const none = (label) => [{ label }];
  if (id === "now") {
    return d.on_now.length ? d.on_now.map((p) => ({
      html: progHtml(p, p.is_live ? '<span class="badge live">LIVE</span>' : '<span class="badge">ON</span>'),
      right: `${p.ch.number} ${esc(p.ch.label || p.ch.name)} · until ${fmtTime(p.stop)}`, act: () => pickProgram(p),
    })) : none("No sports on right now, according to the TV guide.");
  }
  if (id === "up") {
    return d.upcoming.length ? d.upcoming.map((p) => ({
      html: progHtml(p, `<span class="badge">${esc(fmtWhen(p.start))}</span>`),
      right: `${p.ch.number} ${esc(p.ch.label || p.ch.name)}`, act: () => pickProgram(p),
    })) : none("No upcoming sports in the TV guide.");
  }
  if (id === "ch") {
    return d.channels.length ? d.channels.map((c) => ({
      html: `<b class="num">${c.number}</b> <span>${esc(c.name)}</span>`,
      right: c.now ? (c.now.is_live ? "LIVE · " : "") + esc(c.now.title) : "",
      act: () => { closePanels(); tuneNum(c.number); },
    })) : none("No sports channels found.");
  }
  // events & PPV
  if (!SP.group) {
    return d.groups.length ? d.groups.map((g) => ({
      html: `<span class="badge ${g.kind}">${g.kind === "ppv" ? "PPV" : "EVENTS"}</span> <span>${esc(g.name)}</span>`,
      right: `${g.live ? `<b class="live-n">${g.live} live</b> · ` : ""}${g.named ? `${g.named} event${g.named === 1 ? "" : "s"}` : `${g.count} feeds`}`,
      act: () => openGroup(g.name),
    })) : none("None of your playlists have event or PPV groups (like ESPN+, PPV or NFL Game Pass).");
  }
  const g = SP.group, st = SP.checkState;
  const checking = st && st.running;
  const head = [
    { label: "◀  All event groups", act: () => { SP.group = null; renderSports(0); } },
    { label: checking ? `Checking which feeds are live… ${st.done}/${st.total}`
        : g.checkable ? `⟳  Check which feeds are live now${g.checked ? ` (last checked ${ago(g.checked)})` : ""}`
        : "These feeds are from Xtream, which isn't checked automatically. Just pick one to try it.",
      act: () => { if (!checking && g.checkable) startCheck(); } },
  ];
  // empty / ended slots ("NO EVENT STREAMING", "ESPN+ 12") stay folded away unless asked for
  const real = g.feeds.filter((f) => !f.slot || f.status === "ok"), slots = g.feeds.filter((f) => f.slot && f.status !== "ok");
  const shown = real.length && !SP.showSlots ? real : g.feeds;
  const items = head.concat(shown.map((f) => feedItem({ ...f, kind: g.kind })));
  if (real.length && slots.length) {
    items.push({ label: SP.showSlots ? "Hide empty and ended slots" : `Show ${slots.length} empty and ended slots`,
                 act: () => { SP.showSlots = !SP.showSlots; renderSports(); } });
  }
  return items;
}
function renderSports(sel) {
  if (!SP.data) return;
  const counts = spCounts();
  $("sp-tabs").innerHTML = SP_TABS.map(([, label], i) =>
    `<div class="tab${i === SP.tab ? " sel" : ""}" data-t="${i}">${label} <small>${counts[i]}</small></div>`).join("");
  SP.items = spItems();
  if (sel === undefined) sel = Math.max(0, selIndex($("sp-list")));
  fillList($("sp-list"), SP.items, Math.min(sel, SP.items.length - 1));
  const li = $("sp-list").querySelector("li.sel"); if (li) li.scrollIntoView({ block: "nearest" });
  const g = SP.group;
  $("sp-status").textContent = g ? `${g.name} · ${g.named ? `${g.named} events · ` : ""}${g.count} feeds${g.live ? ` · ${g.live} live` : ""}` : "";
  $("sp-foot").innerHTML = g
    ? "<b>OK</b> Watch &nbsp; <b>Esc</b> Back to groups &nbsp; Event feeds only have video while the event is on."
    : "<b>◀ ▶</b> Tabs &nbsp; <b>OK</b> Watch / Record &nbsp; <b>Esc</b> Close";
}
async function openGroup(name) {
  try { SP.group = await api(`/api/sports/group?name=${encodeURIComponent(name)}`); } catch (e) { toast(e.message); return; }
  SP.checkState = null; SP.showSlots = false;
  renderSports(SP.group.feeds.length ? 2 : 0);
  // feeds checked more than 15 minutes ago (or never): look again, in the background
  if (SP.group.checkable && now() - (SP.group.checked || 0) > 900) startCheck();
}
async function startCheck() {
  const g = SP.group; if (!g) return;
  let st;
  try { st = await api("/api/sports/check", { method: "POST", body: { group: g.name } }); } catch (e) { toast(e.message); return; }
  if (!st.running) { toast("Nothing to check"); return; }
  SP.checkState = st; renderSports();
  clearInterval(SP.poll);
  SP.poll = setInterval(async () => {
    try { SP.checkState = await api("/api/sports/check"); } catch (_) { return; }
    if (!SP.group || SP.group.name !== g.name) { clearInterval(SP.poll); return; }
    if (!SP.checkState.running) {
      clearInterval(SP.poll); SP.poll = null;
      const keep = selIndex($("sp-list"));
      try { SP.group = await api(`/api/sports/group?name=${encodeURIComponent(g.name)}`); } catch (_) {}
      const gi = SP.data.groups.find((x) => x.name === g.name); if (gi) gi.live = SP.group.live;
      renderSports(keep);
      toast(SP.group.live ? `${SP.group.live} ${g.name} feed${SP.group.live === 1 ? " is" : "s are"} live now` : `Nothing live on ${g.name} right now`);
    } else if (top() === "sports") renderSports();
  }, 1500);
}
function sportsKey(k) {
  if (k === "Back") { if (SP.group) { SP.group = null; renderSports(0); } else closeSports(); return; }
  if (k === "t") { closeSports(); return; }
  if (k === "ArrowLeft" || k === "ArrowRight") {
    SP.tab = (SP.tab + (k === "ArrowRight" ? 1 : -1) + SP_TABS.length) % SP_TABS.length;
    SP.group = null; renderSports(0); return;
  }
  listNav($("sp-list"), k, (_, i) => { const it = SP.items[i]; if (it && it.act) it.act(); });
}
$("sp-tabs").addEventListener("click", (e) => {
  const t = e.target.closest(".tab"); if (!t) return;
  SP.tab = +t.dataset.t; SP.group = null; renderSports(0);
});
$("sp-list").addEventListener("click", (e) => {
  const li = e.target.closest("li"); if (!li) return;
  const it = SP.items[+li.dataset.i]; if (it && it.act) it.act();
});

// ---------------------------------------------------------------- volume / fullscreen / power
function setVolume(v) {
  video.volume = Math.max(0, Math.min(1, v)); video.muted = false;
  localStorage.setItem("tb.vol", video.volume);
  toast(`Volume ${"▮".repeat(Math.round(video.volume * 20))}${"▯".repeat(20 - Math.round(video.volume * 20))}`, 1200);
}
function toggleMute() { video.muted = !video.muted; toast(video.muted ? "Mute" : "Sound on", 1200); }
function openSetup() { location.href = "/setup"; }
// Android app: stop the stream while the app is in the background, resume on return
window.tbAppPaused = () => {
  if (S.mode !== "live") return;
  if (S.feedCh) { S.resumeFeed = S.feedCh; destroyPlayer(); }
  else if (S.idx >= 0) { S.resumeIdx = S.idx; destroyPlayer(); }
};
window.tbAppResumed = () => {
  if (S.mode !== "live") return;
  const f = S.resumeFeed, i = S.resumeIdx;
  S.resumeFeed = S.resumeIdx = undefined;
  if (f) playFeed({ key: f.feed, name: f.name, group: f.group, logo: f.logo, kind: f.badge === "PPV" ? "ppv" : "events" });
  else if (i !== undefined) tune(i);
};
function toggleFull() {
  // desktop app: make the real window full screen
  if (window.pywebview && window.pywebview.api) { window.pywebview.api.toggle_fullscreen(); return; }
  const d = document, el = d.documentElement;
  if (d.fullscreenElement || d.webkitFullscreenElement) { (d.exitFullscreen || d.webkitExitFullscreen).call(d); return; }
  const req = el.requestFullscreen || el.webkitRequestFullscreen;   // webkit-: older iPad Safari
  if (req) {
    try { const p = req.call(el); if (p && p.catch) p.catch(() => {}); } catch (_) {}
    return;
  }
  // iPhone Safari can't make a page full screen, only the video itself (its own player; Done / swipe down exits)
  if (video.webkitEnterFullscreen) {
    if (video.webkitDisplayingFullscreen) { video.webkitExitFullscreen(); return; }
    // (iPhone streams through srcObject / ManagedMediaSource, so currentSrc is empty while playing)
    if (video.readyState < 1) { toast("Tune to a channel first"); return; }
    try { video.webkitEnterFullscreen(); } catch (e) { toast(`Full screen isn't available here${e && e.name ? ` (${e.name})` : ""}`); }
    return;
  }
  toast("Full screen isn't available in this browser");
}
// leaving the iPhone player can pause the video: keep live TV going
// The Done button pauses it while the player is still closing; resume as soon as that pause turns out to
// be the exit (player gone a moment later) instead of waiting for the close animation to finish.
// A pause tapped inside the full screen player stays paused.
const resumeLive = () => { if (S.mode === "live" && video.paused) video.play().catch(() => {}); };
video.addEventListener("pause", () => {
  if (!video.webkitDisplayingFullscreen) return;
  let n = 0;
  const t = setInterval(() => {
    if (!video.webkitDisplayingFullscreen) { clearInterval(t); resumeLive(); }
    else if (++n > 15) clearInterval(t);   // still full screen after ~750ms: a real pause
  }, 50);
});
video.addEventListener("webkitendfullscreen", resumeLive);
// Android / iPad app: keep the device screen on only while TunerBox is on
function keepAwake(on) {
  try { if (window.TunerBoxApp && window.TunerBoxApp.keepAwake) window.TunerBoxApp.keepAwake(!!on); } catch (_) {}
}
function powerOff() {
  keepAwake(false);
  destroyPlayer(); S.mode = "off"; hideBanner(); hideStatic(); IDLE.offSince = Date.now();
  $("power").classList.remove("hidden");
}
// iPad Safari only lets a video play with sound if play() was first called during a tap.
// Doing it once on power-on unlocks the element for every later channel change.
function unlockVideo() {
  if (S.unlocked) return;
  S.unlocked = true;
  try { const pr = video.play(); if (pr && pr.catch) pr.catch(() => {}); } catch (_) {}
}
function showTapPlay() {
  clearTimeout(S.watchdog);
  $("tapplay").classList.remove("hidden");
}
$("tapplay").addEventListener("click", () => {
  $("tapplay").classList.add("hidden");
  const pr = video.play(); if (pr && pr.catch) pr.catch(() => {});
});
async function powerOn() {
  unlockVideo(); keepAwake(true);
  $("power").classList.add("hidden");
  video.volume = parseFloat(localStorage.getItem("tb.vol") || "0.8");
  try { await loadChannels(); } catch (e) { toast("Can't reach TunerBox server"); return; }
  await loadFavs();
  if (!S.channels.length) {
    S.mode = "empty";
    showStatic("", "No channels yet", "Press OK (Enter) or click here to add a playlist");
    return;
  }
  const want = parseInt(new URLSearchParams(location.search).get("ch") || "", 10);
  if (!isNaN(want)) localStorage.setItem("tb.last", want);
  const last = parseInt(localStorage.getItem("tb.last") || "", 10);
  const i = !isNaN(last) ? nearestIdx(last) : 0;
  tune(i < 0 ? 0 : i);
}

// ---------------------------------------------------------------- input / remote mapping
// Every button (keyboard key, TV-remote key, gamepad button) has an input id, and
// bindings map input ids -> actions. Defaults < server-wide defaults < this device.
const ACTIONS = [
  ["ArrowUp", "Up"], ["ArrowDown", "Down"], ["ArrowLeft", "Left"], ["ArrowRight", "Right"],
  ["Enter", "OK / Select"], ["Back", "Back / Exit"],
  ["ChannelUp", "Channel up / Page up"], ["ChannelDown", "Channel down / Page down"],
  ["g", "Guide"], ["i", "Info"], ["m", "Menu"], ["l", "Last channel"],
  ["*", "Favorite: add / remove"], ["v", "Favorites guide / show all"],
  ["r", "Record"], ["d", "Recordings (DVR)"], ["/", "Search"], ["t", "Sports & PPV"],
  ["VolUp", "Volume up"], ["VolDown", "Volume down"], ["u", "Mute"],
  [" ", "Play / Pause"], ["s", "Stop playback"], ["f", "Full screen"], ["Power", "Power on / off"],
  ...[1, 2, 3, 4, 5, 6, 7, 8, 9, 0].map((n) => [String(n), `Number ${n}`]),
];
const ACTION_LABEL = Object.fromEntries(ACTIONS);
const DEFAULT_BINDINGS = {
  ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight",
  Enter: "Enter", Select: "Enter",
  Escape: "Back", Backspace: "Back", BrowserBack: "Back", GoBack: "Back", Exit: "Back",
  PageUp: "ChannelUp", ChannelUp: "ChannelUp", MediaTrackNext: "ChannelUp",
  PageDown: "ChannelDown", ChannelDown: "ChannelDown", MediaTrackPrevious: "ChannelDown",
  g: "g", Guide: "g", BrowserHome: "g", ColorF1Green: "g",
  i: "i", Info: "i", ColorF3Blue: "i",
  m: "m", ContextMenu: "m", Settings: "m",
  l: "l", LastNumberRedial: "l", ColorF2Yellow: "l",
  r: "r", MediaRecord: "r", ColorF0Red: "r",
  d: "d", DVR: "d",
  "*": "*", a: "*", Favorites: "v", v: "v",
  "/": "/", BrowserSearch: "/", Find: "/", F3: "/", t: "t",
  "+": "VolUp", "=": "VolUp", AudioVolumeUp: "VolUp",
  "-": "VolDown", "_": "VolDown", AudioVolumeDown: "VolDown",
  u: "u", AudioVolumeMute: "u",
  " ": " ", MediaPlayPause: " ", MediaPlay: " ", MediaPause: " ",
  s: "s", MediaStop: "s",
  f: "f", F11: "f", Power: "Power", PowerOff: "Power", p: "Power",
  ...Object.fromEntries([0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => [String(n), String(n)])),
  // standard-layout gamepads (Xbox / PlayStation / many Bluetooth remotes)
  "pad:b12": "ArrowUp", "pad:b13": "ArrowDown", "pad:b14": "ArrowLeft", "pad:b15": "ArrowRight",
  "pad:a1-": "ArrowUp", "pad:a1+": "ArrowDown", "pad:a0-": "ArrowLeft", "pad:a0+": "ArrowRight",
  "pad:b0": "Enter", "pad:b1": "Back", "pad:b2": "i", "pad:b3": "g",
  "pad:b4": "ChannelDown", "pad:b5": "ChannelUp", "pad:b6": "VolDown", "pad:b7": "VolUp",
  "pad:b8": "l", "pad:b9": "m",
};
let serverBindings = {};
const localBindings = () => { try { return JSON.parse(localStorage.getItem("tb.keys") || "{}"); } catch (_) { return {}; } };
const bindings = () => ({ ...DEFAULT_BINDINGS, ...serverBindings, ...localBindings() });
function setLocalBinding(id, action) {
  const b = localBindings(); b[id] = action; localStorage.setItem("tb.keys", JSON.stringify(b));
}
async function loadServerBindings() {
  try { S.settings = await api("/api/settings"); serverBindings = S.settings.keymap || {}; } catch (_) {}
}

function inputId(ev) {
  if (ev.key && ev.key !== "Unidentified" && ev.key !== "Process") return ev.key.length === 1 ? ev.key.toLowerCase() : ev.key;
  if (ev.code) return "code:" + ev.code;
  return "kc:" + ev.keyCode;
}
function prettyInput(id) {
  const names = { ArrowUp: "▲", ArrowDown: "▼", ArrowLeft: "◀", ArrowRight: "▶", " ": "Space", Escape: "Esc" };
  if (names[id]) return names[id];
  const pad = /^pad:b(\d+)$/.exec(id);
  if (pad) return `Pad ${["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Start", "LS", "RS", "D▲", "D▼", "D◀", "D▶", "Home"][+pad[1]] || "btn " + pad[1]}`;
  const ax = /^pad:a(\d+)([+-])$/.exec(id);
  if (ax) return `Stick ${ax[1] % 2 ? (ax[2] === "-" ? "▲" : "▼") : (ax[2] === "-" ? "◀" : "▶")}`;
  return id.length === 1 ? id.toUpperCase() : id;
}

// learn mode: the next button pressed gets bound to `learning`
let learning = null, learnTimer = null;
function startLearn(action) {
  learning = action;
  $("learn").classList.remove("hidden");
  $("learn-action").textContent = ACTION_LABEL[action];
  let left = 10;
  $("learn-count").textContent = left;
  clearInterval(learnTimer);
  learnTimer = setInterval(() => { left -= 1; $("learn-count").textContent = left; if (left <= 0) stopLearn(); }, 1000);
}
function stopLearn() { learning = null; clearInterval(learnTimer); $("learn").classList.add("hidden"); }

function dispatch(id, ev) {
  $("remote-last").textContent = `Last button: ${prettyInput(id)}  (${id})`;
  if (wake(true)) return true;
  if (learning) {
    const action = learning; stopLearn();
    setLocalBinding(id, action);
    toast(`${prettyInput(id)} → ${ACTION_LABEL[action]}`);
    if (top() === "remotecfg") renderRemote();
    return true;
  }
  const action = bindings()[id];
  if (S.mode === "off") { powerOn(); return true; }
  // never lock yourself out of the remote setup screen
  const fallback = top() === "remotecfg" && { ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", Enter: "Enter", Escape: "Back" }[id];
  if (!action && !fallback) return false;
  return handleKey(action || fallback, ev);
}

// remote setup panel
let remoteSel = 0;
function remoteEntries() {
  const b = bindings();
  const byAction = {};
  for (const [id, act] of Object.entries(b)) if (act) (byAction[act] = byAction[act] || []).push(id);
  const rows = ACTIONS.map(([act, label]) => ({
    label, right: (byAction[act] || []).filter((id) => !id.startsWith("pad:a")).map(prettyInput).join("  ·  ") || "—",
    act: () => actionDialog(act),
  }));
  rows.push({ label: "Save this mapping as the default for all devices", act: saveRemoteDefault });
  rows.push({ label: "Reset this device to defaults", act: () => { localStorage.removeItem("tb.keys"); toast("Remote mapping reset"); renderRemote(); } });
  rows.push({ label: "Done", act: closeRemote });
  return rows;
}
function renderRemote() {
  fillList($("remote-list"), remoteEntries(), remoteSel);
  const li = $("remote-list").querySelector("li.sel"); if (li) li.scrollIntoView({ block: "nearest" });
}
function openRemote() {
  hideBanner();
  remoteSel = 0; renderRemote();
  S.layer.push("remotecfg"); $("remotecfg").classList.remove("hidden");
}
function closeRemote() { stopLearn(); S.layer = S.layer.filter((l) => l !== "remotecfg"); $("remotecfg").classList.add("hidden"); }
function actionDialog(act) {
  const ids = Object.entries(bindings()).filter(([, a]) => a === act).map(([id]) => id);
  openDialog(ACTION_LABEL[act], ids.length ? `Buttons: ${ids.map(prettyInput).join(", ")}` : "No buttons assigned", [
    { label: "Add a button…", act: () => { closeDialog(); startLearn(act); } },
    { label: "Replace all buttons with a new one…", act: () => { closeDialog(); ids.forEach((id) => setLocalBinding(id, "")); renderRemote(); startLearn(act); } },
    { label: "Remove all buttons", act: () => { ids.forEach((id) => setLocalBinding(id, "")); closeDialog(); renderRemote(); } },
    { label: "Back", act: closeDialog },
  ]);
}
async function saveRemoteDefault() {
  const merged = { ...serverBindings, ...localBindings() };
  await api("/api/settings", { method: "POST", body: { keymap: merged } });
  serverBindings = merged; localStorage.removeItem("tb.keys");
  toast("Saved as the default remote mapping for every device");
}
$("remote-list").addEventListener("click", (e) => {
  const li = e.target.closest("li"); if (!li) return;
  remoteSel = +li.dataset.i; remoteEntries()[remoteSel].act();
});

function handleKey(k, ev) {
  if (k === "Power") { if (S.mode === "off") powerOn(); else powerOff(); return true; }
  if (S.mode === "off") { powerOn(); return true; }
  const layer = top();
  if (/^[0-9]$/.test(k) && !["dialog", "menu", "dvr", "remotecfg", "search", "sports"].includes(layer) && S.mode === "live") { digit(k); return true; }
  if (S.digits && k === "Enter") { commitDigits(); return true; }

  if (layer === "dialog") {
    if (k === "Back") closeDialog(); else listNav($("dlg-list"), k, (_, i) => dlg.entries[i].act());
    return true;
  }
  if (layer === "remotecfg") {
    if (k === "Back") { closeRemote(); return true; }
    listNav($("remote-list"), k, (_, i) => { remoteSel = i; remoteEntries()[i].act(); });
    const items = [...$("remote-list").querySelectorAll("li")];
    const i = items.findIndex((li) => li.classList.contains("sel"));
    if (i >= 0) remoteSel = i;
    return true;
  }
  if (layer === "search") { searchKey(k); return true; }
  if (layer === "sports") { sportsKey(k); return true; }
  if (layer === "menu") {
    if (k === "Back" || k === "m") closeMenu(); else listNav($("menu-list"), k, (_, i) => menuShown[i].act());
    return true;
  }
  if (layer === "dvr") {
    if (k === "Back" || k === "d") closeDvr(); else listNav($("dvr-list"), k, (_, i) => dvrPick(i));
    return true;
  }
  if (layer === "guide") {
    switch (k) {
      case "ArrowUp": G.move(-1); break;
      case "ArrowDown": G.move(1); break;
      case "ArrowLeft": G.horiz(-1); break;
      case "ArrowRight": G.horiz(1); break;
      case "ChannelUp": G.page(-1); break;
      case "ChannelDown": G.page(1); break;
      case "Enter": G.ok(); break;
      case "r": G.record(); break;
      case "*": G.fav(); break;
      case "v": G.toggleView(); break;
      case "g": case "Back": G.close(); break;
      case "m": G.close(); openMenu(); break;
      case "d": G.close(); openDvr(); break;
      case "/": G.close(); openSearch(); break;
      case "t": G.close(); openSports(); break;
      case "u": toggleMute(); break;
      case "VolUp": setVolume(video.volume + 0.05); break;
      case "VolDown": setVolume(video.volume - 0.05); break;
      default: return false;
    }
    return true;
  }
  if (S.mode === "playback") {
    switch (k) {
      case " ": case "Enter": video.paused ? video.play() : video.pause(); showPlaybackBar(); break;
      case "ArrowLeft": video.currentTime = Math.max(0, video.currentTime - 10); showPlaybackBar(); break;
      case "ArrowRight": video.currentTime += 30; showPlaybackBar(); break;
      case "ChannelUp": video.currentTime = Math.max(0, video.currentTime - 300); showPlaybackBar(); break;
      case "ChannelDown": video.currentTime += 300; showPlaybackBar(); break;
      case "Back":
        if (!$("banner").classList.contains("hidden")) hideBanner(); else backToLive();
        break;
      case "s": backToLive(); break;
      case "g": hideBanner(); G.open(); break;
      case "v": hideBanner(); G.open(true); break;
      case "i":
        if (!$("banner").classList.contains("hidden")) { $("banner").classList.toggle("full"); clearTimeout(S.bannerTimer); S.bannerTimer = setTimeout(hideBanner, 12000); }
        else { $("playback").classList.add("hidden"); recInfo(); }
        break;
      case "/": openSearch(); break;
      case "t": openSports(); break;
      case "d": openDvr(); break;
      case "m": openMenu(); break;
      case "u": toggleMute(); break;
      case "VolUp": setVolume(video.volume + 0.05); break;
      case "VolDown": setVolume(video.volume - 0.05); break;
      case "f": toggleFull(); break;
      default: return false;
    }
    return true;
  }
  if (S.mode === "empty") {
    if (k === "Enter") openSetup();
    else if (k === "m") openMenu();
    else if (k === "f") toggleFull();
    else return false;
    return true;
  }
  // live TV
  switch (k) {
    case "ArrowUp": case "ChannelUp": chStep(1); break;
    case "ArrowDown": case "ChannelDown": chStep(-1); break;
    case "g": G.open(); break;
    case "v": G.open(true); break;
    case "*": toggleFav(curCh()); break;
    case "i": case "Enter":
      if (!$("banner").classList.contains("hidden") && k === "i") { $("banner").classList.toggle("full"); clearTimeout(S.bannerTimer); S.bannerTimer = setTimeout(hideBanner, 12000); }
      else showBanner(k === "i");
      break;
    case "Back": if (!$("banner").classList.contains("hidden")) hideBanner(); else if (S.lastIdx >= 0) tune(S.lastIdx); break;
    case "l": if (S.lastIdx >= 0) tune(S.lastIdx); break;
    case "m": openMenu(); break;
    case "d": openDvr(); break;
    case "/": openSearch(); break;
    case "t": openSports(); break;
    case "r": {
      const ch = curCh();
      if (ch && ch.feed) toast("Event feeds can't be recorded (they have no guide listing)");
      else if (ch) nowNext(ch.number, true).then((d) => d.now ? programDialog(ch, d.now) : toast("No guide information to record"));
      break;
    }
    case "u": toggleMute(); break;
    case "VolUp": case "ArrowRight": setVolume(video.volume + 0.05); break;
    case "VolDown": case "ArrowLeft": setVolume(video.volume - 0.05); break;
    case "f": toggleFull(); break;
    default: return false;
  }
  return true;
}
document.addEventListener("keydown", (ev) => {
  if (ev.ctrlKey || ev.altKey || ev.metaKey) return;
  try {
    if (top() === "search" && !learning && searchRawKey(ev)) return;
    if (dispatch(inputId(ev), ev)) ev.preventDefault();
  } catch (e) { report(`key ${ev.key}: ${e && (e.stack || e.message)}`); }
});

// gamepads: edge-triggered buttons + stick directions, with auto-repeat while held
(function gamepads() {
  const held = new Map();   // id -> next repeat time
  const poll = () => {
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    const down = new Set();
    for (const gp of pads) {
      if (!gp) continue;
      gp.buttons.forEach((b, i) => { if (b.pressed || b.value > 0.6) down.add(`pad:b${i}`); });
      gp.axes.slice(0, 4).forEach((v, i) => { if (v < -0.6) down.add(`pad:a${i}-`); else if (v > 0.6) down.add(`pad:a${i}+`); });
    }
    const t = performance.now();
    for (const id of down) {
      if (!held.has(id)) { held.set(id, t + 450); dispatch(id); }
      else if (t >= held.get(id) && !learning && /Arrow|Channel|Vol/.test(bindings()[id] || "")) { held.set(id, t + 140); dispatch(id); }
    }
    for (const id of [...held.keys()]) if (!down.has(id)) held.delete(id);
    requestAnimationFrame(poll);
  };
  window.addEventListener("gamepadconnected", (e) => toast(`Controller connected: ${e.gamepad.id.slice(0, 40)}`));
  requestAnimationFrame(poll);
})();
loadServerBindings();

// ---------------------------------------------------------------- sleep timer + screensaver
// Like a cable box: after `sleep_hours` without a button press, ask "Are you still watching?" and turn
// off after 2 minutes (stops the stream). When off, a screensaver starts after `screensaver_minutes`.
// Neither happens while a recording is running or a phone remote is open.
const IDLE = { last: Date.now(), ask: null, askEnd: 0, saver: null, offSince: Date.now() };
const ASK_SECS = 120;
const SLEEP_HOURS = [1, 2, 3, 4, 6, 8, 12], SAVER_MINUTES = [1, 2, 5, 10, 15, 30, 60];
const sleepH = () => { const h = S.settings.sleep_hours; return h === undefined || +h <= 0 ? 4 : +h; };
const saverM = () => { const m = S.settings.screensaver_minutes; return m === undefined || +m <= 0 ? 10 : +m; };
const sleepOn = () => S.settings.sleep_enabled !== false && +S.settings.sleep_hours !== 0;
const saverOn = () => S.settings.screensaver_enabled !== false && +S.settings.screensaver_minutes !== 0;
const sleepMs = () => sleepOn() ? sleepH() * 3600e3 : 0;
const saverMs = () => saverOn() ? saverM() * 60e3 : 0;
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const stepNext = (list, cur) => list[(list.indexOf(cur) + 1) % list.length] ?? list[0];

// Menu › Sleep timer & screensaver: OK on a line changes it, saved for every screen
async function saveIdleSetting(change) {
  const old = { ...S.settings };
  Object.assign(S.settings, change);
  try { S.settings = { ...S.settings, ...(await api("/api/settings", { method: "POST", body: change })) }; }
  catch (e) { S.settings = old; toast(`Not saved: ${e.message}`); }
  IDLE.last = Date.now();
}
function sleepDialog(sel = 0) {
  const entries = [
    { label: `Sleep timer: ${sleepOn() ? "On" : "Off"}`, right: "OK to switch",
      act: async () => { await saveIdleSetting({ sleep_enabled: !sleepOn(), sleep_hours: sleepH() }); sleepDialog(0); } },
    { label: `Turn off after: ${plural(sleepH(), "hour")}${sleepOn() ? "" : "  (timer is off)"}`, right: "OK to change",
      act: async () => { await saveIdleSetting({ sleep_hours: stepNext(SLEEP_HOURS, sleepH()) }); sleepDialog(1); } },
    { label: `Screensaver: ${saverOn() ? "On" : "Off"}`, right: "OK to switch",
      act: async () => { await saveIdleSetting({ screensaver_enabled: !saverOn(), screensaver_minutes: saverM() }); sleepDialog(2); } },
    { label: `Screensaver starts after: ${plural(saverM(), "minute")}${saverOn() ? "" : "  (screensaver is off)"}`, right: "OK to change",
      act: async () => { await saveIdleSetting({ screensaver_minutes: stepNext(SAVER_MINUTES, saverM()) }); sleepDialog(3); } },
    { label: "Done", act: closeDialog },
  ];
  openDialog("Sleep timer & screensaver",
    "The sleep timer asks “Are you still watching?” and turns the TV off when nobody has pressed a button for that long. The screensaver starts once the TV is off. Neither happens while a recording is running or a phone remote is open. These settings apply to every screen.",
    entries);
  fillList($("dlg-list"), entries, sel);
}
async function idleBusy() {
  try { return (await api("/api/idle")).busy; } catch (_) { return false; }
}
// a button press / remote command / tap. Returns true when it only woke the screen.
function wake(fromButton) {
  IDLE.last = Date.now();
  if (IDLE.ask) { stopAsk(); return fromButton; }
  if (IDLE.saver) {
    stopSaver();
    if (fromButton && S.mode === "off") powerOn();
    return fromButton;
  }
  return false;
}
function startAsk() {
  IDLE.askEnd = Date.now() + ASK_SECS * 1000;
  $("stillthere").classList.remove("hidden");
  const tick = async () => {
    const left = Math.max(0, Math.round((IDLE.askEnd - Date.now()) / 1000));
    $("st-count").textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    if (left > 0) return;
    stopAsk();
    if (await idleBusy()) { IDLE.last = Date.now(); return; }
    report("Sleep timer: turned off after no button presses");
    powerOff(); startSaver();
  };
  tick(); IDLE.ask = setInterval(tick, 1000);
}
function stopAsk() { clearInterval(IDLE.ask); IDLE.ask = null; $("stillthere").classList.add("hidden"); }
function startSaver() {
  if (IDLE.saver) return;
  $("saver").classList.remove("hidden");
  const box = $("saver-box");
  const move = () => {   // drift around the screen so nothing burns in
    $("saver-time").textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    $("saver-date").textContent = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
    box.style.opacity = 0;
    setTimeout(() => {
      box.style.left = `${5 + Math.random() * Math.max(0, 90 - box.offsetWidth / innerWidth * 100)}%`;
      box.style.top = `${5 + Math.random() * Math.max(0, 90 - box.offsetHeight / innerHeight * 100)}%`;
      box.style.opacity = 1;
    }, 1500);
  };
  move(); IDLE.saver = setInterval(move, 20000);
}
function stopSaver() { clearInterval(IDLE.saver); IDLE.saver = null; $("saver").classList.add("hidden"); }
setInterval(async () => {
  const idle = Date.now() - IDLE.last;
  if (S.mode !== "off") IDLE.offSince = Date.now();
  if ((S.mode === "live" || S.mode === "playback") && !IDLE.ask && sleepMs() > 0 && idle >= sleepMs()) {
    if (await idleBusy()) return;            // recording or a phone remote open: keep going
    if (!IDLE.ask && Date.now() - IDLE.last >= sleepMs()) startAsk();
  } else if (IDLE.ask && await idleBusy()) {
    stopAsk(); IDLE.last = Date.now();
  } else if (S.mode === "off" && saverMs() > 0) {
    const offFor = Math.min(idle, Date.now() - IDLE.offSince);
    if (!IDLE.saver && offFor >= saverMs() && !(await idleBusy())) startSaver();
    else if (IDLE.saver && await idleBusy()) stopSaver();
  }
}, 15000);
$("saver").addEventListener("click", () => wake(true));
$("stillthere").addEventListener("click", () => wake(true));
$("tv").addEventListener("touchstart", () => { IDLE.last = Date.now(); }, { passive: true });

// mouse / touch
let idleTimer, lastTouch = 0;
function hideRemoteLater(ms) {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => { $("tv").classList.add("idle"); $("remote").classList.add("hidden"); }, ms);
}
document.addEventListener("mousemove", () => {
  IDLE.last = Date.now();
  if (IDLE.saver) stopSaver();
  // iOS Safari fires a fake mousemove after every tap; if that changes what's on screen it drops the
  // tap's click, so touch input manages the remote bar itself (see touchend below)
  if (Date.now() - lastTouch < 1000) return;
  $("tv").classList.remove("idle");
  if (S.mode !== "off" && !top()) $("remote").classList.remove("hidden");
  hideRemoteLater(3000);
});
$("power").addEventListener("click", () => powerOn());
$("static").addEventListener("click", () => { if (S.mode === "empty" && !top()) openSetup(); });
$("remote").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  // picking an option puts the bar away (CH up/down keep it, for flipping through channels)
  if (b.dataset.k !== "ChannelUp" && b.dataset.k !== "ChannelDown") { clearTimeout(idleTimer); $("remote").classList.add("hidden"); }
  handleKey(b.dataset.k);
});
// tap / click on the picture while watching: show the button bar and the info banner together,
// or put both away if either is showing
function toggleOverlay() {
  const r = $("remote"), shown = !r.classList.contains("hidden") || !$("banner").classList.contains("hidden");
  if (shown) {
    r.classList.add("hidden"); clearTimeout(idleTimer);
    hideBanner(); clearTimeout(S.bannerTimer); $("playback").classList.add("hidden");
    return;
  }
  r.classList.remove("hidden"); hideRemoteLater(6000);
  if (S.mode === "live") showBanner();
  else if (S.mode === "playback") { $("playback").classList.add("hidden"); recInfo(); }
}
$("learn").addEventListener("click", stopLearn);
$("back").addEventListener("click", () => { if (top()) handleKey("Back"); });
setInterval(() => { $("back").classList.toggle("hidden", !(isPhone() && top() && top() !== "learn")); }, 250);
video.addEventListener("click", () => {
  if (top() || Date.now() - lastTouch < 1000) return;     // touch taps are handled in touchend below
  if (S.mode === "live" || S.mode === "playback") toggleOverlay();
});
video.addEventListener("dblclick", toggleFull);
$("g-rows").addEventListener("click", (e) => {
  const cell = e.target.closest(".g-p"), row = e.target.closest(".g-row");
  if (!row) return;
  const i = +row.dataset.i;
  if (e.target.closest(".g-fav")) { toggleFav(G.ch(i)); return; }
  if (cell) {
    const s = +cell.dataset.s;
    if (G.row === i && G.selected() && G.selected().start === s) { G.ok(); return; }
    G.row = i; G.focusT = Math.max(s, G.win); G.render();
  } else { tune(G.list[i]); G.close(); }
});
$("g-rows").addEventListener("wheel", (e) => { e.preventDefault(); G.move(e.deltaY > 0 ? 1 : -1); }, { passive: false });
// touch: drag up/down to scroll channels, sideways to scroll time. The move/end listeners go on the
// touched element itself because render() replaces it, and a detached target no longer bubbles.
$("g-rows").addEventListener("touchstart", (e) => {
  const t0 = e.touches[0], el = e.target, rowsEl = $("g-rows");
  const st = { x: t0.clientX, y: t0.clientY, top: G.top, win: G.win, axis: null };
  const move = (ev) => {
    const t = ev.touches[0], dx = t.clientX - st.x, dy = t.clientY - st.y;
    if (!st.axis) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 10) return;
      st.axis = Math.abs(dy) >= Math.abs(dx) ? "y" : "x";
    }
    ev.preventDefault();
    if (st.axis === "y") G.scrollTo(st.top - Math.round(dy / (rowsEl.clientHeight / G.rows)));
    else {
      const w = (rowsEl.querySelector(".g-progs") || rowsEl).clientWidth;
      G.timeTo(st.win - Math.round(dx / (w * HALF / G.span)) * HALF);
    }
  };
  const end = () => { el.removeEventListener("touchmove", move); el.removeEventListener("touchend", end); el.removeEventListener("touchcancel", end); };
  el.addEventListener("touchmove", move, { passive: false });
  el.addEventListener("touchend", end); el.addEventListener("touchcancel", end);
}, { passive: true });
let touchY = null;
$("tv").addEventListener("touchstart", (e) => { touchY = e.touches[0].clientY; lastTouch = Date.now(); }, { passive: true });
$("tv").addEventListener("touchend", (e) => {
  lastTouch = Date.now();
  if (touchY === null || top()) { touchY = null; return; }
  const dy = e.changedTouches[0].clientY - touchY; touchY = null;
  // taps on the remote bar (or other tap targets) are clicks for them, not "toggle the bar"
  if (e.target.closest && e.target.closest("#remote")) { hideRemoteLater(5000); return; }
  if (e.target.closest && e.target.closest("#power, #tapplay, #saver, #stillthere, #learn")) return;
  if (Math.abs(dy) > 80) chStep(dy < 0 ? 1 : -1);
  else if (S.mode === "live" || S.mode === "playback") toggleOverlay();
}, { passive: true });

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
})();
