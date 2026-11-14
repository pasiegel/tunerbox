// ---------------------------------------------------------------- Recordings
// Laid out like Home and drawn with its tiles: the TV keeps playing in the corner with the highlighted
// recording's details beside it, then rows: recording now, continue watching, recently recorded, a row
// for each show with more than one recording, the rest, what's coming up and what didn't record.
const DV = { rows: [], r: 0, cols: {}, seq: 0, state: "loading" };     // state: loading | ok | error
const DV_RECENT = 20;

function dvRows(recs) {
  const tile = (t) => (r) => ({ ...r, art: !!r.image, t });
  const done = recs.filter((r) => (r.status === "done" || r.status === "cancelled") && r.file);
  const newest = [...done].sort((a, b) => b.start - a.start);
  const rows = [];
  const add = (id, label, items) => { if (items.length) rows.push({ id, label, items }); };
  add("now", "Recording now", recs.filter((r) => r.status === "recording").map(tile("sched")));
  add("cont", "Continue watching", done.filter((r) => !r.watched && r.position > RESUME_MIN)
    .sort((a, b) => (b.watched_at || 0) - (a.watched_at || 0)).map(tile("rec")));
  add("recent", "Recently recorded", newest.slice(0, DV_RECENT).map(tile("rec")));
  // one row per show you have more than one recording of, the one recorded last first
  const shows = new Map();
  for (const r of newest) { const k = r.title || "Recording"; if (!shows.has(k)) shows.set(k, []); shows.get(k).push(r); }
  const single = [];
  let series = 0;
  for (const [title, list] of shows) {
    if (list.length > 1) { add(`s:${title}`, `${title} · ${list.length}`, list.map(tile("rec"))); series++; }
    else single.push(list[0]);
  }
  // the one-offs again on their own, unless "Recently recorded" already has everything
  if (series || newest.length > DV_RECENT) add("other", series ? "Other recordings" : "All recordings", single.map(tile("rec")));
  add("coming", "Coming up", recs.filter((r) => r.status === "scheduled").sort((a, b) => a.start - b.start).map(tile("sched")));
  add("failed", "Didn't record", recs.filter((r) => r.status === "failed").sort((a, b) => b.start - a.start).map(tile("fail")));
  return rows;
}

async function openDvr() {
  if (S.mode === "off") return;
  hideBanner(); closeMenu(); closeSearch(); closeSports(); closeOD(); $("playback").classList.add("hidden");
  if (top() === "guide") G.close();
  if (top() === "home") homeCover(); else closeHome();     // from Home: Back comes back to it
  if (top() !== "dvr") S.layer.push("dvr");
  $("dvr").classList.remove("hidden"); video.classList.add("pip");
  DV.r = 0; DV.cols = {};
  if (DV.state !== "ok") DV.state = "loading";
  dvRender();
  await dvLoad(true);
}
function closeDvr() {
  DV.seq++;
  if (!S.layer.includes("dvr")) return;
  S.layer = S.layer.filter((l) => l !== "dvr");
  $("dvr").classList.add("hidden");
  if (top() === "home") homeReveal();
  else if (top() !== "guide" && top() !== "od") video.classList.remove("pip");
}
// fetch and redraw, staying on the same tile (or, the first time, on the first row)
async function dvLoad(first) {
  const seq = ++DV.seq;
  let recs;
  try { recs = await api("/api/recordings"); } catch (_) {
    if (seq !== DV.seq || !S.layer.includes("dvr") || DV.state === "ok") return;     // keep what's shown
    DV.state = "error"; dvRender(); return;
  }
  if (seq !== DV.seq || !S.layer.includes("dvr")) return;
  DV.state = "ok";
  const row = DV.rows[DV.r], key = row && hmKey(row.items[DV.cols[row.id] || 0]);
  DV.rows = dvRows(recs);
  if (first) DV.r = 0;
  else {
    const r = DV.rows.findIndex((x) => row && x.id === row.id);
    DV.r = r >= 0 ? r : Math.max(0, Math.min(DV.r, DV.rows.length - 1));
    const i = DV.rows[DV.r] ? DV.rows[DV.r].items.findIndex((it) => hmKey(it) === key) : -1;
    if (i >= 0) DV.cols[DV.rows[DV.r].id] = i;
  }
  dvRender();
}

