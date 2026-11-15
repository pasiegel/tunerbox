/* TunerBox setup page */
(() => {
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
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
    card("library", "Local library", o.libraries ? `${o.library.movies} · ${o.library.shows}` : "Off",
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
    if (!r.started) toast("Another job is already running");
    pollJob();
  } catch (e) { toast(e.message); }
}
let polling = false;
const editing = () => !!document.querySelector(".src-edit:not(.hidden)");     // a playlist's Edit form is open: don't redraw it away
async function pollJob() {
  if (polling) return; polling = true;
  let wasRunning = false, ticks = 0;
  for (;;) {
    let j; try { j = await api("/api/jobs"); } catch (_) { break; }
    if (j.running) {
      wasRunning = true;
      // reading On Demand: its categories arrive before the titles, so keep the playlist cards current
      if (j.name.startsWith("vod") && ++ticks % 2 === 0 && (location.hash.slice(1) || "overview") === "sources" && $("groups").classList.contains("hidden") && !editing()) loaders.sources();
      $("job").classList.remove("hidden");
      $("job-text").textContent = `${j.stage}${j.total ? ` (${j.done}/${j.total})` : ""}`;
      $("job-bar").style.width = j.total ? `${j.done / j.total * 100}%` : "15%";
    } else {
      $("job").classList.add("hidden");
      if (wasRunning) { toast(j.message); badges(); const cur = location.hash.slice(1) || "overview"; loaders[cur] && !(cur === "sources" && editing()) && loaders[cur](); }
      break;
    }
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
    const feed = s ? `<div class="feed"><span class="dot ${s.status}" title="${s.status}"></span>${s.logo ? `<img src="${esc(s.logo)}" onerror="this.remove()">` : ""}
        <div><div>${esc(feedName(s))}</div><div class="meta">${esc(s.source_name)}${s.grp ? " · " + esc(s.grp) : ""}${s.tvg_id ? " · " + esc(s.tvg_id) : ""}</div></div></div>`
      : '<span class="muted">—</span>';
    return `<tr data-n="${c.number}"><td class="num">${c.number}</td>
      <td>${esc(c.name)}<div class="cs">${esc(c.callsign || "")}${c.affiliate ? " · " + esc(c.affiliate) : ""}</div></td>
      <td>${feed}</td><td>${tag}</td>
      <td class="acts"><button class="btn small" data-a="pick">Change</button>${s ? '<button class="btn small" data-a="play">&#9654;</button>' : ""}
      ${c.mode === "none" ? "" : '<button class="btn small" data-a="off" title="Never map this channel">Off</button>'}
      ${c.mode === "manual" || c.mode === "none" ? '<button class="btn small" data-a="auto" title="Go back to automatic matching">Auto</button>' : ""}</td></tr>`;
  }).join("");
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
    <td><div class="feed"><span class="dot ${e.status}"></span>${e.logo ? `<img src="${esc(e.logo)}" onerror="this.remove()">` : ""}${esc(e.name)}</div></td>
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
loaders.sources = async () => {
  const list = await api("/api/sources");
  $("src-list").innerHTML = list.length ? list.map((s) => `<div class="card" data-id="${s.id}">
      <div class="card-head"><div class="t">${esc(s.name)} <span class="tag">${{ xtream: "Xtream", hdhomerun: "HDHomeRun" }[s.type] || "M3U"}</span> ${s.enabled ? "" : '<span class="tag none">disabled</span>'}</div>
        <button class="btn small" data-a="edit">Edit</button>
        <button class="btn small" data-a="toggle">${s.enabled ? "Disable" : "Enable"}</button>
        <button class="btn small danger" data-a="del">Delete</button></div>
      <div class="u">${esc(s.url)}${s.username ? " · user " + esc(s.username) : ""}</div>
      <form class="src-edit hidden" data-type="${s.type}">
        <label>Name <input name="name" value="${esc(s.name)}" required></label>
        <label>${{ xtream: "Server", hdhomerun: "Address (IP or name)" }[s.type] || "Playlist URL or file path"} <input name="url" value="${esc(s.url)}" required></label>
        ${s.type === "xtream" ? `<label>Username <input name="username" value="${esc(s.username)}" autocomplete="off"></label>
        <label>Password <input name="password" type="password" placeholder="${s.password ? "Unchanged" : ""}" autocomplete="new-password"></label>` : ""}
        <div class="row"><button class="btn small primary">Save</button><button type="button" class="btn small" data-a="edit-cancel">Cancel</button>
        <span class="muted small">A new address or login is read right away.</span></div>
      </form>
      <div class="muted small">${s.count} channels${s.vod.movies ? ` · ${s.vod.movies.toLocaleString()} movies` : ""}${s.vod.series ? ` · ${s.vod.series.toLocaleString()} shows` : ""} · ${esc(s.status || "not scanned yet")}${s.updated_at ? " · " + fmt(s.updated_at) : ""}</div>
      ${s.epg_url ? `<div class="muted small">Guide: ${esc(s.epg_url.replace(/password=[^&]+/, "password=…"))}</div>` : ""}
      <div class="row"><button class="btn small primary" data-a="groups">${s.type === "hdhomerun" ? "Channels…" : "Channel groups…"}</button>
      <span class="muted small">${((w) => s.hidden ? `${s.hidden} ${w}${s.hidden === 1 ? "" : "s"} hidden` : `All ${w}s shown`)(s.type === "hdhomerun" ? "channel" : "group")}</span></div>
      ${vodRows(s)}
      <div class="row"><span class="muted small">Video:</span><select data-a="video" class="small">
        <option value="" ${!s.video_mode ? "selected" : ""}>Default (Settings)</option>
        <option value="copy" ${s.video_mode === "copy" ? "selected" : ""}>Original</option>
        <option value="h264" ${s.video_mode === "h264" ? "selected" : ""}>Re-encode to H.264${s.type === "hdhomerun" ? " (needed for antenna TV)" : ""}</option>
        <option value="roku720" ${s.video_mode === "roku720" ? "selected" : ""}>Re-encode to 720p (Roku, slow devices)</option></select></div>
      ${connRow(s)}
      <details class="adv"${s.exclude_groups ? " open" : ""}><summary class="muted small">Skip pattern (advanced)</summary>
      <div class="row"><input data-f="exclude_groups" value="${esc(s.exclude_groups)}" placeholder="regex, e.g. ^(UK|AR)|XXX" style="flex:1">
      <button class="btn small" data-a="save">Save</button></div></details></div>`).join("")
    : '<p class="muted">No playlists yet. Add your M3U link or Xtream login below, then press <b>Scan playlists now</b>.</p>';
};
// How many streams TunerBox opens on a playlist at once. Automatic: what an Xtream account or HDHomeRun
// says (no limit for a plain M3U, which can't say). Set it for an M3U provider that has a limit.
function connRow(s) {
  const auto = s.conn_auto ? `${s.conn_auto} at a time, from your ${s.type === "hdhomerun" ? "tuner" : "provider"}` : "no limit";
  const own = +s.max_conn || 0;
  // 1-10, and a larger number set some other way (backup import, API: up to 99) so it shows as set
  const opts = Array.from({ length: Math.max(10, own) }, (_, i) => i + 1).filter((n) => n <= 10 || n === own)
    .map((n) => `<option value="${n}" ${own === n ? "selected" : ""}>${n}</option>`).join("");
  return `<div class="row"><span class="muted small">${s.type === "hdhomerun" ? "Tuners" : "Connections"}:</span><select data-a="conn" class="small">
      <option value="0" ${own ? "" : "selected"}>Automatic (${auto})</option>${opts}</select>
      <span class="muted small">TunerBox never opens more than this, and tells a TV what's using them.</span></div>`;
}
// On Demand switches on a playlist's card: movies + TV shows for an Xtream login, video files for a plain M3U
// On Demand on a playlist's card: a row each for movies and TV shows, with its categories and its switch
// (a plain M3U has one switch for its video files, which can be movies and episodes)
function vodRows(s) {
  if (s.type === "hdhomerun") return "";
  const catNote = (kind) => { const [shown, all] = s.vod.cats[kind]; return all ? (shown === all ? `All ${all} categories shown` : `${shown} of ${all} categories shown`) : "Reading the categories…"; };
  const btn = (kind, on) => `<button class="btn small" data-a="vodcats" data-kind="${kind}" ${on ? "" : "disabled"}>${kind === "tags" ? "Languages…" : "Categories…"}</button>`;
  const [tShown, tAll] = s.vod.tags || [0, 0];
  const tagNote = tAll < 2 ? "Labels like EN, DE or 4K in front of the names are read with the titles"
    : tShown === tAll ? `All ${tAll} labels shown (languages like EN and DE, and platforms like NF)` : `${tShown} of ${tAll} labels shown`;
  const tagRow = () => (s.vod_movies || s.vod_series) && tAll > 1 ? row("", "tags", "Languages and labels", tagNote, true) : "";
  const sw = (key) => `<input type="checkbox" class="toggle" id="vod-${s.id}-${key}" data-a="vod" data-k="${key}" ${s[key] ? "checked" : ""}>`;
  const row = (key, kind, title, note, on) => `<div class="vod-row"><label class="vod-t" for="vod-${s.id}-${key}"><b>${title}</b><small class="muted">${note}</small></label>
      ${kind ? btn(kind, on) : "<span></span>"}${key ? sw(key) : "<span></span>"}</div>`;
  if (s.type === "xtream") {
    return `<div class="vod-box"><div class="vod-h">On Demand</div>
      ${row("vod_movies", "movie", "Movies", s.vod_movies ? catNote("movie") : "Off", s.vod_movies)}
      ${row("vod_series", "series", "TV shows", s.vod_series ? catNote("series") : "Off", s.vod_series)}
      ${tagRow()}</div>`;
  }
  const n = s.vod.files;
  return `<div class="vod-box"><div class="vod-h">On Demand</div>
    ${row("vod_movies", "", "Movie and episode files", `Entries ending in .mp4 or .mkv become titles instead of channels${n ? ` (${n.toLocaleString()} found)` : ""}`)}
    ${s.vod_movies ? row("", "movie", "Movies", catNote("movie"), true) + row("", "series", "TV shows", catNote("series"), true) + tagRow() : ""}</div>`;
}
$("src-list").addEventListener("change", async (e) => {
  if (e.target.dataset.a === "vod") {
    const on = e.target.checked;
    let r;
    try { r = await api(`/api/sources/${e.target.closest(".card").dataset.id}`, { method: "PATCH", body: { [e.target.dataset.k]: on ? 1 : 0 } }); }
    catch (err) { toast(err.message); e.target.checked = !on; return; }
    toast(!on ? "On Demand turned off for this playlist" : r.job ? "Reading the list from your provider: the categories show up here in a moment…" : "Saved");
    if (r.job) pollJob();
    loaders.sources();
    return;
  }
  if (e.target.dataset.a === "conn") {
    try { await api(`/api/sources/${e.target.closest(".card").dataset.id}`, { method: "PATCH", body: { max_conn: +e.target.value } }); }
    catch (err) { toast(err.message); return; }
    toast("Saved: applies from the next channel change");
    return;
  }
  if (e.target.dataset.a !== "video") return;
  await api(`/api/sources/${e.target.closest(".card").dataset.id}`, { method: "PATCH", body: { video_mode: e.target.value } });
  toast("Saved: applies from the next channel change");
});
$("src-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const card = b.closest(".card"), id = card.dataset.id;
  if (b.dataset.a === "del" && confirm("Delete this playlist?")) await api(`/api/sources/${id}`, { method: "DELETE" });
  if (b.dataset.a === "toggle") await api(`/api/sources/${id}`, { method: "PATCH", body: { enabled: b.textContent === "Enable" ? 1 : 0 } });
  if (b.dataset.a === "edit" || b.dataset.a === "edit-cancel") {
    const f = card.querySelector(".src-edit"), open = b.dataset.a === "edit" && f.classList.contains("hidden");
    if (!open) f.reset();
    f.classList.toggle("hidden", !open);
    if (open) f.elements.name.focus();
    return;
  }
  if (b.closest(".src-edit")) return;             // the form's Save: handled by its submit
  if (b.dataset.a === "groups") { openGroups(+id); return; }
  if (b.dataset.a === "vodcats") { openGroups(+id, b.dataset.kind); return; }
  if (b.dataset.a === "save") { await api(`/api/sources/${id}`, { method: "PATCH", body: { exclude_groups: card.querySelector("[data-f]").value } }); toast("Saved - rescan to apply"); }
  loaders.sources();
});
$("src-list").addEventListener("submit", async (e) => {
  const f = e.target.closest(".src-edit"); if (!f) return;
  e.preventDefault();
  const body = Object.fromEntries([...f.elements].filter((el) => el.name).map((el) => [el.name, el.value.trim()]));
  if (!body.password) delete body.password;       // blank: keep the saved one
  let r;
  try { r = await api(`/api/sources/${f.closest(".card").dataset.id}`, { method: "PATCH", body }); } catch (err) { toast(err.message); return; }
  toast(r.job === "scan" ? "Saved. Reading the playlist again…" : "Saved");
  loaders.sources();
  if (r.job) pollJob();
});
const srcType = () => {
  const t = $("src-type").value, xt = t === "xtream", hd = t === "hdhomerun";
  document.querySelectorAll(".xt").forEach((el) => el.classList.toggle("hidden", !xt));
  document.querySelectorAll(".hd").forEach((el) => el.classList.toggle("hidden", !hd));
  document.querySelectorAll(".hd-hide").forEach((el) => el.classList.toggle("hidden", hd));
  $("src-url-label").textContent = xt ? "Server URL (http://host:port)" : hd ? "Tuner address (IP, e.g. 192.168.1.40)" : "Playlist URL or file path";
  $("src-form").url.placeholder = hd ? "192.168.1.40" : "https://… or C:\\path\\list.m3u";
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
$("src-type").onchange = srcType; srcType();
$("src-form").onsubmit = async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  try { await api("/api/sources", { method: "POST", body: data }); e.target.reset(); srcType(); $("hd-found").textContent = ""; toast("Added - scanning…"); loaders.sources(); startJob("scan"); }
  catch (err) { toast(err.message); }
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
const TN_WHO = { tv: "📺", dvr: "⏺", app: "🔌", setup: "⚙" };
let tnTimer;
loaders.tuners = async () => {
  clearTimeout(tnTimer);
  if ((location.hash.slice(1) || "overview") !== "tuners") return;
  let d;
  try { d = await api("/api/tuners"); } catch (e) { $("tn-body").innerHTML = `<tr><td colspan="4" class="muted">${esc(e.message)}</td></tr>`; }
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
    $("tn-body").innerHTML = d.tuners.map((t) => `<tr>
      <td>${t.kind === "ondemand" ? '<span class="tag">On Demand</span> ' : t.kind === "feed" ? '<span class="tag">Feed</span> ' : ""}${esc(t.title)}${t.stream && t.kind === "channel" ? `<div class="muted small">${esc(t.stream)}</div>` : ""}</td>
      <td>${esc(names[t.source_id] || "Unknown")}</td>
      <td>${t.viewers.map((v) => `<div>${TN_WHO[v.kind] || ""} ${esc(v.name)}</div>`).join("") || '<span class="muted">Starting…</span>'}</td>
      <td>${new Date(t.since * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}${t.mb ? `<div class="muted small">${t.mb >= 1000 ? (t.mb / 1000).toFixed(1) + " GB" : Math.round(t.mb) + " MB"}</div>` : ""}</td>
    </tr>`).join("") || '<tr><td colspan="4" class="muted">Nothing is playing or recording right now.</td></tr>';
  }
  tnTimer = setTimeout(loaders.tuners, 4000);
};

