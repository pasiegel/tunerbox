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
function closePanels() { closeSearch(); closeSports(); closeHome(); }
// a guide listing from search/sports: on now -> watch, later -> record options
function pickProgram(p) {
  const i = S.byNum.get(p.ch.number);
  if (i === undefined) { toast(`Channel ${p.ch.number} is not available`); return; }
  const n0 = now();
  if (p.start <= n0 && n0 < p.stop) { closePanels(); tune(i); return; }
  programDialog(S.channels[i], p);
}
// A game from the Sports screen: every channel showing it (p.ch, p.also) and every event feed named
// after it (p.feeds). The guide can't tell whether a channel really has it (blackouts, regional
// feeds, a wrong guide), and most feeds are never checked, so with more than one choice: ask.
// `direct` (Home's tiles): no asking, play the best place; the others are in Menu › Switch feed.
const gameChans = (p) => (p.ch ? [p.ch, ...(p.also || [])] : []).filter((c) => S.byNum.has(c.number));
const gameFeeds = (p) => p.feeds || (p.feed ? [p.feed] : []);
const clip = (s, n) => (s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s);
function pickGame(p, direct = false) {
  const n0 = now(), feeds = gameFeeds(p);
  if (p.ch && !(p.start <= n0 && n0 < p.stop)) { pickProgram(p); return; }      // not on yet: record / remind
  const go = (fn) => () => { closeDialog(); closePanels(); fn(); };
  const chans = gameChans(p).map((c) => ({
    html: `<span class="badge ch">CH</span> <span>${c.number} ${esc(c.label || c.name)}</span>`,
    // no signal there: the first feed is tried by itself (failOver)
    act: go(() => { const i = S.byNum.get(c.number); tune(i); if (feeds[0]) S.gameFeed = { idx: i, feed: feeds[0], until: p.stop }; }) }));
  const fs = feeds.map((f) => ({ live: f.status === "ok", html: `${feedBadge(f)} <span>${esc(clip(f.name, 64))}</span>`,
                                 right: esc(f.group || ""), act: go(() => playFeed(f)) }));
  // feeds that were checked and are live, then the channels, then the feeds nobody has checked
  const opts = [...fs.filter((o) => o.live), ...chans, ...fs.filter((o) => !o.live)];
  if (!opts.length) { toast(`Channel ${p.ch.number} is not available`); return; }
  if (opts.length === 1 || direct) { opts[0].act(); return; }
  openDialog(p.event ? p.event.name : p.title, `${opts.length} places have this game. If one doesn't work, try the next.`, opts);
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
// SR.vodOnly: opened from On Demand, so it searches only the movies and shows
const SR = { q: "", zone: "kb", r: 0, c: 0, sel: 0, items: [], timer: null, seq: 0, vodOnly: false };
const sInput = $("s-input");
const finePointer = !!(window.matchMedia && matchMedia("(pointer: fine)").matches);

function openSearch(vodOnly = false) {
  hideBanner(); closeMenu(); closeSports();
  if (top() !== "search") S.layer.push("search");
  $("search").classList.remove("hidden");
  if (vodOnly !== SR.vodOnly) { SR.vodOnly = vodOnly; SR.items = []; SR.sel = 0; if (SR.q.trim()) runSearch(); }
  $("s-title").textContent = vodOnly ? "Search On Demand" : "Search";
  sInput.placeholder = vodOnly ? "Title, year or actor…" : "Channel, show, movie, team or event";
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
  if (!q) { SR.items = []; SR.vod = 0; renderSearch(); return; }
  $("s-count").textContent = "Searching…";
  let d;
  try {
    d = SR.vodOnly ? { channels: [], programs: [], feeds: [], vod: await api(`/api/vod/search?q=${encodeURIComponent(q)}`) }
                   : await api(`/api/search?q=${encodeURIComponent(q)}`);
  } catch (e) { $("s-count").textContent = e.message; return; }
  if (seq !== SR.seq) return;
  const n0 = now();
  SR.items = [
    ...d.channels.map((c) => ({ html: `<span class="badge ch">CH</span><b class="num">${c.number}</b> <span>${esc(c.name)}</span>`,
                                right: esc(c.now || ""), act: () => { closePanels(); tuneNum(c.number); } })),
    ...d.programs.map((p) => ({ html: progHtml(p, p.start <= n0 ? '<span class="badge live">ON NOW</span>' : `<span class="badge">${esc(fmtWhen(p.start))}</span>`),
                                right: `${p.ch.number} ${esc(p.ch.label || p.ch.name)}`, act: () => pickProgram(p) })),
    ...d.feeds.map(feedItem),
  ];
  SR.vod = (d.vod || []).length;
  SR.items.push(...(d.vod || []).map(vodSearchItem));
  SR.sel = Math.min(SR.sel, Math.max(0, SR.items.length - 1));
  renderSearch();
}
function renderSearch() {
  const q = SR.q.trim();
  $("s-count").textContent = q ? `${SR.items.length || "No"} result${SR.items.length === 1 ? "" : "s"}`
    + (SR.vod && SR.items.length && !SR.vodOnly ? ` · ${SR.items.length - SR.vod} on TV · ${SR.vod} On Demand` : "") : "";
  const entries = SR.items.length ? SR.items
    : [{ label: SR.vodOnly ? (q ? "Nothing found in On Demand. Try fewer letters." : "Type a title, a year or an actor.")
         : q ? "Nothing found. Try fewer letters, a team name or a channel number." : "Type a channel name or number, a show, a movie, a team or an event." }];
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

// ---------------------------------------------------------------- sports & events
// A list of sports down the left (Live now, Your teams, Coming up, each sport, Sports channels, Event
// feeds) and what's picked on the right. Live now: the first game big (a favorite team's first), the
// other games as score cards, other live sports, and what starts soon. A sport: its live or next event
// big, then the rest under a heading per day. Replays and shows about sport ("kind" from the server) stay
// folded away until asked for. An older server sends no "kind": then nothing is folded away.
const SP = { sec: "now", zone: "rail", ri: 0, mi: 0, data: null, errors: {}, group: null, rail: [], items: [],
             showRep: false, scores: {}, flash: new Set(), poll: null, seq: 0, showSlots: false };
const SP_LISTS = [["now", "on_now"], ["up", "upcoming"], ["ch", "channels"], ["ev", "groups"]];
const SP_GROUP = { MMA: "Fights", Boxing: "Fights" };          // sports the rail shows together
const spGroupOf = (p) => SP_GROUP[p.sport] || p.sport || "Other";
const spHidden = (p) => !SP.showRep && (p.kind === "replay" || p.kind === "show");
const spLive = (p) => p.is_live || p.kind === "game" || p.kind === "live";
const spFav = (p) => !!(p.event && p.event.fav && p.event.fav.length);
const spClose = (p) => !!(p.event && (p.event.close || p.event.ot));
const spCloseTag = (e) => (e && e.ot ? '<span class="sp-tag close">OVERTIME</span>' : e && e.close ? '<span class="sp-tag close">CLOSE GAME</span>' : "");
const spEndOfToday = () => { const d = new Date(); d.setHours(24, 0, 0, 0); return d.getTime() / 1000; };
const spPct = (p) => Math.max(0, Math.min(100, (now() - p.start) / ((p.stop - p.start) || 1) * 100));
const spIn = (t) => { const m = Math.max(0, Math.round((t - now()) / 60)); return m < 60 ? `in ${m} min` : `in ${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`; };

async function openSportsEnhanced() {
  hideBanner(); closeMenu(); closeSearch();
  if (top() !== "sports") S.layer.push("sports");
  $("sports").classList.remove("hidden");
  SP.group = null; SP.zone = "rail"; SP.mi = 0; SP.showRep = false; SP.flash.clear();
  const seq = ++SP.seq;
  SP.data = {}; SP.errors = {};
  renderSports();
  // the lists arrive one at a time (what's on now first); each one redraws the screen
  for (const [id, key] of SP_LISTS) {
    try { Object.assign(SP.data, await api(`/api/sports?tab=${id}`)); }
    catch (e) { if (seq === SP.seq) SP.errors[key] = e.message; }
    if (seq !== SP.seq) return;
    if (!(key in SP.data) && !SP.errors[key]) SP.errors[key] = "Couldn't load this list";
    renderSports();
  }
  clearInterval(SP.poll);
  SP.poll = setInterval(spRefresh, 60000);      // scores and what's live, every minute while it's open
}
async function spRefresh() {
  if (top() !== "sports") return;
  const seq = SP.seq;
  try {
    const d = await api("/api/sports?tab=now");
    if (seq !== SP.seq) return;
    Object.assign(SP.data, d);
    renderSports();
  } catch (_) {}
}
function closeSportsEnhanced() {
  SP.seq++;
  S.layer = S.layer.filter((l) => l !== "sports");
  $("sports").classList.add("hidden");
  clearInterval(SP.poll); SP.poll = null;
}
// Which layout the Sports & Events screen uses (Setup › Settings › Sports): "basic" (44-sports-basic.js,
// the default) or "enhanced" (this one). Everything else opens, closes and sends keys through these three.
let spLayout = "";
// Sports opens straight away in the layout already known, and asks TunerBox again meanwhile: a change
// made in Setup since swaps the screen for the other one as soon as the answer comes.
const spWanted = () => (S.settings && S.settings.sports_layout === "enhanced" ? "enhanced" : "basic");
function openSports() {
  const want = spWanted();
  if (spLayout && spLayout !== want) closeSports();
  spLayout = want;
  const opened = want === "enhanced" ? openSportsEnhanced() : openSportsBasic();
  api("/api/settings").then((st) => {
    S.settings = { ...S.settings, ...st };
    if (top() === "sports" && spLayout && spWanted() !== spLayout) { closeSports(); openSports(); }
  }).catch(() => {});
  return opened;
}
function closeSports() { closeSportsEnhanced(); closeSportsBasic(); spLayout = ""; }
function sportsKey(k) { return spLayout === "enhanced" ? sportsKeyEnhanced(k) : sportsKeyBasic(k); }

const ago = (t) => { const m = Math.round((now() - t) / 60); return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };

const SPORT_ICONS = {
  Football: "🏈", Basketball: "🏀", Baseball: "⚾", Softball: "🥎", Hockey: "🏒", Soccer: "⚽", Golf: "⛳",
  Tennis: "🎾", Motorsports: "🏁", MMA: "🥋", Boxing: "🥊", Wrestling: "🤼", Volleyball: "🏐", Lacrosse: "🥍",
  Rugby: "🏉", Cricket: "🏏", "Horse Racing": "🏇", Cycling: "🚴", "Track & Field": "🏃", Skiing: "⛷️",
  "Figure Skating": "⛸️", Gymnastics: "🤸", Swimming: "🏊", Bowling: "🎳", Darts: "🎯", Billiards: "🎱",
  Rodeo: "🤠", Curling: "🥌", Poker: "🃏", Fights: "🥊",
};
const sportEmoji = (p) => SPORT_ICONS[p.sport] || "";
const sportIcon = (p) => { const i = sportEmoji(p); return i ? `<span class="sp-ic" aria-hidden="true">${i}</span>` : ""; };
// what a game is headed with: the server's league heading and matchup (sports.heading). A phone has
// no room for both, so a scoreboard game is just its teams there (the icon still says the sport).
const spHeading = (p) => (isPhone() && p.event && p.matchup ? [p.matchup, ""] : [p.heading || p.title, p.matchup ?? p.subtitle ?? ""]);

// where a row is on, and what OK does with it
function spWhere(p, until = true) {
  const chans = gameChans(p).length, feeds = gameFeeds(p), more = chans + feeds.length - 1;
  if (p.ch) {
    const also = more > 1 ? ` + ${more} more` : feeds.length ? ` + ${feeds[0].kind === "ppv" ? "PPV" : "feed"}` : more > 0 ? " + 1 more" : "";
    return `${p.ch.number} ${p.ch.label || p.ch.name}${also}${until && p.start <= now() ? ` · until ${fmtTime(p.stop)}` : ""}`;
  }
  if (feeds.length) return (feeds[0].group || "Event feed") + (more > 0 ? ` + ${more} more` : "");
  const on = p.event && p.event.on.length ? `on ${p.event.on.join(", ")}` : "";
  return on ? `${on} · no channel` : "no channel";
}
function spAct(p) {
  const e = p.event, chans = gameChans(p).length, feeds = gameFeeds(p);
  if (p.ch) return () => (feeds.length || chans > 1 ? pickGame(p) : pickProgram(p));
  if (feeds.length) return () => pickGame(p);
  const on = e && e.on.length ? `on ${e.on.join(", ")}` : "";
  return () => toast(`${e ? e.name : p.title}: none of your channels or event feeds has it${on ? ` (it's ${on})` : ""}`, 4000);
}
const spBadge = (p, timeOnly) => {
  if (p.start > now()) return `<span class="badge${p.ch || p.feed ? "" : " off"}">${esc(timeOnly ? fmtTime(p.start) : fmtWhen(p.start))}</span>`;
  if (spLive(p)) return '<span class="badge live">LIVE</span>';
  if (p.kind === "replay") return '<span class="badge off">REPLAY</span>';
  if (p.kind === "show") return '<span class="badge show">SHOW</span>';
  return `<span class="badge${p.ch || p.feed ? "" : " off"}">${p.ch || p.feed ? "ON" : "SOON"}</span>`;
};

// ---- the left list
function spRail() {
  const d = SP.data, rows = [...(d.on_now || []), ...(d.upcoming || [])];
  const shown = rows.filter((p) => !spHidden(p));
  const rail = [{ key: "now", name: "Live now", icon: "📡", n: (d.on_now || []).filter((p) => !spHidden(p)).length,
                  live: (d.on_now || []).filter(spLive).length }];
  const favs = rows.filter((p) => p.event && p.event.fav && p.event.fav.length);
  if (favs.length) rail.push({ key: "fav", name: "Your teams", icon: "★", n: favs.length, live: favs.filter((p) => p.start <= now()).length });
  rail.push({ key: "up", name: "Coming up", icon: "⏱", n: (d.upcoming || []).filter((p) => !spHidden(p)).length }, { line: true });
  // each sport, counting what's on today; one with nothing today folds under "More sports"
  const by = new Map(), eod = spEndOfToday();
  for (const p of shown) {
    const g = spGroupOf(p);
    if (!by.has(g)) by.set(g, { key: `s:${g}`, name: g, icon: SPORT_ICONS[g] || "🏅", n: 0, live: 0, later: 0, next: 0 });
    const r = by.get(g);
    if (p.start < eod) r.n++; else { r.later++; if (!r.next || p.start < r.next) r.next = p.start; }
    if (p.start <= now() && spLive(p)) r.live++;
  }
  // live sports first, then the busiest; "Other" last
  const sports = [...by.values()].sort((a, b) => (a.name === "Other") - (b.name === "Other") || b.live - a.live || b.n - a.n || a.name.localeCompare(b.name));
  const folded = sports.filter((r) => !r.n && r.key !== SP.sec);
  SP.moreSports = folded;
  rail.push(...sports.filter((r) => !folded.includes(r)));
  if (folded.length) rail.push({ key: "more", name: "More sports", icon: "⋯", n: folded.length });
  rail.push({ line: true }, { key: "ch", name: "Sports channels", icon: "📺", n: (d.channels || []).length },
            { key: "ev", name: "Event feeds", icon: "🎟", n: (d.groups || []).length });
  return rail;
}

// ---- the right side: blocks of focusable things (class "f", data-i = their index in SP.items)
function spTeams(e) {
  const t = (e.teams || []).slice(0, 2);
  return t.length === 2 ? t : null;
}
function spLogo(t, big) {
  const style = t.color ? ` style="--tc:#${esc(t.color)}"` : "";
  // a logo goes on white (ESPN's are made for a light background: Oklahoma's crimson vanishes on crimson),
  // ringed in the team's colour; one that doesn't load leaves the colour tile and the initials
  return t.logo ? `<span class="sp-logo pic${big ? " big" : ""}"${style}><img alt="" loading="lazy" src="${esc(safePic(t.logo))}" onerror="this.parentNode.classList.remove('pic');this.remove()"><b>${esc(t.abbr || "")}</b></span>`
    : `<span class="sp-logo${big ? " big" : ""} txt"${style}><b>${esc(t.abbr || t.name || "")}</b></span>`;
}
function spHero(p, i) {
  const e = p.event, teams = e && spTeams(e), live = p.start <= now();
  const fav = e && e.fav && e.fav.length ? '<span class="sp-tag fav">★ YOUR TEAM</span>' : "";
  const tags = `${fav}${live && spLive(p) ? '<span class="sp-tag live">LIVE</span>' : live ? "" : '<span class="sp-tag soon">NEXT UP</span>'}${spCloseTag(e)}`;
  const flash = e && SP.flash.has(e.id) ? " flash" : "";
  const [head, sub] = spHeading(p);
  const where = esc(spWhere(p));
  const ok = live ? "OK Watch" : "OK Remind or record";
  const color = teams && teams[0].color ? ` style="--ta:#${esc(teams[0].color)}"` : "";
  let mid;
  if (teams) {
    const sc = (t) => (e.state !== "pre" && t.score !== "" ? esc(t.score) : "");
    const score = teams.every((t) => sc(t) !== "") ? `${sc(teams[0])}<i>–</i>${sc(teams[1])}` : `<i>${e.sport === "Soccer" || e.sport === "MMA" ? "vs" : "@"}</i>`;
    mid = `<div class="sp-hm">${spLogo(teams[0], true)}<span class="sp-hn">${esc(teams[0].name)}</span>`
      + `<span class="sp-hs">${score}</span><span class="sp-hn r">${esc(teams[1].name)}</span>${spLogo(teams[1], true)}</div>`;
  } else {
    mid = `<div class="sp-hm solo">${live ? "" : `<span class="sp-count"><small>STARTS</small><b>${esc(spIn(p.start).replace("in ", ""))}</b><small>${esc(fmtWhen(p.start))}</small></span>`}`
      + `<span class="sp-ht"><span>${sportIcon(p)}${esc(head)}</span>${sub ? `<small>${esc(sub)}</small>` : ""}</span></div>`;
  }
  const detail = e ? [e.heading || e.league_name, e.detail].filter(Boolean).join(" · ") : (p.sport || "");
  return `<div class="sp-hero f${flash}" data-i="${i}"${color}>`
    + `<div class="sp-htop">${tags}<span>${esc(teams ? detail : (p.sport || ""))}</span></div>`
    + mid
    + (live ? `<div class="sp-prog"><i style="width:${spPct(p).toFixed(0)}%"></i></div>` : "")
    + `<div class="sp-hfoot"><span>${where}</span><span class="sp-sp"></span><span class="sp-ok">${ok}</span></div></div>`;
}
function spCard(p, i) {
  const e = p.event, teams = spTeams(e) || [];
  const flash = SP.flash.has(e.id) ? " flash" : "";
  const row = (t) => `<div class="sp-cr">${spLogo(t)}<span>${esc(t.name)}</span><b>${e.state !== "pre" ? esc(t.score) : ""}</b></div>`;
  const color = teams[0] && teams[0].color ? ` style="--ta:#${esc(teams[0].color)}"` : "";
  return `<div class="sp-card f${flash}${e.close || e.ot ? " close" : ""}" data-i="${i}"${color}><div class="sp-ctop">${spLive(p) && p.start <= now() ? '<span class="sp-tag live">LIVE</span>' : ""}${e.ot ? '<span class="sp-tag close">OT</span>' : e.close ? '<span class="sp-tag close">CLOSE</span>' : ""}`
    + `<span>${esc(e.league)}</span><span class="sp-sp"></span><span class="sp-clk">${esc(e.detail || (p.start > now() ? fmtWhen(p.start) : ""))}</span></div>`
    + teams.map(row).join("")
    + (p.start <= now() ? `<div class="sp-prog"><i style="width:${spPct(p).toFixed(0)}%"></i></div>` : "")
    + `<div class="sp-cw">${esc(spWhere(p, false))}</div></div>`;
}
function spRow(p, i, timeOnly = false) {
  const [t, s] = spHeading(p), e = p.event;
  const fav = e && e.fav && e.fav.length ? '<span class="sp-fav">★</span>' : "";
  const score = e && [e.score, e.detail].filter(Boolean).join(" · ");
  return `<div class="sp-row f${e && SP.flash.has(e.id) ? " flash" : ""}${p.ch || gameFeeds(p).length ? "" : " nochan"}" data-i="${i}">${spBadge(p, timeOnly)}`
    + `<span class="sp-main"><span class="sp-t">${fav}${sportIcon(p)}${esc(t)}</span>${s ? `<small class="sp-s">${esc(s)}</small>` : ""}</span>`
    + (score ? `<small class="score">${esc(score)}</small>` : "") + `<span class="r">${esc(spWhere(p))}</span>`
    + (p.start <= now() && p.stop > now() ? `<span class="sp-rprog"><i style="width:${spPct(p).toFixed(0)}%"></i></span>` : "") + "</div>";
}
const spHead = (label, extra = "") => `<div class="sp-h">${esc(label)}${extra}</div>`;
// rows under a heading per day: "On now" for what's started, then Later today, Tomorrow, Thursday...
function spByDay(rows, add) {
  const today = new Date(), tmw = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1).toDateString();
  let html = "", head = "";
  for (const p of rows) {
    const d = new Date(p.start * 1000), ds = d.toDateString();
    const h = p.start <= now() ? "On now" : ds === today.toDateString() ? "Later today" : ds === tmw ? "Tomorrow"
      : d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
    if (h !== head) { head = h; html += spHead(h); }
    html += spRow(p, add(spAct(p)), true);             // the day is in the heading
  }
  return html;
}

// builds the right side; returns [html, items]
function spMain() {
  const d = SP.data, items = [], n0 = now();
  const add = (act) => { items.push({ act }); return items.length - 1; };
  const sec = SP.sec;
  const wait = (key) => (d[key] ? "" : `<div class="sp-msg">${esc(SP.errors[key] || "Loading…")}</div>`);
  const hiddenNote = (list) => {
    const n = list.filter((p) => p.kind === "replay" || p.kind === "show").length;
    if (!n) return "";
    const i = add(() => { SP.showRep = !SP.showRep; renderSports(); });
    return `<div class="sp-more f" data-i="${i}">${SP.showRep ? "Hide replays and shows" : `Show ${n} replay${n === 1 ? "" : "s"} and show${n === 1 ? "" : "s"}`}</div>`;
  };
  // The big one is the most interesting game: a favorite team's, then a close one late on, then the
  // first (the server's order). The others are score cards, close ones first: six at most, the sixth
  // place "+N more games" when there are more; an empty place in the last row takes a game starting soon.
  const heroCards = (rows, fill = []) => {
    const games = rows.filter((p) => p.event && spTeams(p.event));
    if (!games.length) return ["", rows, new Set()];
    const rank = (p) => (spFav(p) ? 0 : spClose(p) ? 1 : 2);
    const [first, ...rest] = games.map((p, i) => [p, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([p]) => p);
    let cards = rest.slice(0, 6), extra = [], more = [];
    if (rest.length > 6) { cards = rest.slice(0, 5); more = rest.slice(5); }
    else extra = fill.filter((p) => p.event && spTeams(p.event) && !games.includes(p)).slice(0, (3 - cards.length % 3) % 3);
    let html = spHero(first, add(spAct(first)));
    if (cards.length || more.length) {
      html += `<div class="sp-cards">${[...cards, ...extra].map((p) => spCard(p, add(spAct(p)))).join("")}`;
      if (more.length) {
        const count = new Map();
        for (const p of more) count.set(spGroupOf(p), (count.get(spGroupOf(p)) || 0) + 1);
        const g = [...count].sort((a, b) => b[1] - a[1])[0][0];
        const i = add(() => { SP.sec = `s:${g}`; SP.mi = 0; renderSports(); });
        html += `<div class="sp-card sp-mcard f" data-i="${i}"><b>+${more.length} more game${more.length === 1 ? "" : "s"}</b><span>See all ${esc(g)} ▸</span></div>`;
      }
      html += "</div>";
    }
    const used = new Set([first, ...cards, ...extra]);
    return [html, rows.filter((p) => !used.has(p)), used];
  };
  if (sec === "now" || sec === "fav") {
    const all = sec === "fav" ? [...(d.on_now || []), ...(d.upcoming || [])].filter((p) => p.event && p.event.fav && p.event.fav.length) : (d.on_now || []);
    const title = sec === "fav" ? "Your teams" : "Live now";
    const shown = all.filter((p) => !spHidden(p));
    const live = shown.filter((p) => p.start <= n0 && spLive(p)).length;
    let html = `<div class="sp-title"><h2>${title}</h2><span>${sec === "fav" ? `${shown.length} game${shown.length === 1 ? "" : "s"} on now or soon` : `${live} live · scores refresh every few minutes`}</span></div>` + wait("on_now");
    if (!d.on_now) return [html, items];
    const fill = sec === "now" ? (d.upcoming || []).filter((p) => !spHidden(p) && p.start < n0 + 3 * 3600) : [];
    const [top, others, used] = heroCards(shown, fill);
    // live first, then the rest ("Live:" in a title counts, not just the guide's flag); in order otherwise
    const rest = [...others.filter((p) => p.start <= n0 && spLive(p)), ...others.filter((p) => !(p.start <= n0 && spLive(p)))];
    html += top;
    // starting soon: your teams' games first, then by start; what the cards already show is left out
    const next = sec === "now" ? (d.upcoming || []).filter((p) => !spHidden(p) && p.start < n0 + 12 * 3600 && !used.has(p)) : [];
    const soon = [...next.filter(spFav), ...next.filter((p) => !spFav(p))].slice(0, 4);
    if (rest.length || soon.length || !top) {
      html += '<div class="sp-split"><div class="sp-col">';
      html += spHead(top ? (sec === "now" ? "Also on now" : "More") : (sec === "now" ? "On now" : "Games")) + (rest.length ? rest.map((p) => spRow(p, add(spAct(p)))).join("") : `<div class="sp-msg">${top ? "Nothing else on." : "No sports on right now."}</div>`);
      html += hiddenNote(all) + "</div>";
      if (soon.length) {
        html += `<div class="sp-col narrow">${spHead("Starting soon")}` + soon.map((p) => {
          const e = p.event, [t, sub] = spHeading(p), feeds = gameFeeds(p);
          const where = p.ch ? `${p.ch.number} ${p.ch.label || p.ch.name}` : feeds.length ? feeds[0].group || "Event feed" : "";
          const info = [e ? p.heading || e.league_name : sub, where].filter(Boolean).join(" · ");
          return `<div class="sp-soon f" data-i="${add(spAct(p))}"><b>${esc(spIn(p.start))}</b><span class="sp-st">`
            + `<span>${spFav(p) ? '<span class="sp-fav">★</span>' : ""}${sportIcon(p)}${esc(e ? e.name : t)}</span>${info ? `<small>${esc(info)}</small>` : ""}</span></div>`;
        }).join("") + "</div>";
      }
      html += "</div>";
    } else html += hiddenNote(all);
    return [html, items];
  }
  if (sec === "up") {
    let html = `<div class="sp-title"><h2>Coming up</h2><span>today and tomorrow</span></div>` + wait("upcoming");
    if (!d.upcoming) return [html, items];
    const rows = d.upcoming.filter((p) => !spHidden(p));
    html += spByDay(rows, add);
    if (!rows.length) html += '<div class="sp-msg">No upcoming sports.</div>';
    return [html + hiddenNote(d.upcoming), items];
  }
  if (sec.startsWith("s:")) {
    const g = sec.slice(2);
    const all = [...(d.on_now || []), ...(d.upcoming || [])].filter((p) => spGroupOf(p) === g);
    const shown = all.filter((p) => !spHidden(p));
    const live = shown.filter((p) => p.start <= n0 && spLive(p));
    let html = `<div class="sp-title"><h2>${sportIcon({ sport: g })}${esc(g)}</h2><span>${live.length} on now · ${shown.length - live.length} later</span></div>`;
    let rest = shown;
    const [top, , shownBig] = heroCards(live, shown.filter((p) => p.start > n0 && p.start < n0 + 3 * 3600));
    if (top) { html += top; rest = shown.filter((p) => !shownBig.has(p)); }
    else {
      // the next big thing: a live broadcast in the next day first (WWE NXT over a weekly magazine), else what's next
      const later = shown.filter((p) => p.start > n0 && p.kind !== "replay" && p.kind !== "show");
      const next = live[0] || later.find((p) => (p.kind === "live" || p.kind === "game") && p.start < n0 + 86400) || later[0] || shown[0];
      if (next) { html += spHero(next, add(spAct(next))); rest = shown.filter((p) => p !== next); }
    }
    html += rest.length ? spByDay(rest, add) : '<div class="sp-msg">Nothing else in the guide.</div>';
    return [html + hiddenNote(all), items];
  }
  if (sec === "more") {
    let html = `<div class="sp-title"><h2>More sports</h2><span>nothing on today; these are on later</span></div>`;
    for (const r of SP.moreSports || []) {
      const i = add(() => { SP.sec = r.key; SP.mi = 0; renderSports(); });
      html += `<div class="sp-row f" data-i="${i}"><span class="sp-main"><span class="sp-t"><span class="sp-ic" aria-hidden="true">${r.icon}</span>${esc(r.name)}</span></span>`
        + `<span class="r">${r.later} coming up${r.next ? ` · next ${esc(fmtWhen(r.next))}` : ""}</span></div>`;
    }
    return [html, items];
  }
  if (sec === "ch") {
    let html = `<div class="sp-title"><h2>Sports channels</h2><span>what each is showing now</span></div>` + wait("channels");
    for (const c of d.channels || []) {
      const i = add(() => { closePanels(); tuneNum(c.number); });
      html += `<div class="sp-row f" data-i="${i}"><b class="num">${c.number}</b><span class="sp-main"><span class="sp-t">${esc(c.name)}</span></span>`
        + `<span class="r">${c.now ? (c.now.is_live ? "LIVE · " : "") + esc(c.now.title) : ""}</span></div>`;
    }
    if (d.channels && !d.channels.length) html += '<div class="sp-msg">No sports channels found.</div>';
    return [html, items];
  }
  // event feeds: the groups, or one group's feeds
  if (!SP.group) {
    let html = `<div class="sp-title"><h2>Event feeds</h2><span>PPV and event groups in your playlists</span></div>` + wait("groups");
    for (const g of d.groups || []) {
      const i = add(() => openGroup(g.name));
      html += `<div class="sp-row f" data-i="${i}"><span class="badge ${g.kind}">${g.kind === "ppv" ? "PPV" : "EVENTS"}</span><span class="sp-main"><span class="sp-t">${esc(g.name)}</span></span>`
        + `<span class="r">${g.live ? `<b class="live-n">${g.live} live</b> · ` : ""}${g.named ? `${g.named} event${g.named === 1 ? "" : "s"}` : `${g.count} feeds`}</span></div>`;
    }
    if (d.groups && !d.groups.length) html += '<div class="sp-msg">None of your playlists have event or PPV groups (like ESPN+, PPV or NFL Game Pass).</div>';
    return [html, items];
  }
  const g = SP.group, st = SP.checkState, checking = st && st.running;
  let html = `<div class="sp-title"><h2>${esc(g.name)}</h2><span>${g.named ? `${g.named} events · ` : ""}${g.count} feeds${g.live ? ` · ${g.live} live` : ""}</span></div>`;
  html += `<div class="sp-row f" data-i="${add(() => { SP.group = null; SP.mi = 0; renderSports(); })}"><span class="sp-main"><span class="sp-t">◀  All event groups</span></span></div>`;
  html += `<div class="sp-row f" data-i="${add(() => { if (!checking && g.checkable) startCheck(); })}"><span class="sp-main"><span class="sp-t">${esc(checking ? `Checking which feeds are live… ${st.done}/${st.total}`
    : g.checkable ? `⟳  Check which feeds are live now${g.checked ? ` (last checked ${ago(g.checked)})` : ""}`
    : "These feeds are from Xtream, which isn't checked automatically. Just pick one to try it.")}</span></span></div>`;
  const real = g.feeds.filter((f) => !f.slot || f.status === "ok"), slots = g.feeds.filter((f) => f.slot && f.status !== "ok");
  for (const f of real.length && !SP.showSlots ? real : g.feeds) {
    const it = feedItem({ ...f, kind: g.kind });
    html += `<div class="sp-row f" data-i="${add(it.act)}">${it.html}<span class="r">${it.right}</span></div>`;
  }
  if (real.length && slots.length) {
    html += `<div class="sp-more f" data-i="${add(() => { SP.showSlots = !SP.showSlots; renderSports(); })}">${SP.showSlots ? "Hide empty and ended slots" : `Show ${slots.length} empty and ended slots`}</div>`;
  }
  return [html, items];
}
function renderSports() {
  if (!SP.data) return;
  // a score that changed since the last draw flashes once
  SP.flash.clear();
  for (const p of [...(SP.data.on_now || [])]) {
    const e = p.event; if (!e) continue;
    if (SP.scores[e.id] !== undefined && SP.scores[e.id] !== e.score && e.score) SP.flash.add(e.id);
    SP.scores[e.id] = e.score;
  }
  SP.rail = spRail();
  let ri = SP.rail.findIndex((r) => r.key === SP.sec);
  if (ri < 0) { ri = 0; SP.sec = SP.rail[0].key; }
  SP.ri = ri;
  $("sp-rail").innerHTML = SP.rail.map((r, i) => (r.line ? '<div class="sp-line"></div>'
    : `<div class="sp-ri${i === ri ? " sel" : ""}${i === ri && SP.zone === "rail" ? " focus" : ""}" data-r="${i}"><span class="sp-ico" aria-hidden="true">${r.icon}</span>`
      + `<span class="sp-rn">${esc(r.name)}</span>${r.live ? `<span class="sp-rl">${r.live}</span>` : ""}<span class="sp-rc">${r.n || ""}</span></div>`)).join("");
  const [html, items] = spMain();
  SP.items = items;
  $("sp-main").innerHTML = html;
  if (SP.mi >= items.length) SP.mi = Math.max(0, items.length - 1);
  if (SP.zone === "main" && !items.length) SP.zone = "rail";
  spFocus();
  $("sp-foot").innerHTML = SP.zone === "rail" ? "<b>▲ ▼</b> Pick &nbsp; <b>▶</b> Go in &nbsp; <b>Esc</b> Close"
    : "<b>▲ ▼</b> Move &nbsp; <b>◀</b> Back to the list &nbsp; <b>OK</b> Watch / Remind / Record &nbsp; <b>Esc</b> Close";
}
function spFocus() {
  for (const el of $("sp-main").querySelectorAll(".f.sel")) el.classList.remove("sel");
  const ri = $("sp-rail").querySelector(".sp-ri.sel");
  if (ri) { ri.classList.toggle("focus", SP.zone === "rail"); ri.scrollIntoView({ block: "nearest" }); }
  if (SP.zone !== "main") { $("sp-main").scrollTop = 0; return; }
  const el = $("sp-main").querySelector(`.f[data-i="${SP.mi}"]`);
  if (el) { el.classList.add("sel"); el.scrollIntoView({ block: "nearest" }); }
}
function spPick(ri) {
  const r = SP.rail[ri]; if (!r || r.line) return;
  SP.sec = r.key; SP.group = null; SP.mi = 0; renderSports();
}
async function openGroup(name) {
  try { SP.group = await api(`/api/sports/group?name=${encodeURIComponent(name)}`); } catch (e) { toast(e.message); return; }
  SP.checkState = null; SP.showSlots = false;
  SP.mi = SP.group.feeds.length ? 2 : 0; renderSports();
  // feeds checked more than 15 minutes ago (or never): look again, in the background
  if (SP.group.checkable && now() - (SP.group.checked || 0) > 900) startCheck();
}
async function startCheck() {
  const g = SP.group; if (!g) return;
  let st;
  try { st = await api("/api/sports/check", { method: "POST", body: { group: g.name } }); } catch (e) { toast(e.message); return; }
  if (!st.running) { toast("Nothing to check"); return; }
  SP.checkState = st; renderSports();
  clearInterval(SP.checkPoll);
  SP.checkPoll = setInterval(async () => {
    try { SP.checkState = await api("/api/sports/check"); } catch (_) { return; }
    if (!SP.group || SP.group.name !== g.name) { clearInterval(SP.checkPoll); return; }
    if (!SP.checkState.running) {
      clearInterval(SP.checkPoll); SP.checkPoll = null;
      try { SP.group = await api(`/api/sports/group?name=${encodeURIComponent(g.name)}`); } catch (_) {}
      const gi = (SP.data.groups || []).find((x) => x.name === g.name); if (gi) gi.live = SP.group.live;
      renderSports();
      toast(SP.group.live ? `${SP.group.live} ${g.name} feed${SP.group.live === 1 ? " is" : "s are"} live now` : `Nothing live on ${g.name} right now`);
    } else if (top() === "sports") renderSports();
  }, 1500);
}
function sportsKeyEnhanced(k) {
  if (k === "Back") {
    if (SP.group) { SP.group = null; SP.mi = 0; renderSports(); }
    else if (SP.zone === "main") { SP.zone = "rail"; renderSports(); }
    else closeSports();
    return;
  }
  if (k === "t") { closeSports(); return; }
  if (SP.zone === "rail") {
    const step = k === "ArrowDown" ? 1 : k === "ArrowUp" ? -1 : 0;
    if (step) {
      let i = SP.ri;
      do i = (i + step + SP.rail.length) % SP.rail.length; while (SP.rail[i].line);
      spPick(i); return;
    }
    if ((k === "ArrowRight" || k === "Enter") && SP.items.length) { SP.zone = "main"; SP.mi = 0; renderSports(); }
    return;
  }
  const n = SP.items.length;
  if (k === "ArrowLeft") { SP.zone = "rail"; renderSports(); return; }
  if (k === "ArrowDown" || k === "ArrowRight") { SP.mi = Math.min(n - 1, SP.mi + 1); spFocus(); return; }
  if (k === "ArrowUp") { if (SP.mi === 0) { SP.zone = "rail"; renderSports(); } else { SP.mi--; spFocus(); } return; }
  if (k === "ChannelDown") { SP.mi = Math.min(n - 1, SP.mi + 6); spFocus(); return; }
  if (k === "ChannelUp") { SP.mi = Math.max(0, SP.mi - 6); spFocus(); return; }
  if (k === "Enter") { const it = SP.items[SP.mi]; if (it && it.act) it.act(); }
}
$("sp-rail").addEventListener("click", (e) => {
  const r = e.target.closest(".sp-ri"); if (!r) return;
  SP.zone = "rail"; spPick(+r.dataset.r);
});
$("sp-main").addEventListener("click", (e) => {
  const f = e.target.closest(".f"); if (!f) return;
  SP.zone = "main"; SP.mi = +f.dataset.i; spFocus();
  const it = SP.items[SP.mi]; if (it && it.act) it.act();
});
