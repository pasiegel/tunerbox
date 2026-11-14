// ---------------------------------------------------------------- dialogs & lists
function listNav(listEl, key, onPick) {
  const items = [...listEl.querySelectorAll("li")];
  if (!items.length) return;
  let i = items.findIndex((li) => li.classList.contains("sel"));
  const at = i;
  if (key === "ArrowDown") i = (i + 1) % items.length;
  else if (key === "ArrowUp") i = (i - 1 + items.length) % items.length;
  else if (key === "ChannelDown") i = Math.min(items.length - 1, Math.max(0, i) + 8);
  else if (key === "ChannelUp") i = Math.max(0, i - 8);
  else if (key === "Enter") { if (i >= 0) onPick(items[i], i); return; }
  else return;
  // group titles (li.hdr) can't be highlighted: keep going the same way (back, if that runs off the end)
  const dir = key === "ArrowUp" || key === "ChannelUp" ? -1 : 1, n = items.length;
  const ok = (j) => j >= 0 && j < n && !items[j].classList.contains("hdr");
  if (!ok(i)) {
    let j = i;
    if (key.startsWith("Arrow")) { do j = (j + dir + n) % n; while (!ok(j) && j !== i); }
    else {                          // paging: on past the title, or back the other way at the very top / end
      while (j >= 0 && j < n && !ok(j)) j += dir;
      if (!ok(j)) { j = i; while (j >= 0 && j < n && !ok(j)) j -= dir; }
    }
    i = ok(j) ? j : at;
  }
  items.forEach((li) => li.classList.remove("sel"));
  items[i].classList.add("sel"); items[i].scrollIntoView({ block: "nearest" });
}
function fillList(listEl, entries, sel = 0) {
  listEl.innerHTML = entries.map((e, i) => `<li class="${i === sel ? "sel" : ""}${e.cls ? ` ${e.cls}` : ""}" data-i="${i}">${e.html ?? esc(e.label)}${e.right ? `<span class="r">${e.right}</span>` : ""}</li>`).join("");
}

let dlg = { entries: [] };
function openDialog(title, sub, entries) {
  dlg.entries = entries; $("dlg-img").classList.add("hidden"); $("dlg-img").classList.remove("art"); $("dlg-img").dataset.key = "";
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
  if (p.start <= n0 && n0 < p.stop) entries.push({ label: "Watch now", act: () => { closeDialog(); G.close(); closeHome(); tune(S.byNum.get(ch.number)); } });
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
  $("dlg-img").classList.add("art"); showArt($("dlg-img"), ch.number, p);
}

