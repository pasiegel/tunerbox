// First-time setup (/welcome): area for the guide, a tuner or playlist, a library folder, recordings on or off,
// and pairing a phone. A new install's first screen comes here (api.index); finishing or skipping sets
// welcome_done, so it never shows again. Everything uses the same API as Setup.
(() => {
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
  return r.json();
};
const post = (url, body) => api(url, { method: "POST", body: body || {} });
let toastT;
const toast = (m) => { const t = $("toast"); t.textContent = m; t.classList.remove("hidden"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), 4000); };
const say = (id, text, cls) => { const el = $(id); el.className = cls || "muted"; el.textContent = text || ""; };

// where to go afterwards: the screen that was opened (only a path on this server)
const NEXT = (() => { const n = new URLSearchParams(location.search).get("next") || ""; return /^\/(?!\/)/.test(n) ? n : "/?on"; })();

const steps = [...document.querySelectorAll(".wz-step")];
const names = steps.map((s) => s.dataset.step);
const done = {};            // what each step did, for the summary: {region: "Comcast …", channels: ["…"], …}
let cur = 0, settings = {}, me = {}, needScan = false;

$("wz-dots").innerHTML = steps.slice(1, -1).map((s) => `<li data-for="${s.dataset.step}">${esc(s.dataset.title)}</li>`).join("");

function show(i) {
  if (names[cur] === "phone" && i !== cur) stopPair();
  cur = Math.max(0, Math.min(steps.length - 1, i));
  steps.forEach((s, n) => s.classList.toggle("hidden", n !== cur));
  document.querySelectorAll("#wz-dots li").forEach((li) => {
    const n = names.indexOf(li.dataset.for);
    li.classList.toggle("on", n === cur);
    li.classList.toggle("done", n < cur && !!done[li.dataset.for]);
  });
  $("wz-skipall").classList.toggle("hidden", names[cur] === "done");
  (enter[names[cur]] || (() => {}))();
  focusFirst();
  window.scrollTo(0, 0);
}
const next = () => show(cur + 1);
const back = () => show(cur - 1);
function focusFirst() {
  const s = steps[cur];
  const el = s.querySelector("[data-autofocus]:not(.hidden):not(:disabled)")
    || [...s.querySelectorAll(".btn.primary, .wz-choice, input")].find(visible);
  if (el) el.focus({ preventScroll: true });
}
const visible = (el) => !el.disabled && el.offsetParent !== null && !el.closest(".hidden");

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-next]")) next();
  else if (e.target.closest("[data-back]")) back();
  else if (e.target.closest("[data-skip]")) next();
});

// finishing or skipping: never show this again, then on to TV
async function finish(to) {
  try { await post("/api/settings", { welcome_done: true }); } catch (e) { toast(e.message); return; }
  location.replace(to || NEXT);
}
$("wz-skipall").onclick = () => finish();
$("dn-watch").onclick = () => finish();
$("dn-setup").onclick = () => finish("/setup");

// ---------------------------------------------------------------- background jobs (playlist scan)
let jobTimer = null;
async function startScan() {
  needScan = true;
  try { const r = await post("/api/jobs/scan"); if (r.started) needScan = false; } catch (_) { /* tried again at the end */ }
  watchJobs();
}
function watchJobs() {
  if (jobTimer) return;
  const tick = async () => {
    let j; try { j = await api("/api/jobs"); } catch (_) { j = {}; }
    if (!j.running && needScan) {          // a scan was asked for while another job ran: start it now
      try { const r = await post("/api/jobs/scan"); if (r.started) { needScan = false; j = { running: true, stage: "Reading your playlists" }; } } catch (_) {}
    }
    const box = $("dn-job");
    box.classList.toggle("hidden", !j.running);
    if (j.running) {
      $("dn-job-text").textContent = `${j.stage || "Working"}${j.total ? ` (${j.done}/${j.total})` : ""} - you can start watching while this finishes`;
      $("dn-job-bar").style.width = j.total ? `${(j.done / j.total) * 100}%` : "15%";
    }
    jobTimer = j.running || needScan ? setTimeout(tick, 1500) : null;
  };
  tick();
}

