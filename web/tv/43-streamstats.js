// ---------------------------------------------------------------- stream stats
// Menu › Settings › Stream stats: a small box over the picture that reads what's playing every second:
// picture and sound formats, the download rate, the cache ahead, dropped frames and the player's state.
// Everything comes from this screen's player (mpegts.js) and the <video> element, so it costs the server
// nothing. Pressing the menu entry steps Off › top left › top right › bottom right › bottom left › Off, and
// with a mouse or finger the box can be dragged anywhere. On or off and where it sits are remembered for
// this screen.
const SS = { on: false, timer: null, last: null, pos: "tl", drag: null };
const SS_CORNERS = ["tl", "tr", "br", "bl"];
const SS_NAMES = { tl: "Top left", tr: "Top right", br: "Bottom right", bl: "Bottom left" };
try {
  SS.on = localStorage.getItem("tb.streamStats") === "1";
  const p = localStorage.getItem("tb.streamStatsPos") || "tl";
  // a corner, or "x,y": where a drag left it, as fractions of the room the box has to move in
  if (SS_CORNERS.includes(p)) SS.pos = p;
  else { const [x, y] = p.split(",").map(Number); if (x >= 0 && x <= 1 && y >= 0 && y <= 1) SS.pos = { x, y }; }
} catch (_) { /* private window */ }
const ssOn = () => SS.on;
const ssWhere = () => (typeof SS.pos === "string" ? SS_NAMES[SS.pos] : "Moved");
function ssSave() {
  try {
    localStorage.setItem("tb.streamStats", SS.on ? "1" : "0");
    localStorage.setItem("tb.streamStatsPos", typeof SS.pos === "string" ? SS.pos : `${SS.pos.x.toFixed(3)},${SS.pos.y.toFixed(3)}`);
  } catch (_) {}
}
// the box's Move button: the next corner round (a dragged box goes to the top left)
function ssNextCorner() {
  const i = typeof SS.pos === "string" ? SS_CORNERS.indexOf(SS.pos) : -1;
  SS.pos = SS_CORNERS[(i + 1) % SS_CORNERS.length];
  ssSave(); ssPlace();
}
// the menu entry: off › each corner in turn › off (a dragged box goes back to the top left)
function toggleStreamStats() {
  const i = typeof SS.pos === "string" ? SS_CORNERS.indexOf(SS.pos) : -1;
  if (!SS.on) { SS.on = true; SS.pos = "tl"; }
  else if (i < 0) SS.pos = "tl";
  else if (i === SS_CORNERS.length - 1) SS.on = false;
  else SS.pos = SS_CORNERS[i + 1];
  ssSave(); ssStart();
}
function ssStart() {
  clearInterval(SS.timer); SS.timer = null; SS.last = null;
  if (!SS.on) { $("sstats").classList.add("hidden"); return; }
  ssTick();
  SS.timer = setInterval(ssTick, 1000);
}
// put the box where it belongs: a corner by CSS, a dragged spot kept inside the screen however big the box gets
function ssPlace() {
  const b = $("sstats"), s = b.style, p = SS.pos;
  s.left = s.top = s.right = s.bottom = "";
  if (typeof p === "object") {
    const room = b.parentElement;
    s.left = `${Math.round(p.x * Math.max(0, room.clientWidth - b.offsetWidth))}px`;
    s.top = `${Math.round(p.y * Math.max(0, room.clientHeight - b.offsetHeight))}px`;
    return;
  }
  if (p[0] === "b") { s.top = "auto"; s.bottom = "1.6em"; }
  if (p[1] === "r") { s.left = "auto"; s.right = "1.6em"; }
}