// menu, in groups: { head } entries are the group titles ("" = just a dividing line), skipped by the highlight.
// homeOff: only when Home is turned off in Setup; otherwise Home's shortcuts (and V in the guide) reach them,
// and leaving them out keeps the menu short enough not to scroll.
const MENU = [
  { head: "Watch" },
  { label: "Home", needsHome: true, act: () => { closeMenu(); openHome(); } },
  { label: "TV Guide", act: () => { closeMenu(); G.open(); } },
  { label: "★ Favorites guide", homeOff: true, act: () => { closeMenu(); G.open(true); } },
  { label: "On Demand (movies & TV shows)", homeOff: true, act: () => { closeMenu(); openOD(); } },
  { label: "Sports & Events", homeOff: true, act: () => { closeMenu(); openSports(); } },
  { label: "Search", homeOff: true, act: () => { closeMenu(); openSearch(); } },
  { label: "My Recordings (DVR)", homeOff: true, act: () => { closeMenu(); openDvr(); } },
  { head: "This channel" },
  { get label() {
      const ch = S.mode === "live" && curCh();
      if (!ch || ch.feed) return "Add to Favorites (tune to a channel first)";
      return S.favs.has(ch.number) ? `Remove ${ch.number} ${ch.name} from Favorites` : `★ Add ${ch.number} ${ch.name} to Favorites`;
    },
    act: () => { const ch = S.mode === "live" && curCh(); closeMenu(); if (ch) toggleFav(ch); else toast("Tune to a channel first"); } },
  { label: "Open in VLC / mpv (copy the stream address)", notPhone: true, act: async () => {
    const ch = curCh(); if (!ch) return;
    const url = ch.feed ? `${location.origin}/api/feed/stream?key=${encodeURIComponent(ch.feed)}` : `${location.origin}/api/stream/${ch.number}`;
    try { await navigator.clipboard.writeText(url); } catch (_) {}
    toast(`Stream URL copied: ${url}`, 6000); closeMenu();
  } },
  { head: "Settings" },
  { get label() { return `Sleep timer & screensaver: ${sleepOn() ? plural(sleepH(), "hour") : "sleep off"} · ${saverOn() ? plural(saverM(), "min") : "saver off"}`; },
    act: () => { closeMenu(); sleepDialog(); } },
  { get label() { const b = bufSecs(); return `Stream cache: ${b ? b + " seconds" : "Off"}${localStorage.getItem("tb.buffer") === null ? " (default)" : " (this screen)"}`; },
    act: () => { closeMenu(); bufferDialog(); } },
  { id: "homestart", needsHome: true, get label() { return `Start on Home: ${homeAtStart() ? "On" : "Off"} (this screen)`; },
    act: () => { try { localStorage.setItem("tb.homeStart", homeAtStart() ? "0" : "1"); } catch (_) {} renderMenu(menuShown.indexOf(MENU_HOMESTART)); } },
  { label: "Remote control setup", notPhone: true, act: () => { closeMenu(); openRemote(); } },
  { label: "Use a phone as the remote…", notPhone: true, act: () => { closeMenu(); pairDialog(); } },
  { id: "full", label: "Full screen", act: () => { toggleFull(); closeMenu(); } },
  { head: "Channels & guide" },
  { label: "Channel Setup (playlists, lineup, guide)", act: openSetup },
  { label: "Rescan channels", act: async () => { await api("/api/jobs/scan", { method: "POST" }); toast("Scanning channels in the background…"); closeMenu(); watchJob(); } },
  { label: "Refresh TV guide", act: async () => { await api("/api/jobs/epg", { method: "POST" }); toast("Updating guide in the background…"); closeMenu(); watchJob(); } },
  { head: "" },
  { label: "Turn off", act: () => { closeMenu(); powerOff(); } },
];
const MENU_HOMESTART = MENU.find((e) => e.id === "homestart");
// Android app: switch to another TunerBox PC (in place of Full screen)
if (window.TunerBoxApp) MENU.splice(MENU.findIndex((e) => e.id === "full"), 1, { label: "Change TunerBox server", act: () => window.TunerBoxApp.changeServer() });
// the entries shown right now: phones leave out the ones that only make sense on a TV / PC, and a
// group title goes when nothing is left under it
let menuShown = MENU;
function renderMenu(sel) {
  if (sel === undefined || sel < 0 || menuShown[sel].head !== undefined) sel = menuShown.findIndex((e) => e.head === undefined);
  fillList($("menu-list"), menuShown.map((e) => (e.head !== undefined ? { cls: e.head ? "hdr" : "hdr line", html: esc(e.head) } : e)), sel);
}
function openMenu() {
  hideBanner();
  const items = MENU.filter((e) => !(isPhone() && e.notPhone) && !(e.needsHome && !homeEnabled()) && !(e.homeOff && homeEnabled()));
  menuShown = items.filter((e, i) => e.head === undefined || (items[i + 1] && items[i + 1].head === undefined));
  renderMenu();
  S.layer.push("menu"); $("menu").classList.remove("hidden", "two");
  // still too tall for this screen (a short window, or Home off): two columns instead of scrolling
  const l = $("menu-list");
  if (!isPhone() && l.scrollHeight > l.clientHeight + 2) $("menu").classList.add("two");
  l.scrollTop = 0;
}
// two columns: ◀ ▶ go across to the item at about the same height in the other column
function menuAcross(k) {
  if (!$("menu").classList.contains("two")) return false;
  const items = [...$("menu-list").querySelectorAll("li:not(.hdr)")], cur = $("menu-list").querySelector("li.sel");
  if (!cur) return false;
  const others = items.filter((li) => (k === "ArrowRight" ? li.offsetLeft > cur.offsetLeft : li.offsetLeft < cur.offsetLeft));
  if (!others.length) return true;
  const to = others.reduce((a, b) => (Math.abs(b.offsetTop - cur.offsetTop) < Math.abs(a.offsetTop - cur.offsetTop) ? b : a));
  cur.classList.remove("sel"); to.classList.add("sel");
  return true;
}
function closeMenu() { S.layer = S.layer.filter((l) => l !== "menu"); $("menu").classList.add("hidden"); }
$("menu-list").addEventListener("click", (e) => { const li = e.target.closest("li"); const it = li && menuShown[+li.dataset.i]; if (it && it.act) it.act(); });

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