// ---- drawing (Home's tiles and details)
const DV_EMPTY = {
  loading: "Loading…",
  error: "Couldn't load your recordings. Check that TunerBox is running, then press D again.",
  ok: "No recordings yet. In the guide, highlight a show and press R, or press OK and pick Record.",
};
function dvRender() {
  $("dv-rows").innerHTML = DV.rows.map((row, r) => `<div class="hm-row" data-row="${r}"><div class="hm-label">${esc(row.label)}</div>`
    + `<div class="hm-strip">${row.items.map((it, i) => hmTile(it, r, i)).join("")}</div></div>`).join("")
    + (DV.rows.length ? "" : `<div class="od-empty">${DV_EMPTY[DV.state]}</div>`);
  dvSelect();
}
function dvSelect() {
  const row = DV.rows[DV.r];
  let it = null;
  if (row) {
    const i = Math.min(DV.cols[row.id] || 0, row.items.length - 1);
    DV.cols[row.id] = i; it = row.items[i];
    const old = $("dv-rows").querySelector(".sel"); if (old) old.classList.remove("sel");
    $("dv-rows").querySelectorAll(".hm-row").forEach((el) => el.classList.toggle("cur", +el.dataset.row === DV.r));
    const el = $("dv-rows").querySelector(`[data-r="${DV.r}"][data-i="${i}"]`);
    if (el) {
      el.classList.add("sel");
      if (DV.r === 0) $("dv-rows").scrollTop = 0;
      el.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }
  const x = it ? hmText(it) : { title: "My Recordings", sub: "", meta: "", desc: "" };
  $("dvi-title").textContent = x.title; $("dvi-sub").textContent = x.sub; $("dvi-meta").textContent = x.meta; $("dvi-desc").textContent = x.desc;
  $("dv-foot").innerHTML = "<span><b>OK</b> Play / Options</span><span><b>&#9650;&#9660;&#9664;&#9654;</b> Move</span>"
    + "<span><b>G</b> Guide</span><span><b>Esc</b> Back</span>";
}

// ---- picking
function dvAct(it) {
  if (!it) return;
  if (it.t === "rec") { recDialog(it, () => dvLoad()); return; }
  const after = () => { closeDialog(); G.cache.clear(); dvLoad(); };
  const del = (q, msg) => async () => {
    try { await api(`/api/recordings/${it.id}${q}`, { method: "DELETE" }); toast(msg); } catch (e) { toast(e.message); }
    after();
  };
  const entries = [];
  if (it.status === "recording") {
    if (S.byNum.has(it.number)) entries.push({ label: "Watch channel live", act: () => { closeDialog(); closeDvr(); closeHome(); tune(S.byNum.get(it.number)); } });
    entries.push({ label: "Stop recording", act: del("", "Recording stopped") });
  } else if (it.status === "scheduled") {
    entries.push({ label: "Cancel this recording", act: del("", "Recording cancelled") });
  } else {
    entries.push({ label: "Remove from list", act: del("?delete=1", "Removed") });
  }
  if (it.rule_id && it.t !== "fail") entries.push({ label: `Stop recording new episodes of ${it.title}`, act: async () => {
    try { await api(`/api/rules/${it.rule_id}`, { method: "DELETE" }); toast(`Series recording cancelled: ${it.title}`); } catch (e) { toast(e.message); }
    after();
  } });
  entries.push({ label: "Close", act: closeDialog });
  openDialog(it.title, `${it.number} ${it.channel_name || ""}\n${fmtDay(it.start)} ${fmtTime(it.start)} – ${fmtTime(it.stop)}${it.error ? "\n" + it.error : ""}`, entries);
  $("dlg-img").classList.add("art"); recArt($("dlg-img"), it);
}

function dvrKey(k) {
  if (k === "Back" || k === "d") { closeDvr(); return; }
  const row = DV.rows[DV.r];
  const i = row ? DV.cols[row.id] || 0 : 0, it = row && row.items[i];
  switch (k) {
    case "ArrowUp": if (DV.r > 0) { DV.r--; dvSelect(); } break;
    case "ArrowDown": if (DV.r < DV.rows.length - 1) { DV.r++; dvSelect(); } break;
    case "ArrowLeft": if (row && i > 0) { DV.cols[row.id] = i - 1; dvSelect(); } break;
    case "ArrowRight": if (row && i < row.items.length - 1) { DV.cols[row.id] = i + 1; dvSelect(); } break;
    case "ChannelUp": if (row) { DV.cols[row.id] = 0; dvSelect(); } break;
    case "ChannelDown": if (row) { DV.cols[row.id] = row.items.length - 1; dvSelect(); } break;
    case "Enter": dvAct(it); break;
    case "g": closeDvr(); G.open(); break;
    case "m": openMenu(); break;
    case "/": closeDvr(); openSearch(); break;
    case "t": closeDvr(); openSports(); break;
    case "o": openOD(); break;
    case "u": toggleMute(); break;
    case "VolUp": setVolume(video.volume + 0.05); break;
    case "VolDown": setVolume(video.volume - 0.05); break;
  }
}
$("dv-rows").addEventListener("click", (e) => {
  const el = e.target.closest("[data-r]"); if (!el) return;
  DV.r = +el.dataset.r; DV.cols[DV.rows[DV.r].id] = +el.dataset.i;
  dvSelect(); dvAct(DV.rows[DV.r].items[+el.dataset.i]);
});
// a picture that won't load: show the tile's name on its color instead
$("dv-rows").addEventListener("error", (e) => {
  const img = e.target;
  if (img.tagName !== "IMG") return;
  const tile = img.closest(".hm-tile");
  img.remove();
  if (tile) tile.classList.remove("art");
}, true);
