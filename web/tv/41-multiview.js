// ---------------------------------------------------------------- multiview
// Two to four live channels on one screen. Slot 1 is the main picture (#video, S.player, tune()); the
// other tiles (S.mv.tiles, slot 2-4) each play with their own client id, "<CID>~<slot>", so a tile only
// ever replaces its own stream and every tile counts against the playlist's connection limit.
//
// Every channel change in multiview goes through the server's planner (/api/mv/plan): when the channel
// you pick is only on a playlist whose connections your tiles are using, a tile that's also on another
// playlist moves there first. When nothing can move, it says what's using the connections and offers
// to swap out one of your tiles instead.
//
// Remote: W opens the Multiview dialog (as the on-screen Multiview button does), arrows move the highlight
// (the highlighted tile has the sound), CH+/CH- and numbers change the highlighted tile's channel, Back
// leaves multiview keeping it. OK swaps the big and small pictures in picture in picture, makes the
// highlighted one big in "one big, two/three small", else watches it full screen. With a mouse, each
// picture's corner buttons change its channel or take it off the screen.
// Swapping only moves the pictures around the screen (S.mv.order): no stream restarts.
//
// On Demand picked in multiview plays in the highlighted small picture (t.vod), keeping the rest; Back
// takes it full screen in the On Demand player, at the same spot.

// pictures at most: what the playlists' connections allow (the server's multiview_max: 1 = none), and
// on phones two; iPhone / iPad play HLS natively, one video at a time. Setup can turn multiview off.
const mvEnabled = () => S.settings.multiview_enabled !== false;
const mvMax = () => (!mvEnabled() || useHls() || !window.mpegts || !mpegts.getFeatureList().mseLivePlayback ? 1
  : Math.min(isPhone() ? 2 : 4, +S.settings.multiview_max || 4));
const mvOn = () => S.mv.tiles.length > 0;
const mvTile = (slot) => S.mv.tiles.find((t) => t.slot === slot);
const mvCid = (slot) => (slot === 1 ? CID : `${CID}~${slot}`);
const mvMainCh = () => (S.mode === "live" ? S.feedCh || S.channels[S.idx] || null : null);
const mvAdding = () => !!S.mv.add && Date.now() - S.mv.add < 5 * 60000;
const mvCanAdd = () => S.mode === "live" && !!mvMainCh() && S.mv.tiles.length + 1 < mvMax();
function mvFocusCh() { const t = S.mv.focus > 1 && mvTile(S.mv.focus); return t ? t.ch : null; }
// where the sound comes from: the highlighted tile
function soundEl() { const t = S.mv.focus > 1 && mvTile(S.mv.focus); return t ? t.v : video; }
function mvFreeSlot() { for (let s = 2; s <= 4; s++) if (!mvTile(s)) return s; return 0; }

// an event / PPV feed as playFeed shows it
function mvFeedCh(f) {
  return { number: "", badge: f.kind === "ppv" ? "PPV" : "LIVE", name: f.name, label: f.name, logo: f.logo || "",
           feed: f.key, group: f.group, mapped: true, alts: 0, epg: null };
}
const mvFeedOf = (ch) => ({ key: ch.feed, name: ch.name, logo: ch.logo, group: ch.group, kind: ch.badge === "PPV" ? "ppv" : "events" });
const mvName = (ch) => `${ch.number || ch.badge || ""} ${ch.name}`.trim();

// ---- layouts: how many pictures each holds. One with room for more than are on screen shows
// "+ Add a channel" in its spare places. S.mv.pref: the layout picked for each number of pictures.
const MV_SIZE = { pip: 2, split: 2, big: 3, big3: 4, grid: 4 };
const MV_LAYOUT_NAMES = { pip: "Picture in picture", split: "Side by side", big: "One big, two small", big3: "One big, three small", grid: "Four squares" };
const mvBigLayout = (l) => l === "big" || l === "big3";      // one big place, the rest small
const mvCount = () => S.mv.tiles.length + 1;
// the layouts that hold `n` pictures and no more places than this screen may fill
const mvFits = (n) => Object.keys(MV_SIZE).filter((l) => MV_SIZE[l] >= n && MV_SIZE[l] <= Math.max(n, mvMax()));
const mvLayouts = () => (mvOn() ? mvFits(mvCount()) : []);
const mvLayout = () => { const l = mvLayouts(), p = S.mv.pref[mvCount()]; return l.includes(p) ? p : l[0] || ""; };
// use layout `l` from now until its places are full (so adding to its spare places keeps it)
function mvSetLayout(l) {
  for (let n = Math.max(2, mvCount()); n <= MV_SIZE[l]; n++) S.mv.pref[n] = l;
  try { localStorage.setItem("tb.mvLayout", JSON.stringify(S.mv.pref)); } catch (_) {}
  mvRender();
}
function mvNextLayout() {
  const l = mvLayouts(); if (l.length < 2) return;
  mvSetLayout(l[(l.indexOf(mvLayout()) + 1) % l.length]);
}
try { Object.assign(S.mv.pref, JSON.parse(localStorage.getItem("tb.mvLayout") || "{}") || {}); } catch (_) {}

