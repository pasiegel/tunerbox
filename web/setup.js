/* TunerBox setup page */
(() => {
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// on the HTTPS port, pictures from plain-http sites would be blocked: TunerBox fetches those (/api/img)
const safePic = (u) => (location.protocol === "https:" && /^http:\/\//i.test(u || "") ? `/api/img?u=${encodeURIComponent(u)}` : (u || ""));
// Search that forgives how a name is typed: case, accents, spaces and punctuation don't matter, and "&" or
// "+" match "and" or "plus" or nothing. "a & e", "ae" and "a and e" find A&E; "espn 2" finds ESPN2;
// "espanol" finds Español; "today’s" (a phone's curly apostrophe) finds Today's. One or two letters
// only match the start of a word, so "e!" finds E! and A&E but not every name with an e in it.
const fold = (s) => String(s ?? "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const searchForms = (s) => { const f = fold(s); return [f.replace(/&/g, " and ").replace(/\+/g, " plus "), f]; };
const squash = (s) => s.replace(/[^a-z0-9]+/g, "");
// an antenna feed's own channel number, from its key ("3:xhd8.1" -> "8.1"), shown before its name
const antNum = (st) => { const m = /:xhd(\d+(?:\.\d+)?)$/.exec(st && st.key || ""); return m ? m[1] : ""; };
const feedName = (st) => (antNum(st) ? `${antNum(st)} ` : "") + (st ? st.name : "");
function matcher(q) {
  const qs = [...new Set(searchForms(q).map(squash))].filter(Boolean);
  if (!qs.length) { const raw = q.trim().toLowerCase(); return (...f) => f.some((x) => String(x ?? "").toLowerCase().includes(raw)); }
  const short = qs.every((k) => k.length < 3);
  return (...fields) => fields.some((x) => searchForms(x).some((f) => {
    if (!short) { const n = squash(f); return qs.some((k) => n.includes(k)); }
    const w = f.split(/[^a-z0-9]+/).filter(Boolean);       // from each word start to the end, squashed
    return w.some((_, i) => { const n = w.slice(i).join(""); return qs.some((k) => n.startsWith(k)); });
  }));
}
const CID = "setup-" + Math.random().toString(36).slice(2);
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
  return r.json();
};
let toastT;
const toast = (m) => { const t = $("toast"); t.textContent = m; t.classList.remove("hidden"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), 3500); };
const fmt = (t) => new Date(t * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

// ---------------------------------------------------------------- tabs
const loaders = {};
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
// the DVR tab is left out while recordings are turned off (Settings › DVR)
const dvrTab = (on) => document.querySelector('#tabs button[data-tab="dvr"]').classList.toggle("hidden", !on);
// Settings › Screens › Multiview: how many channels a screen can show at once, with these playlists
function mvCapNote(s) {
  const el = $("mv-cap"); if (!el || !s) return;
  const n = s.multiview_max || 1;
  el.textContent = s.multiview_enabled === false ? "" : n > 1 ? `Now: up to ${n} at once.` : "Now: one at a time, so it's left out (your playlists allow one connection).";
}
api("/api/settings").then((s) => { dvrTab(s.dvr_enabled !== false); mvCapNote(s); window.tbTheme && tbTheme.server(s.theme); }).catch(() => {});
function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll("main > section").forEach((s) => s.classList.toggle("hidden", s.id !== "tab-" + name));
  location.hash = name;
  loaders[name] && loaders[name]();
}

// ---------------------------------------------------------------- overview: is everything working?
const ago2 = (t) => { if (!t) return "never"; const s = Date.now() / 1000 - t; return s < 120 ? "just now" : s < 7200 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`; };
async function badges(o) {
  try { o = o || await api("/api/overview"); } catch (_) { return; }
  $("nb-channels").textContent = o.weak || "";
  $("nb-library").textContent = o.library.review || "";
  return o;
}
loaders.overview = async () => {
  let o;
  try { o = await api("/api/overview"); } catch (e) { toast(e.message); return; }
  badges(o);
  const card = (tab, k, v, s, cls = "", warn = false) => `<button class="ov-card${warn ? " warn" : ""}" data-go="${tab}"><div class="k">${k}</div><div class="v">${v}</div><div class="s ${cls}">${s}</div></button>`;
  const srcOk = o.playlists.filter((p) => p.enabled && !/^error|fail/i.test(p.status || "")).length;
  const active = o.playlists.filter((p) => p.enabled).length;
  const free = o.dvr.free == null ? "" : `${(o.dvr.free / 1e9).toFixed(o.dvr.free < 1e10 ? 1 : 0)} GB free`;
  $("ov-cards").innerHTML = [
    card("channels", "Lineup channels with a feed", `${o.mapped.toLocaleString()} / ${o.lineup.toLocaleString()}`,
         o.weak ? `${o.weak} weak match${o.weak === 1 ? "" : "es"} to review` : o.lineup ? "● All matches look good" : "No lineup yet", o.weak ? "warn" : o.lineup ? "ok" : "", !!o.weak),
    card("extras", "Extra channels", o.extras.toLocaleString(), "Playlist channels outside your lineup, numbered from 20000"),
    card("sources", "Playlists", active,
         !active ? "Add a playlist to get channels" : srcOk === active ? `● ${active === 1 ? "Online" : "All online"} · scanned ${ago2(o.scanned)}` : `${active - srcOk} with a problem`,
         !active ? "warn" : srcOk === active ? "ok" : "bad", !active || srcOk !== active),
    card("guide", "Guide listings", o.guide.programs.toLocaleString(),
         o.guide.updated ? `● Updated ${ago2(o.guide.updated)}${o.guide.gracenote ? ` · ${esc(o.guide.postal_code)}` : ""}` : "Not downloaded yet", o.guide.updated ? "ok" : "warn"),
    card("dvr", "DVR", o.dvr.recording ? `● ${o.dvr.recording} recording` : `${o.dvr.scheduled} scheduled`,
         [o.dvr.recording ? `${o.dvr.scheduled} scheduled` : "", free].filter(Boolean).join(" · ") || "Nothing scheduled", o.dvr.recording ? "bad" : ""),
    card("library", "My Library", o.libraries ? `${o.library.movies} · ${o.library.shows}` : "Off",
         o.libraries ? (o.library.review ? `movies · shows · ${o.library.review} to review` : "movies · shows") : "Add your own movie and TV folders", o.library.review ? "warn" : "", !!o.library.review),
  ].join("");
  const steps = [];
  if (!active) steps.push('<a data-go="sources">Add a playlist</a> (your M3U link or Xtream login), then press <b>Scan playlists</b>');
  if (o.lineup && !o.guide.gracenote) steps.push('<a data-go="guide">Pick your TV provider</a> so channel numbers match yours');
  if (o.weak) steps.push(`<a data-go="channels">Review ${o.weak} weak match${o.weak === 1 ? "" : "es"}</a> in the channel lineup`);
  $("ov-next").innerHTML = steps.length ? `<b>Next steps</b><ol>${steps.map((x) => `<li>${x}</li>`).join("")}</ol>` : "";
  $("ov-src").innerHTML = o.playlists.length ? o.playlists.map((p) => `<div class="row">
      <span class="dot ${!p.enabled ? "" : /^error|fail/i.test(p.status || "") ? "dead" : "ok"}"></span>
      <b>${esc(p.name)}</b><span class="tag">${esc(p.type)}</span><span class="muted small">${p.count.toLocaleString()} streams</span>
      <span class="grow"></span><span class="muted small">${p.enabled ? esc(p.status || "") : "off"}</span>
      <button class="btn small" data-go="sources">Open</button></div>`).join("")
    : '<p class="muted">No playlists yet.</p>';
};
document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]"); if (!go) return;
  e.preventDefault(); showTab(go.dataset.go); window.scrollTo(0, 0);
});
$("ov-scan").onclick = () => startJob("scan");
$("ov-rematch").onclick = () => startJob("match");
badges();

// ---------------------------------------------------------------- jobs
async function startJob(name, q = "") {
  try {
    const r = await api(`/api/jobs/${name}${q}`, { method: "POST" });
    if (r.started === "queued") toast(`Queued: starts after ${r.after || "the job that's running"}`);
    else if (!r.started) toast("Another job is already running");
    pollJob();
  } catch (e) { toast(e.message); }
}
let polling = false;
// two lanes run side by side: playlists, On Demand and the guide (the top of /api/jobs), and My Library ("library").
// Each has its row in the job bar; each one's result is shown when it finishes
function jobRow(row, text, bar, j, prefix = "") {
  $(row).classList.toggle("hidden", !j.running);
  if (!j.running) return;
  $(text).textContent = `${prefix}${j.stage}${j.total ? ` (${j.done}/${j.total})` : ""}`;
  $(bar).style.width = j.total ? `${j.done / j.total * 100}%` : "15%";
}
async function pollJob() {
  if (polling) return; polling = true;
  const was = { main: false, lib: false, vid: false };
  let ticks = 0;
  const reload = () => { badges(); const cur = location.hash.slice(1) || "overview"; loaders[cur] && !(cur === "sources" && editing()) && loaders[cur](); };
  for (;;) {
    let j; try { j = await api("/api/jobs"); } catch (_) { break; }
    const lib = j.library || {}, vid = j.videos || {};
    // reading On Demand: its categories arrive before the titles, so keep the playlist cards current
    if (j.running && j.name.startsWith("vod") && ++ticks % 2 === 0 && (location.hash.slice(1) || "overview") === "sources" && $("groups").classList.contains("hidden") && !editing()) loaders.sources();
    jobRow("job-main", "job-text", "job-bar", j);
    jobRow("job-lib", "lib-job-text", "lib-job-bar", lib, "My Library: ");
    // grabbing video details has its own row, so a scan finding new videos shows beside it.
    // The NAS stopped answering: nothing runs until it starts again by itself
    const resumeIn = !vid.running && vid.resume_at ? Math.max(0, Math.round(vid.resume_at - Date.now() / 1000)) : -1;
    if (resumeIn >= 0) jobRow("job-vid", "vid-job-text", "vid-job-bar",
      { running: true, stage: `the NAS stopped answering; grabbing video details starts again in ${resumeIn < 60 ? `${resumeIn} s` : `${Math.ceil(resumeIn / 60)} min`}`, total: 0 }, "My Library: ");
    else jobRow("job-vid", "vid-job-text", "vid-job-bar", vid, "My Library: ");
    const busy = j.running || lib.running || vid.running || resumeIn >= 0;
    $("job").classList.toggle("hidden", !busy);
    if (was.main && !j.running) { toast(j.message); reload(); }
    if (was.lib && !lib.running) { toast(lib.message); reload(); }
    if (was.vid && !vid.running) { toast(vid.message); reload(); }
    was.main = !!j.running; was.lib = !!lib.running; was.vid = !!vid.running;
    if (!busy) break;
    await new Promise((r) => setTimeout(r, 1500));
  }
  polling = false;
}
$("btn-scan").onclick = () => startJob("scan");
$("btn-scan2").onclick = () => startJob("scan", $("force-probe").checked ? "?force=1" : "");
$("btn-rematch").onclick = () => startJob("match");
$("btn-epg").onclick = () => startJob("epg");

// ---------------------------------------------------------------- lineup
let lineup = [];
const openFeeds = new Set();        // lineup numbers whose feed list is shown
loaders.channels = async () => {
  lineup = await api("/api/lineup");
  renderLineup();
};
const isWeak = (c) => c.stream && c.mode === "auto" && (c.score < 92 || /network/i.test(c.reason));
function renderLineup() {
  const q = $("ch-search").value.trim();
  const f = $("ch-filter").value, hit = matcher(q);
  const rows = lineup.filter((c) => {
    if (f === "mapped" && !c.stream) return false;
    if (f === "unmapped" && (c.stream || c.mode === "none")) return false;
    if (f === "manual" && c.mode !== "manual") return false;
    if (f === "none" && c.mode !== "none") return false;
    if (f === "weak" && !isWeak(c)) return false;
    if (!q) return true;
    return String(c.number).startsWith(q) || hit(c.name, c.callsign, c.affiliate, c.stream && feedName(c.stream));
  });
  const mapped = lineup.filter((c) => c.stream).length;
  $("ch-stats").textContent = `${mapped} of ${lineup.length} lineup channels have a feed · showing ${rows.length}`;
  $("ch-body").innerHTML = rows.map((c) => {
    const s = c.stream;
    const tag = c.mode === "none" ? '<span class="tag none">OFF</span>'
      : c.mode === "manual" ? '<span class="tag manual">MANUAL</span>'
      : s ? `<span class="tag ${isWeak(c) ? "weak" : "auto"}" title="${esc(c.reason)}">AUTO ${c.score}${c.alts ? ` +${c.alts}` : ""}</span>` : "";
    const feed = s ? `<div class="feed"><span class="dot ${s.status}" title="${s.status}"></span>${s.logo ? `<img src="${esc(safePic(s.logo))}" onerror="this.remove()">` : ""}
        <div><div>${esc(feedName(s))}</div><div class="meta">${esc(s.source_name)}${s.grp ? " · " + esc(s.grp) : ""}${s.tvg_id ? " · " + esc(s.tvg_id) : ""}</div></div></div>`
      : '<span class="muted">—</span>';
    return `<tr data-n="${c.number}"${openFeeds.has(c.number) ? ' class="fd-open"' : ""}><td class="num">${c.number}</td>
      <td>${esc(c.name)}<div class="cs">${esc(c.callsign || "")}${c.affiliate ? " · " + esc(c.affiliate) : ""}</div></td>
      <td>${feed}</td><td>${tag}</td>
      <td class="acts">${hasFeedList(c) ? `<button class="btn small" data-a="feeds" aria-expanded="${openFeeds.has(c.number)}" title="See every feed matched to this channel">Feeds ${c.feeds.length} ${openFeeds.has(c.number) ? "&#9652;" : "&#9662;"}</button>` : ""}<button class="btn small" data-a="pick">Change</button>${s ? '<button class="btn small" data-a="play">&#9654;</button>' : ""}
      ${c.mode === "none" ? "" : '<button class="btn small" data-a="off" title="Never map this channel">Off</button>'}
      ${c.mode === "manual" || c.mode === "none" ? '<button class="btn small" data-a="auto" title="Go back to automatic matching">Auto</button>' : ""}</td></tr>`
      + (openFeeds.has(c.number) ? feedRows(c) : "");
  }).join("");
}
// every feed on a channel: the main one, then the backups tried when it fails. Wrong ones can be removed.
// (also once every feed is removed, so "Allow again" stays reachable)
const hasFeedList = (c) => c.removed > 0 || (c.mode === "auto" && c.feeds.length > 1);
// ▲ ▼ among the backups (moving Backup 1 up, or Main down, is what Make main does). With three feeds each
// backup has one arrow, so they share one slot; with more, ▲ and ▼ keep their own slots, a missing one
// hidden, so the buttons line up from row to row. Two feeds: Make main alone.
const chev = (up) => `<svg viewBox="0 0 10 10" width="10" height="10" aria-hidden="true"><path d="${up ? "M2 6.5 5 3.5 8 6.5" : "M2 3.5 5 6.5 8 3.5"}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
function moveBtns(i, last) {
  if (last < 2) return "";
  const btn = (up, on) => on
    ? `<button class="btn small mv" data-a="${up ? "fup" : "fdown"}" title="${up ? "Try this backup sooner" : "Try this backup later"}" aria-label="${up ? "Move up" : "Move down"}">${chev(up)}</button>`
    : '<span class="mv-gap"></span>';
  const up = i > 1, down = i < last;
  return `<span class="fd-move">${last === 2 ? btn(up, true) : btn(true, up) + btn(false, down)}</span>`;
}
function feedRows(c) {
  const canRemove = c.mode === "auto", canOrder = canRemove && c.feeds.length > 1, last = c.feeds.length - 1;
  const items = c.feeds.map((s, i) => `<li data-k="${esc(s.key)}" data-nm="${esc(feedName(s))}" data-i="${i}">
      <span class="fd-role">${i ? `Backup ${i}` : "Main"}</span><span class="dot ${s.status}" title="${s.status}"></span>
      <div class="nm"><div>${esc(feedName(s))}</div><div class="meta">${esc(s.source_name)}${s.grp ? " · " + esc(s.grp) : ""}${s.tvg_id ? " · " + esc(s.tvg_id) : ""}</div></div>
      ${canOrder && i ? `${moveBtns(i, last)}
        <button class="btn small" data-a="fmain" title="Play this feed first">Make main</button>` : ""}
      <button class="btn small" data-a="fprev" title="Preview">&#9654;</button>
      ${canRemove ? '<button class="btn small" data-a="fdel" title="Not this channel: never match it here again">Remove</button>' : ""}</li>`).join("");
  const foot = c.removed ? `<div class="fd-foot muted small">${c.removed} feed${c.removed > 1 ? "s" : ""} removed from this channel.
      <button class="btn small" data-a="frestore">Allow again</button></div>` : "";
  const ord = c.ordered ? `<div class="fd-foot muted small">You chose this order; re-matching keeps it.
      <button class="btn small" data-a="freset">Use best-match order</button></div>` : "";
  return `<tr class="fd-row" data-n="${c.number}"><td></td><td colspan="4">
    <div class="muted small">Feeds for this channel. The main one plays; if it fails, the backups are tried in order.</div>
    <ul class="fd-list">${items || '<li class="muted">No feeds left.</li>'}</ul>${ord}${foot}</td></tr>`;
}
$("ch-search").oninput = renderLineup;
$("ch-filter").onchange = renderLineup;
$("ch-body").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const n = +b.closest("tr").dataset.n;
  const c = lineup.find((x) => x.number === n);
  if (b.dataset.a === "pick") openPicker(c);
  if (b.dataset.a === "play") { openPicker(c); preview(c.stream.key, feedName(c.stream)); }
  if (b.dataset.a === "off") { await api("/api/mapping", { method: "POST", body: { number: n, mode: "none" } }); loaders.channels(); }
  if (b.dataset.a === "auto") { await api("/api/mapping", { method: "POST", body: { number: n, mode: "auto" } }); toast("Re-matching…"); pollJob(); }
  if (b.dataset.a === "feeds") { openFeeds.has(n) ? openFeeds.delete(n) : openFeeds.add(n); renderLineup(); }
  const li = b.closest("li");
  if (b.dataset.a === "fprev") { openPicker(c); preview(li.dataset.k, li.dataset.nm); }
  if (b.dataset.a === "fdel") {
    await api("/api/mapping/remove-feed", { method: "POST", body: { number: n, stream_key: li.dataset.k } });
    toast(`Removed ${li.dataset.nm} from channel ${n}`); loaders.channels();
  }
  if (["fup", "fdown", "fmain"].includes(b.dataset.a)) {
    const keys = c.feeds.map((f) => f.key), i = +li.dataset.i;
    const to = b.dataset.a === "fmain" ? 0 : b.dataset.a === "fup" ? i - 1 : i + 1;
    keys.splice(to, 0, keys.splice(i, 1)[0]);
    await api("/api/mapping/feed-order", { method: "POST", body: { number: n, keys } });
    if (to === 0) toast(`${li.dataset.nm} is now the main feed for channel ${n}`);
    loaders.channels();
  }
  if (b.dataset.a === "freset") {
    await api("/api/mapping/feed-order", { method: "POST", body: { number: n, keys: [] } });
    toast("Re-matching…"); pollJob();
  }
  if (b.dataset.a === "frestore") {
    await api("/api/mapping/restore-feeds", { method: "POST", body: { number: n } });
    toast("Re-matching…"); pollJob();
  }
});

// ---------------------------------------------------------------- picker
let pickFor = null, player = null, searchT, pkSeq = 0;
function openPicker(c) {
  pickFor = c;
  pkSources();
  $("pk-title").textContent = `${c.number}  ${c.name}`;
  $("pk-sub").textContent = c.stream ? `Currently: ${feedName(c.stream)} (${c.stream.source_name})` : "No feed assigned";
  $("pk-search").value = ""; $("pk-now").textContent = ""; $("pk-src").value = "";
  $("picker").classList.remove("hidden");
  loadSuggestions();
  $("pk-search").focus();
}
function closePicker() { stopPreview(); $("picker").classList.add("hidden"); pickFor = null; }
$("pk-close").onclick = closePicker;
$("picker").addEventListener("click", (e) => { if (e.target.id === "picker") closePicker(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && pickFor) closePicker(); });
$("pk-search").oninput = () => { clearTimeout(searchT); searchT = setTimeout(loadSuggestions, 250); };
$("pk-src").onchange = () => { $("pk-search").focus(); loadSuggestions(); };
// the playlists to pick from (enabled ones: the only ones a channel can play from). Hidden with just one.
async function pkSources() {
  let list = [];
  try { list = (await api("/api/sources")).filter((s) => s.enabled); } catch (_) { /* keep "All playlists" */ }
  const sel = $("pk-src"), keep = sel.value;
  sel.innerHTML = '<option value="">All playlists</option>' + list.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  sel.value = list.some((s) => String(s.id) === keep) ? keep : "";
  sel.classList.toggle("hidden", list.length < 2);
}
async function loadSuggestions() {
  const q = $("pk-search").value.trim(), src = $("pk-src").value, seq = ++pkSeq;
  const sel = $("pk-src").selectedOptions[0], where = src && sel ? ` in ${sel.textContent}` : "";
  $("pk-hint").textContent = q ? `Results for "${q}"${where}:` : `Best guesses for this channel${where}:`;
  $("pk-search").placeholder = where ? `Search channels${where}…` : "Search all playlist channels…";
  const list = await api(`/api/suggest?limit=80&${q ? "q=" + encodeURIComponent(q) : "number=" + pickFor.number}${src ? "&source_id=" + src : ""}`);
  if (seq !== pkSeq) return;          // a newer search or playlist pick already asked
  $("pk-results").innerHTML = list.length ? list.map((s) => `<li data-k="${esc(s.key)}" data-n="${esc(feedName(s))}">
      <span class="dot ${s.status}" title="${s.status}"></span>
      <div class="nm"><div>${esc(feedName(s))}</div><div class="muted small">${esc(s.source_name)}${s.grp ? " · " + esc(s.grp) : ""}${s.tvg_id ? " · " + esc(s.tvg_id) : ""}</div></div>
      <span class="sc">${s.score}</span>
      <button class="btn small" data-a="prev">&#9654;</button><button class="btn small primary" data-a="use">Use</button></li>`).join("")
    : '<li class="muted">Nothing found. Try another search.</li>';
}
$("pk-results").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const li = b.closest("li");
  if (b.dataset.a === "prev") preview(li.dataset.k, li.dataset.n);
  if (b.dataset.a === "use") {
    await api("/api/mapping", { method: "POST", body: { number: pickFor.number, stream_key: li.dataset.k, mode: "manual" } });
    toast(`Channel ${pickFor.number} → ${li.dataset.n}`);
    closePicker(); loaders.channels();
  }
});
function stopPreview() {
  if (player) { try { player.destroy(); } catch (_) {} player = null; }
  api("/api/stop", { method: "POST", body: { client: CID } }).catch(() => {});
}
function preview(key, name) {
  stopPreview();
  $("pk-now").textContent = `Loading ${name}…`;
  const v = $("pk-video");
  player = mpegts.createPlayer({ type: "mpegts", isLive: true, url: `${location.origin}/api/preview/${encodeURIComponent(key)}?client=${CID}` }, { enableWorker: true });
  player.attachMediaElement(v); player.load(); player.play();
  player.on(mpegts.Events.ERROR, () => { $("pk-now").textContent = `${name}: no signal`; });
  v.onplaying = () => { $("pk-now").textContent = `Playing: ${name}`; };
}