// ---------------------------------------------------------------- settings
// categories on the left; one group of settings is shown at a time (the whole form still saves together)
const setGroups = [...document.querySelectorAll("#tab-settings .set-group[data-cat]")];
$("set-nav").innerHTML = setGroups.map((g) =>
  `<button type="button" data-cat="${g.dataset.cat}">${esc(g.dataset.label)}<i title="Unsaved changes"></i></button>`).join("");
function showSetCat(cat) {
  if (!setGroups.some((g) => g.dataset.cat === cat)) cat = setGroups[0].dataset.cat;
  setGroups.forEach((g) => g.classList.toggle("hidden", g.dataset.cat !== cat));
  $("set-nav").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.cat === cat));
  try { localStorage.setItem("tb-set-cat", cat); } catch (_) {}
}
$("set-nav").onclick = (e) => { const b = e.target.closest("button"); if (b) showSetCat(b.dataset.cat); };
let savedCat = null;
try { savedCat = localStorage.getItem("tb-set-cat"); } catch (_) {}
showSetCat(savedCat);

const matchWord = (v) => v >= 98 ? "exact" : v >= 90 ? "strict" : v >= 78 ? "balanced" : "loose";

// Sports leagues: tick boxes grouped by sport. What's saved is the hidden sports_leagues field
// (comma-separated here, sent as a list), so the form's load / save / unsaved-changes code covers it.
let leagueList = null;
function syncLeagues() {
  const want = new Set($("set-form").elements.sports_leagues.value.split(",").filter(Boolean));
  $("league-grid").querySelectorAll("input").forEach((b) => (b.checked = want.has(b.value)));
  $("league-count").innerHTML = want.size ? `<b>${want.size}</b> selected` : "Off · TV guide only";
}
async function loadLeagues() {
  if (!leagueList) {
    leagueList = await api("/api/sports/leagues");
    const sports = [...new Set(leagueList.map((l) => l.sport))];
    $("league-grid").innerHTML = sports.map((s) => `<span class="sport">${esc(s)}</span><div class="chips">${leagueList.filter((l) => l.sport === s)
      .map((l) => `<label class="chip"><input type="checkbox" value="${esc(l.key)}"><span>${esc(l.name)}</span></label>`).join("")}</div>`).join("");
  }
  syncLeagues();
}
$("league-grid").addEventListener("change", () => {      // runs before the form's own change handler
  $("set-form").elements.sports_leagues.value = [...$("league-grid").querySelectorAll("input:checked")].map((b) => b.value).sort().join(",");
  syncLeagues();
});

