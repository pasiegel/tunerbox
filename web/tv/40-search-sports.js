// ---------------------------------------------------------------- shared by search + sports
function fmtWhen(t) {
  const d = new Date(t * 1000), today = new Date();
  if (d.toDateString() === today.toDateString()) return fmtTime(t);
  return d.toLocaleDateString([], { weekday: "short" }) + " " + fmtTime(t);
}
function tuneNum(num) {
  const i = S.byNum.get(num);
  if (i === undefined) { toast(`Channel ${num} is not available`); return false; }
  tune(i); return true;
}
function closePanels() { closeSearch(); closeSports(); }
// a guide listing from search/sports: on now -> watch, later -> record options
function pickProgram(p) {
  const i = S.byNum.get(p.ch.number);
  if (i === undefined) { toast(`Channel ${p.ch.number} is not available`); return; }
  const n0 = now();
  if (p.start <= n0 && n0 < p.stop) { closePanels(); tune(i); return; }
  programDialog(S.channels[i], p);
}
const progHtml = (p, badge) => `${badge} <span>${esc(p.title)}${p.subtitle ? ` <small>· ${esc(p.subtitle)}</small>` : ""}</span>`;
function feedBadge(f) {
  if (f.status === "ok") return '<span class="badge live">LIVE</span>';
  if (f.ended) return '<span class="badge off">ENDED</span>';
  if (f.slot) return '<span class="badge off">EMPTY</span>';
  if (f.status === "dead") return '<span class="badge off">OFF AIR</span>';
  return `<span class="badge ${f.kind === "ppv" ? "ppv" : ""}">${f.kind === "ppv" ? "PPV" : "EVENT"}</span>`;
}
const feedItem = (f) => ({ html: `${feedBadge(f)} <span>${esc(f.name)}</span>`, right: esc(f.group || ""),
                           act: () => { closePanels(); playFeed(f); } });
function selIndex(listEl) { return [...listEl.querySelectorAll("li")].findIndex((li) => li.classList.contains("sel")); }

// ---------------------------------------------------------------- search
// On-screen keyboard for remotes; a real keyboard (or the iPad/phone keyboard) types into the box.
const KB_ROWS = [[..."ABCDEFG"], [..."HIJKLMN"], [..."OPQRSTU"], [..."VWXYZ12"], [..."3456789"], ["0", "Space", "⌫", "Clear"]];
const SR = { q: "", zone: "kb", r: 0, c: 0, sel: 0, items: [], timer: null, seq: 0 };
const sInput = $("s-input");
const finePointer = !!(window.matchMedia && matchMedia("(pointer: fine)").matches);