// ---------------------------------------------------------------- extras
let extras = [];
loaders.extras = async () => {
  extras = await api("/api/extras");
  const groups = [...new Set(extras.map((e) => e.grp || ""))].sort();
  const sel = $("ex-group"), cur = sel.value;
  sel.innerHTML = '<option value="">All groups</option>' + groups.map((g) => `<option ${g === cur ? "selected" : ""}>${esc(g)}</option>`).join("");
  renderExtras();
};
function renderExtras() {
  const q = $("ex-search").value.trim(), g = $("ex-group").value, hit = matcher(q);
  const rows = extras.filter((e) => (!g || e.grp === g) && (!q || String(e.number).startsWith(q) || hit(e.name)));
  $("ex-stats").textContent = `${extras.filter((e) => e.enabled).length} shown of ${extras.length} · listing ${Math.min(rows.length, 1500)}`;
  $("ex-body").innerHTML = rows.slice(0, 1500).map((e) => `<tr data-k="${esc(e.stream_key)}">
    <td><input class="numedit" type="number" value="${e.number}"></td>
    <td><div class="feed"><span class="dot ${e.status}"></span>${e.logo ? `<img src="${esc(safePic(e.logo))}" onerror="this.remove()">` : ""}${esc(e.name)}</div></td>
    <td>${esc(e.grp)}</td><td class="muted">${esc(e.source_name)}</td>
    <td><input type="checkbox" ${e.enabled ? "checked" : ""}></td>
    <td class="acts"><button class="btn small" data-a="prev">&#9654;</button></td></tr>`).join("");
}
$("ex-search").oninput = renderExtras;
$("ex-group").onchange = renderExtras;
$("ex-body").addEventListener("change", async (e) => {
  const tr = e.target.closest("tr"); const key = tr.dataset.k;
  try {
    if (e.target.type === "checkbox") await api("/api/extras", { method: "POST", body: { stream_key: key, enabled: e.target.checked } });
    else await api("/api/extras", { method: "POST", body: { stream_key: key, number: +e.target.value } });
    const x = extras.find((x) => x.stream_key === key);
    if (e.target.type === "checkbox") x.enabled = e.target.checked ? 1 : 0; else x.number = +e.target.value;
    toast("Saved");
  } catch (err) { toast(err.message); loaders.extras(); }
});
$("ex-body").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const tr = b.closest("tr"); const x = extras.find((x) => x.stream_key === tr.dataset.k);
  pickFor = { number: x.number, name: x.name };
  $("pk-title").textContent = `${x.number}  ${x.name}`; $("pk-sub").textContent = "Preview";
  $("picker").classList.remove("hidden"); $("pk-results").innerHTML = "";
  preview(x.stream_key, x.name);
});
const bulk = async (on) => {
  const g = $("ex-group").value; if (!g) { toast("Pick a group first"); return; }
  await api("/api/extras/bulk", { method: "POST", body: { group: g, enabled: on } }); loaders.extras();
};
$("ex-on").onclick = () => bulk(true);
$("ex-off").onclick = () => bulk(false);

