/* TunerBox setup page */
(() => {
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
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

// ---------------------------------------------------------------- jobs
async function startJob(name, q = "") {
  try {
    const r = await api(`/api/jobs/${name}${q}`, { method: "POST" });
    if (!r.started) toast("Another job is already running");
    pollJob();
  } catch (e) { toast(e.message); }
}
let polling = false;
async function pollJob() {
  if (polling) return; polling = true;
  let wasRunning = false;
  for (;;) {
    let j; try { j = await api("/api/jobs"); } catch (_) { break; }
    if (j.running) {
      wasRunning = true;
      $("job").classList.remove("hidden");
      $("job-text").textContent = `${j.stage}${j.total ? ` (${j.done}/${j.total})` : ""}`;
      $("job-bar").style.width = j.total ? `${j.done / j.total * 100}%` : "15%";
    } else {
      $("job").classList.add("hidden");
      if (wasRunning) { toast(j.message); const cur = location.hash.slice(1) || "channels"; loaders[cur] && loaders[cur](); }
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
  const q = $("ch-search").value.trim().toLowerCase();
  const f = $("ch-filter").value;
  const rows = lineup.filter((c) => {
    if (f === "mapped" && !c.stream) return false;
    if (f === "unmapped" && (c.stream || c.mode === "none")) return false;
    if (f === "manual" && c.mode !== "manual") return false;
    if (f === "none" && c.mode !== "none") return false;
    if (f === "weak" && !isWeak(c)) return false;
    if (!q) return true;
    return String(c.number).startsWith(q) || c.name.toLowerCase().includes(q) || (c.callsign || "").toLowerCase().includes(q)
      || (c.stream && c.stream.name.toLowerCase().includes(q));
  });
  const mapped = lineup.filter((c) => c.stream).length;
  $("ch-stats").textContent = `${mapped} of ${lineup.length} lineup channels have a feed · showing ${rows.length}`;
  $("ch-body").innerHTML = rows.map((c) => {
    const s = c.stream;
    const tag = c.mode === "none" ? '<span class="tag none">OFF</span>'
      : c.mode === "manual" ? '<span class="tag manual">MANUAL</span>'
      : s ? `<span class="tag ${isWeak(c) ? "weak" : "auto"}" title="${esc(c.reason)}">AUTO ${c.score}${c.alts ? ` +${c.alts}` : ""}</span>` : "";
    const feed = s ? `<div class="feed"><span class="dot ${s.status}" title="${s.status}"></span>${s.logo ? `<img src="${esc(s.logo)}" onerror="this.remove()">` : ""}
        <div><div>${esc(s.name)}</div><div class="meta">${esc(s.source_name)}${s.grp ? " · " + esc(s.grp) : ""}${s.tvg_id ? " · " + esc(s.tvg_id) : ""}</div></div></div>`
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
  if (b.dataset.a === "play") { openPicker(c); preview(c.stream.key, c.stream.name); }
  if (b.dataset.a === "off") { await api("/api/mapping", { method: "POST", body: { number: n, mode: "none" } }); loaders.channels(); }
  if (b.dataset.a === "auto") { await api("/api/mapping", { method: "POST", body: { number: n, mode: "auto" } }); toast("Re-matching…"); pollJob(); }
});

// ---------------------------------------------------------------- picker
let pickFor = null, player = null, searchT;
function openPicker(c) {
  pickFor = c;
  $("pk-title").textContent = `${c.number}  ${c.name}`;
  $("pk-sub").textContent = c.stream ? `Currently: ${c.stream.name} (${c.stream.source_name})` : "No feed assigned";
  $("pk-search").value = ""; $("pk-now").textContent = "";
  $("picker").classList.remove("hidden");
  loadSuggestions();
  $("pk-search").focus();
}
function closePicker() { stopPreview(); $("picker").classList.add("hidden"); pickFor = null; }
$("pk-close").onclick = closePicker;
$("picker").addEventListener("click", (e) => { if (e.target.id === "picker") closePicker(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && pickFor) closePicker(); });
$("pk-search").oninput = () => { clearTimeout(searchT); searchT = setTimeout(loadSuggestions, 250); };
async function loadSuggestions() {
  const q = $("pk-search").value.trim();
  $("pk-hint").textContent = q ? `Results for "${q}":` : "Best guesses for this channel:";
  const list = await api(`/api/suggest?limit=80&${q ? "q=" + encodeURIComponent(q) : "number=" + pickFor.number}`);
  $("pk-results").innerHTML = list.length ? list.map((s) => `<li data-k="${esc(s.key)}" data-n="${esc(s.name)}">
      <span class="dot ${s.status}" title="${s.status}"></span>
      <div class="nm"><div>${esc(s.name)}</div><div class="muted small">${esc(s.source_name)}${s.grp ? " · " + esc(s.grp) : ""}${s.tvg_id ? " · " + esc(s.tvg_id) : ""}</div></div>
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
  const q = $("ex-search").value.trim().toLowerCase(), g = $("ex-group").value;
  const rows = extras.filter((e) => (!g || e.grp === g) && (!q || e.name.toLowerCase().includes(q) || String(e.number).startsWith(q)));
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
      <div class="t">${esc(s.name)} <span class="tag">${{ xtream: "Xtream", hdhomerun: "HDHomeRun" }[s.type] || "M3U"}</span> ${s.enabled ? "" : '<span class="tag none">disabled</span>'}</div>
      <div class="u">${esc(s.url)}${s.username ? " · user " + esc(s.username) : ""}</div>
      <div class="muted small">${s.count} channels · ${esc(s.status || "not scanned yet")}${s.updated_at ? " · " + fmt(s.updated_at) : ""}</div>
      ${s.epg_url ? `<div class="muted small">Guide: ${esc(s.epg_url.replace(/password=[^&]+/, "password=…"))}</div>` : ""}
      <div class="row"><button class="btn small primary" data-a="groups">Groups…</button>
      ${s.hidden ? `<span class="muted small">${s.hidden} group${s.hidden === 1 ? "" : "s"} hidden</span>` : ""}
      <span class="grow"></span>
      <button class="btn small" data-a="toggle">${s.enabled ? "Disable" : "Enable"}</button>
      <button class="btn small danger" data-a="del">Delete</button></div>
      <div class="row"><span class="muted small">Video:</span><select data-a="video" class="small">
        <option value="" ${!s.video_mode ? "selected" : ""}>Default (Settings)</option>
        <option value="copy" ${s.video_mode === "copy" ? "selected" : ""}>Original</option>
        <option value="h264" ${s.video_mode === "h264" ? "selected" : ""}>Re-encode to H.264${s.type === "hdhomerun" ? " (needed for antenna TV)" : ""}</option>
        <option value="roku720" ${s.video_mode === "roku720" ? "selected" : ""}>Re-encode to 720p (Roku, slow devices)</option></select></div>
      <details class="adv"${s.exclude_groups ? " open" : ""}><summary class="muted small">Skip pattern (advanced)</summary>
      <div class="row"><input data-f="exclude_groups" value="${esc(s.exclude_groups)}" placeholder="regex, e.g. ^(UK|AR)|XXX" style="flex:1">
      <button class="btn small" data-a="save">Save</button></div></details></div>`).join("")
    : '<p class="muted">No playlists yet. Add your M3U link or Xtream login below, then press <b>Scan playlists now</b>.</p>';
};
$("src-list").addEventListener("change", async (e) => {
  if (e.target.dataset.a !== "video") return;
  await api(`/api/sources/${e.target.closest(".card").dataset.id}`, { method: "PATCH", body: { video_mode: e.target.value } });
  toast("Saved: applies from the next channel change");
});
$("src-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const card = b.closest(".card"), id = card.dataset.id;
  if (b.dataset.a === "del" && confirm("Delete this playlist?")) await api(`/api/sources/${id}`, { method: "DELETE" });
  if (b.dataset.a === "toggle") await api(`/api/sources/${id}`, { method: "PATCH", body: { enabled: b.textContent === "Enable" ? 1 : 0 } });
  if (b.dataset.a === "groups") { openGroups(+id); return; }
  if (b.dataset.a === "save") { await api(`/api/sources/${id}`, { method: "PATCH", body: { exclude_groups: card.querySelector("[data-f]").value } }); toast("Saved - rescan to apply"); }
  loaders.sources();
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
  $("hd-found").innerHTML = list.map((d) => `<button type="button" class="btn small" data-ip="${esc(d.ip)}" data-name="${esc(d.name)}">${esc(d.name)} ${esc(d.model)} · ${esc(d.ip)} · ${d.tuners} tuners</button>`).join(" ");
};
$("hd-found").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-ip]"); if (!b) return;
  $("src-form").url.value = b.dataset.ip;
  if (!$("src-form").elements.name.value) $("src-form").elements.name.value = b.dataset.name;
});
$("src-type").onchange = srcType; srcType();
$("src-form").onsubmit = async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  try { await api("/api/sources", { method: "POST", body: data }); e.target.reset(); srcType(); $("hd-found").textContent = ""; toast("Added - scanning…"); loaders.sources(); startJob("scan"); }
  catch (err) { toast(err.message); }
};

// ---------------------------------------------------------------- playlist groups
// Tick/untick whole groups. "US| SPORTS", "DE: NEWS"… can be filtered by their country prefix.
const GR = { id: 0, all: [], shown: new Map(), orig: new Set() };
const regionOf = (name) => { const m = /^\W*([A-Za-z]{2,3})\s*[|:]/.exec(name || ""); return m ? m[1].toUpperCase() : ""; };
async function openGroups(id) {
  let d;
  try { d = await api(`/api/sources/${id}/groups`); } catch (e) { toast(e.message); return; }
  GR.id = id; GR.all = d.groups;
  GR.shown = new Map(d.groups.map((g) => [g.name, !g.hidden]));
  GR.orig = new Set(d.groups.filter((g) => g.hidden).map((g) => g.name));
  $("gr-title").textContent = `Groups in ${d.source.name}`;
  const note = $("gr-note");
  note.textContent = d.partial ? "Groups skipped by an older version aren't listed yet. They appear after the next scan." : "";
  note.classList.toggle("hidden", !d.partial);
  const regions = new Map();
  for (const g of d.groups) { const r = regionOf(g.name); regions.set(r, (regions.get(r) || 0) + 1); }
  $("gr-region").innerHTML = '<option value="">All countries</option>'
    + [...regions].filter(([r]) => r).sort((a, b) => b[1] - a[1]).map(([r, n]) => `<option value="${esc(r)}">${esc(r)} (${n} groups)</option>`).join("")
    + (regions.has("") ? `<option value="-">No country label (${regions.get("")} groups)</option>` : "");
  $("gr-search").value = ""; $("gr-show").value = "";
  $("groups").classList.remove("hidden");
  renderGroups();
  $("gr-search").focus();
}
function listedGroups() {
  const q = $("gr-search").value.trim().toLowerCase(), reg = $("gr-region").value, show = $("gr-show").value;
  return GR.all.filter((g) => (!q || g.name.toLowerCase().includes(q))
    && (!reg || (reg === "-" ? !regionOf(g.name) : regionOf(g.name) === reg))
    && (!show || (show === "shown") === GR.shown.get(g.name)));
}
function renderGroups() {
  const rows = listedGroups();
  $("gr-body").innerHTML = rows.slice(0, 3000).map((g) => `<tr data-g="${esc(g.name)}" class="${GR.shown.get(g.name) ? "" : "off"}">
    <td class="gr-c"><input type="checkbox" ${GR.shown.get(g.name) ? "checked" : ""}></td>
    <td>${esc(g.name || "(no group)")}${g.pattern ? ' <span class="tag none" title="Matches the skip pattern, so it stays out even when ticked">skip pattern</span>' : ""}</td>
    <td class="gr-n">${g.count ? g.count.toLocaleString() : ""}</td></tr>`).join("")
    || '<tr><td colspan="3" class="muted">No groups match.</td></tr>';
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
  $("gr-stats").textContent = `Keeping ${gs.toLocaleString()} of ${GR.all.length.toLocaleString()} groups `
    + `(${cs.toLocaleString()} of ${ct.toLocaleString()} channels) · listing ${(listed ?? listedGroups().length).toLocaleString()}`
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
  try { r = await api(`/api/sources/${GR.id}/groups`, { method: "PUT", body: { hidden } }); } catch (e) { toast(e.message); return; }
  closeGroups(); loaders.sources();
  if (r.shown_again) toast(r.job ? "Saved. Rescanning to add the groups back…" : "Saved. Press Scan playlists when the current job finishes to add the groups back.");
  else if (r.newly_hidden) toast(r.job ? `Saved. Removed ${r.newly_hidden} group${r.newly_hidden === 1 ? "" : "s"}, re-matching channels…` : "Saved. Press Re-run auto match when the current job finishes.");
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

// ---------------------------------------------------------------- settings
loaders.settings = async () => {
  settings = await api("/api/settings");
  const f = $("set-form");
  for (const el of f.elements) {
    if (!el.name || !(el.name in settings)) continue;
    if (el.type === "checkbox") el.checked = !!settings[el.name]; else el.value = String(settings[el.name]);
  }
  $("u-m3u").textContent = location.origin + "/playlist.m3u";
  $("u-epg").textContent = location.origin + "/epg.xml";
  $("u-hdhr").textContent = location.origin;
};
$("set-form").onsubmit = async (e) => {
  e.preventDefault();
  const out = {};
  for (const el of e.target.elements) {
    if (!el.name) continue;
    out[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" || el.dataset.num ? +el.value : el.value;
  }
  await api("/api/settings", { method: "POST", body: out });
  toast("Settings saved. Re-run auto match if you changed strictness or extra numbering.");
};

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
  $("lock-setup").disabled = !me.pc;
  $("pair-start").classList.toggle("hidden", !me.pc);
  $("pair-pconly").classList.toggle("hidden", me.pc);
  if (me.pc) renderDevices(await api("/api/devices"));
  else $("dev-body").innerHTML = '<tr><td colspan="4" class="muted">Manage paired phones on the TunerBox PC</td></tr>';
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
showTab(location.hash.slice(1) || "channels");
pollJob();
})();