// ---- the frames on screen: one per picture (the main one's sits over #video), plus "+ Add" in spare places
const mvBox = $("mv");
const mvMainFrame = mvFrame(1);
mvBox.appendChild(mvMainFrame.el);
const mvAddFrames = [0, 1, 2].map(() => {
  const el = document.createElement("div");
  el.className = "mv-f mv-add hidden";
  el.innerHTML = '<div class="mv-add-in"><div class="mv-plus">+</div><div>Add a channel</div><small class="mv-room"></small></div>';
  el.addEventListener("click", () => mvAddPrompt());
  mvBox.appendChild(el);
  return el;
});
const mvAddFrame = mvAddFrames[0];          // new tiles go in before it, so the spare places stay on top

function mvFrame(slot) {
  const el = document.createElement("div");
  el.className = "mv-f"; el.dataset.slot = slot;
  el.innerHTML = '<div class="mv-msg hidden"></div><div class="mv-lab"><span class="mv-snd" aria-label="Sound">&#128266;</span> <span class="mv-ch"></span><span class="mv-now"></span></div>'
    + '<div class="mv-btns"><button class="mv-chg" title="Change channel"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h13l-3-3M20 16H7l3 3"/></svg>Change</button>'
    + '<button class="mv-x" title="Take it off the screen" aria-label="Take it off the screen"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>';
  // the corner buttons: change this picture's channel, or take it off the screen
  el.querySelector(".mv-chg").addEventListener("click", (e) => { e.stopPropagation(); if (mvOn()) mvPicker(undefined, slot); });
  el.querySelector(".mv-x").addEventListener("click", (e) => { e.stopPropagation(); if (mvOn()) mvRemove(slot); });
  el.addEventListener("dblclick", (e) => { if (e.target.closest(".mv-btns")) e.stopImmediatePropagation(); }, true);
  el.addEventListener("click", () => { if (mvOn()) mvFocus(slot); });
  el.addEventListener("dblclick", () => { if (mvOn()) { mvFocus(slot); mvOk(); } });
  return { el, lab: el.querySelector(".mv-ch"), now: el.querySelector(".mv-now"), msg: el.querySelector(".mv-msg") };
}

// the pictures' slots in place order (place 1 is the big one): as swapped, then any new tile at the end
function mvOrder() {
  const slots = [1, ...S.mv.tiles.map((t) => t.slot).sort()];
  const o = S.mv.order.filter((s) => slots.includes(s));
  return [...o, ...slots.filter((s) => !o.includes(s))];
}
function mvRender() {
  const on = mvOn(), tv = $("tv");
  mvBox.classList.toggle("hidden", !on);
  mvBarSync();
  if (!on) {
    delete tv.dataset.mv; delete video.dataset.pos; delete $("static").dataset.pos;
    S.mv.order = []; clearInterval(S.mv.labTimer); S.mv.labTimer = null; return;
  }
  const layout = mvLayout();
  tv.dataset.mv = layout;
  const order = S.mv.order = mvOrder();
  // the main picture (and its No Signal) sits in its frame's place, like the tiles' videos in theirs
  video.dataset.pos = $("static").dataset.pos = mvMainFrame.el.dataset.pos = order.indexOf(1) + 1;
  for (const t of S.mv.tiles) t.el.dataset.pos = order.indexOf(t.slot) + 1;
  const spare = mvCanAdd() ? MV_SIZE[layout] - order.length : 0;
  mvAddFrames.forEach((el, i) => {
    el.classList.toggle("hidden", i >= spare);
    if (i < spare) el.dataset.pos = order.length + 1 + i;
  });
  if (spare > 0) mvRoom();
  if (!S.mv.tiles.some((t) => t.slot === S.mv.focus)) S.mv.focus = 1;
  for (const f of [mvMainFrame, ...S.mv.tiles]) f.el.classList.toggle("focus", +f.el.dataset.slot === S.mv.focus);
  mvLabels();
  if (!S.mv.labTimer) S.mv.labTimer = setInterval(mvLabels, 60000);
}
// each picture's channel and the show on it now
function mvLabels() {
  const set = (f, ch) => {
    if (!ch) { f.lab.textContent = ""; f.now.textContent = ""; return; }
    f.lab.textContent = mvName(ch);
    if (ch.feed || !ch.epg) { f.now.textContent = ch.group ? ` · ${ch.group}` : ""; return; }
    nowNext(ch.number).then((d) => { if (f.lab.textContent === mvName(ch)) f.now.textContent = d.now && d.now.title ? ` · ${d.now.title}` : ""; }).catch(() => {});
  };
  set(mvMainFrame, mvMainCh());
  for (const t of S.mv.tiles) set(t, t.ch);
}
async function mvRoom() {
  let r = [];
  try { r = await api("/api/mv/room"); } catch (_) { return; }
  const text = r.map((s) => `${s.source}: ${Math.max(0, s.limit - s.used)} of ${s.limit} free`).join(" · ");
  for (const el of mvAddFrames) el.querySelector(".mv-room").textContent = text;
}

