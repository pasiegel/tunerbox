/* TunerBox phone remote */
(() => {
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (r.status === 401) { location.replace("/pair"); throw new Error("Not paired"); }
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
  return r.json();
};
let toastT;
function toast(m, ms = 2500) {
  const t = $("toast"); t.textContent = m; t.classList.remove("hidden");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), ms);
}
const buzz = () => { try { navigator.vibrate && navigator.vibrate(12); } catch (_) {} };

// ---------------------------------------------------------------- TV screens
let screens = [], tv = localStorage.getItem("tbr.tv") || "", tvName = localStorage.getItem("tbr.tvname") || "";
async function loadScreens() {
  try { screens = await api("/api/remote/screens"); } catch (_) { return; }
  const online = screens.filter((s) => s.online);
  // keep the same TV across page reloads (its id changes when the TV page reloads, its name doesn't)
  let cur = online.find((s) => s.id === tv) || online.find((s) => s.name === tvName) || online[0];
  tv = cur ? cur.id : "";
  if (cur) { tvName = cur.name; localStorage.setItem("tbr.tv", tv); localStorage.setItem("tbr.tvname", tvName); }
  const sel = $("tv");
  sel.innerHTML = online.length
    ? online.map((s) => `<option value="${esc(s.id)}" ${s.id === tv ? "selected" : ""}>${esc(s.name)}</option>`).join("")
    : '<option value="">No TV screen is open</option>';
  showNow(cur);
}
function showNow(s) {
  const st = (s && s.state) || {};
  if (!s) { $("now-num").textContent = ""; $("now-name").textContent = "Open TunerBox on the PC, iPad or Android box"; $("now-title").textContent = ""; return; }
  if (st.power === false) { $("now-num").textContent = ""; $("now-name").textContent = "Off"; $("now-title").textContent = "Press ⏻ to turn it on"; return; }
  $("now-num").textContent = st.number || (st.feed ? "LIVE" : "");
  $("now-name").textContent = st.name || "";
  const bits = [st.title, st.muted ? "Muted" : st.volume != null ? `Vol ${Math.round(st.volume * 100)}` : "", st.buffer ? `Cache ${st.buffer}s` : ""];
  $("now-title").textContent = bits.filter(Boolean).join(" · ");
}
$("tv").addEventListener("change", (e) => {
  tv = e.target.value; const s = screens.find((x) => x.id === tv);
  tvName = s ? s.name : ""; localStorage.setItem("tbr.tv", tv); localStorage.setItem("tbr.tvname", tvName); showNow(s);
});
setInterval(() => { if (!document.hidden) loadScreens(); }, 2000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadScreens(); });

async function send(cmd) {
  if (!tv) { toast("No TV screen is open"); await loadScreens(); if (!tv) return false; }
  try { await api("/api/remote/send", { method: "POST", body: { screen: tv, cmd } }); return true; }
  catch (e) { toast(e.message); loadScreens(); return false; }
}
const key = (k) => send({ type: "key", key: k });

// ---------------------------------------------------------------- buttons (arrows/volume/channel repeat while held)
let holdT = null;
function stopHold() { clearTimeout(holdT); holdT = null; }
document.querySelectorAll("button[data-k]").forEach((b) => {
  b.addEventListener("pointerdown", (e) => {
    e.preventDefault(); buzz(); b.classList.add("down");
    key(b.dataset.k);
    if (b.classList.contains("rep")) {
      stopHold();
      const again = () => { key(b.dataset.k); holdT = setTimeout(again, 180); };
      holdT = setTimeout(again, 500);
    }
  });
  for (const ev of ["pointerup", "pointerleave", "pointercancel"]) b.addEventListener(ev, () => { b.classList.remove("down"); stopHold(); });
  b.addEventListener("contextmenu", (e) => e.preventDefault());
});

// number pad: type a channel number, then Go (or it goes on its own after a pause)
let typed = "", typedT = null;
function showTyped() { $("typed").textContent = typed ? `Channel ${typed}` : " "; }
async function goTyped() {
  clearTimeout(typedT);
  const n = parseInt(typed, 10); typed = ""; showTyped();
  if (!isNaN(n)) send({ type: "tune", number: n });
}
document.querySelectorAll("button[data-n]").forEach((b) => b.addEventListener("click", () => {
  buzz();
  const n = b.dataset.n;
  if (n === "clr") { typed = ""; showTyped(); clearTimeout(typedT); return; }
  if (n === "go") { goTyped(); return; }
  typed = (typed + n).slice(0, 6); showTyped();
  clearTimeout(typedT); typedT = setTimeout(goTyped, 2500);
}));

// ---------------------------------------------------------------- tabs
function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll("body > section").forEach((s) => s.classList.toggle("hidden", s.id !== "tab-" + name));
  if (name === "chan") loadChannels();
  if (name === "sport") loadSports();
  if (name === "find") $("s-q").focus();
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

function listHtml(items) {
  return items.map((it, i) => `<li data-i="${i}">${it.logo ? `<img src="${esc(it.logo)}" alt="" loading="lazy">` : '<span class="nologo"></span>'}
    <div><b>${esc(it.title)}</b><small>${esc(it.sub || "")}</small></div>${it.badge ? `<span class="badge ${it.live ? "live" : ""}">${esc(it.badge)}</span>` : ""}</li>`).join("");
}
function bindList(el, items) {
  el.innerHTML = listHtml(items);
  el.onclick = async (e) => {
    const li = e.target.closest("li"); if (!li) return;
    const it = items[+li.dataset.i]; buzz();
    if (it.open) { it.open(); return; }
    if (await send(it.cmd)) { li.classList.add("sent"); setTimeout(() => li.classList.remove("sent"), 600); toast(`Tuning ${it.title}`); }
  };
}