function openSearch() {
  hideBanner(); closeMenu(); closeSports();
  if (top() !== "search") S.layer.push("search");
  $("search").classList.remove("hidden");
  SR.zone = "kb"; renderKb(); renderSearch();
  if (finePointer && !window.TunerBoxApp) sInput.focus();
}
function closeSearch() {
  S.layer = S.layer.filter((l) => l !== "search");
  $("search").classList.add("hidden");
  sInput.blur();
}
function renderKb() {
  $("s-kb").innerHTML = KB_ROWS.map((row, r) => `<div class="kb-row">${row.map((k, c) =>
    `<button class="kb-k${k.length > 1 ? " wide" : ""}${SR.zone === "kb" && SR.r === r && SR.c === c ? " sel" : ""}" data-r="${r}" data-c="${c}">${esc(k)}</button>`).join("")}</div>`).join("");
}
function setQuery(q) {
  SR.q = q; if (sInput.value !== q) sInput.value = q;
  clearTimeout(SR.timer); SR.timer = setTimeout(runSearch, 250);
}
function kbPress(k) {
  if (k === "Space") setQuery(SR.q + " ");
  else if (k === "⌫") setQuery(SR.q.slice(0, -1));
  else if (k === "Clear") setQuery("");
  else setQuery(SR.q + k.toLowerCase());
}
async function runSearch() {
  const seq = ++SR.seq, q = SR.q.trim();
  if (!q) { SR.items = []; renderSearch(); return; }
  $("s-count").textContent = "Searching…";
  let d;
  try { d = await api(`/api/search?q=${encodeURIComponent(q)}`); } catch (e) { $("s-count").textContent = e.message; return; }
  if (seq !== SR.seq) return;
  const n0 = now();
  SR.items = [
    ...d.channels.map((c) => ({ html: `<span class="badge ch">CH</span><b class="num">${c.number}</b> <span>${esc(c.name)}</span>`,
                                right: esc(c.now || ""), act: () => { closePanels(); tuneNum(c.number); } })),
    ...d.programs.map((p) => ({ html: progHtml(p, p.start <= n0 ? '<span class="badge live">ON NOW</span>' : `<span class="badge">${esc(fmtWhen(p.start))}</span>`),
                                right: `${p.ch.number} ${esc(p.ch.label || p.ch.name)}`, act: () => pickProgram(p) })),
    ...d.feeds.map(feedItem),
  ];
  SR.sel = Math.min(SR.sel, Math.max(0, SR.items.length - 1));
  renderSearch();
}
function renderSearch() {
  const q = SR.q.trim();
  $("s-count").textContent = q ? `${SR.items.length || "No"} result${SR.items.length === 1 ? "" : "s"}` : "";
  const entries = SR.items.length ? SR.items
    : [{ label: q ? "Nothing found. Try fewer letters, a team name or a channel number." : "Type a channel name or number, a show, a team or an event." }];
  fillList($("s-list"), entries, SR.zone === "res" && SR.items.length ? SR.sel : -1);
  const li = $("s-list").querySelector("li.sel"); if (li) li.scrollIntoView({ block: "nearest" });
}
// keys typed on a real keyboard (before remote mapping): letters go into the search box
function searchRawKey(ev) {
  if (ev.target === sInput) return !["ArrowDown", "ArrowUp", "Enter", "Escape", "PageDown", "PageUp", "F11"].includes(ev.key);
  if (ev.key === "Backspace" && SR.q) { setQuery(SR.q.slice(0, -1)); ev.preventDefault(); return true; }
  if (ev.key && ev.key.length === 1 && /[\p{L}\p{N} &'+.:!-]/u.test(ev.key) && !/^[0-9]$/.test(ev.key)) {
    setQuery(SR.q + ev.key.toLowerCase()); ev.preventDefault(); return true;
  }
  return false;
}
function searchKey(k) {
  if (k === "Back") { closeSearch(); return; }
  if (/^[0-9]$/.test(k)) { setQuery(SR.q + k); return; }
  if (document.activeElement === sInput && SR.zone === "kb") SR.zone = "res";
  if (SR.zone === "kb") {
    const row = KB_ROWS[SR.r];
    if (k === "ArrowUp") SR.r = Math.max(0, SR.r - 1);
    else if (k === "ArrowDown") SR.r = Math.min(KB_ROWS.length - 1, SR.r + 1);
    else if (k === "ArrowLeft") SR.c = Math.max(0, SR.c - 1);
    else if (k === "ArrowRight") {
      if (SR.c < row.length - 1) SR.c++;
      else if (SR.items.length) { SR.zone = "res"; renderKb(); renderSearch(); return; }
    } else if (k === "Enter") { kbPress(row[SR.c]); return; }
    else return;
    SR.c = Math.min(SR.c, KB_ROWS[SR.r].length - 1);
    renderKb(); return;
  }
  const n = SR.items.length;
  if (k === "ArrowLeft") { if (document.activeElement !== sInput) { SR.zone = "kb"; renderKb(); renderSearch(); } return; }
  if (k === "ArrowDown") SR.sel = Math.min(n - 1, SR.sel + 1);
  else if (k === "ArrowUp") SR.sel = Math.max(0, SR.sel - 1);
  else if (k === "ChannelDown") SR.sel = Math.min(n - 1, SR.sel + 8);
  else if (k === "ChannelUp") SR.sel = Math.max(0, SR.sel - 8);
  else if (k === "Enter") { if (SR.items[SR.sel]) SR.items[SR.sel].act(); return; }
  else return;
  renderSearch();
}
sInput.addEventListener("input", () => setQuery(sInput.value));
sInput.addEventListener("focus", () => { SR.zone = "res"; renderKb(); });
$("s-kb").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  SR.zone = "kb"; SR.r = +b.dataset.r; SR.c = +b.dataset.c;
  kbPress(KB_ROWS[SR.r][SR.c]); renderKb();
});
$("s-list").addEventListener("click", (e) => {
  const li = e.target.closest("li"); if (!li || !SR.items.length) return;
  SR.sel = +li.dataset.i; SR.items[SR.sel].act();
});

// ---------------------------------------------------------------- sports & PPV
const SP_TABS = [["now", "On Now"], ["up", "Coming Up"], ["ch", "Sports Channels"], ["ev", "Events & PPV"]];
const SP = { tab: 0, data: null, group: null, items: [], poll: null };