// ---- focus and sound
function mvFocus(slot) {
  if (slot !== 1 && !mvTile(slot)) return;
  const was = soundEl(), vol = was.volume, muted = was.muted;
  S.mv.focus = slot;
  video.muted = slot !== 1 || muted;
  for (const t of S.mv.tiles) { t.v.volume = vol; t.v.muted = t.slot !== slot || muted; }
  if (slot === 1) video.volume = vol;
  mvRender();
  if (!$("banner").classList.contains("hidden")) showBanner();
}
// where each place of a layout is (left, top, width, height in % of the screen), as tv.css draws it; the
// arrow keys go by these rather than by the frames, which may still be sliding into place
const MV_PLACES = {
  split: [[0, 25, 50, 50], [50, 25, 50, 50]], pip: [[0, 0, 100, 100], [67, 4, 30, 30]],
  big: [[0, 16.7, 66.7, 66.7], [66.7, 16.7, 33.3, 33.3], [66.7, 50, 33.3, 33.3]],
  big3: [[0, 12.5, 75, 75], [75, 12.5, 25, 25], [75, 37.5, 25, 25], [75, 62.5, 25, 25]],
  grid: [[0, 0, 50, 50], [50, 0, 50, 50], [0, 50, 50, 50], [50, 50, 50, 50]],
};
const MV_PHONE_SPLIT = [[0, 12, 100, 36], [0, 50, 100, 36]];
// the picture next to the highlighted one in direction `k` (an arrow key)
function mvMove(k) {
  const frames = [mvMainFrame, ...S.mv.tiles];
  const cur = frames.find((f) => +f.el.dataset.slot === S.mv.focus) || mvMainFrame;
  const layout = mvLayout();
  const places = layout === "split" && isPhone() && innerHeight > innerWidth ? MV_PHONE_SPLIT : MV_PLACES[layout] || [];
  const mid = (f) => { const p = places[+f.el.dataset.pos - 1] || [0, 0, 0, 0]; return [p[0] + p[2] / 2, p[1] + p[3] / 2]; };
  const [cx, cy] = mid(cur);
  let best = null, bestD = Infinity;
  for (const f of frames) {
    if (f === cur) continue;
    const [fx, fy] = mid(f), x = fx - cx, y = fy - cy;
    const along = { ArrowRight: x, ArrowLeft: -x, ArrowDown: y, ArrowUp: -y }[k];
    if (along <= 1) continue;
    const d = along + 2 * Math.abs(k === "ArrowLeft" || k === "ArrowRight" ? y : x);
    if (d < bestD) { bestD = d; best = f; }
  }
  if (best) mvFocus(+best.el.dataset.slot);
}

// remote keys on live TV while multiview is on (the rest: 60-input-remote.js)
function mvKey(k) {
  const vt = mvTile(S.mv.focus);
  if (vt && vt.vod && k === " ") { mvVodPause(vt); return true; }
  switch (k) {
    case "ArrowUp": case "ArrowDown": case "ArrowLeft": case "ArrowRight": hideBanner(); mvMove(k); return true;
    case "Enter": hideBanner(); mvOk(); return true;
    case "Back": if (!$("banner").classList.contains("hidden")) hideBanner(); else mvEnd(); return true;
    default: return false;
  }
}

// ---- swapping pictures: only their places change (no stream restarts); the highlight and the sound go
// with the picture that's now big
function mvCanSwap() {
  const l = mvLayout();
  return l === "pip" || (mvBigLayout(l) && mvOrder()[0] !== S.mv.focus);
}
function mvSwap(slot) {
  const o = mvOrder(), i = o.indexOf(slot);
  if (i <= 0) return;
  [o[0], o[i]] = [o[i], o[0]];
  S.mv.order = o;
  mvFocus(slot);
}
// OK (or a double click) on the highlighted picture
function mvOk() {
  const l = mvLayout();
  if (l === "pip") mvSwap(mvOrder()[1]);
  else if (mvBigLayout(l) && mvOrder()[0] !== S.mv.focus) mvSwap(S.mv.focus);
  else mvEnd();
}