// channels
let channels = null;
async function loadChannels() {
  if (!channels) {
    $("ch-list").innerHTML = '<li class="msg">Loading…</li>';
    try { channels = await api("/api/channels"); } catch (e) { $("ch-list").innerHTML = `<li class="msg">${esc(e.message)}</li>`; return; }
  }
  filterChannels();
}
function filterChannels() {
  const q = $("ch-q").value.trim().toLowerCase();
  const hits = (channels || []).filter((c) => c.mapped && (!q || String(c.number).startsWith(q) || (c.label || c.name).toLowerCase().includes(q)));
  const items = hits.slice(0, 300).map((c) => ({ title: `${c.number}  ${c.label || c.name}`, sub: c.kind === "lineup" ? "" : (c.group || ""),
    logo: c.logo, cmd: { type: "tune", number: c.number } }));
  bindList($("ch-list"), items);
  if (hits.length > 300) $("ch-list").insertAdjacentHTML("beforeend", `<li class="msg">${hits.length - 300} more: type to narrow it down</li>`);
  if (!hits.length) $("ch-list").innerHTML = '<li class="msg">No channels match</li>';
}
let chT; $("ch-q").addEventListener("input", () => { clearTimeout(chT); chT = setTimeout(filterChannels, 150); });

// search
const when = (t) => new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
let sT, sSeq = 0;
$("s-q").addEventListener("input", () => { clearTimeout(sT); sT = setTimeout(runSearch, 300); });
async function runSearch() {
  const q = $("s-q").value.trim(), seq = ++sSeq;
  if (q.length < 2) { $("s-list").innerHTML = ""; return; }
  $("s-list").innerHTML = '<li class="msg">Searching…</li>';
  let d; try { d = await api(`/api/search?q=${encodeURIComponent(q)}`); } catch (e) { $("s-list").innerHTML = `<li class="msg">${esc(e.message)}</li>`; return; }
  if (seq !== sSeq) return;
  const n0 = Date.now() / 1000;
  const items = [
    ...d.channels.map((c) => ({ title: `${c.number}  ${c.label || c.name}`, sub: c.now || "", logo: c.logo, cmd: { type: "tune", number: c.number } })),
    ...d.programs.filter((p) => p.start <= n0 && n0 < p.stop).map((p) => ({ title: p.title, sub: `${p.ch.number} ${p.ch.label || p.ch.name}`,
      badge: "ON NOW", live: true, cmd: { type: "tune", number: p.ch.number } })),
    ...d.feeds.map(feedRow),
  ];
  bindList($("s-list"), items);
  if (!items.length) $("s-list").innerHTML = '<li class="msg">Nothing on now matches. Try fewer letters.</li>';
}
function feedRow(f) {
  return { title: f.name, sub: f.group || "", logo: f.logo, badge: f.kind === "ppv" ? "PPV" : f.status === "ok" ? "LIVE" : "",
           live: f.status === "ok",
           cmd: { type: "feed", feed: { key: f.key, name: f.name, kind: f.kind, logo: f.logo || "", group: f.group || "" } } };
}

// sports
async function loadSports() {
  $("sp-list").innerHTML = '<li class="msg">Loading…</li>';
  let d; try { d = await api("/api/sports"); } catch (e) { $("sp-list").innerHTML = `<li class="msg">${esc(e.message)}</li>`; return; }
  const items = [
    ...d.on_now.map((p) => p.key ? feedRow(p) : { title: p.title, sub: `${p.ch.number} ${p.ch.label || p.ch.name}`, badge: "ON NOW", live: true,
      logo: p.ch.logo, cmd: { type: "tune", number: p.ch.number } }),
    ...d.channels.map((c) => ({ title: `${c.number}  ${c.label || c.name}`, sub: c.now ? c.now.title : "Sports channel", logo: c.logo, cmd: { type: "tune", number: c.number } })),
    ...d.groups.filter((g) => g.named).map((g) => ({ title: g.name, sub: `${g.named} event${g.named === 1 ? "" : "s"}${g.live ? ` · ${g.live} live` : ""}`,
      badge: g.kind === "ppv" ? "PPV" : "EVENTS", open: () => openGroup(g.name) })),
  ];
  bindList($("sp-list"), items);
  if (!items.length) $("sp-list").innerHTML = '<li class="msg">No sports on right now</li>';
}
async function openGroup(name) {
  $("sp-list").innerHTML = '<li class="msg">Loading…</li>';
  let g; try { g = await api(`/api/sports/group?name=${encodeURIComponent(name)}`); } catch (e) { toast(e.message); loadSports(); return; }
  const items = [{ title: "‹ Back to Sports", open: loadSports },
    ...g.feeds.filter((f) => !f.slot && !f.ended).map((f) => feedRow({ ...f, kind: g.kind }))];
  bindList($("sp-list"), items);
  window.scrollTo(0, 0);
}

$("forget").addEventListener("click", async (e) => {
  e.preventDefault();
  if (!confirm("Unpair this phone? You'll need a new code from the PC to use it as a remote again.")) return;
  await fetch("/api/pair/forget", { method: "POST" }); location.replace("/pair");
});

loadScreens();
})();