// ---------------------------------------------------------------- sources
// A playlist's card only shows how it is set up; every change is made in its Edit dialog (below), so nothing
// changes by a stray click, such as turning On Demand off, which drops the playlist's whole movie list.
let srcList = [];
// the playlist dialog is open: don't redraw the cards under it
const editing = () => !$("src-dlg").classList.contains("hidden");
const VIDEO = { "": "Default", copy: "Original", h264: "H.264", roku720: "720p" };
const srcKind = (s) => ({ xtream: "Xtream", hdhomerun: "HDHomeRun" }[s.type] || "M3U");
const connAuto = (s) => (s.conn_auto ? `${s.conn_auto} at a time, from your ${s.type === "hdhomerun" ? "tuner" : "provider"}` : "no limit");
const noPass = (u) => String(u || "").replace(/(password=)[^&]+/i, "$1…");
const hostOf = (u) => { try { return new URL(u).host; } catch (_) { return ""; } };
// The card's settings: label, value, and a fuller explanation on hover
function srcSettings(s) {
  const own = +s.max_conn || 0, hd = s.type === "hdhomerun";
  const set = [["Video", VIDEO[s.video_mode || ""] || s.video_mode, s.video_mode ? "" : "As set under Settings"],
               [hd ? "Tuners" : "Connections", own || `Auto${s.conn_auto ? ` (${s.conn_auto})` : ""}`, own ? "Set for this playlist" : `Automatic: ${connAuto(s)}`]];
  if (!hd) {
    const on = s.type === "xtream" ? (s.vod_movies && s.vod_series ? "Movies & shows" : s.vod_movies ? "Movies" : s.vod_series ? "TV shows" : "")
      : s.vod_movies ? "On" : "";
    set.push(["On Demand", on || "Off", ""]);
  }
  set.push([hd ? "Channels" : "Groups", s.hidden ? `${s.hidden.toLocaleString()} hidden` : "All shown", `Pick them with ${hd ? "Channels…" : "Channel groups…"}`]);
  const acc = srcAccount(s);
  if (acc) set.push(["Expires", acc.value, acc.tip, acc.level]);
  if (s.epg_url) {
    const g = hostOf(s.epg_url), same = g && g === hostOf(s.url);
    set.push(["Guide", same ? "From provider" : g || "File", noPass(s.epg_url).replace(/(username=)[^&]+/i, "$1…")]);
  }
  return set.map(([k, v, tip, lvl]) => `<div${tip ? ` title="${esc(tip)}"` : ""}><dt>${k}</dt><dd class="${v === "Off" ? "muted" : lvl ? `exp-${lvl}` : ""}">${esc(v)}</dd></div>`).join("");
}
// Xtream account: when it expires (from the provider, read at each scan). Warn 14 days ahead.
const EXP_WARN_DAYS = 14;
const dateOnly = (t) => new Date(t * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
function srcAccount(s) {
  const a = s.account;
  if (s.type !== "xtream" || !a) return null;
  const days = a.exp ? Math.ceil((a.exp * 1000 - Date.now()) / 86400000) : null;
  const status = a.status && a.status.toLowerCase() !== "active" ? a.status : "";
  const when = days === null ? "" : days < 0 ? `ended ${-days === 1 ? "yesterday" : `${-days} days ago`}`
    : days === 0 ? "today" : days === 1 ? "tomorrow" : `in ${days} days`;
  const value = a.exp ? dateOnly(a.exp) : "Never";
  const tip = [a.exp ? `Expires ${when}` : "No expiry date from the provider", a.status && `Status: ${a.status}`,
               a.trial && "Trial account", a.created && `Created ${dateOnly(a.created)}`, a.checked && `Checked ${fmt(a.checked)}`]
    .filter(Boolean).join(" · ");
  const level = status || (days !== null && days < 0) ? "bad" : days !== null && days <= EXP_WARN_DAYS ? "warn" : "";
  const note = status ? `Account ${status.toLowerCase()}${a.exp ? ` · expires ${dateOnly(a.exp)}` : ""}`
    : level === "bad" ? `Subscription ended ${dateOnly(a.exp)}` : level === "warn" ? `Subscription ends ${when} (${dateOnly(a.exp)})` : "";
  return { value, tip, level, note, trial: a.trial };
}
// the last scan: fine (green, when), failed (red, why), or not yet
function srcStatus(s) {
  const st = s.status || "";
  // failed (often an antenna tuner that was off, busy or slow to answer): offer to read just this one again
  if (st.startsWith("error")) return `<div class="src-st bad"><span class="dot dead"></span><span class="src-why" title="${esc(st)}">${esc(st.replace(/^error:\s*/, "Scan failed: "))}</span>${s.enabled ? '<button class="btn small" data-a="rescan" title="Read this playlist again">Rescan</button>' : ""}</div>`;
  if (!st) return '<div class="src-st"><span class="dot unknown"></span>Not scanned yet</div>';
  return `<div class="src-st"><span class="dot ok"></span>Scanned${s.updated_at ? ` ${fmt(s.updated_at)}` : ""}</div>`;
}
const stat = (n, what) => `<div><b>${(+n || 0).toLocaleString()}</b><span>${what}</span></div>`;
loaders.sources = async () => {
  const list = srcList = await api("/api/sources");
  $("src-list").innerHTML = list.length ? list.map((s) => {
    const hd = s.type === "hdhomerun";
    return `<div class="card src-card${s.enabled ? "" : " off"}" data-id="${s.id}">
      <div class="card-head"><div class="t" title="${esc(s.name)}">${esc(s.name)}</div>
        <span class="tag">${srcKind(s)}</span>${srcAccount(s)?.trial ? '<span class="tag trial">Trial</span>' : ""}${s.enabled ? "" : '<span class="tag none">Disabled</span>'}</div>
      <div class="u" title="${esc(noPass(s.url))}">${esc(hostOf(s.url) || noPass(s.url))}${s.username ? ` · ${esc(s.username)}` : ""}</div>
      ${srcStatus(s)}${(() => { const a = srcAccount(s); return a && a.note ? `<div class="src-st exp-${a.level}" title="${esc(a.tip)}"><span class="dot ${a.level === "bad" ? "dead" : "warn"}"></span>${esc(a.note)}</div>` : ""; })()}
      <div class="src-stats">${stat(s.count, "channels")}${hd ? "" : stat(s.vod.movies, "movies") + stat(s.vod.series, "shows")}</div>
      <dl class="src-set">${srcSettings(s)}</dl>
      ${s.exclude_groups ? `<div class="muted small src-skip">Skipping groups matching <code>${esc(s.exclude_groups)}</code></div>` : ""}
      <div class="src-foot"><button class="btn small" data-a="groups">${hd ? "Channels…" : "Channel groups…"}</button>
        <span class="src-acts"><button class="btn small primary" data-a="edit">Edit</button>
        <button class="btn small" data-a="toggle">${s.enabled ? "Disable" : "Enable"}</button>
        <button class="btn small ghost-danger" data-a="del" title="Delete this playlist">Delete</button></span></div></div>`;
  }).join("")
    : '<p class="muted">No playlists yet. Press <b>+ Add playlist</b> to add your M3U link, Xtream login or HDHomeRun tuner.</p>';
  // the Edit dialog is open: its category counts follow (a new On Demand list being read, categories saved)
  if (srcEditing) { srcEditing = srcList.find((x) => x.id === srcEditing.id) || srcEditing; renderSrcVod(true); }
};
// How many streams TunerBox opens on a playlist at once. Automatic: what an Xtream account or HDHomeRun
// says (no limit for a plain M3U, which can't say). Set it for an M3U provider that has a limit.
function connOptions(s) {
  const own = +s.max_conn || 0;
  // 1-10, and a larger number set some other way (backup import, API: up to 99) so it shows as set
  const opts = Array.from({ length: Math.max(10, own) }, (_, i) => i + 1).filter((n) => n <= 10 || n === own)
    .map((n) => `<option value="${n}" ${own === n ? "selected" : ""}>${n}</option>`).join("");
  return `<option value="0" ${own ? "" : "selected"}>Automatic (${s.id ? connAuto(s) : "from your provider or tuner"})</option>${opts}`;
}
// On Demand in the dialog: a switch each for movies and TV shows (an Xtream login), or one for the video
// files of a plain M3U (which can be movies and episodes), with the categories and labels to pick from.
// The categories can only be picked once On Demand is saved and the list has been read.
function vodRows(s) {
  const added = !!s.id;                             // false while adding a playlist
  const cats = (kind) => (added && s.vod ? s.vod.cats[kind] : [0, 0]);
  const catNote = (kind) => { const [shown, all] = cats(kind); return all ? (shown === all ? `All ${all} categories shown` : `${shown} of ${all} categories shown`) : "The categories are read with the list"; };
  const btn = (kind, on) => `<button type="button" class="btn small" data-a="vodcats" data-kind="${kind}" ${on ? "" : "disabled"}>${kind === "tags" ? "Languages…" : "Categories…"}</button>`;
  const [tShown, tAll] = (added && s.vod && s.vod.tags) || [0, 0];
  const tagNote = tAll < 2 ? "Labels like EN, DE or 4K in front of the names are read with the titles"
    : tShown === tAll ? `All ${tAll} labels shown (languages like EN and DE, and platforms like NF)` : `${tShown} of ${tAll} labels shown`;
  const tagRow = () => (s.vod_movies || s.vod_series) && tAll > 1 ? row("", "tags", "Languages and labels", tagNote, true) : "";
  const sw = (key) => `<input type="checkbox" class="toggle" id="vod-dlg-${key}" name="${key}" ${s[key] ? "checked" : ""}>`;
  const row = (key, kind, title, note, on) => `<div class="vod-row"><label class="vod-t" for="vod-dlg-${key}"><b>${title}</b><small class="muted">${note}</small></label>
      ${kind ? btn(kind, on) : "<span></span>"}${key ? sw(key) : "<span></span>"}</div>`;
  const hint = '<p class="muted small hidden" id="src-vod-hint"></p>';
  if (s.type === "xtream") {
    return `<div class="vod-box">
      ${row("vod_movies", "movie", "Movies", s.vod_movies ? catNote("movie") : "Off", s.vod_movies && added)}
      ${row("vod_series", "series", "TV shows", s.vod_series ? catNote("series") : "Off", s.vod_series && added)}
      ${tagRow()}${hint}</div>`;
  }
  const n = added && s.vod ? s.vod.files : 0;
  return `<div class="vod-box">
    ${row("vod_movies", "", "Movie and episode files", `Entries ending in .mp4 or .mkv become titles instead of channels${n ? ` (${n.toLocaleString()} found)` : ""}`)}
    ${s.vod_movies && added ? row("", "movie", "Movies", catNote("movie"), true) + row("", "series", "TV shows", catNote("series"), true) + tagRow() : ""}${hint}</div>`;
}
$("src-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const card = b.closest(".card"), id = card.dataset.id;
  if (b.dataset.a === "edit") { openSrcDlg(srcList.find((s) => s.id === +id)); return; }
  if (b.dataset.a === "groups") { openGroups(+id); return; }
  if (b.dataset.a === "rescan") { b.disabled = true; b.textContent = "Rescanning…"; startJob("scan", `?source=${id}`); return; }
  if (b.dataset.a === "del" && confirm("Delete this playlist?")) await api(`/api/sources/${id}`, { method: "DELETE" });
  if (b.dataset.a === "toggle") await api(`/api/sources/${id}`, { method: "PATCH", body: { enabled: b.textContent === "Enable" ? 1 : 0 } });
  loaders.sources();
});
const srcType = () => {
  const t = $("src-type").value, xt = t === "xtream", hd = t === "hdhomerun";
  document.querySelectorAll(".xt").forEach((el) => el.classList.toggle("hidden", !xt));
  document.querySelectorAll(".hd").forEach((el) => el.classList.toggle("hidden", !hd || !!srcEditing));
  document.querySelectorAll(".hd-hide").forEach((el) => el.classList.toggle("hidden", hd));
  $("src-url-label").textContent = xt ? "Server URL (http://host:port)" : hd ? "Tuner address (IP, e.g. 192.168.1.40)" : "Playlist URL or file path";
  $("src-form").url.placeholder = hd ? "192.168.1.40" : xt ? "http://host:port" : "https://… or C:\\path\\list.m3u";
  $("src-conn-label").textContent = hd ? "Tuners" : "Connections";
  $("src-video").options[2].textContent = `Re-encode to H.264${hd ? " (needed for antenna TV)" : ""}`;
  renderSrcVod(true);
};
$("hd-find").onclick = async () => {
  $("hd-found").textContent = "Looking…";
  let list; try { list = await api("/api/hdhomerun/discover"); } catch (e) { $("hd-found").textContent = e.message; return; }
  if (!list.length) { $("hd-found").textContent = "No tuners found. Type its address instead (see the HDHomeRun app or your router)."; return; }
  $("hd-found").innerHTML = list.map((d) => `<button type="button" class="btn small" aria-pressed="false" data-ip="${esc(d.ip)}" data-name="${esc(d.name)}">${esc(d.name)} ${esc(d.model)} · ${esc(d.ip)} · ${d.tuners} tuners</button>`).join(" ");
  if (list.length === 1) pickTuner($("hd-found").querySelector("button[data-ip]"));   // only one: fill it in for them
};
// fill the address (and the name, if blank) from a discovered tuner, and mark it as the one picked
const pickTuner = (b) => {
  $("hd-found").querySelectorAll("button[data-ip]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  $("src-form").url.value = b.dataset.ip;
  if (!$("src-form").elements.name.value) $("src-form").elements.name.value = b.dataset.name;
};
$("hd-found").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-ip]"); if (b) pickTuner(b);
});
$("src-type").onchange = srcType;

// Adding or editing a playlist happens in one dialog, and everything in it is saved together. Editing can't
// change the type (an M3U can't turn into an Xtream login), and a blank password keeps the saved one.
let srcEditing = null;             // the playlist being edited, null = adding a new one
const vodKeys = (t) => (t === "xtream" ? ["vod_movies", "vod_series"] : t === "hdhomerun" ? [] : ["vod_movies"]);
// keep = keep what was switched in the dialog (redrawn after a type change or new category counts)
function renderSrcVod(keep) {
  const t = $("src-type").value, box = $("src-vod");
  const now = keep ? Object.fromEntries([...box.querySelectorAll("input.toggle")].map((el) => [el.name, el.checked])) : {};
  if (t === "hdhomerun") { box.innerHTML = ""; return; }
  const base = srcEditing || { id: 0, type: t, vod_movies: 0, vod_series: 0 };
  box.innerHTML = vodRows({ ...base, type: t });
  for (const [k, on] of Object.entries(now)) { const el = box.querySelector(`input[name="${k}"]`); if (el) el.checked = on; }
  vodHint();
}
// what saving will do to On Demand, said before it happens
function vodHint() {
  const el = $("src-vod-hint"); if (!el) return;
  const saved = srcEditing || {}, sw = [...$("src-vod").querySelectorAll("input.toggle")];
  const on = sw.some((x) => x.checked && !saved[x.name]), off = sw.some((x) => !x.checked && saved[x.name]);
  el.textContent = off ? "Saving turns this off and removes these titles from On Demand. Where you stopped and My List are kept."
    : on ? `When you save, the list is read from your ${$("src-type").value === "xtream" ? "provider" : "playlist"}. Its categories can be picked here afterwards.` : "";
  el.classList.toggle("hidden", !on && !off);
}
$("src-vod").addEventListener("change", vodHint);
$("src-vod").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-a=vodcats]");
  if (b && srcEditing) openGroups(srcEditing.id, b.dataset.kind);       // opens over this dialog
});
function openSrcDlg(src) {
  const f = $("src-form");
  srcEditing = src || null;
  f.reset(); $("hd-found").textContent = "";
  if (src) {
    for (const k of ["type", "name", "url", "username", "xtream_format", "exclude_groups", "video_mode"]) f.elements[k].value = src[k] || "";
    if (!src.xtream_format) f.elements.xtream_format.value = "ts";
  }
  $("src-conn").innerHTML = connOptions(src || { id: 0 });
  f.elements.type.disabled = !!src;
  f.elements.password.placeholder = src && src.password ? "Unchanged" : "";
  $("src-dlg-title").textContent = src ? `Edit ${src.name}` : "Add a playlist";
  $("src-dlg-sub").textContent = src ? `${srcKind(src)} · nothing changes until you press Save` : "";
  $("src-save").textContent = src ? "Save" : "Add playlist";
  $("src-tip").classList.toggle("hidden", !!src);
  $("src-vod").innerHTML = "";
  srcType();
  $("src-dlg").classList.remove("hidden");
  $("src-dlg").querySelector(".modal-box").scrollTop = 0;
  (src ? f.elements.name : f.elements.type).focus();
}
function closeSrcDlg() { $("src-dlg").classList.add("hidden"); srcEditing = null; }
$("src-add").onclick = () => openSrcDlg();
$("src-dlg-close").onclick = closeSrcDlg;
$("src-cancel").onclick = closeSrcDlg;
// Escape closes the top window only: the categories list opened over this dialog first
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("groups").classList.contains("hidden") && !$("src-dlg").classList.contains("hidden")) closeSrcDlg();
});
srcType();
$("src-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target, data = Object.fromEntries(new FormData(f));
  const t = srcEditing ? srcEditing.type : data.type, xt = t === "xtream", hd = t === "hdhomerun";
  const vod = Object.fromEntries(vodKeys(t).map((k) => [k, f.elements[k] && f.elements[k].checked ? 1 : 0]));
  const max_conn = +data.max_conn || 0;
  if (!srcEditing) {
    let r;
    try { r = await api("/api/sources", { method: "POST", body: { ...data, max_conn: undefined } }); } catch (err) { toast(err.message); return; }
    closeSrcDlg(); toast("Added - scanning…");
    await startJob("scan", `?source=${r.id}`);      // just this playlist: the others are left as they are
    // On Demand and connections are set once it exists: the On Demand list is read after the scan
    const extra = { ...(max_conn ? { max_conn } : {}), ...Object.fromEntries(Object.entries(vod).filter(([, on]) => on)) };
    if (Object.keys(extra).length) {
      try { await api(`/api/sources/${r.id}`, { method: "PATCH", body: extra }); } catch (err) { toast(err.message); }
    }
    loaders.sources();
    return;
  }
  const src = srcEditing;
  const turnedOff = Object.entries(vod).filter(([k, on]) => !on && src[k]);
  if (turnedOff.length && !confirm(`Turn off On Demand ${xt ? (turnedOff.length === 2 ? "movies and TV shows" : turnedOff[0][0] === "vod_movies" ? "movies" : "TV shows") : "video files"} for ${src.name}?\n\n`
      + `Its titles are removed from On Demand${xt ? ", and turning it back on reads the whole list from your provider again" : ""}. Where you stopped and My List are kept.`)) return;
  const body = { name: data.name.trim(), url: data.url.trim(), video_mode: data.video_mode, max_conn, ...vod };
  if (xt) {
    body.username = data.username.trim(); body.xtream_format = data.xtream_format;
    if (data.password.trim()) body.password = data.password.trim();      // blank: keep the saved one
  }
  if (!hd) body.exclude_groups = data.exclude_groups.trim();
  let r;
  try { r = await api(`/api/sources/${src.id}`, { method: "PATCH", body }); } catch (err) { toast(err.message); return; }
  closeSrcDlg();
  const skip = !hd && body.exclude_groups !== (src.exclude_groups || "");
  toast(r.job === "scan" ? "Saved. Reading the playlist again…"
    : r.job === "vod" ? "Saved. Reading On Demand from your provider: the categories show up in Edit in a moment…"
    : r.job === "refresh" ? "Saved. Reading the playlist again for On Demand…"
    : skip ? "Saved - scan playlists to apply the skip pattern" : "Saved");
  loaders.sources();
  if (r.job) pollJob();
};

// export is a plain download link; a phone that isn't paired gets a message instead of an error page
$("src-export").onclick = async (e) => {
  e.preventDefault();
  try {
    const r = await fetch("/api/sources/export");
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
    const name = (/filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "") || [])[1] || "tunerbox-playlists.json";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob()); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  } catch (err) { toast(err.message); }
};
$("src-import").onclick = () => $("src-import-file").click();
$("src-import-file").onchange = async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  let data;
  try { data = JSON.parse(await f.text()); } catch (_) { toast("That file isn't valid JSON"); return; }
  const n = Array.isArray(data.playlists) ? data.playlists.length : 0;
  if (!confirm(`Import ${n} playlist${n === 1 ? "" : "s"} from ${f.name}? Playlists you already have are updated.`)) return;
  try {
    const r = await api("/api/sources/import", { method: "POST", body: data });
    let msg = `Imported: ${r.added} added, ${r.updated} updated`;
    if (r.skipped.length) msg += `, ${r.skipped.length} skipped (${r.skipped.join("; ")})`;
    toast(r.job ? msg + " - scanning…" : r.added + r.updated ? msg + ". Press Scan playlists now once the current job finishes." : msg);
    loaders.sources();
    if (r.job) pollJob();
  } catch (err) { toast(err.message); }
};