// ---------------------------------------------------------------- 1: area for the guide
const gnCountry = (zip) => (/^[A-Z]\d[A-Z]/i.test(zip) ? "CAN" : "USA");   // A1A 1A1 = Canada
let rgPick = null, rgZip = "";
async function findProviders() {
  const zip = $("rg-zip").value.trim().replace(/\s+/g, "").toUpperCase();
  if (!zip) { say("rg-msg", "Enter your ZIP or postal code first", "bad-text"); return; }
  say("rg-msg", "Looking for providers…");
  $("rg-list").innerHTML = ""; rgPick = null; $("rg-save").disabled = true;
  let list;
  try { list = await api(`/api/gracenote/providers?zip=${encodeURIComponent(zip)}&country=${gnCountry(zip)}`); }
  catch (e) { say("rg-msg", e.message, "bad-text"); return; }
  rgZip = zip;
  const none = `<button type="button" class="wz-opt" data-none="1">No local guide<small>Use online guides only (channel numbers from your playlist)</small></button>`;
  if (!list.length) { say("rg-msg", "No providers found for that code. Check it, or continue without a local guide.", "bad-text"); $("rg-list").innerHTML = none; return; }
  say("rg-msg", `${list.length} provider${list.length === 1 ? "" : "s"} near ${zip}. Pick the one you have (or would have) at home.`);
  $("rg-list").innerHTML = list.map((p) => `<button type="button" class="wz-opt" aria-pressed="false" data-l="${esc(p.lineupId)}" data-h="${esc(p.headendId)}"
      data-d="${esc(p.device || "-")}" data-n="${esc(p.name)}">${esc(p.name)}<small>${[p.location, p.type].filter(Boolean).map(esc).join(" · ")}</small></button>`).join("") + none;
  $("rg-list").querySelector(".wz-opt").focus();
}
$("rg-find").onclick = findProviders;
$("rg-zip").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); findProviders(); } });
$("rg-list").addEventListener("click", (e) => {
  const b = e.target.closest(".wz-opt"); if (!b) return;
  $("rg-list").querySelectorAll(".wz-opt").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  rgPick = b; $("rg-save").disabled = false;
  $("rg-save").focus();
});
$("rg-save").onclick = async () => {
  if (!rgPick) return;
  const gn = rgPick.dataset.none ? { enabled: false }
    : { enabled: true, postal_code: rgZip, country: gnCountry(rgZip), lineup_id: rgPick.dataset.l, headend_id: rgPick.dataset.h, device: rgPick.dataset.d };
  try { settings = await post("/api/settings", { gracenote: gn }); } catch (e) { say("rg-msg", e.message, "bad-text"); return; }
  done.region = rgPick.dataset.none ? "No local guide" : `${rgPick.dataset.n} (${rgZip})`;
  needScan = true;                  // the new channel numbers are used once the playlists are read again
  next();
};