// ---- a tile's own player (the main picture uses play() in 10-playback.js)
function mvKill(t) {
  if (t.vod) { mvVodSave(t); clearTimeout(t.vod.endT); }
  clearTimeout(t.wd);
  const p = t.player; t.player = null;
  if (p) for (const step of ["pause", "unload", "detachMediaElement", "destroy"]) { try { p[step](); } catch (_) {} }
  t.v.removeAttribute("src");
  try { t.v.load(); } catch (_) {}
}
function mvNewTile(slot) {
  const f = mvFrame(slot), v = document.createElement("video");
  v.playsInline = true; v.muted = true; v.volume = soundEl().volume;
  f.el.prepend(v);
  mvBox.insertBefore(f.el, mvAddFrame);
  const t = { ...f, slot, v, ch: null, alt: 0, player: null, retries: 0 };
  S.mv.tiles.push(t);
  return t;
}
function mvDrop(t) {
  mvKill(t); t.el.remove();
  S.mv.tiles = S.mv.tiles.filter((x) => x !== t);
}
function mvMsg(t, text) { t.msg.textContent = text || ""; t.msg.classList.toggle("hidden", !text); }

// Play `ch` (feed `alt`) in tile `t`. Resolves once it plays or has given up, so the next step of a plan
// runs after this tile's stream is on the server (and its old one stopped).
function mvPlay(t, ch, alt) {
  mvKill(t);
  t.ch = ch; t.alt = alt; t.vod = null; mvMsg(t, "");
  mvLabels();
  const key = ch.feed ? `f:${ch.feed}` : `${ch.number}:${alt}`;
  const mode = h264Keys.has(key) ? "h264" : "";
  const p = mpegts.createPlayer({ type: "mpegts", isLive: true, url: streamUrl(ch, alt, false, mode, mvCid(t.slot)) },
    { enableWorker: true, lazyLoad: false, enableStashBuffer: true, stashInitialSize: 256 * 1024,
      autoCleanupSourceBuffer: true, liveBufferLatencyChasing: false });
  t.player = p;
  p.attachMediaElement(t.v);
  return new Promise((resolve) => {
    let played = false, done = false;
    const settle = () => { clearTimeout(cap); resolve(); };
    const cap = setTimeout(settle, 12000);
    const fail = () => {
      if (done || t.player !== p) return;
      done = true; settle();
      mvFailed(t, ch, alt, played);
    };
    // a picture this browser can't show, or (HEVC) can't decode fast enough and stutters: the server converts
    // it to H.264, as for the full-screen picture (codecFallback)
    const codecErr = codecFallback(p, key, mode, () => { if (t.player === p) { done = true; mvPlay(t, ch, alt).then(settle); } },
                                   t.v, () => t.player === p);
    p.on(mpegts.Events.ERROR, (type) => {
      if (t.player !== p) return;
      if (!done && codecErr(type)) return;
      fail();
    });
    p.on(mpegts.Events.LOADING_COMPLETE, fail);
    t.v.addEventListener("playing", () => { if (t.player !== p) return; played = true; t.retries = 0; clearTimeout(t.wd); mvMsg(t, ""); settle(); }, { once: true });
    p.load();
    const pr = p.play(); if (pr && pr.catch) pr.catch(() => {});
    t.wd = setTimeout(() => { if (t.player === p && t.v.readyState < 3) fail(); }, 20000);
  });
}
// ---- On Demand in a small picture
// where On Demand plays in multiview: the highlighted small picture, else the first small one
// (or, picked from "Pick the channel to add" › From On Demand, a new picture)
const mvVodAdding = () => !!S.mv.vodAdd && Date.now() - S.mv.vodAdd < 5 * 60000;
function mvVodTarget() {
  if (S.mode !== "live") return null;
  if (mvVodAdding()) {
    S.mv.vodAdd = 0;
    if (mvCanAdd()) return mvNewTile(mvFreeSlot());
  }
  if (!mvOn()) return null;
  return mvTile(S.mv.focus) || mvOrder().map(mvTile).find(Boolean) || null;
}
// a movie as a picture's "channel" (labels, banner): like an event feed, no number
const mvVodCh = (it) => ({ number: "", badge: "▶", name: it.title, label: it.title, logo: "", feed: `vod:${it.id}`,
                           group: "On Demand", mapped: true, alts: 0, epg: null });
