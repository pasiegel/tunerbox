// ---------------------------------------------------------------- stream stats
// Menu › Settings › Stream stats: a small box in the top-left corner that reads what's playing every second:
// picture and sound formats, the download rate, the cache ahead, dropped frames and the player's state.
// Everything comes from this screen's player (mpegts.js) and the <video> element, so it costs the server
// nothing. On or off is remembered for this screen.
const SS = { on: false, timer: null, last: null };
try { SS.on = localStorage.getItem("tb.streamStats") === "1"; } catch (_) { /* private window */ }
const ssOn = () => SS.on;
function toggleStreamStats() {
  SS.on = !SS.on;
  try { localStorage.setItem("tb.streamStats", SS.on ? "1" : "0"); } catch (_) {}
  ssStart();
}
function ssStart() {
  clearInterval(SS.timer); SS.timer = null; SS.last = null;
  if (!SS.on) { $("sstats").classList.add("hidden"); return; }
  ssTick();
  SS.timer = setInterval(ssTick, 1000);
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

  box.innerHTML = `<div class="ss-head">Stream stats</div><div class="ss-grid">${rows.join("")}</div>`;
  box.classList.remove("hidden");
}
ssStart();