// ---------------------------------------------------------------- 2: tuner or playlist
let chKind = "";
const CH = {
  hdhomerun: { label: "Tuner address (IP, e.g. 192.168.1.40)", ph: "192.168.1.40" },
  m3u: { label: "Playlist address", ph: "https://… or a file on this computer" },
  xtream: { label: "Server address", ph: "http://host:port" },
};
function pickKind(kind) {
  chKind = kind;
  document.querySelectorAll("#ch-kinds .wz-choice").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.kind === kind)));
  const f = $("ch-form");
  f.reset(); f.classList.remove("hidden");
  f.classList.toggle("xt", kind === "xtream"); f.classList.toggle("hd", kind === "hdhomerun");
  f.username.required = kind === "xtream"; f.password.required = kind === "xtream";
  $("ch-url-label").textContent = CH[kind].label; f.url.placeholder = CH[kind].ph;
  say("ch-msg", "");
  if (kind === "hdhomerun") findTuners(); else f.url.focus();
}
$("ch-kinds").addEventListener("click", (e) => { const b = e.target.closest(".wz-choice"); if (b) pickKind(b.dataset.kind); });
async function findTuners() {
  $("ch-found").innerHTML = '<span class="muted">Looking for tuners on your network…</span>';
  let list; try { list = await api("/api/hdhomerun/discover"); } catch (e) { $("ch-found").innerHTML = `<span class="bad-text">${esc(e.message)}</span>`; return; }
  if (!list.length) { $("ch-found").innerHTML = '<span class="muted">No tuners found. Type its address below (the HDHomeRun app or your router shows it).</span>'; $("ch-form").url.focus(); return; }
  $("ch-found").innerHTML = list.map((d) => `<button type="button" class="wz-opt" aria-pressed="false" data-ip="${esc(d.ip)}" data-name="${esc(d.name)}">${esc(d.name)} ${esc(d.model)}<small>${esc(d.ip)} · ${d.tuners} tuners</small></button>`).join("");
  const first = $("ch-found").querySelector(".wz-opt");
  pickTuner(first); first.focus();
}
function pickTuner(b) {
  $("ch-found").querySelectorAll(".wz-opt").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  const f = $("ch-form"); f.url.value = b.dataset.ip; if (!f.elements.name.value) f.elements.name.value = b.dataset.name;
}
$("ch-found").addEventListener("click", (e) => { const b = e.target.closest(".wz-opt"); if (b) { pickTuner(b); $("ch-add").focus(); } });
$("ch-find").onclick = findTuners;
$("ch-cancel").onclick = () => {
  $("ch-form").classList.add("hidden"); chKind = "";
  document.querySelectorAll("#ch-kinds .wz-choice").forEach((b) => b.setAttribute("aria-pressed", "false"));
  focusFirst();
};
$("ch-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target, d = Object.fromEntries(new FormData(f));
  const body = { type: chKind, name: d.name.trim(), url: d.url.trim(), username: (d.username || "").trim(), password: (d.password || "").trim() };
  if (!body.url) { f.url.focus(); return; }
  $("ch-add").disabled = true; say("ch-msg", chKind === "hdhomerun" ? "Connecting to the tuner…" : "Adding…");
  try { await post("/api/sources", body); }
  catch (err) { say("ch-msg", err.message, "bad-text"); $("ch-add").disabled = false; return; }
  $("ch-add").disabled = false;
  const label = body.name || body.url;
  (done.channels = done.channels || []).push(label);
  $("ch-added").insertAdjacentHTML("beforeend", `<li>${esc(label)} <span class="muted small">${{ hdhomerun: "Antenna tuner", m3u: "M3U playlist", xtream: "Xtream login" }[chKind]}</span></li>`);
  say("ch-msg", "Added. Its channels are read in the background. Add another, or continue.", "ok-text");
  $("ch-form").classList.add("hidden"); chKind = "";
  document.querySelectorAll("#ch-kinds .wz-choice").forEach((b) => b.setAttribute("aria-pressed", "false"));
  $("ch-next").classList.remove("hidden");
  steps[cur].querySelector("[data-skip]").classList.add("hidden");
  $("ch-next").focus();
  startScan();
};

