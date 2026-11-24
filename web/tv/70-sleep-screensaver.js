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
  if ((S.mode === "live" || S.mode === "playback" || S.mode === "vod") && !IDLE.ask && sleepMs() > 0 && idle >= sleepMs()) {
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
  idleTimer = setTimeout(() => {
    // not while the pointer rests on the controls or the More menu is open
    if ($("remote").matches(":hover") || !$("rm-menu").classList.contains("hidden") || pbHeld()) { hideRemoteLater(1500); return; }
    $("tv").classList.add("idle"); hideRemote();
    // a movie's or recording's progress bar goes with the controls (a paused movie keeps it)
    if ((S.mode === "vod" && !(S.vod && S.vod.paused)) || S.mode === "playback") $("playback").classList.add("hidden");
  }, ms);
}
// the on-screen controls: navigation at the top; this channel, volume and More at the bottom
function showRemote() {
  const r = $("remote"), wasHidden = r.classList.contains("hidden");
  r.classList.remove("hidden");
  r.classList.toggle("live", S.mode === "live");
  if (wasHidden) remoteSync();
}
// the progress bar is being dragged or pointed at: nothing hides meanwhile
function pbHeld() { return !!pbScrub || $("playback").matches(":hover"); }
// the progress bar is up with the controls: it hides when they do (hideRemoteLater), not on its own timer
function pbWithRemote() { clearTimeout(vodBarTimer); clearTimeout(pbTimer); }
function hideRemote() { $("remote").classList.add("hidden"); $("rm-menu").classList.add("hidden"); }
let rmSeq = 0;
async function remoteSync() {
  mvBarSync();
  const vol = $("rm-volume"), a = soundEl();
  vol.value = Math.round((a.muted ? 0 : a.volume) * 100);
  $("rm-mute").querySelector("use").setAttribute("href", a.muted || a.volume === 0 ? "#i-mute" : "#i-vol");
  const ch = curCh();
  if (S.mode !== "live" || !ch) { $("rm-now").innerHTML = ""; return; }
  const fav = S.favs.has(ch.number);
  $("rm-fav").classList.toggle("on", fav);
  $("rm-fav").querySelector("span").textContent = fav ? "Favorite" : "Favorite";
  $("rm-fav").title = fav ? "Remove from Favorites (*)" : "Add to Favorites (*)";
  const head = `<span class="rm-num">${esc(String(ch.badge || ch.number))}</span>`
    + (ch.logo ? `<img class="rm-logo" src="${esc(safePic(ch.logo))}" alt="" onerror="this.remove()">` : `<span class="rm-label">${esc(ch.label || ch.name)}</span>`);
  $("rm-now").innerHTML = `${head}<div class="rm-what"><div class="rm-title">${esc(ch.name)}</div></div>`;
  if (ch.feed || !ch.epg) return;
  const seq = ++rmSeq;
  try {
    const d = await nowNext(ch.number);
    if (seq !== rmSeq || curCh() !== ch) return;
    const p = d.now;
    if (!p) return;
    const pct = Math.min(100, Math.max(0, (now() - p.start) / (p.stop - p.start) * 100));
    const sub = [p.episode, p.subtitle].filter(Boolean).join(" · ");
    const meta = [`${fmtTime(p.start)} – ${fmtTime(p.stop)}`, d.next && `Next: ${fmtTime(d.next.start)} ${d.next.title}`].filter(Boolean).join(" · ");
    $("rm-now").innerHTML = `${head}<div class="rm-what"><div class="rm-title">${esc(p.title || ch.name)} ${flags(p)}</div>`
      + `<div class="rm-sub">${esc([sub, meta].filter(Boolean).join("  ·  "))}</div>`
      + `<div class="rm-bar"><i style="width:${pct}%"></i></div></div>`;
  } catch (_) {}
}
$("rm-volume").addEventListener("input", (e) => {
  const a = soundEl();
  a.volume = +e.target.value / 100; a.muted = a.volume === 0;
  try { localStorage.setItem("tb.vol", a.volume); } catch (_) {}
  $("rm-mute").querySelector("use").setAttribute("href", a.muted ? "#i-mute" : "#i-vol");
});
$("remote").addEventListener("wheel", (e) => {          // the mouse wheel over the controls: volume
  if (!e.target.closest(".rm-vol")) return;
  e.preventDefault();
  const a = soundEl(), v = Math.max(0, Math.min(1, a.volume + (e.deltaY < 0 ? 0.05 : -0.05)));
  a.volume = v; a.muted = v === 0; $("rm-volume").value = Math.round(v * 100);
  try { localStorage.setItem("tb.vol", v); } catch (_) {}
}, { passive: false });
document.addEventListener("mousemove", () => {
  IDLE.last = Date.now();
  if (IDLE.saver) stopSaver();
  // iOS Safari fires a fake mousemove after every tap; if that changes what's on screen it drops the
  // tap's click, so touch input manages the remote bar itself (see touchend below)
  if (Date.now() - lastTouch < 1000) return;
  $("tv").classList.remove("idle");
  if (S.mode !== "off" && !top()) showRemote();
  hideRemoteLater(3000);
  // a movie, show or recording: its progress bar comes up with the controls (not over the info banner)
  if (!top() && (S.mode === "vod" || S.mode === "playback") && $("banner").classList.contains("hidden")
      && Date.now() - pbMoveShown > 250) {
    pbMoveShown = Date.now();
    if (S.mode === "vod") { if (S.vod && !S.vod.done) showVodBar(); } else showPlaybackBar();
  }
  if (!$("playback").classList.contains("hidden") && !$("remote").classList.contains("hidden")) pbWithRemote();
});
let pbMoveShown = 0;
$("power").addEventListener("click", () => powerOn());
$("static").addEventListener("click", () => { if (S.mode === "empty" && !top()) openSetup(); });
$("remote").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.id === "rm-more") {
    $("rm-startover").classList.toggle("hidden", !cuLive());        // only on channels with catch-up
    $("rm-menu").classList.toggle("hidden"); hideRemoteLater(4000); return;
  }
  if (b.id === "rm-startover") { clearTimeout(idleTimer); hideRemote(); startOver(); return; }
  if (b.id === "rm-mv") { clearTimeout(idleTimer); hideRemote(); mvOpen(); return; }
  if (!b.dataset.k) return;
  // CH up/down, favorite and mute keep the controls up (flipping through channels); others put them away
  const stay = ["ChannelUp", "ChannelDown", "*", "u"].includes(b.dataset.k);
  if (!stay) {
    clearTimeout(idleTimer); hideRemote();
    if (S.mode === "vod" || S.mode === "playback") $("playback").classList.add("hidden");    // the key below brings it back if it needs it
  }
  handleKey(b.dataset.k);
  if (stay) setTimeout(remoteSync, b.dataset.k.startsWith("Channel") ? 400 : 150);
});
// tap / click on the picture while watching: show the button bar and the info banner together,
// or put both away if either is showing
function toggleOverlay() {
  const r = $("remote"), shown = !r.classList.contains("hidden") || !$("banner").classList.contains("hidden");
  if (shown) {
    hideRemote(); clearTimeout(idleTimer);
    hideBanner(); clearTimeout(S.bannerTimer); $("playback").classList.add("hidden");
    return;
  }
  showRemote(); hideRemoteLater(6000);
  if (S.mode === "live") showBanner();
  else if (S.mode === "vod") { showVodBar(); pbWithRemote(); }
  else if (S.mode === "playback") { $("playback").classList.add("hidden"); recInfo(); }
}
$("learn").addEventListener("click", stopLearn);
$("back").addEventListener("click", () => { if (top()) handleKey("Back"); });
// The floating Close: on phones and touch screens (iPad, tablets) always, and with a mouse once it's been
// used (Chrome on a PC). A remote or keyboard has Back, so a key press hides it again until the mouse moves.
const touchScreen = navigator.maxTouchPoints > 0 && !!(window.matchMedia && matchMedia("(any-pointer: coarse)").matches);
let mouseUsed = false;
document.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse") mouseUsed = true; }, { passive: true });
document.addEventListener("pointerdown", (e) => { if (e.pointerType === "mouse") mouseUsed = true; }, { passive: true });
document.addEventListener("keydown", () => { mouseUsed = false; }, true);
setInterval(() => {
  const want = isPhone() || touchScreen || mouseUsed;
  $("back").classList.toggle("hidden", !(want && top() && top() !== "learn"));
}, 250);
video.addEventListener("click", () => {
  // Home's corner TV: a click or tap goes back to full-screen TV, like Home's "Watch TV"
  if (top() === "home") { closeHome(); if (S.mode === "live") showBanner(); return; }
  if (top() || Date.now() - lastTouch < 1000) return;     // touch taps are handled in touchend below
  // mouse: moving it already shows the button bar, so a click toggles just the info banner
  if (!$("banner").classList.contains("hidden")) { hideBanner(); clearTimeout(S.bannerTimer); return; }
  if (S.mode === "vod") { vodPause(); return; }             // click the picture: pause / play
  if (S.mode === "live") showBanner();
  else if (S.mode === "playback") { $("playback").classList.add("hidden"); recInfo(); }
});
video.addEventListener("dblclick", toggleFull);
$("g-rows").addEventListener("click", (e) => {
  const cell = e.target.closest(".g-p"), row = e.target.closest(".g-row");
  if (!row) return;
  const i = +row.dataset.i;
  G.filterFocus = false;
  if (e.target.closest(".g-fav")) { toggleFav(G.ch(i)); return; }
  if (cell) {
    const s = +cell.dataset.s;
    if (G.row === i && G.selected() && G.selected().start === s) { G.ok(); return; }
    G.row = i; G.focusT = Math.max(s, G.win); G.render();
  } else { tune(G.list[i]); closeHome(); G.close(); }
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
  if (e.target.closest && e.target.closest("#power, #tapplay, #saver, #stillthere, #learn, #pb-seek")) return;
  if (Math.abs(dy) > 80) { if (S.mode !== "vod") chStep(dy < 0 ? 1 : -1); }     // no channel change by accident mid-movie
  else if (S.mode === "live" || S.mode === "playback" || S.mode === "vod") toggleOverlay();
}, { passive: true });

