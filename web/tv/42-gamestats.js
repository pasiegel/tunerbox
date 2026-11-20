// ---------------------------------------------------------------- game stats
// X while watching a game: a card in the top right corner over the picture with the box score, team
// stats and the latest plays, for the game the scoreboard matched to this channel or event feed. On the
// Live scores tab it becomes a slim panel down the right with a tile per game, like Home's. The picture
// keeps playing full size behind it (on a phone the panel is a sheet under it); channel and volume keys
// still work, and the panel follows the new channel.
const GS = { tab: 0, data: null, others: [], sel: 0, key: "", t: 0, timer: null };
const GS_REFRESH = 20000;

// where the panel sits (not on a phone, where it's a sheet):
//   "r" / "l": snapped to the right or left side, at the top, as tall as what the tab shows (a short Team
//              stats card, a long Scores list), up to the height of the screen
//   "br" / "bl": a bottom corner
//   {x, y}: where it was dragged, as fractions of the room it has to move in, so it stays on screen
// and its size if it's been resized: on a side the width, and a height it's at least (sh: taller than a
// short tab needs, but a longer one still grows it); anywhere else the width and height (h). Both remembered
// for this screen.
const GS_SPOTS = ["r", "br", "bl", "l"];         // what Move (and OK on the remote) steps through
const GS_SNAP = 40;                              // dragged this close to a side (px), it snaps there
GS.pos = "r"; GS.size = null; GS.drag = null;
try {
  let p = localStorage.getItem("tb.gameStatsPos") || "r";
  p = { tr: "r", tl: "l" }[p] || p;              // the top corners became the sides
  if (GS_SPOTS.includes(p)) GS.pos = p;
  else { const [x, y] = p.split(",").map(Number); if (x >= 0 && x <= 1 && y >= 0 && y <= 1) GS.pos = { x, y }; }
  const [w, h, sh] = (localStorage.getItem("tb.gameStatsSize") || "").split(",").map(Number);
  if (w > 0) GS.size = { w, h: h > 0 ? h : 0, sh: sh > 0 ? sh : 0 };
} catch (_) { /* private window */ }
const gsSide = () => GS.pos === "l" || GS.pos === "r";
const GS_SCALE = [0.9, 1.6];                     // how much the text may shrink or grow with the panel's size
const GS_MIN_W = 19;                             // the narrowest the handle makes it (em): the score line still reads
const GS_MIN_H = 15;                             // and the shortest: a few rows of stats under the score and tabs
const GS_WIDE_EM = 48;                           // this wide (in the text size it comes with): two columns
GS.wide = false;
// switching between one tab per team and one tab for both keeps you on the same thing
function gsSetWide(wide) {
  const before = (gsTabs()[GS.tab] || {}).label;
  GS.wide = wide;
  if (!GS.data) return;
  const tabs = gsTabs(), abbrs = GS.data.players.map((p) => p.abbr);
  let i = tabs.findIndex((t) => t.label === before);
  if (i < 0 && (before === "Players" || abbrs.includes(before))) i = tabs.findIndex((t) => t.both || t.team);
  GS.tab = Math.max(0, i);
  gsRender();
}
function gsSave() {
  try {
    localStorage.setItem("tb.gameStatsPos", typeof GS.pos === "string" ? GS.pos : `${GS.pos.x.toFixed(3)},${GS.pos.y.toFixed(3)}`);
    if (GS.size) localStorage.setItem("tb.gameStatsSize", [GS.size.w, GS.size.h, GS.size.sh || 0].map(Math.round).join(","));
    else localStorage.removeItem("tb.gameStatsSize");
  } catch (_) {}
}
function gsPlace() {
  const b = $("gstats"), s = b.style, p = GS.pos, z = GS.size, room = b.parentElement, side = gsSide();
  const fixedH = !!z && !side && z.h > 0, minH = !!z && side && z.sh > 0;
  s.left = s.top = s.right = s.bottom = s.width = s.height = s.minHeight = s.fontSize = "";
  b.classList.toggle("sized", (fixedH || minH) && !isPhone());
  b.classList.toggle("side", side && !isPhone());
  if (isPhone()) return;
  if (z) s.width = `${Math.min(z.w, room.clientWidth)}px`;
  if (fixedH) s.height = `${Math.min(z.h, room.clientHeight)}px`;
  if (minH) s.minHeight = `${Math.round(z.sh)}px`;          // the CSS max-height still keeps it on screen
  // made bigger, the text grows with it, up to GS_SCALE. Twice as wide as it comes: two columns (both teams'
  // players side by side, the plays in columns, the scores in a grid), each column's text sized like a panel
  // of its own. A short strip along the bottom keeps the text small enough to show a few rows.
  let wide = false;
  if (z) {
    const base = parseFloat(getComputedStyle(room).fontSize) * 0.92;      // the panel's text size as it comes
    const w = Math.min(z.w, room.clientWidth);
    wide = w >= GS_WIDE_EM * base;
    let scale = w / (wide ? 2 : 1) / (24 * base);
    if (fixedH) scale = Math.min(scale, z.h / (16 * base));
    scale = Math.max(GS_SCALE[0], Math.min(GS_SCALE[1], scale));
    if (Math.abs(scale - 1) > 0.02) s.fontSize = `${(0.92 * scale).toFixed(3)}em`;
  }
  b.classList.toggle("wide", wide);
  if (wide !== GS.wide) gsSetWide(wide);         // re-drawn with the other layout (which then places it again)
  if (typeof p === "object") {
    s.right = "auto";
    s.left = `${Math.round(p.x * Math.max(0, room.clientWidth - b.offsetWidth))}px`;
    s.top = `${Math.round(p.y * Math.max(0, room.clientHeight - b.offsetHeight))}px`;
  } else {
    if (p[0] === "b") { s.top = "auto"; s.bottom = "1.6em"; }
    if (p.endsWith("l")) { s.right = "auto"; s.left = "1.6em"; }
  }
  gsFit();
  // the resize handle goes on the corner facing the middle of the screen, so it grows into the open
  // (on a side: the bottom corner away from the edge)
  if (GS.drag && GS.drag.resize) return;
  const r = b.getBoundingClientRect(), m = room.getBoundingClientRect();
  b.classList.toggle("rz-t", !side && r.top + r.height / 2 > m.top + m.height / 2);
  b.classList.toggle("rz-l", side ? p === "r" : r.left + r.width / 2 > m.left + m.width / 2);
}
// Move (and OK on the remote): right side › bottom right › bottom left › left side
function gsNextCorner() {
  const i = typeof GS.pos === "string" ? GS_SPOTS.indexOf(GS.pos) : -1;
  gsSnap(GS_SPOTS[(i + 1) % GS_SPOTS.length]);
}
// snapping to a side starts it at the usual size again, as tall as what the tab shows (resize it after if you like)
function gsSnap(spot) {
  GS.pos = spot;
  if (gsSide()) GS.size = null;
  gsSave(); gsPlace();
}