// ---------------------------------------------------------------- 3: library folder
let libKind = "movie";
$("lib-kind").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-kind]"); if (!b) return;
  libKind = b.dataset.kind;
  $("lib-kind").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
});
const libBody = () => {
  const f = $("lib-form");
  return { kind: libKind, name: f.elements.name.value.trim(), location: f.location.value.trim(), username: f.username.value.trim(), password: f.password.value };
};
$("lib-test").onclick = async () => {
  const body = libBody();
  if (!body.location) { say("lib-msg", "Enter the folder or network location first", "bad-text"); $("lib-form").location.focus(); return; }
  say("lib-msg", "Connecting…");
  try {
    const r = await post("/api/library/browse", { ...body, path: "" });
    say("lib-msg", r.ok ? `✓ ${r.message}${r.folders && r.folders.length ? ` · ${r.folders.length} folder${r.folders.length === 1 ? "" : "s"} inside` : ""}` : r.message, r.ok ? "ok-text" : "bad-text");
  } catch (e) { say("lib-msg", e.message, "bad-text"); }
};
$("lib-form").onsubmit = async (e) => {
  e.preventDefault();
  const body = libBody();
  if (!body.location) return;
  $("lib-add").disabled = true; say("lib-msg", "Adding…");
  try { await post("/api/library/shares", body); }
  catch (err) { say("lib-msg", err.message, "bad-text"); $("lib-add").disabled = false; return; }
  $("lib-add").disabled = false;
  const label = `${body.name || body.location} (${libKind === "movie" ? "Movies" : "TV shows"})`;
  (done.library = done.library || []).push(label);
  $("lib-added").insertAdjacentHTML("beforeend", `<li>${esc(label)}</li>`);
  say("lib-msg", "Added. TunerBox is reading the folder; its titles show up in On Demand. Add another, or continue.", "ok-text");
  $("lib-form").reset();
  $("lib-next").classList.remove("hidden");
  steps[cur].querySelector("[data-skip]").classList.add("hidden");
  $("lib-next").focus();
  watchJobs();
};

// ---------------------------------------------------------------- 4: recordings
function markDvr(on) {
  document.querySelectorAll("#dvr-pick .wz-choice").forEach((b) => b.setAttribute("aria-pressed", String((b.dataset.dvr === "1") === on)));
}
$("dvr-pick").addEventListener("click", async (e) => {
  const b = e.target.closest(".wz-choice"); if (!b) return;
  const on = b.dataset.dvr === "1";
  try { settings = await post("/api/settings", { dvr_enabled: on }); } catch (err) { say("dvr-msg", err.message, "bad-text"); return; }
  markDvr(on);
  done.dvr = on ? "On" : "Off";
  next();
});