const ssCodec = (c) => {
  if (!c) return "";
  const l = c.toLowerCase();
  if (/^(hvc1|hev1)/.test(l)) return `HEVC (H.265)${l.startsWith("hvc1.2") || l.startsWith("hev1.2") ? " 10-bit" : ""}`;
  if (/^avc/.test(l)) return "H.264";
  if (/^mp4a\.40/.test(l) || l === "aac") return "AAC";
  if (/^mp4a\.6b|^mp3|^mp4a\.69/.test(l)) return "MP3";
  if (/^ac-3/.test(l)) return "Dolby Digital (AC-3)";
  if (/^ec-3/.test(l)) return "Dolby Digital Plus (E-AC-3)";
  if (/^opus/.test(l)) return "Opus";
  return c;
};
const ssRate = (kbps) => (kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${Math.round(kbps)} kbps`);
const ssChans = (n) => ({ 1: "mono", 2: "stereo", 6: "5.1", 8: "7.1" }[n] || (n ? `${n} ch` : ""));

// what's on screen and how it reaches this screen
function ssWhat() {
  if (S.mode === "live") {
    const ch = curCh();
    if (!ch) return { title: "", key: "" };
    const key = ch.feed ? `f:${ch.feed}` : `${ch.number}:${S.alt}`;
    const title = ch.feed ? (ch.label || ch.name || "Event feed") : `${ch.number} ${ch.name}${S.alt ? ` · backup ${S.alt}` : ""}`;
    return { title, key };
  }
  if (S.mode === "vod" && S.vod) return { title: S.vod.it.title, key: `v:${S.vod.it.id}` };
  if (S.mode === "playback") return { title: S.rec ? S.rec.title + (S.rec.subtitle ? ` · ${S.rec.subtitle}` : "") : "Recording", key: "" };
  return null;
}

function ssTick() {
  const box = $("sstats"), what = ssWhat();
  // a recording that isn't .ts plays as a plain file, with no player object: the <video> element is enough
  if (!SS.on || !what || (!S.player && !video.currentSrc)) { box.classList.add("hidden"); SS.last = null; return; }
  const p = S.player, mse = !!p && typeof p.attachMediaElement === "function";
  const mi = (mse && p.mediaInfo) || {}, si = (mse && p.statisticsInfo) || {};
  const rows = [];
  const row = (k, v) => v && rows.push(`<div class="ss-k">${k}</div><div class="ss-v">${v}</div>`);

  row("Playing", esc(what.title));
  row("Player", `${mse ? "MPEG-TS (Media Source)" : p ? "HLS (built in)" : "Direct file"}${what.key && h264Keys.has(what.key) ? " · converted to H.264" : ""}`);

  // picture: the decoded size wins (the stream's own header can say otherwise until the first frame)
  const w = video.videoWidth || mi.width, h = video.videoHeight || mi.height;
  const q = video.getVideoPlaybackQuality ? video.getVideoPlaybackQuality() : null;
  const frames = q ? { total: q.totalVideoFrames, dropped: q.droppedVideoFrames }
                   : { total: si.decodedFrames || 0, dropped: si.droppedFrames || 0 };
  const t = performance.now(), prev = SS.last;
  SS.last = { t, p, ...frames };
  let fps = mi.fps ? Math.round(mi.fps * 100) / 100 : 0, dropNow = "";
  if (prev && prev.p === p && t > prev.t) {
    const secs = (t - prev.t) / 1000, n = frames.total - prev.total, d = frames.dropped - prev.dropped;
    if (!video.paused && n > 0) { if (!fps) fps = Math.round(n / secs); dropNow = d > 0 ? ` · ${d} in the last second` : ""; }
  }
  row("Video", [w && h ? `${w}×${h}` : "", esc(ssCodec(mi.videoCodec)), fps ? `${fps} fps` : ""].filter(Boolean).join(" · "));
  row("Audio", [esc(ssCodec(mi.audioCodec)), mi.audioSampleRate ? `${(mi.audioSampleRate / 1000).toFixed(1)} kHz` : "",
                ssChans(mi.audioChannelCount)].filter(Boolean).join(" · "));
  if (si.speed !== undefined) row("Download", ssRate(si.speed * 8));
  const dr = (mi.videoDataRate || 0) + (mi.audioDataRate || 0);
  if (dr) row("Bitrate", ssRate(dr));

  const ahead = bufferedAhead(), want = bufSecs();
  row("Cache", `${ahead.toFixed(1)} s ahead${want ? ` (set to ${want} s)` : ""}`);
  if (frames.total) {
    const pct = frames.dropped / frames.total * 100;
    row("Dropped", `<span class="${pct >= 1 || dropNow ? "ss-bad" : ""}">${frames.dropped} of ${frames.total} (${pct.toFixed(pct < 10 ? 1 : 0)}%)${dropNow}</span>`);
  }
  const state = video.paused ? "Paused" : video.readyState < 3 ? `<span class="ss-bad">Waiting for data</span>` : "Playing";
  row("State", state);

  // the header is built once (so a click on Move isn't lost to the once-a-second refresh), the rows every tick
  if (!box.querySelector(".ss-grid")) {
    box.innerHTML = `<div class="ss-head" title="Drag to move"><span class="ss-grip">&#10303;</span>Stream stats`
      + `<button class="ss-move" type="button" title="Move to the next corner">&#10530; Move</button>`
      + `<button class="ss-close" type="button" title="Turn off stream stats (Menu › Stream stats turns it back on)" aria-label="Close">&#10005;</button></div><div class="ss-grid"></div>`;
  }
  box.querySelector(".ss-grid").innerHTML = rows.join("");
  box.classList.remove("hidden");
  if (!SS.drag) ssPlace();
}

// drag it with a mouse or finger; a press that doesn't move isn't taken as a tap on the picture
$("sstats").addEventListener("pointerdown", (e) => {
  e.stopPropagation();
  if (e.target.closest(".ss-move")) { e.preventDefault(); ssNextCorner(); return; }
  if (e.target.closest(".ss-close")) { e.preventDefault(); SS.on = false; ssSave(); ssStart(); return; }
  const b = $("sstats"), r = b.getBoundingClientRect(), room = b.parentElement.getBoundingClientRect();
  SS.drag = { id: e.pointerId, dx: e.clientX - r.left, dy: e.clientY - r.top, room, moved: false, x0: e.clientX, y0: e.clientY };
  b.setPointerCapture(e.pointerId); b.classList.add("drag");
  e.preventDefault();
});
$("sstats").addEventListener("pointermove", (e) => {
  const d = SS.drag, b = $("sstats");
  if (!d || d.id !== e.pointerId) return;
  if (!d.moved && Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < 4) return;
  d.moved = true;
  const w = Math.max(0, d.room.width - b.offsetWidth), h = Math.max(0, d.room.height - b.offsetHeight);
  const x = Math.min(w, Math.max(0, e.clientX - d.room.left - d.dx)), y = Math.min(h, Math.max(0, e.clientY - d.room.top - d.dy));
  SS.pos = { x: w ? x / w : 0, y: h ? y / h : 0 };
  ssPlace();
});
function ssDragEnd(e) {
  const d = SS.drag;
  if (!d || d.id !== e.pointerId) return;
  SS.drag = null; $("sstats").classList.remove("drag");
  if (d.moved) ssSave();
}
$("sstats").addEventListener("pointerup", ssDragEnd);
$("sstats").addEventListener("pointercancel", ssDragEnd);
$("sstats").addEventListener("click", (e) => e.stopPropagation());
ssStart();
