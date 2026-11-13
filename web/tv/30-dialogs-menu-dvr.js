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
  listEl.innerHTML = entries.map((e, i) => `<li class="${i === sel ? "sel" : ""}${e.cls ? ` ${e.cls}` : ""}" data-i="${i}">${e.html ?? esc(e.label)}${e.right ? `<span class="r">${e.right}</span>` : ""}</li>`).join("");
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
  if (p.start > n0) {
    entries.push(findReminder(ch.number, p.start)
      ? { label: "🔕 Cancel reminder", act: () => { closeDialog(); toggleReminder(ch, p); } }
      : { label: "🔔 Remind me when it starts", act: () => { closeDialog(); toggleReminder(ch, p); } });
  }
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
  { label: "Sports & Events", act: () => { closeMenu(); openSports(); } },
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

