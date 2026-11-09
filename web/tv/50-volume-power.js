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