// ---------------------------------------------------------------- 5: pair a phone
let pairTimer = null, pairBefore = 0;
function stopPair(cancel = true) {
  if (!pairTimer) return;
  clearInterval(pairTimer); pairTimer = null;
  if (cancel) post("/api/pair/cancel").catch(() => {});
  $("ph-live").classList.add("hidden"); $("ph-idle").classList.remove("hidden");
}
$("ph-start").onclick = async () => {
  let p; try { p = await post("/api/pair/start"); } catch (e) { say("ph-msg", e.message, "bad-text"); return; }
  try { pairBefore = (await api("/api/devices")).length; } catch (_) { pairBefore = 0; }
  $("ph-qr").src = p.qr || ""; $("ph-qr").classList.toggle("hidden", !p.qr);
  $("ph-addr").textContent = p.address;
  $("ph-code").textContent = `${p.code.slice(0, 3)} ${p.code.slice(3)}`;
  $("ph-idle").classList.add("hidden"); $("ph-live").classList.remove("hidden"); say("ph-msg", "");
  clearInterval(pairTimer);
  pairTimer = setInterval(async () => {
    const left = Math.round(p.expires - Date.now() / 1000);
    $("ph-left").textContent = left > 0 ? `Code works for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "";
    let st; try { st = await api("/api/pair/state"); } catch (_) { return; }
    if (st.devices.length > pairBefore) {
      const name = st.devices[st.devices.length - 1].name;
      stopPair(false);
      (done.phone = done.phone || []).push(name);
      $("ph-added").insertAdjacentHTML("beforeend", `<li>${esc(name)} is paired</li>`);
      $("ph-start").textContent = "Pair another phone";
      say("ph-msg", "On the phone, TunerBox opens the remote. Add it to the home screen to find it again.", "ok-text");
      $("ph-next").classList.remove("hidden");
      steps[cur].querySelector("[data-skip]").classList.add("hidden");
      $("ph-next").focus();
    } else if (!st.active) { stopPair(false); say("ph-msg", "The code expired. Press Pair a phone for a new one.", "bad-text"); }
  }, 1000);
};

// ---------------------------------------------------------------- 6: summary
function summary() {
  const row = (label, v) => `<li class="${v ? "" : "skip"}"><b>${label}</b> ${v ? esc(Array.isArray(v) ? v.join(", ") : v) : "Skipped"}</li>`;
  $("dn-list").innerHTML = row("TV guide area", done.region) + row("Channels", done.channels) + row("My Library", done.library)
    + row("Recordings", done.dvr) + row("Phone remote", done.phone);
  if (needScan) startScan(); else watchJobs();
}

// what each step does when it opens
const enter = {
  library() {
    $("lib-where").innerHTML = me.server
      ? "On this server TunerBox sees its own folders: use a network share (<code>\\\\nas\\Media\\Movies</code>) or a folder mounted into it, such as <code>/media/Movies</code>."
      : "A network share (<code>\\\\nas\\Media\\Movies</code> or <code>smb://192.168.1.20/Media/Movies</code>) or a folder on this computer (<code>D:\\Movies</code>, <code>/home/me/Movies</code>).";
  },
  dvr() { markDvr(settings.dvr_enabled !== false); },
  phone() {
    const can = me.manage !== false;
    $("ph-idle").classList.toggle("hidden", !can);
    if (!can) say("ph-msg", "Pairing starts on the TunerBox PC itself (Setup › Phones there).");
  },
  done: summary,
};

// ---------------------------------------------------------------- TV remote: arrows move, OK presses, Back goes back
const FOCUSABLE = "button, input, select, a[href], [tabindex='0']";
function move(dir) {
  const items = [...steps[cur].querySelectorAll(FOCUSABLE), $("wz-skipall")].filter(visible);
  const a = document.activeElement;
  if (!items.includes(a)) { focusFirst(); return; }
  const r = a.getBoundingClientRect(), ax = r.left + r.width / 2, ay = r.top + r.height / 2;
  let best = null, bestScore = Infinity;
  for (const el of items) {
    if (el === a) continue;
    const q = el.getBoundingClientRect(), x = q.left + q.width / 2, y = q.top + q.height / 2;
    const dx = x - ax, dy = y - ay;
    const main = dir === "ArrowDown" ? dy : dir === "ArrowUp" ? -dy : dir === "ArrowRight" ? dx : -dx;
    const side = dir === "ArrowDown" || dir === "ArrowUp" ? Math.abs(dx) : Math.abs(dy);
    // in the direction pressed (overlapping rows count for up/down), nearest first, straight lines preferred
    const ahead = dir === "ArrowDown" ? q.top >= r.bottom - 2 : dir === "ArrowUp" ? q.bottom <= r.top + 2 : main > 4;
    if (!ahead) continue;
    const score = main + side * 2.5;
    if (score < bestScore) { bestScore = score; best = el; }
  }
  if (best) { best.focus(); best.scrollIntoView({ block: "nearest" }); }
}
document.addEventListener("keydown", (e) => {
  document.body.classList.add("kb");
  const a = document.activeElement, typing = a && a.tagName === "INPUT";
  if (e.key.startsWith("Arrow")) {
    // in a text box, left/right move the cursor; past either end they move on, as on a TV keyboard
    if (typing && e.key === "ArrowLeft" && !(a.selectionStart === 0 && a.selectionEnd === 0)) return;
    if (typing && e.key === "ArrowRight" && a.selectionStart < a.value.length) return;
    e.preventDefault(); move(e.key);
  } else if (e.key === "Escape" || e.key === "GoBack" || e.key === "BrowserBack" || (e.key === "Backspace" && !typing)) {
    if (cur > 0) { e.preventDefault(); back(); }
  }
});
document.addEventListener("mousedown", () => document.body.classList.remove("kb"));

// ---------------------------------------------------------------- start
Promise.all([api("/api/settings").catch(() => ({})), api("/api/me").catch(() => ({}))]).then(([s, m]) => {
  settings = s; me = m;
  window.tbTheme && s.theme && tbTheme.server(s.theme);
  show(0);
});
})();