const mvVodPos = (t) => (t.vod ? t.vod.base + (t.v.currentTime || 0) : 0);
function mvVodSave(t) {
  const v = t.vod; if (!v || v.done || v.it.catchup) return;      // catch-up shows aren't resumed later
  const pos = mvVodPos(t); if (pos < 5) return;
  fetch("/api/vod/progress", { method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
                               body: JSON.stringify({ id: v.it.id, position: pos, duration: v.it.duration || 0 }) }).catch(() => {});
}
function mvVodPause(t) {
  if (t.v.paused) { const pr = t.v.play(); if (pr && pr.catch) pr.catch(() => {}); mvMsg(t, ""); }
  else { t.v.pause(); mvVodSave(t); mvMsg(t, "Paused · Play/Pause to go on"); }
}
// play `it` from `at` seconds in tile `t` (its own client id, so the other pictures keep playing)
function mvVodPlay(t, it, at, retries = 0) {
  mvKill(t);
  if (it.duration) at = Math.min(at, it.duration - 5);
  if (it.catchup) at = Math.floor(at / 60) * 60;          // the archive starts on the minute
  at = Math.max(0, at);
  t.ch = mvVodCh(it); t.alt = 0; t.vod = { it, base: at, retries, done: false }; mvMsg(t, "");
  mvRender();
  const v = t.vod, key = `v:${it.id}`, mode = h264Keys.has(key) ? "h264" : "";
  const url = `${location.origin}/api/vod/stream/${vodPath(it.id)}?ss=${at.toFixed(1)}&client=${mvCid(t.slot)}` + (mode ? `&mode=${mode}` : "");
  const p = mpegts.createPlayer({ type: "mpegts", isLive: true, url },
    { enableWorker: true, lazyLoad: false, enableStashBuffer: true, stashInitialSize: 256 * 1024,
      autoCleanupSourceBuffer: true, liveBufferLatencyChasing: false });
  t.player = p;
  p.attachMediaElement(t.v);
  // it stopped: the end of the title, or the provider dropped it (then reconnect where it was)
  const stopped = () => {
    if (t.player !== p || t.vod !== v) return;
    const pos = mvVodPos(t), dur = it.duration || 0;
    if (dur && pos >= dur - 60) { mvVodSave(t); v.done = true; mvKill(t); mvMsg(t, `${it.title}: the end`); return; }
    if (v.retries < 3) {
      mvMsg(t, "Reconnecting…");
      setTimeout(() => { if (t.player === p && t.vod === v) mvVodPlay(t, it, pos, v.retries + 1); }, 1500);
    } else { mvKill(t); mvMsg(t, `Can't play ${it.title} right now`); }
  };
  // can't show the picture, or it stutters (slow HEVC): converted to H.264 from where it is, as in full screen
  const codecErr = codecFallback(p, key, mode, () => { if (t.player === p && t.vod === v) mvVodPlay(t, it, mvVodPos(t), v.retries); },
                                 t.v, () => t.player === p);
  p.on(mpegts.Events.ERROR, (type) => {
    if (t.player !== p) return;
    if (codecErr(type)) return;
    stopped();
  });
  p.on(mpegts.Events.LOADING_COMPLETE, () => {
    if (t.player !== p) return;
    const b = t.v.buffered, ahead = b.length ? b.end(b.length - 1) - t.v.currentTime : 0;
    v.endT = setTimeout(stopped, (Math.max(0, ahead) + 1) * 1000);
  });
  t.v.addEventListener("playing", () => { if (t.player === p) { clearTimeout(t.wd); v.retries = 0; } }, { once: true });
  p.load();
  const pr = p.play(); if (pr && pr.catch) pr.catch(() => {});
  t.wd = setTimeout(() => { if (t.player === p && t.v.readyState < 3) stopped(); }, 40000);
}
// keep each movie's spot, as the full-screen player does
setInterval(() => { for (const t of S.mv.tiles) if (t.vod && t.player && !t.v.paused) mvVodSave(t); }, 15000);

// a tile's stream ended or never started: like the main picture, reconnect, then the backup feeds,
// then say why (stopped from Setup, or every connection in use)
async function mvFailed(t, ch, alt, played) {
  const p = t.player;
  let stopped = false;
  try { stopped = (await api(`/api/stopped?client=${encodeURIComponent(mvCid(t.slot))}`)).stopped; } catch (_) {}
  if (t.player !== p || !mvTile(t.slot)) return;
  if (stopped) { mvKill(t); mvMsg(t, "Stopped from Setup › Tuners"); return; }
  if (played && ++t.retries <= 3) { mvMsg(t, "Reacquiring signal…"); setTimeout(() => { if (t.player === p) mvPlay(t, ch, alt); }, 1500); return; }
  if (!played && !ch.feed && alt < (ch.alts || 0)) { mvPlay(t, ch, alt + 1); return; }
  mvKill(t);
  mvMsg(t, ch.feed ? "Not live right now" : "No Signal");
  let r;
  try { r = await api(`/api/busy?${new URLSearchParams({ ...(ch.feed ? { key: ch.feed } : { number: ch.number }), client: mvCid(t.slot) })}`); } catch (_) { return; }
  if (r.full && r.full.length && t.ch === ch && !t.player) mvMsg(t, `All connections in use: ${r.full[0].source} allows ${r.full[0].limit}`);
}

// the main picture started playing (or gave up): resolves within 12 seconds either way
function mvMainSettled() {
  return new Promise((resolve) => {
    const done = () => { clearTimeout(t); video.removeEventListener("playing", done); resolve(); };
    const t = setTimeout(done, 12000);
    video.addEventListener("playing", done);
  });
}
function mvMainPlay(ch, alt) {
  const settled = mvMainSettled();
  if (ch.feed) playFeed(mvFeedOf(ch), true);
  else tune(S.byNum.get(ch.number), alt, true);
  mvRender();
  return settled;
}