// ---------------------------------------------------------------- playlist groups
// Tick/untick whole groups. "US| SPORTS", "DE: NEWS"… can be filtered by their country prefix.
// The same picker edits a playlist's On Demand movie or TV show categories (GR.vod = "movie" / "series").
const GR = { id: 0, vod: false, chans: false, all: [], shown: new Map(), orig: new Set() };   // chans: an antenna, one row per channel
// ... and (GR.vod = "tags") which labels in front of the titles are shown: "EN", "4K-FR", "NF".
const grUrl = () => `/api/sources/${GR.id}/${GR.vod === "tags" ? "vod-tags" : GR.vod ? `vod-cats?kind=${GR.vod}` : "groups"}`;
// "US| NEWS", "DE: SPORT" and, in On Demand categories, "EN - MOVIES"
const regionOf = (name) => { const m = /^\W*([A-Za-z]{2,3})\s*(?:[|:]|-\s)/.exec(name || ""); return m ? m[1].toUpperCase() : ""; };
async function openGroups(id, vod = "") {
  let d;
  GR.id = id; GR.vod = vod;
  try { d = await api(grUrl()); } catch (e) { toast(e.message); return; }
  GR.all = d.groups; GR.chans = !!d.channels;
  GR.shown = new Map(d.groups.map((g) => [g.name, !g.hidden]));
  GR.orig = new Set(d.groups.filter((g) => g.hidden).map((g) => g.name));
  const [what, these] = { movie: ["Movie", "movies"], series: ["TV show", "TV shows"] }[vod] || [];
  const tags = vod === "tags";
  $("gr-title").textContent = tags ? `On Demand languages and labels in ${d.source.name}` : `${vod ? `${what} categories` : GR.chans ? "Channels" : "Channel groups"} in ${d.source.name}`;
  $("gr-sub").textContent = tags ? "Most providers put a label in front of every title: a language (EN, DE, FR), a language and quality (4K-EN) or a platform (NF = Netflix, D+ = Disney+). Tick the ones you want. Labels the provider adds later start unticked."
    : vod ? `Untick a category to leave its ${these} out of On Demand.`
    : GR.chans ? "Untick a channel to leave it out of TunerBox." : "Untick a group to leave its channels out of TunerBox.";
  $("gr-search").placeholder = tags ? "Search labels…" : vod ? "Search categories…" : GR.chans ? "Search channels…" : "Search groups…";
  $("gr-th").textContent = tags ? "Label · example" : vod ? "Category" : GR.chans ? "Channel" : "Group"; $("gr-th-n").textContent = vod ? "Titles" : "Channels";
  $("gr-region").classList.toggle("hidden", tags || GR.chans);
  $("groups").classList.toggle("chans", GR.chans);
  const note = $("gr-note");
  note.textContent = d.partial ? "Groups skipped by an older version aren't listed yet. They appear after the next scan." : "";
  note.classList.toggle("hidden", !d.partial);
  const regions = new Map();
  for (const g of d.groups) { const r = regionOf(g.name); regions.set(r, (regions.get(r) || 0) + 1); }
  const unit = vod ? "categories" : "groups";
  $("gr-region").innerHTML = `<option value="">${vod ? "All languages" : "All countries"}</option>`
    + [...regions].filter(([r]) => r).sort((a, b) => b[1] - a[1]).map(([r, n]) => `<option value="${esc(r)}">${esc(r)} (${n} ${unit})</option>`).join("")
    + (regions.has("") ? `<option value="-">No ${vod ? "language" : "country"} label (${regions.get("")} ${unit})</option>` : "");
  $("gr-search").value = ""; $("gr-show").value = "";
  $("groups").classList.remove("hidden");
  renderGroups();
  $("gr-search").focus();
}
function listedGroups() {
  const q = $("gr-search").value.trim(), reg = $("gr-region").value, show = $("gr-show").value, hit = matcher(q);
  return GR.all.filter((g) => (!q || hit(g.name))
    && (!reg || (reg === "-" ? !regionOf(g.name) : regionOf(g.name) === reg))
    && (!show || (show === "shown") === GR.shown.get(g.name)));
}
function renderGroups() {
  const rows = listedGroups();
  $("gr-body").innerHTML = rows.slice(0, 3000).map((g) => `<tr data-g="${esc(g.name)}" class="${GR.shown.get(g.name) ? "" : "off"}">
    <td class="gr-c"><input type="checkbox" ${GR.shown.get(g.name) ? "checked" : ""}></td>
    <td>${esc(g.name || "(no group)")}${g.example ? ` <small class="muted">e.g. ${esc(g.example)}${g.series && g.movies ? ` · ${g.movies.toLocaleString()} movies, ${g.series.toLocaleString()} shows` : g.series ? " · shows" : ""}</small>` : ""}${g.pattern ? ' <span class="tag none" title="Matches the skip pattern, so it stays out even when ticked">skip pattern</span>' : ""}</td>
    <td class="gr-n">${g.count ? g.count.toLocaleString() : ""}</td></tr>`).join("")
    || `<tr><td colspan="3" class="muted">${GR.vod && !GR.all.length ? "No categories yet: they are read at the next scan." : "Nothing matches."}</td></tr>`;
  groupStats(rows.length);
  // Check/Uncheck all only touch what's listed: say so when a filter narrows the list
  const n = rows.length < GR.all.length ? ` ${rows.length.toLocaleString()} listed` : " all";
  $("gr-all-on").textContent = `Check${n}`;
  $("gr-all-off").textContent = `Uncheck${n}`;
}
function groupStats(listed) {
  let gs = 0, cs = 0, ct = 0;
  for (const g of GR.all) { ct += g.count; if (GR.shown.get(g.name) && !g.pattern) { gs++; cs += g.count; } }
  const changed = GR.all.filter((g) => GR.orig.has(g.name) === GR.shown.get(g.name)).length;
  $("gr-stats").textContent = (GR.chans ? `Keeping ${gs.toLocaleString()} of ${GR.all.length.toLocaleString()} channels `
    : `Keeping ${gs.toLocaleString()} of ${GR.all.length.toLocaleString()} ${GR.vod === "tags" ? "labels" : GR.vod ? "categories" : "groups"} `
    + `(${cs.toLocaleString()} of ${ct.toLocaleString()} ${GR.vod ? "titles" : "channels"}) `) + `· listing ${(listed ?? listedGroups().length).toLocaleString()}`
    + (changed ? ` · ${changed} unsaved change${changed === 1 ? "" : "s"}` : "");
}
function setListed(on, others) {
  const listed = new Set(listedGroups().map((g) => g.name));
  for (const g of GR.all) {
    if (listed.has(g.name)) GR.shown.set(g.name, on);
    else if (others !== undefined) GR.shown.set(g.name, others);
  }
  renderGroups();
}
function closeGroups() { $("groups").classList.add("hidden"); }
$("gr-search").oninput = renderGroups;
$("gr-region").onchange = renderGroups;
$("gr-show").onchange = renderGroups;
$("gr-all-on").onclick = () => setListed(true);      // only the listed (filtered) groups
$("gr-all-off").onclick = () => setListed(false);
$("gr-only").onclick = () => setListed(true, false);
$("gr-body").addEventListener("change", (e) => {
  const tr = e.target.closest("tr"); if (!tr) return;
  GR.shown.set(tr.dataset.g, e.target.checked);
  tr.classList.toggle("off", !e.target.checked);
  groupStats();
});
$("gr-close").onclick = closeGroups;
$("gr-cancel").onclick = closeGroups;
$("groups").addEventListener("click", (e) => { if (e.target.id === "groups") closeGroups(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("groups").classList.contains("hidden")) closeGroups(); });
$("gr-save").onclick = async () => {
  const hidden = GR.all.filter((g) => !GR.shown.get(g.name)).map((g) => g.name);
  let r;
  try { r = await api(grUrl(), { method: "PUT", body: { hidden } }); } catch (e) { toast(e.message); return; }
  closeGroups(); loaders.sources();
  if (GR.vod === "tags") toast(`Saved: On Demand shows ${r.hidden ? `${r.shown} label${r.shown === 1 ? "" : "s"}` : "every label"}`);
  else if (GR.vod) toast(r.job ? "Saved. Reading those categories from your provider…" : "Saved");
  else if (r.shown_again) { const w = GR.chans ? "channels" : "groups"; toast(r.job ? `Saved. Rescanning to add the ${w} back…` : `Saved. Press Scan playlists when the current job finishes to add the ${w} back.`); }
  else if (r.newly_hidden) toast(r.job ? `Saved. Removed ${r.newly_hidden} ${GR.chans ? "channel" : "group"}${r.newly_hidden === 1 ? "" : "s"}, re-matching channels…` : "Saved. Press Re-run auto match when the current job finishes.");
  else toast("Saved");
  if (r.job) pollJob();
};

// ---------------------------------------------------------------- guide
let settings = {};
loaders.guide = async () => {
  settings = await api("/api/settings");
  const g = settings.gracenote;
  $("gn-zip").value = g.postal_code; $("gn-enabled").checked = g.enabled; $("gn-hours").value = String(g.hours);
  $("gn-lineup").innerHTML = g.lineup_id
    ? `<option value="${esc(g.lineup_id)}" data-h="${esc(g.headend_id)}" data-d="${esc(g.device)}">${esc(g.lineup_id)} (current)</option>`
    : '<option value="" data-h="" data-d="X">Enter your ZIP code and press Find providers</option>';
  $("xmltv").value = (settings.xmltv_sources || []).join("\n");
  const [st, j] = await Promise.all([api("/api/stats"), api("/api/jobs")]);
  $("guide-stats").textContent = `${st.programs.toLocaleString()} programs · updated ${j.epg_updated ? fmt(j.epg_updated) : "never"}`;
};
const gnCountry = (zip) => (/^[A-Z]\d[A-Z]/i.test(zip) ? "CAN" : "USA");   // A1A 1A1 = Canada
$("gn-find").onclick = async () => {
  try {
    const zip = $("gn-zip").value.trim().replace(/\s+/g, "").toUpperCase();
    const list = await api(`/api/gracenote/providers?zip=${encodeURIComponent(zip)}&country=${gnCountry(zip)}`);
    $("gn-lineup").innerHTML = list.map((p) => `<option value="${esc(p.lineupId)}" data-h="${esc(p.headendId)}" data-d="${esc(p.device || "-")}"
      ${p.lineupId === settings.gracenote.lineup_id ? "selected" : ""}>${esc(p.name)} - ${esc(p.location)} (${esc(p.type)})</option>`).join("");
    if (!list.length) { toast("No providers found for that ZIP code"); return; }
    $("gn-enabled").checked = true;
    toast("Pick your provider, then press Save");
  } catch (e) { toast(e.message); }
};
$("guide-save").onclick = async () => {
  const o = $("gn-lineup").selectedOptions[0];
  await api("/api/settings", { method: "POST", body: {
    gracenote: { enabled: $("gn-enabled").checked, postal_code: $("gn-zip").value.trim().replace(/\s+/g, "").toUpperCase(),
      country: gnCountry($("gn-zip").value.trim()), lineup_id: o.value,
      headend_id: o.dataset.h, device: o.dataset.d, hours: +$("gn-hours").value },
    xmltv_sources: $("xmltv").value.split(/\n+/).map((s) => s.trim()).filter(Boolean),
  } });
  toast(o.value ? "Saved - press Scan playlists now (Playlists tab) to use these channel numbers" : "Saved");
};

// ---------------------------------------------------------------- dvr
loaders.dvr = async () => {
  const [recs, rules] = await Promise.all([api("/api/recordings"), api("/api/rules")]);
  $("rec-body").innerHTML = recs.map((r) => `<tr data-id="${r.id}"><td><span class="tag">${r.status}</span></td>
    <td>${esc(r.title)}${r.subtitle ? ` <span class="muted">· ${esc(r.subtitle)}</span>` : ""}${r.error ? `<div class="muted small">${esc(r.error)}</div>` : ""}</td>
    <td>${r.number}</td><td>${fmt(r.start)}</td>
    <td class="acts">${r.status === "done" ? `<a class="btn small" href="/api/recordings/${r.id}/file" target="_blank">Open</a>` : ""}
    <button class="btn small danger" data-a="${["scheduled", "recording"].includes(r.status) ? "cancel" : "del"}">${r.status === "scheduled" ? "Cancel" : r.status === "recording" ? "Stop" : "Delete"}</button></td></tr>`).join("")
    || '<tr><td colspan="5" class="muted">Nothing recorded yet. In the TV guide, highlight a show and press R.</td></tr>';
  $("rule-body").innerHTML = rules.map((r) => `<tr data-id="${r.id}"><td>${esc(r.title)}</td><td>${r.number ?? "Any"}</td><td>${r.new_only ? "Yes" : "No"}</td>
    <td class="acts"><button class="btn small danger" data-a="rule">Delete</button></td></tr>`).join("")
    || '<tr><td colspan="4" class="muted">No series recordings.</td></tr>';
};
document.querySelector("#tab-dvr").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const id = b.closest("tr").dataset.id;
  if (b.dataset.a === "cancel") await api(`/api/recordings/${id}`, { method: "DELETE" });
  if (b.dataset.a === "del" && confirm("Delete this recording and its file?")) await api(`/api/recordings/${id}?delete=1`, { method: "DELETE" });
  if (b.dataset.a === "rule") await api(`/api/rules/${id}`, { method: "DELETE" });
  loaders.dvr();
});

// ---------------------------------------------------------------- tuners in use
const TN_WHO = { tv: "📺", dvr: "⏺", app: "🔌", setup: "⚙", roku: "📺" };
// ---------------------------------------------------------------- the log, followed live while its tab is open
const LOG_KEEP = 3000;
const LOG = { pos: -1, lines: [], waiting: [], timer: null, paused: false };
const LOG_LINE = /^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),\d+ (\S+) (DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$/;
const LOG_PLAY = /On Demand: playing|Stream start|Stream end|^Recording: |HLS (start|stop|end)/i;
const LOG_LIB = /^(My Library:|Video details|TMDB)/;
function logShow(stick) {
  const q = $("log-q").value.trim().toLowerCase(), lv = $("log-level").value;
  const keep = LOG.lines.filter((l) => (!q || l.raw.toLowerCase().includes(q))
    && (lv !== "warn" || l.level === "WARNING" || l.level === "ERROR" || l.level === "CRITICAL")
    && (lv !== "play" || LOG_PLAY.test(l.msg)) && (lv !== "lib" || LOG_LIB.test(l.msg) || l.who === "library"));
  const v = $("log-view");
  // at the bottom (or asked to go there): stay at the bottom; scrolled up to read: stay put
  stick = stick || v.scrollHeight - v.scrollTop - v.clientHeight < 30;
  v.innerHTML = keep.map((l) => l.gap ? `<div class="gap">${esc(l.gap)}</div>`
    : `<div class="ln ${l.cls}"><span class="t">${esc(l.time)}</span><span class="w">${esc(l.who)}</span><span class="m">${esc(l.msg)}</span></div>`).join("")
    || `<div class="gap">${LOG.lines.length ? "Nothing matches." : "Nothing logged yet."}</div>`;
  $("log-shown").textContent = q || lv ? `${keep.length} of ${LOG.lines.length} lines` : `${LOG.lines.length} lines`;
  if (stick) v.scrollTop = v.scrollHeight;
}
// Paused: new lines wait (counted on the button) and the view stays as it is until Follow
function logPaused() {
  const b = $("log-follow");
  b.textContent = LOG.paused ? `Follow${LOG.waiting.length ? ` (${LOG.waiting.length} new)` : ""}` : "Pause";
  b.setAttribute("aria-pressed", String(!LOG.paused));
}
function logAdd(lines) {
  LOG.lines.push(...lines);
  if (LOG.lines.length > LOG_KEEP) LOG.lines.splice(0, LOG.lines.length - LOG_KEEP);
}
function logParse(raw) {
  const m = LOG_LINE.exec(raw);
  if (!m) return { raw, time: "", who: "", level: "INFO", msg: raw, cls: "" };   // a traceback's other lines
  const [, , time, who, level, msg] = m;
  const cls = level === "ERROR" || level === "CRITICAL" ? "err" : level === "WARNING" ? "warn" : LOG_PLAY.test(msg) ? "play" : "";
  return { raw, time, who: who.replace(/^tunerbox\./, ""), level, msg, cls };
}
loaders.log = async () => {
  clearTimeout(LOG.timer);
  if ((location.hash.slice(1) || "overview") !== "log") return;
  try {
    const r = await api(`/api/log?after=${LOG.pos}`);
    const first = LOG.pos < 0;
    const fresh = [...(r.restarted && (LOG.lines.length || LOG.waiting.length) ? [{ gap: "The log was started over (it's kept to about 5 MB)", raw: "" }] : []),
                   ...r.lines.map(logParse)];
    if (LOG.paused) { LOG.waiting.push(...fresh); logPaused(); }
    else if (fresh.length || first) { logAdd(fresh); logShow(first); }
    LOG.pos = r.pos;
  } catch (e) { $("log-view").innerHTML = `<div class="gap">${esc(e.message)}</div>`; }
  LOG.timer = setTimeout(loaders.log, 2000);
};
$("log-q").oninput = () => logShow();
$("log-level").onchange = () => logShow();
$("log-follow").onclick = () => {
  LOG.paused = !LOG.paused;
  if (!LOG.paused) { logAdd(LOG.waiting.splice(0)); logShow(true); }     // the lines that waited, then on as before
  logPaused();
};
$("log-clear").onclick = () => { LOG.lines = []; LOG.waiting = []; logPaused(); logShow(true); };

let tnTimer, tnRows = {};
const tnTime = (s) => new Date(s * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
// the show on now (from the guide), under the channel: "Jeopardy! · S41E62 — until 7:30 PM"
const tnShow = (s) => !s ? "" : `<div class="small">${esc(s.title)}${s.subtitle ? ` · ${esc(s.subtitle)}` : ""}${s.episode ? ` · ${esc(s.episode)}` : ""}
  <span class="muted">— until ${tnTime(s.stop)}</span></div>`;
loaders.tuners = async () => {
  clearTimeout(tnTimer);
  if ((location.hash.slice(1) || "overview") !== "tuners") return;
  let d;
  try { d = await api("/api/tuners"); } catch (e) { $("tn-body").innerHTML = `<tr><td colspan="5" class="muted">${esc(e.message)}</td></tr>`; }
  if (d) {
    const names = Object.fromEntries(d.sources.map((s) => [s.id, s.name]));
    $("tn-src").innerHTML = d.sources.map((s) => {
      const full = s.limit && s.used >= s.limit, pct = s.limit ? Math.min(100, s.used / s.limit * 100) : 0;
      return `<div class="card tn-card${full ? " full" : ""}">
        <div class="card-head"><div class="t">${esc(s.name)}</div><span class="tag">${{ xtream: "Xtream", hdhomerun: "HDHomeRun" }[s.type] || "M3U"}</span></div>
        <div class="tn-count"><b>${s.used}</b> ${s.limit ? `of ${s.limit} connection${s.limit === 1 ? "" : "s"} in use` : `connection${s.used === 1 ? "" : "s"} in use`}</div>
        ${s.limit ? `<div class="tn-meter"><i style="width:${pct}%"></i></div>` : ""}
        <div class="muted small">${full ? "Full: another channel or movie on this playlist won't start until one stops."
          : s.limit ? `${s.limit - s.used} free` : s.type === "xtream" ? "Limit not known yet (it's read when the playlist is scanned). You can set one under Playlists." : "No limit set. If your provider has one, set it under Playlists › Connections."}</div>
      </div>`;
    }).join("") || '<p class="muted">No playlists yet.</p>';
    tnRows = Object.fromEntries(d.tuners.map((t) => [t.id, t]));
    $("tn-body").innerHTML = d.tuners.map((t) => `<tr data-id="${esc(t.id)}">
      <td>${t.kind === "ondemand" ? '<span class="tag">On Demand</span> ' : t.kind === "feed" ? '<span class="tag">Feed</span> ' : ""}${esc(t.title)}${tnShow(t.show)}${t.stream && t.kind === "channel" ? `<div class="muted small">${esc(t.stream)}</div>` : ""}</td>
      <td>${esc(names[t.source_id] || "Unknown")}</td>
      <td>${t.viewers.map((v) => `<div>${TN_WHO[v.kind] || ""} ${esc(v.name)}</div>`).join("") || '<span class="muted">Starting…</span>'}</td>
      <td>${tnTime(t.since)}${t.mb ? `<div class="muted small">${t.mb >= 1000 ? (t.mb / 1000).toFixed(1) + " GB" : Math.round(t.mb) + " MB"}</div>` : ""}</td>
      <td class="acts"><button class="btn small danger" data-a="stop">Stop</button></td>
    </tr>`).join("") || '<tr><td colspan="5" class="muted">Nothing is playing or recording right now.</td></tr>';
  }
  tnTimer = setTimeout(loaders.tuners, 4000);
};

// force-stop a stream (stuck, or holding a connection something else needs)
$("tn-body").addEventListener("click", async (e) => {
  const b = e.target.closest('button[data-a="stop"]'); if (!b) return;
  const t = tnRows[b.closest("tr").dataset.id]; if (!t) return;
  const rec = t.viewers.filter((v) => v.kind === "dvr").map((v) => v.name);
  const who = t.viewers.filter((v) => v.kind !== "dvr").map((v) => v.name);
  const msg = [`Stop ${t.title}?`, who.length ? `It ends for: ${who.join(", ")}.` : "",
    rec.length ? `This also stops ${rec.length === 1 ? "the recording" : "these recordings"}: ${rec.map((n) => n.replace(/^Recording: /, "")).join(", ")}.` : ""].filter(Boolean).join("\n\n");
  if (!confirm(msg)) return;
  b.disabled = true;
  try { await api("/api/tuners/stop", { method: "POST", body: { id: t.id } }); toast(`Stopped ${t.title}`); }
  catch (err) { toast(err.message); }
  loaders.tuners();
});

// ---------------------------------------------------------------- settings
// categories on the left; one group of settings is shown at a time (the whole form still saves together)
const setGroups = [...document.querySelectorAll("#tab-settings .set-group[data-cat]")];
$("set-nav").innerHTML = setGroups.map((g) =>
  `<button type="button" data-cat="${g.dataset.cat}">${esc(g.dataset.label)}<i title="Unsaved changes"></i></button>`).join("") +
  // the lock lives with pairing (you pair this browser before locking), so this one jumps there
  '<button type="button" class="set-jump" data-jump="lock" title="Setup › Phones">Lock setup ›</button>';
function showSetCat(cat) {
  if (!setGroups.some((g) => g.dataset.cat === cat)) cat = setGroups[0].dataset.cat;
  setGroups.forEach((g) => g.classList.toggle("hidden", g.dataset.cat !== cat));
  $("set-nav").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.cat === cat));
  try { localStorage.setItem("tb-set-cat", cat); } catch (_) {}
}
$("set-nav").onclick = (e) => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.jump) { showTab("phones"); $(b.dataset.jump).scrollIntoView({ block: "start" }); return; }
  showSetCat(b.dataset.cat);
};
let savedCat = null;
try { savedCat = localStorage.getItem("tb-set-cat"); } catch (_) {}
showSetCat(savedCat);