const gsKey = (ch) => (ch ? (ch.feed ? `f:${ch.feed}` : `c:${ch.number}`) : "");
function gsTabs() {
  const d = GS.data;
  // no game the scoreboard knows on this channel: just the live games, if there are any
  if (!d) return GS.others.length ? [{ label: "Scores", scores: true }] : [];
  // one tab per team's players, so the list stays short, then the team stats, the plays, and every
  // other game on now in the leagues you follow
  // wide (made bigger): both teams' players side by side on one tab
  const players = GS.wide && d.players.length === 2 ? [{ label: "Players", both: true }]
    : d.players.map((p) => ({ label: p.abbr, team: p }));
  return [...players,
          d.team_stats.length && { label: "Team" }, d.plays.length && { label: "Plays" },
          GS.others.length && { label: "Scores", scores: true }].filter(Boolean);
}

function openStats() {
  const ch = S.mode === "live" && curCh();
  if (!ch) { toast("Tune to a game to see its stats"); return; }
  hideBanner(); clearTimeout(S.bannerTimer);
  if (top() !== "stats") S.layer.push("stats");
  $("gstats").classList.remove("hidden"); $("tv").classList.add("gs-open");
  GS.tab = 0; GS.data = null; GS.key = ""; gsRender("Loading…");
  gsLoad();
  clearInterval(GS.timer);
  // a cheap check every 2 s: a new channel (number keys, channel up/down) loads at once, otherwise every 20 s
  GS.timer = setInterval(() => {
    if (S.mode !== "live") { closeStats(); return; }
    if (gsKey(curCh()) !== GS.key || Date.now() - GS.t > GS_REFRESH) gsLoad(); }, 2000);
}
function closeStats() {
  clearInterval(GS.timer); GS.timer = null;
  S.layer = S.layer.filter((l) => l !== "stats");
  $("gstats").classList.add("hidden"); $("tv").classList.remove("gs-open");
}