// what this screen's pictures play now, for the planner
function mvState() {
  const out = [], main = mvMainCh();
  if (main) out.push({ slot: 1, number: main.feed ? null : main.number, key: main.feed || null, alt: S.feedCh ? 0 : S.alt });
  for (const t of S.mv.tiles) if (t.ch && !t.vod) out.push({ slot: t.slot, number: t.ch.feed ? null : t.ch.number, key: t.ch.feed || null, alt: t.alt });
  return out;
}

// Put channel `ch` in picture `slot` (a new tile, or replacing what's there), moving other tiles to
// another playlist first when the planner says so. Resolves true when it went ahead.
async function mvPut(slot, ch) {
  if (!ch || !ch.mapped) { toast(`${ch ? mvName(ch) : "That channel"} is not available`); return false; }
  const seq = ++S.mv.seq;
  let r;
  try {
    r = await api("/api/mv/plan", { method: "POST", body: { client: CID, tiles: mvState(), slot,
      want: ch.feed ? { key: ch.feed } : { number: ch.number } } });
  } catch (e) { toast(`Can't play that: ${e.message}`, 4000); return false; }
  if (seq !== S.mv.seq) return false;
  if (!r.ok) { mvNoRoom(r, slot, ch); return false; }
  if (r.moves.length) {
    toast(`${r.note}…`, 5000);
    // the planner counted the picture being replaced as gone: stop its stream first, so a tile moving
    // onto the playlist it was using finds the connection free. /api/stop ends it on the server now,
    // rather than whenever the server notices the browser hung up.
    const old = slot > 1 && mvTile(slot);
    if (old) { mvKill(old); mvMsg(old, ""); }
    else if (slot === 1) destroyPlayer();
    try { await api("/api/stop", { method: "POST", body: { client: mvCid(slot) } }); } catch (_) {}
    if (seq !== S.mv.seq) return false;
  }
  for (const m of r.moves) {
    if (m.slot === 1) { const main = mvMainCh(); if (main && !main.feed) await mvMainPlay(main, m.alt); }
    else { const t = mvTile(m.slot); if (t) await mvPlay(t, t.ch, m.alt); }
    if (seq !== S.mv.seq) return false;
  }
  if (slot === 1) await mvMainPlay(ch, r.alt);
  else {
    const t = mvTile(slot) || mvNewTile(slot);
    mvRender();
    await mvPlay(t, ch, r.alt);
  }
  mvRender();
  return true;
}

// No playlist that has the channel has a connection left, and none of the tiles could move. Say what's
// using them, and offer to swap one of this screen's own pictures for it.
function mvNoRoom(r, slot, ch) {
  const using = r.full.flatMap((f) => f.using);
  const entries = [];
  for (const u of using) {
    const who = u.viewers.map((v) => v.name).join(", ");
    if (u.slot && u.slot !== slot) entries.push({ label: `Swap ${u.title} for ${mvName(ch)}`, right: esc(u.slot === 1 ? "main picture" : `tile ${u.slot}`),
                                                  act: () => { closeDialog(); mvPut(u.slot, ch); } });
    else if (!u.slot) entries.push({ label: u.viewers.some((v) => v.kind === "dvr") ? `Recording ${u.title}` : u.title, right: esc(who), act: () => {} });
  }
  entries.push({ label: "OK", act: closeDialog });
  const lim = r.full.map((f) => `${f.source} allows ${f.limit} connection${f.limit === 1 ? "" : "s"}`).join("; ");
  openDialog(`Can't add ${mvName(ch)}`, `${r.reason}${lim ? ` (${lim})` : ""}, and ${ch.feed ? "this feed" : "this channel"} isn't on another playlist with one free. `
    + "Your channels that are on another playlist were already checked.", entries);
  const first = entries.findIndex((e) => e.label.startsWith("Swap"));
  fillList($("dlg-list"), entries, first < 0 ? entries.length - 1 : first);
}

// tune() / playFeed() in multiview, or while picking a channel to add: true when this took it
function mvRoute(what) {
  const adding = mvAdding();
  if (S.mode !== "live" || (!mvOn() && !adding)) return false;
  const ch = what.feed ? mvFeedCh(what.feed) : S.channels[what.idx];
  if (!ch) return false;
  S.mv.add = 0;
  if (adding && !mvCanAdd()) { toast(`There's room for ${mvMax()} channels at once`); return true; }
  mvPut(adding ? mvFreeSlot() : S.mv.focus, ch);
  return true;
}