async function openSports() {
  hideBanner(); closeMenu(); closeSearch();
  if (top() !== "sports") S.layer.push("sports");
  $("sports").classList.remove("hidden");
  SP.group = null;
  $("sp-list").innerHTML = "<li>Loading…</li>";
  try { SP.data = await api("/api/sports"); } catch (e) { $("sp-list").innerHTML = `<li>${esc(e.message)}</li>`; return; }
  const counts = spCounts();
  if (!counts[SP.tab]) { const t = counts.findIndex((n) => n > 0); if (t >= 0) SP.tab = t; }
  renderSports(0);
}
function closeSports() {
  S.layer = S.layer.filter((l) => l !== "sports");
  $("sports").classList.add("hidden");
  clearInterval(SP.poll); SP.poll = null;
}
const spCounts = () => SP.data ? [SP.data.on_now.length, SP.data.upcoming.length, SP.data.channels.length, SP.data.groups.length] : [0, 0, 0, 0];
const ago = (t) => { const m = Math.round((now() - t) / 60); return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };

// A row of On Now / Coming Up. Games from the scoreboard (p.event) carry a score, and may have no
// guide listing: then they play from an event feed (p.feed), or nothing here carries them.
function spGame(p, badge, until) {
  const e = p.event, score = e && [e.score, e.detail].filter(Boolean).join(" · ");
  // title, matchup and score are separate pieces, so a phone can stack them (tv.css: li.game)
  const html = `${badge}<span class="sp-main"><span class="sp-t">${esc(p.title)}</span>${p.subtitle ? `<small class="sp-s">${esc(p.subtitle)}</small>` : ""}</span>`
    + (score ? `<small class="score">${esc(score)}</small>` : "");
  const cls = "game";
  if (p.ch) return { cls, html, right: `${p.ch.number} ${esc(p.ch.label || p.ch.name)}${until ? ` · until ${fmtTime(p.stop)}` : ""}`, act: () => pickProgram(p) };
  if (p.feed) return { cls, html, right: esc(p.feed.group || "Event feed"), act: () => { closePanels(); playFeed(p.feed); } };
  const on = e.on.length ? `on ${e.on.join(", ")}` : "";
  return { cls: "game nochan", html, right: esc(on ? `${on} · no channel` : "no channel"),
           act: () => toast(`${e.name}: none of your channels or event feeds has it${on ? ` (it's ${on})` : ""}`, 4000) };
}
function spItems() {
  const d = SP.data, n0 = now(), id = SP_TABS[SP.tab][0];
  const none = (label) => [{ label }];
  if (id === "now") {
    return d.on_now.length ? d.on_now.map((p) =>
      spGame(p, p.is_live ? '<span class="badge live">LIVE</span>' : `<span class="badge${p.ch || p.feed ? "" : " off"}">${p.ch || p.feed ? "ON" : "SOON"}</span>`, true))
      : none("No sports on right now.");
  }
  if (id === "up") {
    return d.upcoming.length ? d.upcoming.map((p) =>
      spGame(p, `<span class="badge${p.ch || p.feed ? "" : " off"}">${esc(fmtWhen(p.start))}</span>`))
      : none("No upcoming sports.");
  }
  if (id === "ch") {
    return d.channels.length ? d.channels.map((c) => ({
      html: `<b class="num">${c.number}</b> <span>${esc(c.name)}</span>`,
      right: c.now ? (c.now.is_live ? "LIVE · " : "") + esc(c.now.title) : "",
      act: () => { closePanels(); tuneNum(c.number); },
    })) : none("No sports channels found.");
  }
  // events & PPV
  if (!SP.group) {
    return d.groups.length ? d.groups.map((g) => ({
      html: `<span class="badge ${g.kind}">${g.kind === "ppv" ? "PPV" : "EVENTS"}</span> <span>${esc(g.name)}</span>`,
      right: `${g.live ? `<b class="live-n">${g.live} live</b> · ` : ""}${g.named ? `${g.named} event${g.named === 1 ? "" : "s"}` : `${g.count} feeds`}`,
      act: () => openGroup(g.name),
    })) : none("None of your playlists have event or PPV groups (like ESPN+, PPV or NFL Game Pass).");
  }
  const g = SP.group, st = SP.checkState;
  const checking = st && st.running;
  const head = [
    { label: "◀  All event groups", act: () => { SP.group = null; renderSports(0); } },
    { label: checking ? `Checking which feeds are live… ${st.done}/${st.total}`
        : g.checkable ? `⟳  Check which feeds are live now${g.checked ? ` (last checked ${ago(g.checked)})` : ""}`
        : "These feeds are from Xtream, which isn't checked automatically. Just pick one to try it.",
      act: () => { if (!checking && g.checkable) startCheck(); } },
  ];
  // empty / ended slots ("NO EVENT STREAMING", "ESPN+ 12") stay folded away unless asked for
  const real = g.feeds.filter((f) => !f.slot || f.status === "ok"), slots = g.feeds.filter((f) => f.slot && f.status !== "ok");
  const shown = real.length && !SP.showSlots ? real : g.feeds;
  const items = head.concat(shown.map((f) => feedItem({ ...f, kind: g.kind })));
  if (real.length && slots.length) {
    items.push({ label: SP.showSlots ? "Hide empty and ended slots" : `Show ${slots.length} empty and ended slots`,
                 act: () => { SP.showSlots = !SP.showSlots; renderSports(); } });
  }
  return items;
}
function renderSports(sel) {
  if (!SP.data) return;
  const counts = spCounts();
  $("sp-tabs").innerHTML = SP_TABS.map(([, label], i) =>
    `<div class="tab${i === SP.tab ? " sel" : ""}" data-t="${i}">${label} <small>${counts[i]}</small></div>`).join("");
  SP.items = spItems();
  if (sel === undefined) sel = Math.max(0, selIndex($("sp-list")));
  fillList($("sp-list"), SP.items, Math.min(sel, SP.items.length - 1));
  const li = $("sp-list").querySelector("li.sel"); if (li) li.scrollIntoView({ block: "nearest" });
  const g = SP.group;
  $("sp-status").textContent = g ? `${g.name} · ${g.named ? `${g.named} events · ` : ""}${g.count} feeds${g.live ? ` · ${g.live} live` : ""}` : "";
  $("sp-foot").innerHTML = g
    ? "<b>OK</b> Watch &nbsp; <b>Esc</b> Back to groups &nbsp; Event feeds only have video while the event is on."
    : "<b>◀ ▶</b> Tabs &nbsp; <b>OK</b> Watch / Record &nbsp; <b>Esc</b> Close";
}
async function openGroup(name) {
  try { SP.group = await api(`/api/sports/group?name=${encodeURIComponent(name)}`); } catch (e) { toast(e.message); return; }
  SP.checkState = null; SP.showSlots = false;
  renderSports(SP.group.feeds.length ? 2 : 0);
  // feeds checked more than 15 minutes ago (or never): look again, in the background
  if (SP.group.checkable && now() - (SP.group.checked || 0) > 900) startCheck();
}
async function startCheck() {
  const g = SP.group; if (!g) return;
  let st;
  try { st = await api("/api/sports/check", { method: "POST", body: { group: g.name } }); } catch (e) { toast(e.message); return; }
  if (!st.running) { toast("Nothing to check"); return; }
  SP.checkState = st; renderSports();
  clearInterval(SP.poll);
  SP.poll = setInterval(async () => {
    try { SP.checkState = await api("/api/sports/check"); } catch (_) { return; }
    if (!SP.group || SP.group.name !== g.name) { clearInterval(SP.poll); return; }
    if (!SP.checkState.running) {
      clearInterval(SP.poll); SP.poll = null;
      const keep = selIndex($("sp-list"));
      try { SP.group = await api(`/api/sports/group?name=${encodeURIComponent(g.name)}`); } catch (_) {}
      const gi = SP.data.groups.find((x) => x.name === g.name); if (gi) gi.live = SP.group.live;
      renderSports(keep);
      toast(SP.group.live ? `${SP.group.live} ${g.name} feed${SP.group.live === 1 ? " is" : "s are"} live now` : `Nothing live on ${g.name} right now`);
    } else if (top() === "sports") renderSports();
  }, 1500);
}
function sportsKey(k) {
  if (k === "Back") { if (SP.group) { SP.group = null; renderSports(0); } else closeSports(); return; }
  if (k === "t") { closeSports(); return; }
  if (k === "ArrowLeft" || k === "ArrowRight") {
    SP.tab = (SP.tab + (k === "ArrowRight" ? 1 : -1) + SP_TABS.length) % SP_TABS.length;
    SP.group = null; renderSports(0); return;
  }
  listNav($("sp-list"), k, (_, i) => { const it = SP.items[i]; if (it && it.act) it.act(); });
}
$("sp-tabs").addEventListener("click", (e) => {
  const t = e.target.closest(".tab"); if (!t) return;
  SP.tab = +t.dataset.t; SP.group = null; renderSports(0);
});
$("sp-list").addEventListener("click", (e) => {
  const li = e.target.closest("li"); if (!li) return;
  const it = SP.items[+li.dataset.i]; if (it && it.act) it.act();
});