loaders.settings = async () => {
  settings = await api("/api/settings");
  const f = $("set-form");
  for (const el of f.elements) {
    if (!el.name || !(el.name in settings)) continue;
    const v = settings[el.name];
    if (el.type === "checkbox") { el.checked = !!v; continue; }
    if (el.dataset.list) { el.value = [...v].sort().join(","); continue; }
    const shown = el.dataset.scale ? v / +el.dataset.scale : v;
    // a value set in settings.json that the list doesn't offer: show it rather than silently saving another
    if (el.tagName === "SELECT" && ![...el.options].some((o) => o.value === String(shown))) el.add(new Option(String(shown), String(shown)));
    el.value = String(shown);
  }
  try { await loadLeagues(); } catch (e) { $("league-grid").textContent = e.message; }
  rememberSaved(); syncSettings();
  $("u-m3u").textContent = location.origin + "/playlist.m3u";
  $("u-epg").textContent = location.origin + "/epg.xml";
  $("u-hdhr").textContent = location.origin;
};
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
  $("set-nav").querySelectorAll("button").forEach((b) => b.classList.toggle("dirty", dirtyCats.has(b.dataset.cat)));
  const dirty = changed.length > 0;
  f.querySelectorAll("[data-needs]").forEach((row) => {
    const on = f.elements[row.dataset.needs].checked;
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
  }
  try { await api("/api/settings", { method: "POST", body: out }); }
  catch (err) { toast(err.message); return; }
  rememberSaved(); syncSettings();
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
    : '<p class="muted">No local channels yet. Make one below.</p>';
  if (!mcEditing && !$("mc-form").number.value) $("mc-form").number.value = (await api("/api/media/next-number")).number;
  $("mc-browse").classList.toggle("hidden", !(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder));
};
function mcReset() {
  mcEditing = null; $("mc-form").reset(); $("mc-checked").textContent = "";
  $("mc-form-title").textContent = "New local channel"; $("mc-save").textContent = "Create channel";
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
const LIB_STATE = { ok: ["ok", "Matched"], weak: ["weak", "Check this"], none: ["none", "Not found"], pinned: ["pinned", "Picked by you"],
                    ignored: ["none", "Left out"], "": ["pending", "Not looked up"] };
loaders.library = async () => {
  libData = await api("/api/library");
  const c = libData.counts, s = libData.settings;
  $("lib-summary").innerHTML = libData.shares.length ? `
    <div class="st"><b>${c.movies.toLocaleString()}</b>movie${c.movies === 1 ? "" : "s"}</div>
    <div class="st"><b>${c.shows.toLocaleString()}</b>show${c.shows === 1 ? "" : "s"} · ${c.episodes.toLocaleString()} episode${c.episodes === 1 ? "" : "s"}</div>
    <div class="st ${c.review ? "warn" : ""}"><b>${c.review}</b>need review</div>
    <div class="st"><b>${s.tmdb_key_set ? "On" : "Off"}</b>TMDB details</div>` : "";
  $("lib-list").innerHTML = libData.shares.length ? libData.shares.map((sh) => `<div class="card" data-id="${sh.id}">
      <div class="t">${esc(sh.name)} <span class="tag">${libKind(sh.kind)}</span> ${sh.enabled ? "" : '<span class="tag none">off</span>'}
        ${sh.review ? `<span class="tag weak">${sh.review} to review</span>` : ""}</div>
      <div class="u">${esc(sh.location)}${sh.username ? ` · as ${esc(sh.username)}` : ""}</div>
      <div class="muted small">${libStatus(sh)}${sh.scanned_at ? " · scanned " + fmt(sh.scanned_at) : ""}</div>
      <div class="row"><button class="btn small primary" data-a="scan">Scan now</button><span class="grow"></span>
        <button class="btn small" data-a="review">Review</button><button class="btn small" data-a="edit">Edit</button>
        <button class="btn small" data-a="toggle">${sh.enabled ? "Turn off" : "Turn on"}</button>
        <button class="btn small danger" data-a="del">Remove</button></div></div>`).join("")
    : '<p class="muted">No library folders yet. Add your Movies folder and your TV folder below.</p>';
  $("lib-lang").value = s.language || "";
  $("lib-auto").value = String(s.auto_scan_hours);
  if (![...$("lib-auto").options].some((o) => o.selected)) $("lib-auto").value = "24";
  $("lib-video").value = s.video_mode || "";
  $("lib-key").value = "";
  $("lib-key").placeholder = s.tmdb_key_set ? `saved (${s.tmdb_key_hint || "hidden"}): paste a new one to replace it` : "paste your key";
  $("lib-key-state").textContent = s.tmdb_key_set ? "" : "No key yet: titles use the names from your files.";
  $("lib-review-count").textContent = c.review ? `${c.review} need review` : "";
  libTitles();
};
async function libTitles() {
  const q = $("lib-q").value.trim(), f = $("lib-filter").value;
  let rows;
  try { rows = await api(`/api/library/titles?filter=${f}&q=${encodeURIComponent(q)}`); } catch (e) { toast(e.message); return; }
  $("lib-shown").textContent = rows.length >= 300 ? "first 300 shown" : `${rows.length} title${rows.length === 1 ? "" : "s"}`;
  $("lib-body").innerHTML = rows.length ? rows.map((t) => {
    const [cls, label] = LIB_STATE[t.state] || LIB_STATE[""];
    const pct = t.score && (t.state === "ok" || t.state === "weak") ? ` · ${t.score}%` : "";
    return `<tr data-id="${esc(t.id)}">
      <td>${t.poster ? `<img class="poster" loading="lazy" src="/api/vod/poster/${encodeURIComponent(t.id)}" alt="">` : '<span class="poster"></span>'}</td>
      <td><b>${esc(t.name)}</b>${t.year ? ` <span class="muted">(${esc(t.year)})</span>` : ""}
        <div class="muted small">${t.kind === "movie" ? "Movie" : `TV · ${t.eps} episode${t.eps === 1 ? "" : "s"}`} · ${esc(t.category)}</div></td>
      <td class="file">${esc(t.file)}</td>
      <td><span class="tag ${cls}">${label}${pct}</span></td>
      <td class="acts"><button class="btn small" data-a="fix">Fix</button>
        <button class="btn small" data-a="${t.state === "ignored" ? "unignore" : "ignore"}">${t.state === "ignored" ? "Bring back" : "Leave out"}</button></td></tr>`;
  }).join("") : `<tr><td colspan="5" class="muted">${f === "review" ? "Nothing to review." : "No titles."}</td></tr>`;
  libTitles.rows = rows;
}
$("lib-filter").onchange = libTitles;
$("lib-q").oninput = () => { clearTimeout(libQT); libQT = setTimeout(libTitles, 300); };

function libForm() {
  const f = $("lib-form");
  return { kind: f.kind.value, name: f.elements.name.value.trim(), location: f.location.value.trim(),
           username: f.username.value.trim(), password: f.password.value, id: libEditing || undefined };
}
function libReset() {
  libEditing = null; libBrowse = null; $("lib-form").reset();
  $("lib-tested").textContent = ""; $("lib-browse").classList.add("hidden");
  $("lib-form-title").textContent = "Add a library folder"; $("lib-save").textContent = "Add library";
  $("lib-cancel").classList.add("hidden"); $("lib-form").password.placeholder = "";
}
$("lib-cancel").onclick = libReset;
$("lib-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const id = +b.closest(".card").dataset.id, sh = libData.shares.find((x) => x.id === id);
  try {
    if (b.dataset.a === "del") {
      if (!confirm(`Remove ${sh.name} from On Demand? Your video files are not touched.`)) return;
      await api(`/api/library/shares/${id}`, { method: "DELETE" }); toast("Library removed");
    } else if (b.dataset.a === "scan") {
      await api("/api/library/scan", { method: "POST", body: { share_id: id } });
      toast("Scanning…"); pollJob();
    } else if (b.dataset.a === "toggle") {
      await api(`/api/library/shares/${id}`, { method: "PUT", body: { enabled: !sh.enabled } });
    } else if (b.dataset.a === "review") {
      $("lib-filter").value = "review"; $("lib-q").value = sh.name; libTitles();
      $("lib-body").scrollIntoView({ behavior: "smooth" }); return;
    } else if (b.dataset.a === "edit") {
      libReset(); libEditing = id; const f = $("lib-form");
      f.kind.value = sh.kind; f.elements.name.value = sh.name; f.location.value = sh.location; f.username.value = sh.username || "";
      f.password.placeholder = sh.has_password ? "saved (leave empty to keep it)" : "";
      $("lib-form-title").textContent = `Edit ${sh.name}`; $("lib-save").textContent = "Save changes";
      $("lib-cancel").classList.remove("hidden"); f.scrollIntoView({ behavior: "smooth" });
      return;
    }
  } catch (err) { toast(err.message); }
  loaders.library();
});
$("lib-form").onsubmit = async (e) => {
  e.preventDefault();
  const body = libForm();
  try {
    const r = libEditing ? await api(`/api/library/shares/${libEditing}`, { method: "PUT", body })
                         : await api("/api/library/shares", { method: "POST", body });
    toast(r.job ? "Saved. Scanning the folder…" : "Saved"); if (r.job) pollJob();
    libReset(); loaders.library();
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

$("lib-save-settings").onclick = async () => {
  const body = { language: $("lib-lang").value.trim() || "en-US", auto_scan_hours: +$("lib-auto").value, video_mode: $("lib-video").value };
  if ($("lib-key").value.trim()) body.tmdb_key = $("lib-key").value.trim();
  try {
    const hadKey = libData && libData.settings.tmdb_key_set;
    await api("/api/library/settings", { method: "POST", body });
    toast(body.tmdb_key && !hadKey ? "Saved. Downloading details…" : "Saved");
    if (body.tmdb_key && !hadKey) pollJob();
    loaders.library();
  } catch (e) { toast(e.message); }
};
$("lib-test-key").onclick = async () => {
  $("lib-key-state").textContent = "Testing…";
  const r = await api("/api/library/test-key", { method: "POST", body: { key: $("lib-key").value.trim() } }).catch((e) => ({ ok: false, message: e.message }));
  $("lib-key-state").innerHTML = r.ok ? `<span style="color:var(--ok)">✓</span> ${esc(r.message)}` : `<span class="bad-text">${esc(r.message)}</span>`;
};
$("lib-scan").onclick = async () => { await api("/api/library/scan", { method: "POST", body: {} }).catch((e) => toast(e.message)); pollJob(); };
$("lib-refresh").onclick = async () => {
  if (!confirm("Download every title's details and posters from TMDB again? Titles you picked by hand stay as they are.")) return;
  await api("/api/library/scan", { method: "POST", body: { refresh: true } }).catch((e) => toast(e.message)); pollJob();
};

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
      ${r.poster ? `<img src="${esc(r.poster)}" alt="" loading="lazy">` : '<span class="noimg"></span>'}
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
  $("lock-setup").disabled = !me.pc;
  $("lock-hint").textContent = me.server && !me.paired && !me.require_pairing
    ? "Before turning this on, pair this browser too (open /pair here with a code), or only your phones will be able to change setup." : "";
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
})();
