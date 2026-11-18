// ---------------------------------------------------------------- game stats
// X while watching a game: a card in the top right corner over the picture with the box score, team
// stats and the latest plays, for the game the scoreboard matched to this channel or event feed. On the
// Live scores tab it becomes a slim panel down the right with a tile per game, like Home's. The picture
// keeps playing full size behind it (on a phone the panel is a sheet under it); channel and volume keys
// still work, and the panel follows the new channel.
const GS = { tab: 0, data: null, others: [], sel: 0, key: "", t: 0, timer: null };
const GS_REFRESH = 20000;

const gsKey = (ch) => (ch ? (ch.feed ? `f:${ch.feed}` : `c:${ch.number}`) : "");
function gsTabs() {
  const d = GS.data;
  // no game the scoreboard knows on this channel: just the live games, if there are any
  if (!d) return GS.others.length ? [{ label: "Live scores", scores: true }] : [];
  // one tab per team's players, so the list stays short, then the team stats, the plays, and every
  // other game on now in the leagues you follow
  return [...d.players.map((p) => ({ label: p.abbr, team: p })),
          d.team_stats.length && { label: "Team stats" }, d.plays.length && { label: "Plays" },
          GS.others.length && { label: "Live scores", scores: true }].filter(Boolean);
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

function gsRender(msg) {
  const d = GS.data, body = $("gs-body"), scroll = body.scrollTop;
  const scoresOnly = !d && GS.others.length && msg === undefined;
  const tabs = gsTabs(), tab = tabs[GS.tab];
  // the Live scores tab (or nothing but live scores) uses the tall side panel, everything else the corner card
  const onScores = msg === undefined && !!(tab && tab.scores);
  $("gstats").classList.toggle("scores", onScores);
  $("gs-keys").innerHTML = onScores ? "<b>&#9664; &#9654;</b> Tabs &nbsp; <b>&#9650; &#9660;</b> Pick &nbsp; <b>OK</b> Watch &nbsp; <b>Esc</b> Close"
    : "<b>&#9664; &#9654;</b> Tabs &nbsp; <b>&#9650; &#9660;</b> Scroll &nbsp; <b>Esc</b> Close";
  $("gs-league").textContent = d ? (d.state === "in" ? `${d.league} · LIVE` : d.league) : "";
  $("gs-clock").textContent = d ? d.detail : "";
  $("gs-upd").textContent = "";
  if (!d && !scoresOnly) {
    $("gs-teams").innerHTML = ""; $("gs-tabs").innerHTML = "";
    body.innerHTML = `<div class="gs-empty">${esc(msg || "")}</div>`;
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
  else if (tab.team) {
    for (const t of tab.team.tables) {
      const head = [t.title || "Player", ...t.cols];
      html += gsTable(head, t.rows.map((r) => [`${r.num ? `<small>#${esc(r.num)}</small>` : ""}${esc(r.name)}`, ...r.stats.map(esc)]));
    }
  } else if (tab.scores) {
    html = GS.others.map(gsGame).join("");
  } else if (tab.label === "Team stats") {
    html = gsTable(["", ...d.teams.map((t) => t.abbr)], d.team_stats.map((s) => [esc(s.label), ...s.values.map(esc)]), "team");
  } else {
    html = d.plays.map((p) => `<div class="gs-play${p.score ? " score" : ""}"><small>${esc(p.when)}</small>${esc(p.text)}</div>`).join("");
  }
  body.innerHTML = html;
  body.scrollTop = msg === undefined ? scroll : 0;
  if (tab && tab.scores) gsShowSel();
  if (d && d.updated) $("gs-upd").textContent = `Updated ${fmtTime(d.updated)}`;
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
    case "Enter": if (gsOnScores()) gsWatch(GS.others[GS.sel]); break;
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