const matchWord = (v) => v >= 98 ? "exact" : v >= 90 ? "strict" : v >= 78 ? "balanced" : "loose";

// Sports & Events layout: two cards; what's saved is the hidden sports_layout field
function renderSportsLayout() {
  const v = $("set-form").elements.sports_layout.value || "basic";
  for (const b of $("sports-layout").querySelectorAll(".layout-opt")) b.setAttribute("aria-checked", String(b.dataset.v === v));
}
$("sports-layout").addEventListener("click", (e) => {
  const b = e.target.closest(".layout-opt"); if (!b) return;
  $("set-form").elements.sports_layout.value = b.dataset.v;
  renderSportsLayout(); syncSettings();
});

// Sports leagues: tick boxes grouped by sport. What's saved is the hidden sports_leagues field
// (comma-separated here, sent as a list), so the form's load / save / unsaved-changes code covers it.
let leagueList = null;
// Each sport is a small card: its leagues as chips, how many are picked, and All / None.
const SPORT_ICON = { Football: "🏈", Basketball: "🏀", Baseball: "⚾", Hockey: "🏒", Soccer: "⚽", MMA: "🥊" };
function syncLeagues() {
  const want = new Set($("set-form").elements.sports_leagues.value.split(",").filter(Boolean));
  $("league-grid").querySelectorAll("input").forEach((b) => (b.checked = want.has(b.value)));
  for (const card of $("league-grid").querySelectorAll(".league-card")) {
    const boxes = [...card.querySelectorAll("input")], on = boxes.filter((b) => b.checked).length;
    card.querySelector(".lc-n").textContent = `${on} of ${boxes.length}`;
    card.querySelector(".lc-all").textContent = on === boxes.length ? "None" : "All";
    card.classList.toggle("on", on > 0);
  }
  $("league-count").innerHTML = want.size ? `<b>${want.size}</b> selected` : "Off · TV guide only";
}
function saveLeagues() {
  $("set-form").elements.sports_leagues.value = [...$("league-grid").querySelectorAll("input:checked")].map((b) => b.value).sort().join(",");
  syncLeagues();
}
async function loadLeagues() {
  if (!leagueList) {
    leagueList = await api("/api/sports/leagues");
    const sports = [...new Set(leagueList.map((l) => l.sport))];
    $("league-grid").innerHTML = sports.map((s) => `<div class="league-card"><div class="lc-head"><span class="lc-ico" aria-hidden="true">${SPORT_ICON[s] || "🏅"}</span>`
      + `<b>${esc(s)}</b><span class="lc-n"></span><button type="button" class="lc-all" aria-label="Pick all or none of ${esc(s)}"></button></div><div class="chips">`
      + leagueList.filter((l) => l.sport === s).map((l) => `<label class="chip"><input type="checkbox" value="${esc(l.key)}"><span>${esc(l.name)}</span></label>`).join("")
      + "</div></div>").join("");
  }
  syncLeagues();
}
$("league-grid").addEventListener("change", saveLeagues);     // runs before the form's own change handler
$("league-grid").addEventListener("click", (e) => {
  const b = e.target.closest(".lc-all"); if (!b) return;
  const boxes = [...b.closest(".league-card").querySelectorAll("input")], all = boxes.every((x) => x.checked);
  boxes.forEach((x) => (x.checked = !all));
  saveLeagues(); syncSettings();
});

// Favorite teams: picked by sport, then league, from ESPN's team lists. Saved as the hidden
// sports_teams field ("nfl:21,ncaaf:333"), like the leagues above.
const teamCache = {};          // league key -> [{id, name, abbr, logo}]
const TEAM_SHOW = 200;         // a college league has hundreds of teams: more than this needs the filter
const favField = () => $("set-form").elements.sports_teams;
const favKeys = () => favField().value.split(",").filter(Boolean);
async function leagueTeams(key) {
  if (!teamCache[key]) teamCache[key] = await api(`/api/sports/teams?league=${encodeURIComponent(key)}`);
  return teamCache[key];
}
const teamLogo = (t) => (t && t.logo ? `<img src="${esc(safePic(t.logo))}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : "");
function setFavs(keys) {
  favField().value = [...new Set(keys)].sort().join(",");
  renderFavs(); renderTeamList(); syncSettings();
}
function renderFavs() {
  const keys = favKeys(), tag = (lg) => (leagueList || []).find((l) => l.key === lg)?.name || lg.toUpperCase();
  $("team-count").innerHTML = keys.length ? `<b>${keys.length}</b> selected` : "None";
  $("fav-teams").innerHTML = keys.map((k) => {
    const [lg, id] = k.split(":"), t = (teamCache[lg] || []).find((x) => x.id === id);
    return `<button type="button" class="fav-team" data-k="${esc(k)}" aria-label="Remove ${esc(t ? t.name : k)}">${teamLogo(t)}<span>${esc(t ? t.name : `Team ${id}`)}</span><small>${esc(tag(lg))}</small><span class="x" aria-hidden="true">✕</span></button>`;
  }).join("");
}
// the sport and league picked in the team picker (buttons, not lists)
const TP = { sport: "", league: "" };
async function renderTeamList() {
  const lg = TP.league, box = $("team-list");
  if (!lg) { box.innerHTML = ""; return; }
  let teams;
  try { teams = await leagueTeams(lg); } catch (e) { box.innerHTML = `<span class="note">${esc(e.message)}</span>`; return; }
  if (TP.league !== lg) return;          // changed while loading
  const q = $("team-filter").value.trim().toLowerCase(), want = new Set(favKeys());
  const hits = teams.filter((t) => !q || t.name.toLowerCase().includes(q) || t.abbr.toLowerCase() === q);
  const shown = hits.slice(0, TEAM_SHOW);
  box.innerHTML = shown.map((t) => `<label class="team-tile"><input type="checkbox" value="${esc(`${lg}:${t.id}`)}"${want.has(`${lg}:${t.id}`) ? " checked" : ""}><span>${teamLogo(t) || `<i class="tt-abbr">${esc(t.abbr)}</i>`}<b>${esc(t.name)}</b></span></label>`).join("")
    + (!hits.length ? `<span class="note">No team matches “${esc(q)}”.</span>`
       : hits.length > shown.length ? `<span class="note">${hits.length - shown.length} more · type part of a name to find them</span>` : "");
}
// leagues with teams to pick: not fight cards (UFC, PFL). An older server sends no "teams": all have them
const teamLeagues = () => (leagueList || []).filter((l) => l.teams !== false);
const segBtn = (v, label, on) => `<button type="button" data-v="${esc(v)}" aria-pressed="${on}">${label}</button>`;
function fillTeamLeagues() {
  const ls = teamLeagues().filter((l) => l.sport === TP.sport);
  if (!ls.some((l) => l.key === TP.league)) TP.league = ls.length ? ls[0].key : "";
  $("team-sport").innerHTML = [...new Set(teamLeagues().map((l) => l.sport))]
    .map((sp) => segBtn(sp, `<span aria-hidden="true">${SPORT_ICON[sp] || "🏅"}</span> ${esc(sp)}`, sp === TP.sport)).join("");
  $("team-league").innerHTML = ls.map((l) => segBtn(l.key, esc(l.name), l.key === TP.league)).join("");
  $("team-league").classList.toggle("hidden", ls.length < 2);
  $("team-filter").value = "";
  renderTeamList();
}
async function loadTeams() {
  if (!TP.sport && leagueList) {
    TP.sport = (teamLeagues()[0] || {}).sport || "";
    fillTeamLeagues();
  }
  renderFavs();
  // the names of teams already picked come from their leagues' lists
  await Promise.all([...new Set(favKeys().map((k) => k.split(":")[0]))].map((lg) => leagueTeams(lg).catch(() => null)));
  renderFavs();
}
$("team-sport").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  TP.sport = b.dataset.v; TP.league = ""; fillTeamLeagues();
});
$("team-league").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  TP.league = b.dataset.v; fillTeamLeagues();
});
$("team-filter").addEventListener("input", (e) => { e.stopPropagation(); renderTeamList(); });
$("team-list").addEventListener("change", (e) => {
  const b = e.target.closest("input"); if (!b) return;
  setFavs(b.checked ? [...favKeys(), b.value] : favKeys().filter((k) => k !== b.value));
});
$("fav-teams").addEventListener("click", (e) => {
  const b = e.target.closest(".fav-team"); if (b) setFavs(favKeys().filter((k) => k !== b.dataset.k));
});

// ---- Settings › Screens › Home rows: which of Home's rows show, top to bottom.
// Same ids and usual order as tunerbox/home.py ROWS.
const HOME_ROWS = {
  fav: ["★", "Favorites on now"], teams: ["⚑", "Your teams on now"], cont: ["▶", "Continue Watching: On Demand"],
  contrec: ["●", "Continue Watching: Recordings"], libnew: ["✦", "New in My Library"], sports: ["◎", "Sports on now"],
  recent: ["↺", "Recent channels"], recorded: ["▣", "Recently recorded"], coming: ["◷", "Coming up"],
};
// the saved order, with any row it doesn't name yet put back after the rows it usually follows (as home.rows())
function homeOrder(saved) {
  const all = Object.keys(HOME_ROWS), order = (saved || []).filter((r) => r in HOME_ROWS);
  all.forEach((r, i) => {
    if (order.includes(r)) return;
    const before = all.slice(0, i).filter((x) => order.includes(x)).pop();
    order.splice(before ? order.indexOf(before) + 1 : 0, 0, r);
  });
  return order;
}
const homeField = (name) => $("set-form").elements[name];
const homeVal = (name) => { try { return JSON.parse(homeField(name).value || "[]"); } catch (_) { return []; } };
function renderHomeRows() {
  const order = homeOrder(homeVal("home_order")), hidden = new Set(homeVal("home_hidden"));
  $("home-rows").innerHTML = order.map((id, i) => {
    const [icon, name] = HOME_ROWS[id];
    return `<li class="home-row${hidden.has(id) ? " off" : ""}" data-id="${id}" draggable="true">
      <span class="home-grip" aria-hidden="true" title="Drag to move">⠿</span>
      <span class="home-icon" aria-hidden="true">${icon}\uFE0E</span>
      <span class="home-row-name">${esc(name)}</span>
      <span class="home-move">
        <button type="button" class="icon-btn" data-mv="-1" aria-label="Move ${esc(name)} up"${i ? "" : " disabled"}>↑</button>
        <button type="button" class="icon-btn" data-mv="1" aria-label="Move ${esc(name)} down"${i < order.length - 1 ? "" : " disabled"}>↓</button>
      </span>
      <input type="checkbox" class="toggle" aria-label="Show ${esc(name)}"${hidden.has(id) ? "" : " checked"}></li>`;
  }).join("");
}
function setHomeRows(order, hidden, focus) {
  homeField("home_order").value = JSON.stringify(order);
  homeField("home_hidden").value = JSON.stringify(order.filter((r) => hidden.has(r)));
  renderHomeRows(); syncSettings();
  if (focus) $("home-rows").querySelector(`[data-id="${focus[0]}"] [data-mv="${focus[1]}"]:not(:disabled)`)?.focus();
}
$("home-rows").addEventListener("click", (e) => {
  const b = e.target.closest("[data-mv]"); if (!b) return;
  e.preventDefault();
  const id = b.closest(".home-row").dataset.id, order = homeOrder(homeVal("home_order"));
  const i = order.indexOf(id), j = i + +b.dataset.mv;
  if (j < 0 || j >= order.length) return;
  [order[i], order[j]] = [order[j], order[i]];
  setHomeRows(order, new Set(homeVal("home_hidden")), [id, b.dataset.mv]);
});
$("home-rows").addEventListener("change", (e) => {
  e.stopPropagation();
  const t = e.target.closest(".toggle"); if (!t) return;
  const id = t.closest(".home-row").dataset.id, hidden = new Set(homeVal("home_hidden"));
  if (t.checked) hidden.delete(id); else hidden.add(id);
  setHomeRows(homeOrder(homeVal("home_order")), hidden);
});
// drag a row to a new place (mouse; on touch screens the arrows do it)
let homeDrag = null;
$("home-rows").addEventListener("dragstart", (e) => {
  const li = e.target.closest(".home-row"); if (!li) return;
  homeDrag = li.dataset.id; li.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", homeDrag);
});
$("home-rows").addEventListener("dragover", (e) => {
  const li = e.target.closest(".home-row"); if (!homeDrag || !li) return;
  e.preventDefault();
  const below = e.clientY > li.getBoundingClientRect().top + li.offsetHeight / 2;
  $("home-rows").querySelectorAll(".drop-above, .drop-below").forEach((x) => x.classList.remove("drop-above", "drop-below"));
  if (li.dataset.id !== homeDrag) li.classList.add(below ? "drop-below" : "drop-above");
});
$("home-rows").addEventListener("drop", (e) => {
  const li = e.target.closest(".home-row"); if (!homeDrag || !li) return;
  e.preventDefault();
  const below = li.classList.contains("drop-below"), order = homeOrder(homeVal("home_order")).filter((r) => r !== homeDrag);
  if (li.dataset.id !== homeDrag) order.splice(order.indexOf(li.dataset.id) + (below ? 1 : 0), 0, homeDrag);
  else order.splice(homeOrder(homeVal("home_order")).indexOf(homeDrag), 0, homeDrag);
  setHomeRows(order, new Set(homeVal("home_hidden")));
});
$("home-rows").addEventListener("dragend", () => { homeDrag = null; renderHomeRows(); });
$("home-rows-reset").addEventListener("click", (e) => { e.preventDefault(); setHomeRows(Object.keys(HOME_ROWS), new Set()); });

loaders.settings = async () => {
  settings = await api("/api/settings");
  const f = $("set-form");
  for (const el of f.elements) {
    if (!el.name || !(el.name in settings)) continue;
    const v = settings[el.name];
    if (el.type === "checkbox") { el.checked = !!v; continue; }
    if (el.dataset.json) { el.value = JSON.stringify(el.name === "home_order" ? homeOrder(v) : v); continue; }
    if (el.dataset.list) { el.value = [...v].sort().join(","); continue; }
    const shown = el.dataset.scale ? v / +el.dataset.scale : v;
    // a value set in settings.json that the list doesn't offer: show it rather than silently saving another
    if (el.tagName === "SELECT" && ![...el.options].some((o) => o.value === String(shown))) el.add(new Option(String(shown), String(shown)));
    el.value = String(shown);
  }
  renderHomeRows();
  renderSportsLayout();
  try { await loadLeagues(); } catch (e) { $("league-grid").textContent = e.message; }
  loadTeams();
  rememberSaved(); syncSettings();
  // other apps always get the http address, even when Setup is open over HTTPS (they'd refuse TunerBox's certificate)
  let base = location.origin;
  if (location.protocol === "https:") { try { base = (await api("/api/me")).http_base || base; } catch (_) {} }
  $("u-m3u").textContent = base + "/playlist.m3u";
  $("u-epg").textContent = base + "/epg.xml";
  $("u-hdhr").textContent = base;
  $("https-ca").href = base + "/tunerbox-ca.crt";
  loadHttps();
};

async function loadHttps() {
  const el = $("https-status");
  let s;
  try { s = await api("/api/https"); } catch (e) { el.textContent = e.message; return; }
  const host = location.hostname;
  const url = `https://${host}:${s.port}/`;
  el.innerHTML = !s.enabled ? "HTTPS is off."
    : s.running ? `HTTPS is on: <a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a>`
    : s.error ? `<span class="bad">HTTPS isn't running: ${esc(s.error)}</span>`
    : "HTTPS starts when TunerBox is restarted.";
  if (s.enabled) el.innerHTML += `<br>The certificate covers ${esc([...s.names, ...s.ips].join(", "))}.`;
}
// "unsaved" means the form differs from what was last loaded or saved, so undoing a change clears it again
const setFields = () => [...$("set-form").elements].filter((el) => el.name);
const fieldVal = (el) => (el.type === "checkbox" ? String(el.checked) : el.value);
let setSavedVals = {};
const rememberSaved = () => { setSavedVals = Object.fromEntries(setFields().map((el) => [el.name, fieldVal(el)])); };

