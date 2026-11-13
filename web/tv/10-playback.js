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
  vodLeave();                    // leaving a movie: its position is saved
  const ch = S.channels[idx];
  if (S.idx !== idx && S.idx >= 0 && alt === 0 && !S.feedCh) S.lastIdx = S.idx;
  S.feedCh = null;
  if (S.gameFeed && S.gameFeed.idx !== idx) S.gameFeed = null;
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
  vodLeave();
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
  else if (S.gameFeed && S.gameFeed.idx === idx && now() < S.gameFeed.until) {
    // tuned for a game that an event feed carries too (Sports screen): watch it there instead
    const f = S.gameFeed.feed; S.gameFeed = null;
    toast(`No signal on ${ch.number}. Trying ${f.group || "the event feed"}…`, 4000); playFeed(f);
  } else { destroyPlayer(); showStatic(ch.number, "No Signal", ch.name); }
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
  $("od-clock").textContent = t; $("od-date").textContent = day;
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