async function gsLoad() {
  const ch = curCh(), key = gsKey(ch);
  if (!ch) return;
  const moved = key !== GS.key;
  GS.key = key; GS.t = Date.now();
  if (moved) { GS.data = null; GS.others = []; GS.tab = 0; GS.sel = 0; gsRender("Loading…"); }
  let d;
  try { d = await api(`/api/sports/stats?${ch.feed ? `feed=${encodeURIComponent(ch.feed)}` : `ch=${encodeURIComponent(ch.number)}`}`); }
  catch (e) { if (!GS.data) gsRender(`Couldn't get the stats: ${e.message}`); return; }
  if (gsKey(curCh()) !== key || $("gstats").classList.contains("hidden")) return;
  GS.data = d.game; GS.others = d.others || [];
  GS.sel = Math.min(GS.sel, Math.max(0, GS.others.length - 1));
  if (d.enabled === false) { gsRender("Sports scores are off. To turn them on, go to Setup › Settings › Sports and pick the leagues (or teams) you follow."); return; }
  if (!d.game && !GS.others.length) { gsRender("No games are live right now."); return; }
  GS.tab = Math.min(GS.tab, Math.max(0, gsTabs().length - 1));
  gsRender();
}

// made-attempted columns ("10-21", "22/31") get a wider slot so they stay on one line
const GS_WIDE = new Set(["FG", "3PT", "FT", "C/ATT", "H-AB"]);
function gsTable(cols, rows, cls = "") {
  return `<table class="gs-tbl ${cls}"><thead><tr>${cols.map((c) => `<th${GS_WIDE.has(c) ? ' class="w"' : ""}>${esc(c)}</th>`).join("")}</tr></thead>`
    + `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

// one other game on now, drawn like Home's tiles (hmMatchup: the two logos on their colours, split down
// the middle, the score and clock between them), with the matchup, league and where to watch underneath
function gsGame(o, i) {
  const n = (o.ch ? 1 + o.also.length : 0) + o.feeds.length;
  const where = !n ? "Not on your channels" : n > 1 ? `${n} places to watch` : o.ch ? `Ch ${o.ch.number}` : (o.feeds[0].group || "Event feed");
  // soccer lists the home side first, so "vs" rather than "at"
  const name = o.name || o.teams.map((x) => x.name || x.abbr).join(o.sport === "Soccer" ? " vs " : " at ");
  return `<div class="gs-game${i === GS.sel ? " sel" : ""}" data-i="${i}">${hmMatchup(o, null, true)}`
    + `<div class="gs-gx"><b>${esc(name)}</b><span><em>${esc(o.league)}</em> · ${esc(where)}</span></div></div>`;
}

// keep the tabs and the key hints on one line each: the tabs closer together and smaller a step at a time,
// then the hints least needed left out, until they fit
function gsFit() {
  const tabs = $("gs-tabs"), keys = $("gs-keys");
  tabs.classList.remove("tight", "tighter");
  if (tabs.scrollWidth > tabs.clientWidth + 1) tabs.classList.add("tight");
  if (tabs.scrollWidth > tabs.clientWidth + 1) tabs.classList.add("tighter");
  const spans = [...keys.children].sort((a, b) => b.dataset.drop - a.dataset.drop);
  spans.forEach((el) => { el.hidden = false; });
  for (const el of spans) {
    if (keys.scrollWidth <= keys.clientWidth + 1 || el.dataset.drop === "0") break;
    el.hidden = true;
  }
}

// one team's player tables
function gsTeamTables(team) {
  let html = "";
  for (const t of team.tables) {
    const head = [t.title || "Player", ...t.cols];
    html += gsTable(head, t.rows.map((r) => [`${r.num ? `<small>#${esc(r.num)}</small>` : ""}${esc(r.name)}`, ...r.stats.map(esc)]));
  }
  return html;
}

function gsRender(msg) {
  const d = GS.data, body = $("gs-body"), scroll = body.scrollTop;
  const scoresOnly = !d && GS.others.length && msg === undefined;
  const tabs = gsTabs(), tab = tabs[GS.tab];
  // the Live scores tab (or nothing but live scores) uses the tall side panel, everything else the corner card
  const onScores = msg === undefined && !!(tab && tab.scores);
  $("gstats").classList.toggle("scores", onScores);
  // the key hints, each with how readily it's left out when they don't all fit (gsFit): Move has its button
  const hint = (keys, what, drop) => `<span data-drop="${drop}"><b>${keys}</b> ${what}</span>`;
  $("gs-keys").innerHTML = (onScores
    ? [hint("&#9664; &#9654;", "Tabs", 2), hint("&#9650; &#9660;", "Pick", 3), hint("OK", "Watch", 1), hint("Esc", "Close", 0)]
    : [hint("&#9664; &#9654;", "Tabs", 2), hint("&#9650; &#9660;", "Scroll", 3), hint("OK", "Move", 4), hint("Esc", "Close", 0)]).join("");
  $("gs-league").textContent = d ? (d.state === "in" ? `${d.league} · LIVE` : d.league) : "";
  $("gs-clock").textContent = d ? d.detail : "";
  $("gs-upd").textContent = "";
  if (!d && !scoresOnly) {
    $("gs-teams").innerHTML = ""; $("gs-tabs").innerHTML = "";
    body.innerHTML = `<div class="gs-empty">${esc(msg || "")}</div>`;
    gsPlace();
    return;
  }
  const hi = d ? Math.max(...d.teams.map((t) => +t.score || 0)) : 0;
  // no game on this channel: no header, just the Live scores tab. Otherwise one line: logo, short
  // name and score for each side, mirrored either side of the dash (the leader brighter)
  // the record goes under the name; a logo that won't load is dropped (the name is beside it)
  $("gs-teams").innerHTML = !d ? "" : d.teams.map((t, i) => `<div class="gs-team ${i ? "r" : "l"}${t.score !== "" && +t.score === hi ? " lead" : ""}" title="${esc(t.name)}">`
    + `<i style="${/^[0-9a-f]{6}$/i.test(t.color || "") ? `background:#${t.color}` : ""}"></i>`
    + (t.logo ? `<img src="${esc(safePic(t.logo))}" alt="" onerror="this.remove()">` : "")
    + `<div><b>${esc(t.short || t.abbr || t.name)}</b>${t.record ? `<small>${esc(t.record)}</small>` : ""}</div><em>${esc(t.score)}</em></div>`).join(`<span class="gs-dash">–</span>`);
  $("gs-tabs").innerHTML = tabs.map((t, i) => `<div class="tab${i === GS.tab ? " sel" : ""}" data-i="${i}">${esc(t.label)}</div>`).join("");
  let html = "";
  if (!tab) html = `<div class="gs-empty">${d && d.state === "pre" ? "Stats start when the game does." : "No stats for this game yet."}</div>`;
  else if (tab.both) {
    html = `<div class="gs-cols">${d.players.map((p) => `<div><div class="gs-colh">${esc(p.abbr)}</div>${gsTeamTables(p)}</div>`).join("")}</div>`;
  } else if (tab.team) {
    html = gsTeamTables(tab.team);
  } else if (tab.scores) {
    html = GS.others.map(gsGame).join("");
  } else if (tab.label === "Team") {
    html = gsTable(["", ...d.teams.map((t) => t.abbr)], d.team_stats.map((s) => [esc(s.label), ...s.values.map(esc)]), "team");
  } else {
    html = d.plays.map((p) => `<div class="gs-play${p.score ? " score" : ""}"><small>${esc(p.when)}</small>${esc(p.text)}</div>`).join("");
  }
  body.innerHTML = html;
  gsFit();
  body.dataset.tab = !tab ? "" : tab.scores ? "scores" : tab.both ? "both" : tab.team ? "team" : tab.label === "Plays" ? "plays" : "stats";
  body.scrollTop = msg === undefined ? scroll : 0;
  if (tab && tab.scores) gsShowSel();
  if (d && d.updated) { $("gs-upd").textContent = fmtTime(d.updated); $("gs-upd").title = `Updated ${fmtTime(d.updated)}`; }
  if (!GS.drag) gsPlace();
}

const gsOnScores = () => { const t = gsTabs()[GS.tab]; return !!(t && t.scores); };
function gsShowSel() {
  const el = $("gs-body").querySelector(".gs-game.sel");
  if (el) el.scrollIntoView({ block: "nearest" });
}
function gsPick(step) {
  const n = GS.others.length;
  if (!n) return;
  GS.sel = Math.max(0, Math.min(n - 1, GS.sel + step));
  $("gs-body").querySelectorAll(".gs-game").forEach((el, i) => el.classList.toggle("sel", i === GS.sel));
  gsShowSel();
}
// watch one of the other games the way the Sports screen does: one place goes straight there, more
// than one asks which channel or event feed. The panel stays open and follows to the new game.
function gsWatch(o) {
  if (!o) return;
  if (!(o.ch && S.byNum.has(o.ch.number)) && !o.feeds.length) { toast("None of your channels or event feeds has this game"); return; }
  pickGame({ ...o, event: { name: o.name } });
}

function gsSwitch(step) {
  const n = gsTabs().length;
  if (n < 2) return;
  GS.tab = (GS.tab + step + n) % n;
  gsRender(); $("gs-body").scrollTop = 0;
}

function statsKey(k) {
  switch (k) {
    case "Back": case "x": closeStats(); break;
    case "ArrowLeft": gsSwitch(-1); break;
    case "ArrowRight": gsSwitch(1); break;
    case "ArrowUp": if (gsOnScores()) gsPick(-1); else $("gs-body").scrollBy({ top: -120, behavior: "smooth" }); break;
    case "ArrowDown": if (gsOnScores()) gsPick(1); else $("gs-body").scrollBy({ top: 120, behavior: "smooth" }); break;
    case "Enter": if (gsOnScores()) gsWatch(GS.others[GS.sel]); else if (!isPhone()) gsNextCorner(); break;
    case "ChannelUp": chStep(1); break;
    case "ChannelDown": chStep(-1); break;
    case "u": toggleMute(); break;
    case "VolUp": setVolume(video.volume + 0.05); break;
    case "VolDown": setVolume(video.volume - 0.05); break;
    case "l": if (S.lastIdx >= 0) tune(S.lastIdx); break;
    case "f": toggleFull(); break;
    default: closeStats(); return handleKey(k);   // Guide, Menu, Home…: put the panel away and go there
  }
  return true;
}
$("gs-tabs").addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if (t) { GS.tab = +t.dataset.i; gsRender(); $("gs-body").scrollTop = 0; }
});
$("gs-body").addEventListener("click", (e) => {
  const g = e.target.closest(".gs-game");
  if (g) { GS.sel = +g.dataset.i; gsWatch(GS.others[GS.sel]); }
});

// drag by the top line, the score or the key hints (the tabs, tiles and stats stay clickable and scrollable)
$("gstats").addEventListener("pointerdown", (e) => {
  if (isPhone()) return;
  if (e.target.closest("#gs-move")) { e.preventDefault(); e.stopPropagation(); gsNextCorner(); return; }
  const snap = e.target.closest(".gs-snap");
  if (snap) { e.preventDefault(); e.stopPropagation(); gsSnap(snap.dataset.snap); return; }
  const resize = !!e.target.closest("#gs-resize");
  // a double-click on the handle (pointer capture keeps a real dblclick from reaching it): back to the
  // usual size, as tall as what's in it, where it is now
  if (resize && e.timeStamp - (GS.rzDown || -1e9) < 400) { GS.rzDown = 0; e.preventDefault(); e.stopPropagation(); GS.size = null; gsSave(); gsPlace(); return; }
  if (resize) GS.rzDown = e.timeStamp;
  if (!resize && !e.target.closest(".gs-head, #gs-teams, .gs-foot")) return;
  const b = $("gstats"), r = b.getBoundingClientRect(), room = b.parentElement.getBoundingClientRect();
  const t = b.classList.contains("rz-t"), l = b.classList.contains("rz-l");
  // leaving a side: a resized panel keeps the height it had there
  if (!resize && gsSide() && GS.size) GS.size.h = b.offsetHeight;
  // resizing keeps the corner opposite the handle where it is
  GS.drag = { id: e.pointerId, dx: e.clientX - r.left, dy: e.clientY - r.top, room, moved: false, x0: e.clientX, y0: e.clientY,
              resize, t, l, ax: l ? r.right : r.left, ay: t ? r.bottom : r.top, em: parseFloat(getComputedStyle(b).fontSize) || 16 };
  b.setPointerCapture(e.pointerId);
  e.preventDefault(); e.stopPropagation();
});
$("gstats").addEventListener("pointermove", (e) => {
  const d = GS.drag, b = $("gstats");
  if (!d || d.id !== e.pointerId) return;
  if (!d.moved && Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < 4) return;
  if (!d.moved) { d.moved = true; b.classList.add(d.resize ? "resizing" : "drag"); }
  if (d.resize) {
    const R = d.room, cx = Math.min(R.right, Math.max(R.left, e.clientX)), cy = Math.min(R.bottom, Math.max(R.top, e.clientY));
    // no smaller than the tables need, no bigger than the screen (less the usual margin top and bottom)
    const maxW = Math.min(R.width, innerWidth * 0.92), maxH = R.height - 3.2 * d.em;
    const nw = Math.min(maxW, Math.max(GS_MIN_W * d.em, Math.abs(cx - d.ax))), nh = Math.min(maxH, Math.max(GS_MIN_H * d.em, Math.abs(cy - d.ay)));
    const left = Math.min(R.width - nw, Math.max(0, (d.l ? d.ax - nw : d.ax) - R.left));
    const top = Math.min(R.height - nh, Math.max(0, (d.t ? d.ay - nh : d.ay) - R.top));
    if (gsSide()) {                  // on a side: the width, and the least height (a longer tab still grows it)
      GS.size = { w: nw, h: GS.size ? GS.size.h : 0, sh: nh };
      gsPlace();
      return;
    }
    GS.size = { w: nw, h: nh, sh: GS.size ? GS.size.sh || 0 : 0 };
    GS.pos = { x: R.width > nw ? left / (R.width - nw) : 0, y: R.height > nh ? top / (R.height - nh) : 0 };
    gsPlace();
    return;
  }
  const w = Math.max(0, d.room.width - b.offsetWidth), h = Math.max(0, d.room.height - b.offsetHeight);
  const x = Math.min(w, Math.max(0, e.clientX - d.room.left - d.dx)), y = Math.min(h, Math.max(0, e.clientY - d.room.top - d.dy));
  GS.pos = { x: w ? x / w : 0, y: h ? y / h : 0 };
  // near a side: let go and it snaps there
  d.snap = x <= GS_SNAP ? "l" : x >= w - GS_SNAP ? "r" : "";
  b.classList.toggle("snap-l", d.snap === "l"); b.classList.toggle("snap-r", d.snap === "r");
  gsPlace();
});
function gsDragEnd(e) {
  const d = GS.drag;
  if (!d || d.id !== e.pointerId) return;
  GS.drag = null; $("gstats").classList.remove("drag", "resizing", "snap-l", "snap-r");
  if (d.snap) { gsSnap(d.snap); return; }
  if (d.moved) { gsSave(); gsPlace(); }
}
$("gstats").addEventListener("pointerup", gsDragEnd);
$("gstats").addEventListener("pointercancel", gsDragEnd);
$("gs-move").addEventListener("click", (e) => e.stopPropagation());
document.querySelectorAll(".gs-snap").forEach((el) => el.addEventListener("click", (e) => e.stopPropagation()));