// options that only matter when a switch is on are hidden (but still saved) while it's off
function syncSettings() {
  const f = $("set-form");
  const mt = f.elements.match_threshold;
  $("mt-val").innerHTML = `<b>${esc(mt.value)}</b> · ${matchWord(+mt.value)}`;
  const changed = setFields().filter((el) => fieldVal(el) !== setSavedVals[el.name]);
  const dirtyCats = new Set(changed.map((el) => el.closest(".set-group[data-cat]")?.dataset.cat));
  $("set-nav").querySelectorAll("button[data-cat]").forEach((b) => b.classList.toggle("dirty", dirtyCats.has(b.dataset.cat)));
  const dirty = changed.length > 0;
  f.querySelectorAll("[data-needs]").forEach((row) => {
    const need = f.elements[row.dataset.needs];      // a switch that's on, or a choice that isn't "off"
    const on = need.type === "checkbox" ? need.checked : need.value !== "off";
    row.classList.toggle("off", !on);
    row.querySelectorAll("input, select").forEach((el) => (el.disabled = !on));
  });
  document.querySelector(".savebar").classList.toggle("dirty", dirty);
  $("set-dirty").textContent = dirty ? "Unsaved changes" : "All changes saved";
}
$("set-form").addEventListener("input", syncSettings);
$("set-form").addEventListener("change", syncSettings);
$("set-form").onsubmit = async (e) => {
  e.preventDefault();
  const out = {};
  for (const el of e.target.elements) {
    if (!el.name) continue;
    out[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" || el.dataset.num ? +el.value : el.value;
    if (el.dataset.scale) out[el.name] = Math.round(out[el.name] * +el.dataset.scale);
    if (el.dataset.list) out[el.name] = el.value.split(",").filter(Boolean);
    if (el.dataset.json) out[el.name] = JSON.parse(el.value || "[]");
  }
  try { await api("/api/settings", { method: "POST", body: out }); }
  catch (err) { toast(err.message); return; }
  rememberSaved(); syncSettings(); dvrTab(out.dvr_enabled !== false); if (out.theme && window.tbTheme) tbTheme.server(out.theme);
  api("/api/settings").then(mvCapNote).catch(() => {});
  toast("Settings saved");
};

// copy buttons for the links other apps use. navigator.clipboard only exists on https or localhost,
// so on a plain-http LAN address fall back to the old select-and-copy
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
  const ta = document.createElement("textarea");
  ta.value = text; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.appendChild(ta); ta.select();
  let ok = false; try { ok = document.execCommand("copy"); } catch (_) {}
  ta.remove();
  return ok;
}
document.querySelector(".set-links").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  const ok = await copyText($(b.dataset.copy).textContent);
  b.textContent = ok ? "Copied" : "Copy failed";
  clearTimeout(b._t); b._t = setTimeout(() => (b.textContent = "Copy"), 1600);
});

// ---------------------------------------------------------------- local channels
let mcEditing = null, mcList = [];
const mcStatus = (c) => {
  const st = c.status || "";
  if (st.startsWith("error")) return `<span class="bad-text">${esc(st.replace(/^error: /, ""))}</span>`;
  return esc(st.replace(/^ok: /, ""));
};
loaders.media = async () => {
  mcList = await api("/api/media/channels");
  $("mc-list").innerHTML = mcList.length ? mcList.map((c) => `<div class="card" data-id="${c.id}">
      <div class="t">${c.number} · ${esc(c.name)} <span class="tag">${{ order: "In order", rotate: "Rotate shows", shuffle: "Shuffle" }[c.play_order] || ""}</span> ${c.enabled ? "" : '<span class="tag none">off</span>'}</div>
      ${c.folders.map((f) => `<div class="u">${esc(f)}</div>`).join("")}
      <div class="muted small">${mcStatus(c)}${c.updated_at ? " · read " + fmt(c.updated_at) : ""}</div>
      ${c.now ? `<div class="small">On now: <b>${esc(c.now.title)}</b>${c.now.episode ? " " + esc(c.now.episode) : ""}${c.now.subtitle ? " · " + esc(c.now.subtitle) : ""}</div>` : ""}
      <div class="row"><a class="btn small primary" href="/?ch=${c.number}">Watch</a><span class="grow"></span>
        <button class="btn small" data-a="edit">Edit</button><button class="btn small" data-a="scan">Re-read folders</button>
        <button class="btn small" data-a="toggle">${c.enabled ? "Turn off" : "Turn on"}</button>
        <button class="btn small danger" data-a="del">Delete</button></div></div>`).join("")
    : '<p class="muted">No channels in My Channels yet. Make one below.</p>';
  if (!mcEditing && !$("mc-form").number.value) $("mc-form").number.value = (await api("/api/media/next-number")).number;
  $("mc-browse").classList.toggle("hidden", !(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder));
};
function mcReset() {
  mcEditing = null; $("mc-form").reset(); $("mc-checked").textContent = "";
  $("mc-form-title").textContent = "New channel"; $("mc-save").textContent = "Create channel";
  $("mc-cancel").classList.add("hidden"); $("mc-form").number.value = "";
  loaders.media();
}
$("mc-cancel").onclick = mcReset;
$("mc-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const id = +b.closest(".card").dataset.id, c = mcList.find((x) => x.id === id);
  try {
    if (b.dataset.a === "del") {
      if (!confirm(`Delete channel ${c.number} ${c.name}? Your video files are not touched.`)) return;
      await api(`/api/media/channels/${id}`, { method: "DELETE" }); toast("Channel deleted");
    } else if (b.dataset.a === "scan") {
      const r = await api(`/api/media/channels/${id}/scan`, { method: "POST" });
      toast(r.job ? "Reading the folders…" : "Another job is running; try again in a moment"); pollJob();
    } else if (b.dataset.a === "toggle") {
      await api(`/api/media/channels/${id}`, { method: "PUT", body: { enabled: !c.enabled } });
    } else if (b.dataset.a === "edit") {
      mcEditing = id; const f = $("mc-form");
      f.elements.name.value = c.name; f.number.value = c.number; f.folders.value = c.folders.join("\n");
      f.play_order.value = c.play_order; f.recurse.checked = !!c.recurse; f.logo.value = c.logo || "";
      $("mc-form-title").textContent = `Edit channel ${c.number}`; $("mc-save").textContent = "Save changes";
      $("mc-cancel").classList.remove("hidden"); f.scrollIntoView({ behavior: "smooth" });
      return;
    }
  } catch (err) { toast(err.message); }
  loaders.media();
});
$("mc-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { name: f.elements.name.value, number: +f.number.value || 0, folders: f.folders.value.split(/\r?\n/).filter((x) => x.trim()),
                 play_order: f.play_order.value, recurse: f.recurse.checked, logo: f.logo.value };
  try {
    const r = mcEditing ? await api(`/api/media/channels/${mcEditing}`, { method: "PUT", body })
                        : await api("/api/media/channels", { method: "POST", body });
    toast(r.job ? "Saved. Reading the folders…" : "Saved"); if (r.job) pollJob();
    mcReset();
  } catch (err) { toast(err.message); }
};
$("mc-browse").onclick = async () => {
  const p = await window.pywebview.api.pick_folder();
  if (!p) return;
  const t = $("mc-form").folders; t.value = (t.value.trim() ? t.value.trim() + "\n" : "") + p;
  if (!$("mc-form").elements.name.value) $("mc-form").elements.name.value = p.split(/[\\/]/).filter(Boolean).pop();
};
$("mc-check").onclick = async () => {
  const lines = $("mc-form").folders.value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
  if (!lines.length) { $("mc-checked").textContent = "Add a folder first"; return; }
  $("mc-checked").textContent = "Checking…";
  const out = [];
  for (const l of lines) {
    const r = await api(`/api/media/folder?path=${encodeURIComponent(l)}`).catch((e) => ({ ok: false, message: e.message }));
    out.push(`${l.split(/[\\/]/).filter(Boolean).pop()}: ${r.message}`);
  }
  $("mc-checked").textContent = out.join(" · ");
};

// ---------------------------------------------------------------- local library (your own movies and shows)
let libData = null, libEditing = null, libBrowse = null, libFixFor = null, libQT = null;
const libKind = (k) => (k === "movie" ? "Movies" : "TV shows");
const libStatus = (s) => {
  const st = s.status || "";
  if (st.startsWith("error")) return `<span class="bad-text">${esc(st.replace(/^error: /, ""))}</span>`;
  return st ? esc(st.replace(/^ok: /, "")) : "Not scanned yet";
};
const LIB_STATE = { ok: ["ok", "Matched"], weak: ["weak", "Check this"], none: ["none", "Not found"], pinned: ["pinned", "Picked by you"], nfo: ["ok", "From NFO file"],
                    ignored: ["none", "Left out"], "": ["pending", "Not looked up"] };
// grabbing video details: files read from the NAS at once (2 unless changed; 1 is the gentlest, more is faster on a NAS that keeps up)
const LIB_AT_ONCE = [[1, "1 file at a time"], [2, "2 files at once"], [3, "3 files at once"], [4, "4 files at once"]];
const LIB_REMOVE = [[0, "the next scan"], [1, "1 day"], [7, "7 days"], [30, "30 days"], [90, "90 days"]];
const LIB_EVERY = [[0.5, "30 minutes"], [1, "hour"], [3, "3 hours"], [6, "6 hours"], [12, "12 hours"], [24, "day"], [168, "week"]];
const libEvery = (h) => (LIB_EVERY.find(([x]) => x === h) || [h, `${h} hours`])[1];
const libOpts = (list, cur) => (list.some(([v]) => v === cur) ? list : [...list, [cur, `${cur}`]])
  .map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${l}</option>`).join("");
const libIn = (t) => { const s = t - Date.now() / 1000; return s < 60 ? "soon" : s < 3600 ? `in ${Math.round(s / 60)} min` : s < 86400 ? `in ${Math.round(s / 3600)} h` : `in ${Math.round(s / 86400)} d`; };
const LIB_VIDEO = { "": "Default", copy: "Original", h264: "H.264", roku720: "720p" };
const libOn = () => libData.shares.some((x) => x.enabled);
// how long a task's last run took, roughly: "1 s", "12 s", "3 min", "1 h 5 min"
const libTook = (t) => {
  if (t < 59.5) return `${Math.max(1, Math.round(t))} s`;
  const m = Math.round(t / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}`;
};
const libHour = (h) => (h === 0 ? "midnight" : h === 12 ? "noon" : h < 12 ? `${h} am` : `${h - 12} pm`);
// weekdays as the server counts them (0 Monday … 6 Sunday), listed from Sunday
const LIB_DAYS = [[6, "Sunday"], [0, "Monday"], [1, "Tuesday"], [2, "Wednesday"], [3, "Thursday"], [4, "Friday"], [5, "Saturday"]];
const libDayName = (d) => (LIB_DAYS.find(([x]) => x === d) || [0, ""])[1];
// "on Sunday at 4 am", "from 4 am": what's set beside Every
const libAt = (t) => [t.every_hours === 168 && t.on_day >= 0 ? `on ${libDayName(t.on_day)}` : "",
                      t.at_hour >= 0 && t.every_hours > 1 ? `${t.every_hours >= 24 ? "at" : "from"} ${libHour(t.at_hour)}` : ""].filter(Boolean).join(" ");
// the "on" (weekly) and "at" / "from" pickers beside Every: none for every hour
const libAtPick = (every, at, day) => (every <= 1 ? "" : (every === 168 ? `<span>on</span>
  <select data-a="day" aria-label="On what day">${libOpts([[-1, "any day"], ...LIB_DAYS], day >= 0 ? day : -1)}</select>` : "")
  + `<span>${every >= 24 ? "at" : "from"}</span>
  <select data-a="at" aria-label="At what time">${libOpts([[-1, "any time"], ...[...Array(24).keys()].map((h) => [h, libHour(h)])], at >= 0 ? at : -1)}</select>`);
const libPicked = (row) => { const at = row.querySelector("[data-a=at]"), day = row.querySelector("[data-a=day]");
                             return { at_hour: at ? +at.value : -1, on_day: day ? +day.value : -1 }; };