// ---- starting, changing and leaving multiview
// the Multiview button (on-screen controls) and the W key: pick a layout, then the channel to add
// the button shows while multiview is on, or a channel could be added
function mvBarSync() {
  const show = S.mode === "live" && (mvOn() || mvCanAdd());
  document.querySelectorAll("#remote .rm-mv").forEach((el) => el.classList.toggle("hidden", !show));
  const b = $("rm-mv");
  b.classList.toggle("on", mvOn());
  b.querySelector("span").textContent = mvOn() ? "Multiview on" : "Multiview";
}
const mvLayoutIcon = (l) => `<span class="mv-ico" data-l="${l}">${"<i></i>".repeat(MV_SIZE[l])}</span>`;
// step 1: the layouts that fit; in multiview, changing it (or leaving). `was`: the one to highlight
// (coming back from step 2)
function mvOpen(was) {
  if (!mvOn() && !mvCanAdd()) {
    toast(!mvEnabled() ? "Multiview is turned off in Setup › Settings › Screens"
      : mvMax() < 2 ? "This screen can only play one channel at a time" : "Tune to a channel first", 4000);
    return;
  }
  const on = mvOn(), cur = on ? mvLayout() : "", list = mvFits(2);
  // in multiview, a layout with fewer places says which channels it stops
  const stops = (l) => {
    const names = mvGone(l).map((s) => (s === 1 ? mvMainCh() : (mvTile(s) || {}).ch)).filter(Boolean).map(mvName);
    return names.length ? `<small>Stops ${esc(names.join(", "))}</small>` : "";
  };
  const entries = list.map((l) => ({
    cls: `mv-lay${l === cur ? " cur" : ""}`, html: `${mvLayoutIcon(l)}<span>${MV_LAYOUT_NAMES[l]}</span>${on ? stops(l) : ""}`,
    act: () => {
      if (on) { closeDialog(); mvFitTo(l); return; }
      mvPicker(l);
    },
  }));
  if (on) entries.push({ cls: "mv-row", label: "Leave multiview (keep the highlighted channel)", act: () => { closeDialog(); mvEnd(); } });
  openDialog(on ? "Multiview" : "Watch two or more channels",
    on ? `Up to ${mvMax()} at once. A layout with more places shows "+ Add a channel" in the spare ones; one with fewer stops `
      + "the channels that don't fit (the big one and the highlighted one stay), freeing their connections."
      : `Pick how the pictures share the screen, then the channel to add. Up to ${mvMax()} at once.`, entries);
  $("dialog").classList.add("mv-dlg");
  $("dialog").style.setProperty("--mv-n", entries.filter((e) => e.cls.startsWith("mv-lay")).length);
  const at = was ? list.indexOf(was) : entries.findIndex((e) => e.cls.includes("cur"));
  fillList($("dlg-list"), entries, Math.max(0, at));
}
// Layout `l` with fewer places than there are pictures: which ones stop. The one in the big place and
// the highlighted one stay, then the rest in place order.
function mvGone(l) {
  const o = mvOrder(), keep = [...new Set([o[0], S.mv.focus, ...o])].slice(0, MV_SIZE[l]);
  return o.filter((s) => !keep.includes(s));
}
// change to layout `l`, stopping the pictures it has no place for (their connections are free at once)
async function mvFitTo(l) {
  const gone = mvGone(l);
  if (gone.length) {
    const names = gone.map((s) => (s === 1 ? mvMainCh() : (mvTile(s) || {}).ch)).filter(Boolean).map(mvName);
    S.mv.seq++;                       // a channel change under way is called off
    for (const s of gone) {
      const t = s !== 1 && mvTile(s);
      if (!t) continue;
      mvDrop(t);
      api("/api/stop", { method: "POST", body: { client: mvCid(s) } }).catch(() => {});
    }
    if (gone.includes(1)) await mvRemove(1);      // a picture that stays takes the main one's place
    toast(`Stopped ${names.join(", ")} to fit ${MV_LAYOUT_NAMES[l]}`, 4000);
  }
  if (mvOn()) mvSetLayout(l);
  else mvRender();
}
// step 2: the channel to add (favorites, recent, or find one), in layout `layout` (or the current one).
// With `slot`: the channel to play in that picture instead (its corner Change button).
function mvPicker(layout, slot) {
  const swap = slot !== undefined;
  if (!swap && !mvCanAdd()) { toast(mvMax() < 2 ? "This screen can only play one channel at a time" : `There's room for ${mvMax()} channels at once`, 4000); return; }
  if (swap) mvFocus(slot);            // the Guide, Sports, Search and On Demand put what you pick in the highlighted picture
  S.mv.add = swap ? 0 : Date.now();
  const was = swap ? (slot === 1 ? mvMainCh() : (mvTile(slot) || {}).ch) : null;
  const on = new Set([mvMainCh(), ...S.mv.tiles.map((t) => t.ch)].filter((c) => c && !c.feed).map((c) => c.number));
  const go = (fn) => () => { closeDialog(); if (layout) mvSetLayout(layout); fn(); };
  const chan = (c) => ({ html: `<span class="num">${c.number}</span><span>${esc(c.name)}</span>`,
                         act: go(() => { S.mv.add = 0; if (swap) mvPut(slot, c); else mvAddNow(c); }) });
  const ok = (c) => c && c.mapped && !on.has(c.number);
  const favs = S.channels.filter((c) => S.favs.has(c.number) && ok(c)).slice(0, 6);
  const recent = recentNums().map((n) => S.channels[S.byNum.get(n)]).filter((c) => ok(c) && !S.favs.has(c.number)).slice(0, 4);
  const hdr = (label) => ({ cls: "hdr", label, act: () => {} });
  const find = (label, fn) => ({ label, act: go(() => { S.mv.add = swap ? 0 : Date.now(); fn(); }) });
  // On Demand: the title picked plays in this picture, or (adding) in a new one
  const fromOD = { label: "From On Demand…", act: go(() => { S.mv.add = 0; if (!swap) S.mv.vodAdd = Date.now(); openOD(); }) };
  const entries = [
    ...(favs.length ? [hdr("Favorites"), ...favs.map(chan)] : []),
    ...(recent.length ? [hdr("Recent"), ...recent.map(chan)] : []),
    hdr("Find a channel"),
    find("From the TV Guide…", () => { G.open(); toast("Pick a channel to add to the screen", 3500); }),
    find("From Sports & Events…", openSports),
    // movies and shows (a playlist's On Demand, or My Library) play in a small picture
    ...(vodOn() && (!swap || slot > 1) ? [fromOD] : []),
    find("Search…", () => openSearch()),
  ];
  const where = MV_LAYOUT_NAMES[layout || mvLayout()] || "";
  const busy = "If its playlist is busy, a channel that's also on another playlist moves there to make room.";
  if (swap) openDialog(`Change ${was ? mvName(was) : "this picture"}`, `Pick what plays in this picture instead. ${busy}`, entries);
  else openDialog("Pick the channel to add", `${where ? `${where} · ` : ""}it plays without sound until you highlight it. ${busy}`, entries);
  $("dialog").classList.add("mv-dlg", "mv-chans");
  fillList($("dlg-list"), entries, entries.findIndex((e) => e.cls !== "hdr"));
  dlg.back = layout && !mvOn() ? () => { S.mv.add = 0; mvOpen(layout); } : () => { S.mv.add = 0; closeDialog(); };
}
const mvAddPrompt = () => mvPicker();
function mvAddNow(ch) {
  if (!mvCanAdd()) { toast(`There's room for ${mvMax()} channels at once`); return; }
  mvPut(mvFreeSlot(), ch);
}
// take picture `slot` off the screen; the main picture's place goes to the next tile
async function mvRemove(slot) {
  const t = mvTile(slot);
  if (t) {
    const had = S.mv.focus === slot, muted = soundEl().muted;      // read before the tile goes
    mvDrop(t);
    if (had) { mvFocus(1); video.muted = muted; }
    mvRender(); return;
  }
  const next = S.mv.tiles.slice().sort((a, b) => (!!a.vod - !!b.vod) || a.slot - b.slot)[0];
  if (!next) return;
  await mvPromote(next);
  mvRender();
}
// tile `t`'s channel moves to the main picture (it's already on: no new connection), then the tile goes
async function mvPromote(t) {
  if (t.vod) {          // a movie or show: full screen in the On Demand player (pause, skip, tracks), same spot
    const pos = mvVodPos(t), id = t.vod.it.id;
    S.mv.tiles = S.mv.tiles.filter((x) => x !== t);
    mvKill(t); t.el.remove();
    mvRender();
    await playVod(id, pos, false, true);
    return;
  }
  const sound = S.mv.focus === t.slot, muted = soundEl().muted;   // read before the tile goes
  S.mv.tiles = S.mv.tiles.filter((x) => x !== t);
  t.el.classList.add("hidden");
  if (sound) { mvFocus(1); video.muted = muted; }
  await mvMainPlay(t.ch, t.alt);
  mvKill(t); t.el.remove();
}
// leave multiview: the picture in `slot` (the highlighted one) fills the screen, the rest stop
async function mvEnd(slot = S.mv.focus) {
  const keep = slot > 1 && mvTile(slot);
  const muted = soundEl().muted;
  for (const t of [...S.mv.tiles]) if (t !== keep) mvDrop(t);
  S.mv.focus = 1; S.mv.seq++;
  if (keep) await mvPromote(keep);
  video.muted = muted;
  mvRender(); hideStatic();
  const ch = mvMainCh(); if (ch && keep) showBanner();
}
// everything but the main picture stops now (power off, a movie, a recording)
function mvStop() {
  S.mv.add = 0; S.mv.vodAdd = 0; S.mv.seq++;
  if (!mvOn()) return;
  const muted = soundEl().muted;
  for (const t of [...S.mv.tiles]) mvDrop(t);
  S.mv.focus = 1; video.muted = muted;
  mvRender();
}
window.addEventListener("pagehide", () => { for (const t of S.mv.tiles) mvKill(t); });     // (saves a movie's spot)