// a recorded show: resume, play, delete. `after` runs once it's deleted (redraw the list it came from)
function recDialog(r, after) {
  const resume = !r.watched && r.position > RESUME_MIN;
  const play = (from) => () => { closeDialog(); closeDvr(); playRecording(r, from); };
  const entries = [];
  if (resume) entries.push({ label: `Resume from ${fmtDur(r.position)}`, act: play(r.position) });
  entries.push({ label: resume ? "Play from the beginning" : "Play", act: play(0) });
  entries.push({ label: "Delete recording", act: async () => {
    try { await api(`/api/recordings/${r.id}?delete=1`, { method: "DELETE" }); } catch (e) { toast(`Can't delete: ${e.message}`); }
    closeDialog(); after();
  } });
  entries.push({ label: "Close", act: closeDialog });
  openDialog(r.title, `${r.number} ${r.channel_name || ""}\n${fmtDay(r.start)} ${fmtTime(r.start)} – ${fmtTime(r.stop)}`
    + (resume ? `\nYou stopped at ${fmtDur(r.position)}` : r.watched ? "\nWatched" : "") + (r.error ? "\n" + r.error : ""), entries);
  $("dlg-img").classList.add("art"); recArt($("dlg-img"), r);
}

function playRecording(r, from = 0) {
  vodLeave(); recLeave(); closeHome();
  destroyPlayer(); hideStatic(); hideBanner();
  S.mode = "playback";
  if (from > 0) video.addEventListener("loadedmetadata", () => { if (S.rec === r) video.currentTime = from; }, { once: true });
  if (r.file.endsWith(".ts") && window.mpegts) {
    S.player = mpegts.createPlayer({ type: "mpegts", isLive: false, url: `${location.origin}/api/recordings/${r.id}/file` });
    S.player.attachMediaElement(video); S.player.load(); S.player.play();
  } else {
    video.src = `/api/recordings/${r.id}/file`; video.play().catch(() => {});
  }
  $("pb-title").textContent = r.title + (r.subtitle ? ` · ${r.subtitle}` : "");
  S.rec = r;
  clearInterval(S.recSaveT); S.recSaveT = setInterval(recSave, 15000);
  showPlaybackBar();
}
// where playback of a recording got to, on the server, so any screen can resume it
function recSave(ended) {
  const r = S.rec, d = video.duration;
  if (!r || S.mode !== "playback" || !isFinite(d) || !d) return;
  const pos = ended ? d : video.currentTime;
  if (pos < 5) return;
  r.position = Math.round(pos); r.duration = Math.round(d);
  fetch(`/api/recordings/${r.id}/progress`, { method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
                                              body: JSON.stringify({ position: pos, duration: d }) }).catch(() => {});
}
function recLeave(ended) { if (S.rec) recSave(ended); clearInterval(S.recSaveT); S.rec = null; }
// Info on a recording: the banner, filled from the recording instead of the live guide
function recInfo(full = false) {
  const r = S.rec; if (!r) return;
  const ch = S.channels[S.byNum.get(r.number)] || {};
  $("b-num").textContent = r.number; $("b-name").textContent = r.channel_name || ch.name || "";
  const logo = $("b-logo");
  if (ch.logo) { logo.src = ch.logo; logo.style.display = ""; } else logo.style.display = "none";
  recArt($("b-art"), r); $("b-art").dataset.ch = "";          // the live banner picks its own picture again
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
video.addEventListener("ended", () => { if (S.mode === "playback") { recLeave(true); backToLive(); } });
function backToLive() { $("playback").classList.add("hidden"); hideBanner(); recLeave(); destroyPlayer(); if (S.idx >= 0) tune(S.idx); }