loaders.library = async () => {
  libData = await api("/api/library");
  const c = libData.counts, s = libData.settings, tasks = libData.tasks || [];
  const plural = (n, w) => `${w}${n === 1 ? "" : "s"}`;
  const stat = (n, label, extra = "", act = "") => `<div class="st ${extra}"${act ? ` data-a="${act}" role="button" tabindex="0"` : ""}><b>${n.toLocaleString()}</b><span>${label}</span></div>`;
  $("lib-summary").innerHTML = libData.shares.length ? [
    stat(c.movies, plural(c.movies, "movie")),
    stat(c.shows, plural(c.shows, "show")),
    stat(c.episodes, plural(c.episodes, "episode")),
    stat(c.review, "need review", c.review ? "warn link" : "", c.review ? "review" : ""),
    c.missing ? stat(c.missing, "missing files", "link", "missing") : "",
  ].join("") : "";
  $("lib-list").innerHTML = libData.shares.length ? libData.shares.map((sh) => `<div class="card lib-card${sh.enabled ? "" : " off"}" data-id="${sh.id}">
      <div class="lib-card-top"><span class="lib-ico" aria-hidden="true">${sh.kind === "movie" ? "🎬" : "📺"}</span>
        <div class="lib-card-name"><div class="t">${esc(sh.name)}</div>
          <div class="u" title="${esc(sh.location)}">${esc(sh.location)}${sh.username ? ` · as ${esc(sh.username)}` : ""}</div></div>
        <button class="btn small" data-a="edit">Edit</button></div>
      <div class="lib-card-tags"><span class="tag">${libKind(sh.kind)}</span>${sh.enabled ? "" : '<span class="tag none">Hidden from On Demand</span>'}
        ${sh.review ? `<a class="tag weak" data-a="review" href="#library">${sh.review} to review</a>` : ""}</div>
      <div class="row"><span class="muted small grow">${libStatus(sh)}${sh.scanned_at ? ` · ${ago(sh.scanned_at)}` : ""}</span>
        <button class="btn small" data-a="scan" ${sh.enabled ? "" : "disabled"} title="Read every folder of this library again, download every title's details and posters again (titles you picked by hand stay as they are) and grab every video's details again (length, resolution, HDR, audio). Takes a while on a big library.">Full rescan</button></div></div>`).join("")
    : `<div class="lib-empty"><b>No libraries yet</b><span class="muted">Add your Movies folder and your TV folder, on your NAS or this PC.</span>
        <button type="button" class="btn primary" data-a="add">+ Add library</button></div>`;
  $("lib-scan").disabled = !libOn();
  $("lib-review-count").textContent = c.review ? `${c.review} need review` : "";
  $("lib-review-count").classList.toggle("hidden", !c.review);
  // details panel
  $("lib-tmdb-card").innerHTML = `<div class="lib-panel-head"><b>Details and posters</b>
      <span class="tag ${s.tmdb_key_set ? "ok" : "weak"}">${s.tmdb_key_set ? "TMDB on" : "No TMDB key"}</span>
      <span class="grow"></span><button class="btn small" data-a="tmdb">${s.tmdb_key_set ? "Edit" : "Set up"}</button></div>
    <div class="muted small">${s.tmdb_key_set ? "Titles get posters, plots and cast from TMDB."
      : "Without a key, titles show the names from your folders and files. A free TMDB key adds posters, plots and cast."}</div>
    <dl class="lib-facts"><dt>Language</dt><dd>${esc(s.language || "en-US")}</dd>
      <dt>Video</dt><dd>${LIB_VIDEO[s.video_mode || ""] || esc(s.video_mode)}</dd></dl>`;
  // tasks panel
  const on = tasks.filter((t) => t.enabled), nxt = on.length && libOn() ? on.reduce((a, b) => (b.next_run < a.next_run ? b : a)) : null;
  const last = tasks.filter((t) => t.last_run).sort((a, b) => b.last_run - a.last_run)[0];
  const failed = tasks.filter((t) => (t.status || "").startsWith("error"));
  $("lib-tasks-card").innerHTML = `<div class="lib-panel-head"><b>Automatic tasks</b>
      <span class="tag ${on.length ? "ok" : "none"}">${on.length} of ${tasks.length} on</span>
      ${failed.length ? `<span class="tag none">${failed.length} failed</span>` : ""}
      <span class="grow"></span>
      <button class="btn small" data-a="run-all" ${on.length && libOn() ? "" : "disabled"} title="Run every task that's on now, one after another">Run all</button>
      <button class="btn small" data-a="tasks">Edit</button></div>
    <ul class="lib-task-list">${tasks.map((t) => {
      const bad = (t.status || "").startsWith("error");
      const bits = t.enabled ? [`Every ${libEvery(t.every_hours)}${libAt(t) ? ` ${libAt(t)}` : ""}`] : ["Off"];
      if (t.last_run) bits.push(`took ${libTook(t.took)}`);
      return `<li class="${bad ? "bad" : t.enabled ? "on" : ""}" title="${esc(t.about)}"><span class="nm">${esc(t.name)}</span>
        <span class="sub">${bits.join(" · ")}${bad ? ' · <span class="bad-text">failed</span>' : ""}</span></li>`;
    }).join("")}</ul>
    <div class="muted small">${nxt ? `Next: ${esc(nxt.name.toLowerCase())}, ${libIn(nxt.next_run)}` : libOn() ? "Nothing scheduled" : "Waits until a library is added"}${last ? ` · Last ran ${ago(last.last_run)}` : ""}</div>`;
  if (!$("tasks-dlg").classList.contains("hidden")) libTasks(true);  // a task finished while the dialog is open
  libTitles();
};
$("lib-summary").addEventListener("click", (e) => {
  const a = (e.target.closest("[data-a]") || {}).dataset?.a;
  if (a === "review" || a === "missing") libShow(a);
});
$("lib-summary").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.click(); });
$("lib-tmdb-card").addEventListener("click", (e) => { if (e.target.closest("[data-a=tmdb]")) openTmdbDlg(); });
$("lib-tasks-card").addEventListener("click", (e) => {
  if (e.target.closest("[data-a=tasks]")) openTasksDlg();
  else if (e.target.closest("[data-a=run-all]")) libRunAll();
});
// a job started, or waiting for another one (the guide update…) to finish first
function libStarted(r, msg) {
  toast(r.queued ? `Queued: starts after ${r.after || "the job that's running"}` : msg);
  pollJob();
}
async function libRunAll() {
  try { libStarted(await api("/api/library/tasks/run-all", { method: "POST" }), "Running all tasks…"); } catch (e) { toast(e.message); }
}
function libShow(filter, q = "") {
  $("lib-filter").value = filter; $("lib-q").value = q; libTitles();
  $("lib-body").scrollIntoView({ behavior: "smooth" });
}
$("lib-scan").onclick = async () => {
  try { libStarted(await api("/api/library/scan", { method: "POST", body: {} }), "Scanning…"); } catch (e) { toast(e.message); }
};
function libModal(id, close) {
  $(id).addEventListener("mousedown", (e) => { if (e.target === $(id)) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$(id).classList.contains("hidden")) close(); });
}

// ---- Details dialog: TMDB key and language, and how library videos play
function openTmdbDlg() {
  const s = libData.settings;
  $("tmdb-form").reset();
  $("lib-lang").value = s.language || "en-US";
  $("lib-key").placeholder = s.tmdb_key_set ? `saved (${s.tmdb_key_hint || "hidden"}): leave empty to keep it` : "paste your key";
  $("lib-key-state").textContent = "";
  $("tmdb-remove").classList.toggle("hidden", !s.tmdb_key_set);
  $("lib-refresh").classList.toggle("hidden", !s.tmdb_key_set);
  $("lib-video").value = s.video_mode || "";
  $("tmdb-dlg").classList.remove("hidden");
  $("lib-key").focus();
}
function closeTmdbDlg() { $("tmdb-dlg").classList.add("hidden"); }
$("tmdb-close").onclick = closeTmdbDlg;
$("tmdb-cancel").onclick = closeTmdbDlg;
libModal("tmdb-dlg", closeTmdbDlg);
$("tmdb-form").onsubmit = async (e) => {
  e.preventDefault();
  const hadKey = libData.settings.tmdb_key_set, key = $("lib-key").value.trim();
  const body = { language: $("lib-lang").value.trim() || "en-US", video_mode: $("lib-video").value };
  if (key) body.tmdb_key = key;
  try {
    await api("/api/library/settings", { method: "POST", body });
    toast(key && !hadKey ? "Saved. Downloading details…" : "Saved");
    if (key && !hadKey) pollJob();
    closeTmdbDlg(); loaders.library();
  } catch (err) { toast(err.message); }
};
$("lib-test-key").onclick = async () => {
  $("lib-key-state").textContent = "Testing…";
  const r = await api("/api/library/test-key", { method: "POST", body: { key: $("lib-key").value.trim() } }).catch((e) => ({ ok: false, message: e.message }));
  $("lib-key-state").innerHTML = r.ok ? `<span style="color:var(--ok)">✓</span> ${esc(r.message)}` : `<span class="bad-text">${esc(r.message)}</span>`;
};
$("tmdb-remove").onclick = async () => {
  if (!confirm("Remove the TMDB key? Details already downloaded are kept; new titles show the names from your files.")) return;
  try {
    libData.settings = await api("/api/library/settings", { method: "POST", body: { tmdb_key: "", clear_key: true } });
    toast("Key removed"); openTmdbDlg(); loaders.library();
  } catch (err) { toast(err.message); }
};
$("lib-refresh").onclick = async () => {
  if (!confirm("Download every title's details and posters from TMDB again? Titles you picked by hand stay as they are.")) return;
  try { await api("/api/library/scan", { method: "POST", body: { refresh: true } }); toast("Downloading…"); pollJob(); } catch (e) { toast(e.message); }
};

// ---- Automatic tasks dialog
function libTasks(keepEdits = false) {
  // redrawn while the dialog is open (a task finished): keep what was changed but not saved yet
  const edits = {};
  if (keepEdits) {
    for (const row of $("lib-tasks").querySelectorAll(".lib-task")) {
      edits[row.dataset.key] = { enabled: row.querySelector("[data-a=on]").checked, every_hours: +row.querySelector("[data-a=every]").value,
                                 ...libPicked(row) };
    }
  }
  const removeAfter = $("lib-remove") && keepEdits ? +$("lib-remove").value : +libData.settings.remove_after_days;
  const atOnce = $("lib-at-once") && keepEdits ? +$("lib-at-once").value : +(libData.settings.read_at_once || 2);
  const can = libOn();
  $("lib-tasks").innerHTML = (libData.tasks || []).map((t0) => {
    const t = { ...t0, ...(edits[t0.key] || {}) };
    const st = t.status || "", bad = st.startsWith("error");
    const last = t.last_run ? `<span class="${bad ? "bad-text" : ""}">${bad ? "✕" : "✓"} ${ago(t.last_run)}, took ${libTook(t.took)}: ${esc(st.replace(/^(ok|error): /, "") || "done")}</span>` : "Not run yet";
    return `<div class="lib-task${t.enabled ? "" : " off"}" data-key="${esc(t.key)}">
      <input type="checkbox" class="toggle" data-a="on" aria-label="${esc(t.name)}" ${t.enabled ? "checked" : ""}>
      <div class="lib-task-main"><b>${esc(t.name)}</b>
        <div class="muted small">${esc(t.about)}</div>
        <div class="lib-task-opts">Every <select data-a="every" aria-label="${esc(t.name)}: how often">${libOpts(LIB_EVERY, t.every_hours)}</select>
          <span class="lib-at">${libAtPick(t.every_hours, t.at_hour, t.on_day)}</span>
          ${t.key === "remove" ? `<span>once missing for</span> <select id="lib-remove" aria-label="Remove missing videos after">${libOpts(LIB_REMOVE, removeAfter)}</select>` : ""}
          ${t.key === "scan" ? `<span>video details: read</span> <select id="lib-at-once" aria-label="Video files read at once" title="More at once is faster on a NAS that keeps up. If the log says the NAS stopped answering, go back to 1.">${libOpts(LIB_AT_ONCE, atOnce)}</select>` : ""}</div>
        <div class="lib-task-last small muted">${last}</div></div>
      <button type="button" class="btn small" data-a="run" ${can ? "" : 'disabled title="Add a library first"'}>Run now</button></div>`;
  }).join("");
}
function openTasksDlg() {
  libTasks();
  $("tasks-dlg").classList.remove("hidden");
  $("tasks-dlg").querySelector(".lib-tasks").scrollTop = 0;
}
function closeTasksDlg() { $("tasks-dlg").classList.add("hidden"); }
$("tasks-close").onclick = closeTasksDlg;
$("tasks-cancel").onclick = closeTasksDlg;
libModal("tasks-dlg", closeTasksDlg);
$("tasks-run-all").onclick = libRunAll;
$("tasks-reset").onclick = async () => {
  if (!confirm("Put every task back to its default: on, at its usual schedule, starting at 4 am, missing videos removed after 30 days, and video files read 2 at once? This is saved straight away.")) return;
  try {
    await api("/api/library/tasks/reset", { method: "POST" });
    await loaders.library(); libTasks(); toast("Back to the defaults");
  } catch (e) { toast(e.message); }
};
$("lib-tasks").addEventListener("change", (e) => {
  if (e.target.dataset.a === "every") {
    // the time picker appears (it isn't there for every hour): 4 am, like the defaults
    const row = e.target.closest(".lib-task"), p = libPicked(row), had = row.querySelector("[data-a=at]");
    row.querySelector(".lib-at").innerHTML = libAtPick(+e.target.value, had ? p.at_hour : 4, p.on_day);
  }
  if (e.target.dataset.a === "on") e.target.closest(".lib-task").classList.toggle("off", !e.target.checked);
});
$("tasks-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const days = +$("lib-remove").value, atOnce = $("lib-at-once") ? +$("lib-at-once").value : +(libData.settings.read_at_once || 2);
    const changed = { ...(days !== +libData.settings.remove_after_days ? { remove_after_days: days } : {}),
                      ...(atOnce !== +(libData.settings.read_at_once || 2) ? { read_at_once: atOnce } : {}) };
    if (Object.keys(changed).length) await api("/api/library/settings", { method: "POST", body: changed });
    for (const row of $("lib-tasks").querySelectorAll(".lib-task")) {
      const t = libData.tasks.find((x) => x.key === row.dataset.key);
      const on = row.querySelector("[data-a=on]").checked, every = +row.querySelector("[data-a=every]").value;
      const { at_hour: at, on_day: day } = libPicked(row);
      if (on !== !!t.enabled || every !== t.every_hours || at !== (t.at_hour ?? -1) || day !== (every === 168 ? t.on_day ?? -1 : -1)) {
        await api(`/api/library/tasks/${t.key}`, { method: "PUT", body: { enabled: on, every_hours: every, at_hour: at, on_day: day } });
      }
    }
    toast("Saved"); closeTasksDlg(); loaders.library();
  } catch (err) { toast(err.message); }
};
$("lib-tasks").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-a=run]");
  if (!b) return;
  const t = libData.tasks.find((x) => x.key === b.closest(".lib-task").dataset.key);
  try { libStarted(await api(`/api/library/tasks/${t.key}/run`, { method: "POST" }), `${t.name}…`); }
  catch (err) { toast(err.message); }
});
// a title's file under Matches, in its own Quality column: badges for resolution and HDR, then the codecs and
// sound, then the file (type, size, bitrate). A show: its episodes' total size
function libQuality(t) {
  const size = !t.size ? "" : t.size >= 1e9 ? `${(t.size / 1e9).toFixed(1)} GB` : `${Math.round(t.size / 1e6)} MB`;
  const rate = !t.bitrate ? "" : t.bitrate >= 1000 ? `${(t.bitrate / 1000).toFixed(1)} Mbps` : `${t.bitrate} kbps`;
  const file = [(t.ext || "").toUpperCase(), size, rate].filter(Boolean).join(" · ");
  const av = t.av || t.hint;      // not read yet: what the file name says, marked as such
  if (!av) {
    const note = t.kind === "movie" ? '<div class="lq-note">No video details yet</div>' : "";
    return file ? `${note}<div class="lq-line">${esc(file)}</div>` : '<span class="muted">—</span>';
  }
  const guess = t.av ? "" : '<div class="lq-note">From the file name; not read yet</div>';
  const res = av.w >= 3200 ? "4K" : av.w >= 1800 ? "1080p" : av.w >= 1200 ? "720p" : av.w ? "SD" : "";
  const vc = { hevc: "HEVC", h264: "H.264", av1: "AV1", vp9: "VP9", mpeg2video: "MPEG-2", mpeg4: "MPEG-4" }[av.vcodec] || av.vcodec || "";
  const ac = { eac3: "DD+", ac3: "DD", truehd: "TrueHD", dts: "DTS", aac: "AAC", mp3: "MP3", opus: "Opus", flac: "FLAC" }[av.acodec] || av.acodec || "";
  const badges = (res ? `<span class="lq-res">${res}</span>` : "") + (av.hdr ? `<span class="lq-hdr">${esc(av.hdr)}</span>` : "");
  const codecs = [vc, [av.layout, ac].filter(Boolean).join(" ")].filter(Boolean).join(" · ");
  return (badges ? `<div class="lq-badges">${badges}</div>` : "")
    + (codecs ? `<div class="lq-line">${esc(codecs)}</div>` : "") + (file ? `<div class="lq-line">${esc(file)}</div>` : "") + guess;
}
async function libTitles() {
  const q = $("lib-q").value.trim(), f = $("lib-filter").value, kind = $("lib-kind").value;
  let rows;
  try { rows = await api(`/api/library/titles?filter=${f}&kind=${kind}&q=${encodeURIComponent(q)}`); } catch (e) { toast(e.message); return; }
  $("lib-shown").textContent = rows.length >= 300 ? "first 300 shown" : `${rows.length} title${rows.length === 1 ? "" : "s"}`;
  $("lib-body").innerHTML = rows.length ? rows.map((t) => {
    const [cls, label] = LIB_STATE[t.state] || LIB_STATE[""];
    const pct = t.score && (t.state === "ok" || t.state === "weak") ? ` · ${t.score}%` : "";
    return `<tr data-id="${esc(t.id)}">
      <td>${t.poster ? `<img class="poster" loading="lazy" src="/api/vod/poster/${encodeURIComponent(t.id)}" alt="">` : '<span class="poster"></span>'}</td>
      <td><b>${esc(t.name)}</b>${t.year ? ` <span class="muted">(${esc(t.year)})</span>` : ""}
        <div class="muted small">${t.kind === "movie" ? "Movie" : `TV · ${t.eps} episode${t.eps === 1 ? "" : "s"}`} · ${esc(t.category)}</div></td>
      <td class="file">${esc(t.file)}</td>
      <td class="lq">${libQuality(t)}</td>
      <td><span class="tag ${cls}">${label}${pct}</span>${t.missing_since ? `<div class="small bad-text">File missing since ${fmt(t.missing_since)}</div>` : ""}</td>
      <td class="acts"><button class="btn small" data-a="fix">Fix</button>
        <button class="btn small" data-a="${t.state === "ignored" ? "unignore" : "ignore"}">${t.state === "ignored" ? "Bring back" : "Leave out"}</button></td></tr>`;
  }).join("") : `<tr><td colspan="6" class="muted">${f === "review" ? "Nothing to review." : f === "nodetails" ? "Every video's details are in." : "No titles."}</td></tr>`;
  libTitles.rows = rows;
}
$("lib-filter").onchange = libTitles;
$("lib-kind").onchange = libTitles;
$("lib-q").oninput = () => { clearTimeout(libQT); libQT = setTimeout(libTitles, 300); };

function libForm() {
  const f = $("lib-form");
  return { kind: f.kind.value, name: f.elements.name.value.trim(), location: f.location.value.trim(),
           username: f.username.value.trim(), password: f.password.value, id: libEditing || undefined,
           ...(libEditing ? { enabled: f.elements.enabled.checked } : {}) };
}
// + Add library and Edit open the same dialog; nothing changes until Add library / Save changes
function openLibDlg(sh) {
  libEditing = sh ? sh.id : null; libBrowse = null;
  const f = $("lib-form"); f.reset();
  $("lib-tested").textContent = ""; $("lib-browse").classList.add("hidden");
  if (sh) {
    f.kind.value = sh.kind; f.elements.name.value = sh.name; f.location.value = sh.location; f.username.value = sh.username || "";
  }
  f.password.placeholder = sh && sh.has_password ? "saved (leave empty to keep it)" : "";
  f.elements.enabled.checked = !sh || !!sh.enabled;
  f.querySelectorAll(".lib-only-edit").forEach((x) => x.classList.toggle("hidden", !sh));
  f.querySelector(".lib-where").open = false;
  $("lib-dlg-title").textContent = sh ? `Edit ${sh.name}` : "Add a library";
  $("lib-dlg-sub").textContent = sh ? `${libKind(sh.kind)} · nothing changes until you press Save changes` : "A folder of movies or a folder of TV shows";
  $("lib-dlg-tip").textContent = sh ? "Changing the folder or what it holds scans it again." : "It's scanned as soon as it's added.";
  $("lib-save").textContent = sh ? "Save changes" : "Add library";
  $("lib-dlg").classList.remove("hidden");
  $("lib-dlg").querySelector(".modal-box").scrollTop = 0;
  (sh ? f.elements.name : f.location).focus();
}
function closeLibDlg() { $("lib-dlg").classList.add("hidden"); libEditing = null; libBrowse = null; }
$("lib-add").onclick = () => openLibDlg(null);
$("lib-dlg-close").onclick = closeLibDlg;
$("lib-cancel").onclick = closeLibDlg;
$("lib-dlg").addEventListener("mousedown", (e) => { if (e.target === $("lib-dlg")) closeLibDlg(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("lib-dlg").classList.contains("hidden")) closeLibDlg(); });
$("lib-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-a]"); if (!b) return;
  e.preventDefault();
  if (b.dataset.a === "add") { $("lib-add").click(); return; }
  const sh = libData.shares.find((x) => x.id === +b.closest(".card").dataset.id);
  if (b.dataset.a === "edit") openLibDlg(sh);
  else if (b.dataset.a === "review") libShow("review", sh.name);
  else if (b.dataset.a === "scan") {
    try { libStarted(await api("/api/library/scan", { method: "POST", body: { share_id: sh.id, refresh: true } }), `Reading all of ${sh.name} again…`); }
    catch (err) { toast(err.message); }
  }
});
$("lib-del").onclick = async () => {
  const sh = libData.shares.find((x) => x.id === libEditing);
  if (!sh || !confirm(`Remove ${sh.name} from On Demand? Your video files are not touched.`)) return;
  try { await api(`/api/library/shares/${sh.id}`, { method: "DELETE" }); toast("Library removed"); closeLibDlg(); loaders.library(); }
  catch (err) { toast(err.message); }
};
$("lib-form").onsubmit = async (e) => {
  e.preventDefault();
  const body = libForm();
  try {
    const r = libEditing ? await api(`/api/library/shares/${libEditing}`, { method: "PUT", body })
                         : await api("/api/library/shares", { method: "POST", body });
    toast(r.job ? (libEditing ? "Saved. Scanning the folder…" : "Added. Scanning the folder…") : "Saved"); if (r.job) pollJob();
    closeLibDlg(); loaders.library();
  } catch (err) { toast(err.message); }
};

// Connect & browse: check the login, then click into folders to pick the one with the movies or shows
async function libOpen(path) {
  $("lib-tested").textContent = "Connecting…";
  let r;
  try { r = await api("/api/library/browse", { method: "POST", body: { ...libBrowse.conn, path } }); }
  catch (e) { r = { ok: false, message: e.message }; }
  $("lib-tested").innerHTML = r.ok ? `<span style="color:var(--ok)">✓</span> ${esc(r.message)}` : `<span class="bad-text">${esc(r.message)}</span>`;
  if (!r.ok) { $("lib-browse").classList.add("hidden"); return; }
  libBrowse.path = path;
  const parts = path ? path.split("/") : [];
  const sep = /^[\\/]{2}|^smb:/i.test(libBrowse.conn.location) ? "\\" : "/";
  const crumbs = [`<a data-p="">${esc(libBrowse.conn.location)}</a>`].concat(parts.map((p, i) => `<a data-p="${esc(parts.slice(0, i + 1).join("/"))}">${esc(p)}</a>`));
  $("lib-browse").innerHTML = `<div class="crumb">${crumbs.join(` <span class="muted">${sep}</span> `)}<span class="grow"></span>
      ${path ? '<button type="button" class="btn small primary" id="lib-use">Use this folder</button>' : ""}</div>
    <ul>${r.folders.map((f) => `<li data-f="${esc(f)}">${esc(f)}</li>`).join("") || '<li class="muted" data-f="">No folders inside</li>'}</ul>`;
  $("lib-browse").classList.remove("hidden");
}
$("lib-test").onclick = () => {
  const conn = libForm();
  if (!conn.location) { $("lib-tested").textContent = "Enter the network location first"; return; }
  libBrowse = { conn, path: "" };
  libOpen("");
};
$("lib-browse").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-p]"), li = e.target.closest("li[data-f]");
  if (a) libOpen(a.dataset.p);
  else if (li && li.dataset.f) libOpen(libBrowse.path ? `${libBrowse.path}/${li.dataset.f}` : li.dataset.f);
  else if (e.target.id === "lib-use") {
    const f = $("lib-form"), base = libBrowse.conn.location.replace(/[\\/]+$/, "");
    const sep = /^[\\/]{2}|^smb:/i.test(base) && !/^smb:/i.test(base) ? "\\" : "/";
    f.location.value = base + sep + libBrowse.path.split("/").join(sep);
    const last = libBrowse.path.split("/").pop();
    if (!f.elements.name.value) f.elements.name.value = last;
    if (/\b(tv|shows?|series)\b/i.test(last)) f.kind.value = "series";
    else if (/\bmovies?|films?\b/i.test(last)) f.kind.value = "movie";
    libBrowse.conn.location = f.location.value; libBrowse.path = "";
    $("lib-browse").classList.add("hidden");
    $("lib-tested").textContent = `Folder picked: ${last}. Check "This folder has", then press ${libEditing ? "Save changes" : "Add library"}.`;
  }
});

// the matches table: Fix (pick the TMDB title) and Leave out
$("lib-body").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const id = b.closest("tr").dataset.id, t = (libTitles.rows || []).find((x) => x.id === id);
  try {
    if (b.dataset.a === "fix") { libFix(t); return; }
    await api(`/api/library/titles/${encodeURIComponent(id)}/ignore`, { method: "POST", body: { on: b.dataset.a === "ignore" } });
    toast(b.dataset.a === "ignore" ? "Left out of On Demand" : "Back in On Demand");
  } catch (err) { toast(err.message); }
  loaders.library();
});
function libFix(t) {
  libFixFor = t;
  $("lib-fix-title").textContent = `Fix match: ${t.file_name}${t.file_year ? ` (${t.file_year})` : ""}`;
  $("lib-fix-sub").textContent = t.file;
  $("lib-fix-q").value = t.file_name; $("lib-fix-year").value = t.file_year || "";
  $("lib-fix-list").innerHTML = "";
  $("lib-fix").classList.remove("hidden");
  libFixSearch();
}
async function libFixSearch() {
  const q = $("lib-fix-q").value.trim(); if (!q) return;
  $("lib-fix-list").innerHTML = '<li class="muted">Searching TMDB…</li>';
  let res;
  try { res = await api(`/api/library/tmdb?kind=${libFixFor.kind}&q=${encodeURIComponent(q)}&year=${encodeURIComponent($("lib-fix-year").value.trim())}`); }
  catch (e) { $("lib-fix-list").innerHTML = `<li class="bad-text">${esc(e.message)}</li>`; return; }
  $("lib-fix-list").innerHTML = res.length ? res.map((r) => `<li>
      ${r.poster ? `<img src="${esc(safePic(r.poster))}" alt="" loading="lazy">` : '<span class="noimg"></span>'}
      <div class="tx"><b>${esc(r.name)}</b> ${r.year ? `<span class="muted">(${esc(r.year)})</span>` : ""}${r.id === libFixFor.tmdb_id ? ' <span class="tag ok">current</span>' : ""}
        <small>${esc(r.overview || "No description.")}</small></div>
      <button class="btn small primary" data-tmdb="${r.id}">Use this</button></li>`).join("")
    : '<li class="muted">Nothing found. Try fewer words, or no year.</li>';
}
$("lib-fix-form").onsubmit = (e) => { e.preventDefault(); libFixSearch(); };
$("lib-fix-close").onclick = () => $("lib-fix").classList.add("hidden");
$("lib-fix-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-tmdb]"); if (!b) return;
  b.disabled = true; b.textContent = "Saving…";
  try {
    await api(`/api/library/titles/${encodeURIComponent(libFixFor.id)}/match`, { method: "POST", body: { tmdb_id: +b.dataset.tmdb } });
    $("lib-fix").classList.add("hidden"); toast("Match saved"); loaders.library();
  } catch (err) { toast(err.message); b.disabled = false; b.textContent = "Use this"; }
});
$("lib-fix-unmatch").onclick = async () => {
  try {
    await api(`/api/library/titles/${encodeURIComponent(libFixFor.id)}/unmatch`, { method: "POST" });
    $("lib-fix").classList.add("hidden"); toast("Using the name from the file"); loaders.library();
  } catch (err) { toast(err.message); }
};

// ---------------------------------------------------------------- phones
let pairTimer = null, pairBefore = 0;
const ago = (t) => { if (!t) return "—"; const s = Date.now() / 1000 - t; return s < 120 ? "just now" : s < 7200 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : fmt(t); };
function renderDevices(list) {
  $("dev-body").innerHTML = list.length ? list.map((d) => `<tr data-id="${esc(d.id)}">
      <td><input class="dev-name" value="${esc(d.name)}" maxlength="40"></td><td>${fmt(d.created)}</td><td>${ago(d.last_seen)}</td>
      <td><button class="btn small" data-a="del">Remove</button></td></tr>`).join("")
    : '<tr><td colspan="4" class="muted">No phones paired yet</td></tr>';
}
loaders.phones = async () => {
  const me = await api("/api/me");
  $("lock-setup").checked = !!me.require_pairing;
  // on a NAS (Docker) any browser at home counts as the TunerBox PC until the lock is on; then paired ones do
  $("lock-setup").disabled = !(me.pc || (me.server && me.paired));
  $("lock-hint").textContent = me.server && !me.paired
    ? "To turn this on, first pair this browser (Pair a phone, then open /pair here and enter the code)." : "";
  $("pair-start").classList.toggle("hidden", !me.manage);
  $("pair-pconly").classList.toggle("hidden", me.manage);
  if (me.manage) renderDevices(await api("/api/devices"));
  else $("dev-body").innerHTML = '<tr><td colspan="4" class="muted">Manage paired phones on the TunerBox PC or a paired phone</td></tr>';
};
function stopPairUi() {
  clearInterval(pairTimer); pairTimer = null;
  $("pair-live").classList.add("hidden"); $("pair-idle").classList.remove("hidden");
}
$("pair-start").onclick = async () => {
  let p; try { p = await api("/api/pair/start", { method: "POST" }); } catch (e) { toast(e.message); return; }
  pairBefore = (await api("/api/devices")).length;
  $("pair-qr").src = p.qr || ""; $("pair-qr").classList.toggle("hidden", !p.qr);
  $("pair-addr").textContent = p.address;
  $("pair-code").textContent = `${p.code.slice(0, 3)} ${p.code.slice(3)}`;
  $("pair-idle").classList.add("hidden"); $("pair-live").classList.remove("hidden");
  clearInterval(pairTimer);
  pairTimer = setInterval(async () => {
    const left = Math.round(p.expires - Date.now() / 1000);
    $("pair-left").textContent = left > 0 ? `Code works for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "";
    let st; try { st = await api("/api/pair/state"); } catch (_) { return; }
    if (st.devices.length > pairBefore) { stopPairUi(); renderDevices(st.devices); toast(`${st.devices[st.devices.length - 1].name} is paired`); }
    else if (!st.active) { stopPairUi(); toast("The pairing code expired"); }
  }, 1000);
};
$("pair-cancel").onclick = async () => { await api("/api/pair/cancel", { method: "POST" }).catch(() => {}); stopPairUi(); };
$("dev-body").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-a=del]"); if (!b) return;
  const tr = b.closest("tr");
  if (!confirm(`Remove ${tr.querySelector(".dev-name").value}? It won't work as a remote until it's paired again.`)) return;
  renderDevices(await api(`/api/devices/${tr.dataset.id}`, { method: "DELETE" }));
});
$("dev-body").addEventListener("change", async (e) => {
  if (!e.target.classList.contains("dev-name")) return;
  await api(`/api/devices/${e.target.closest("tr").dataset.id}`, { method: "PATCH", body: { name: e.target.value } });
  toast("Renamed");
});
$("lock-setup").onchange = async (e) => {
  try { await api("/api/settings", { method: "POST", body: { require_pairing: e.target.checked } }); toast(e.target.checked ? "Setup is locked to this PC and paired phones" : "Setup is open to the whole network"); }
  catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
};

// ---------------------------------------------------------------- start
showTab(location.hash.slice(1) || "overview");
pollJob();
// jobs this page didn't start (a scheduled scan, grabbing video details starting again by itself): shown as they begin
setInterval(() => { if (!polling && !document.hidden) pollJob(); }, 5000);
})();
