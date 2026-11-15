// ---------------------------------------------------------------- Home
// One screen with what you'd want first. Live TV keeps playing in the corner with the highlighted tile's
// details beside it, then rows: shortcuts, favorites on now, your teams' games on now, Continue Watching (On Demand and recordings),
// sports on now, recent channels, new recordings and what's coming up. Everything comes from one request
// (/api/home). It opens when the TV turns on (Menu turns that off for this screen) and with the Home button.
const HM = { rows: [], r: 1, cols: {}, seq: 0, timer: null, state: "loading" };   // state: loading | ok | error
const HM_RECENT = 12;
const homeEnabled = () => S.settings.home_screen !== false;          // Setup › Settings › Screens
const homeAtStart = () => homeEnabled() && localStorage.getItem("tb.homeStart") !== "0";
// the on-screen remote's HOME button only when there's a Home screen
// on-screen remote buttons and key hints for features that are switched off or not set up
const homeButton = () => {
  const hide = (k, off) => document.querySelectorAll(`#remote [data-k="${k}"]`).forEach((b) => b.classList.toggle("hidden", off));
  hide("h", !homeEnabled()); hide("o", !vodOn()); hide("d", !dvrOn());
  document.querySelectorAll("[data-feat]").forEach((el) => el.classList.toggle("hidden", !featOn(el.dataset)));
};
const recentNums = () => { try { return JSON.parse(localStorage.getItem("tb.recent") || "[]"); } catch (_) { return []; } };
// channels this screen tuned to, newest first (Home › Recent channels)
function noteRecent(num) {
  if (num === "" || num === undefined || num === null) return;
  try { localStorage.setItem("tb.recent", JSON.stringify([num, ...recentNums().filter((n) => n !== num)].slice(0, HM_RECENT))); } catch (_) {}
}

const HM_NAV = [
  { label: "Watch TV", info: "Back to full-screen TV", act: () => closeHome() },
  { label: "Guide", info: "What's on, channel by channel", act: () => G.open() },
  { label: "On Demand", feat: "vod", info: "Movies and TV shows from your playlists", act: () => openOD() },
  { label: "Sports", info: "Games on now and coming up, scores and event feeds", act: () => openSports() },
  { label: "Recordings", feat: "dvr", info: "Everything recorded or scheduled", act: () => openDvr() },
  { label: "Search", info: "Channels, shows, games, movies and TV shows", act: () => openSearch() },
  { label: "Menu", info: "Favorites, settings, sleep timer and more", act: () => openMenu() },
];

function homeRows(d) {
  const rows = [{ id: "nav", items: HM_NAV.filter(featOn).map((n) => ({ t: "nav", ...n })) }];
  const add = (id, label, items) => { if (items && items.length) rows.push({ id, label, items }); };
  add("fav", "★ Favorites on now", d.favorites.map((c) => ({ t: "chan", ...c })));
  add("teams", "⭐ Your teams on now", (d.teams || []).map((p) => ({ t: "game", p })));
  // posters and wide recording tiles in separate rows, so each row has one shape
  add("cont", "Continue Watching: On Demand", d.continue.filter((x) => x.type === "vod").map((x) => ({ ...x, t: x.type })));
  add("contrec", "Continue Watching: Recordings", d.continue.filter((x) => x.type !== "vod").map((x) => ({ ...x, t: x.type })));
  add("sports", "Sports on now", d.sports.map((p) => ({ t: "game", p })));
  add("recent", "Recent channels", d.recent.map((c) => ({ t: "chan", ...c })));
  add("recorded", "Recently recorded", d.recorded.map((r) => ({ t: "rec", ...r })));
  add("coming", "Coming up", d.coming.map((x) => ({ ...x, t: x.type === "rec" ? "sched" : "remind" })));
  return rows;
}
const hmKey = (it) => it && ({ nav: `n${it.label}`, chan: `c${it.number}`, vod: `v${it.id}`, rec: `r${it.id}`, sched: `r${it.id}`, fail: `r${it.id}`,
                               remind: `m${it.id}`, game: it.p && `g${it.p.title}${it.p.start}` })[it.t];
const hmPct = (start, stop) => Math.max(0, Math.min(100, (now() - start) / ((stop - start) || 1) * 100));

async function openHome() {
  if (S.mode === "off") return;
  if (!homeEnabled()) { if (S.mode !== "empty") G.open(); return; }       // turned off in Setup: the guide, as before
  if (S.mode === "empty") { toast("No channels yet. Open Menu › Channel Setup to add a playlist."); return; }
  hideBanner(); closeMenu(); closeSearch(); closeSports(); closeDvr(); closeOD();
  if (top() === "guide") G.close();
  $("playback").classList.add("hidden");
  if (top() !== "home") S.layer.push("home");
  $("home").classList.remove("hidden"); video.classList.add("pip");
  HM.cols = {};
  if (!HM.rows.length) HM.rows = homeRows({ favorites: [], teams: [], continue: [], sports: [], recent: [], recorded: [], coming: [] });
  if (HM.state !== "ok") HM.state = "loading";
  HM.r = Math.min(1, HM.rows.length - 1);
  homeRender();
  clearInterval(HM.timer);
  HM.timer = setInterval(() => { if (top() === "home") homeLoad(); }, 60000);
  await homeLoad(true);
}
// Guide and On Demand open over Home (hiding it, as they share its layer) so Back comes back here like
// the other screens; homeReveal() shows it again when they close
function homeCover() { if (top() === "home") $("home").classList.add("hidden"); }
function homeReveal() {
  if (top() !== "home") return;
  $("home").classList.remove("hidden"); video.classList.add("pip");
  homeLoad();
}
function closeHome() {
  clearInterval(HM.timer); HM.seq++;
  if (!S.layer.includes("home")) return;
  S.layer = S.layer.filter((l) => l !== "home");
  $("home").classList.add("hidden");
  if (top() !== "guide" && top() !== "od") video.classList.remove("pip");
}
// fetch and redraw, staying on the same tile (or, the first time, on the first row with something in it)
async function homeLoad(first) {
  const seq = ++HM.seq;
  let d;
  try { d = await api(`/api/home?recent=${recentNums().join(",")}`); } catch (_) {
    if (seq !== HM.seq || !S.layer.includes("home") || HM.state === "ok") return;    // keep what's shown
    HM.state = "error"; homeRender(); return;
  }
  if (seq !== HM.seq || !S.layer.includes("home")) return;
  HM.state = "ok";
  const row = HM.rows[HM.r], key = row && hmKey(row.items[HM.cols[row.id] || 0]);
  HM.rows = homeRows(d);
  if (first) HM.r = Math.min(1, HM.rows.length - 1);
  else {
    const r = HM.rows.findIndex((x) => row && x.id === row.id);
    HM.r = r >= 0 ? r : Math.min(HM.r, HM.rows.length - 1);
    const i = HM.rows[HM.r].items.findIndex((it) => hmKey(it) === key);
    if (i >= 0) HM.cols[HM.rows[HM.r].id] = i;
  }
  homeRender();
}

// ---- drawing
function hmTile(it, r, i) {
  const at = `data-r="${r}" data-i="${i}"`;
  if (it.t === "nav") return `<div class="hm-chip" ${at}>${esc(it.label)}</div>`;
  let matchup = "", pic = "", logo = "", badge = "", live = false, title = "", sub = "", pct = 0, poster = false, tone = "", dimBadge = false, num = "";
  if (it.t === "chan") {
    const p = it.now, e = it.game;
    // a channel showing a game the scoreboard knows: its logos and score, like the Sports rows
    matchup = e ? hmMatchup(e, p) : "";
    pic = !matchup && p && p.image ? `/api/art/${it.number}/${p.start}` : "";
    logo = it.logo; badge = it.number;
    title = p ? p.title : it.name; sub = p ? [p.episode, p.subtitle].filter(Boolean).join(" · ") || it.label : "No information";
    if (e) { title = e.heading || title; sub = e.name; }
    pct = p ? hmPct(p.start, p.stop) : 0; tone = it.name; num = String(it.number);
  } else if (it.t === "vod") {
    poster = true; pic = `/api/vod/poster/${vodPath(it.id)}`;
    title = it.name; sub = it.sub || it.year || ""; pct = it.pct; tone = it.name;
  } else if (it.t === "rec") {
    pic = it.art ? `/api/recordings/${it.id}/art` : "";
    title = it.title; sub = [it.episode, it.subtitle].filter(Boolean).join(" · ") || fmtDay(it.start);
    pct = !it.watched && it.duration && it.position > RESUME_MIN ? it.position / it.duration * 100 : 0;
    badge = it.watched ? "WATCHED" : it.position > RESUME_MIN ? "" : "NEW"; dimBadge = !!it.watched; tone = it.title;
  } else if (it.t === "game") {
    const p = it.p, e = p.event, score = e && [e.score, e.detail].filter(Boolean).join(" · ");
    matchup = hmMatchup(e, p);
    pic = !matchup && p.ch && p.image ? `/api/art/${p.ch.number}/${p.start}` : "";
    live = !!p.is_live; badge = live ? "LIVE" : p.ch ? p.ch.number : "FEED";
    const [t, s] = spHeading(p);
    title = [sportEmoji(p), t].filter(Boolean).join(" ");
    // the matchup picture has the score on it already
    sub = [s, !matchup && score].filter(Boolean).join(" · ") || (p.ch ? p.ch.label || p.ch.name : "Event feed");
    pct = hmPct(p.start, p.stop); tone = p.title;
  } else {            // sched (a recording to come) / remind / fail (one that didn't record)
    pic = it.t !== "remind" && it.art ? `/api/recordings/${it.id}/art` : `/api/art/${it.number}/${it.start}`;
    live = it.status === "recording";
    badge = live ? "● REC" : it.t === "fail" ? "FAILED" : it.conflict ? "⚠ CONFLICT" : it.t === "sched" ? "REC" : "REMIND"; dimBadge = it.t === "fail";
    title = it.title; sub = `${fmtWhen(it.start)} · ${it.number} ${it.channel_name || ""}`; tone = it.title;
  }
  return `<div class="hm-tile t${odTone(String(tone || ""))}${poster ? " poster" : ""}${pic || matchup ? " art" : ""}" ${at}${num ? ` data-num="${esc(num)}"` : ""}>`
    + matchup
    + (logo ? `<img class="logo" alt="" src="${esc(safePic(logo))}">` : num && !pic && !matchup ? `<div class="big">${esc(num)}</div>` : "")
    + (pic ? `<img class="pic" loading="lazy" alt="" src="${esc(safePic(pic))}">` : "")
    + (badge !== "" ? `<span class="hm-badge${live ? " live" : dimBadge ? " dim" : ""}">${esc(badge)}</span>` : "")
    + `<div class="tx"><div class="n">${esc(title)}</div><div class="y">${esc(sub)}</div></div>`
    + (pct ? `<div class="bar"><i style="width:${pct}%"></i></div>` : "") + "</div>";
}
// a game the scoreboard knows: both teams' logos on their colours, split down the middle, and the
// score between them once it has started (the leader brighter) with the clock under it. "" if it has
// no logos, so the tile falls back to the guide's picture.
function hmMatchup(e, p) {
  const t = e && e.teams;
  if (!t || t.length !== 2 || !t[0].logo || !t[1].logo) return "";
  const col = (x, dflt) => (/^[0-9a-f]{6}$/i.test(x.color || "") ? `#${x.color}` : dflt);
  const img = (x, side) => `<img class="mu-${side}" loading="lazy" alt="" src="${esc(safePic(x.logo))}">`;
  const a = parseFloat(t[0].score), b = parseFloat(t[1].score), scored = t[0].score !== "" && t[1].score !== "";
  const sc = (x, lead) => `<b class="${lead ? "lead" : ""}">${esc(x.score)}</b>`;
  const mid = scored
    ? `<span class="mu-sc">${sc(t[0], !(a < b))}<i>–</i>${sc(t[1], !(b < a))}</span>${e.detail ? `<small class="mu-clk">${esc(e.detail)}</small>` : ""}`
    : `<span class="mu-vs">${e.sport === "Soccer" ? "vs" : "@"}</span>${p && p.start > now() ? `<small class="mu-clk">${esc(fmtTime(p.start))}</small>` : ""}`;
  return `<div class="mu${scored ? " scored" : ""}" style="--l:${col(t[0], "#1d3a7a")};--r:${col(t[1], "#5a1f2a")}">${img(t[0], "l")}`
    + `<div class="mu-mid">${mid}</div>${img(t[1], "r")}</div>`;
}
const HM_EMPTY = {
  loading: "Loading…",
  error: "Couldn't load Home. Check that TunerBox is running, then press H again.",
  ok: "Nothing here yet. Press * on a channel to add it to Favorites. What you watch, record and set reminders for shows up here too.",
};
function homeRender() {
  // the shortcuts (row 0) sit in the bar at the top; ▲ from the first row of tiles goes up to them
  const nav = HM.rows.findIndex((row) => row.id === "nav");
  $("hm-topnav").innerHTML = `<span class="hm-brand">Tuner<span>Box</span></span>`
    + (nav >= 0 ? HM.rows[nav].items.map((it, i) => hmTile(it, nav, i)).join("") : "")
    + `<span class="hm-when"><b id="hm-tclock"></b> <span id="hm-tdate"></span></span>`;
  $("hm-rows").innerHTML = HM.rows.map((row, r) => {
    if (row.id === "nav") return "";
    const tiles = row.items.map((it, i) => hmTile(it, r, i)).join("");
    return `<div class="hm-row" data-row="${r}"><div class="hm-label">${esc(row.label)}</div><div class="hm-strip">${tiles}</div></div>`;
  }).join("") + (HM.rows.length >= 3 ? "" : `<div class="od-empty">${HM_EMPTY[HM.state]}</div>`);
  homeClock();
  homeSelect();
}
function homeSelect() {
  const row = HM.rows[HM.r]; if (!row) return;
  const i = Math.min(HM.cols[row.id] || 0, row.items.length - 1);
  HM.cols[row.id] = i;
  const old = $("home").querySelector(".hm-tile.sel, .hm-chip.sel"); if (old) old.classList.remove("sel");
  $("hm-rows").querySelectorAll(".hm-row").forEach((el) => el.classList.toggle("cur", +el.dataset.row === HM.r));
  const el = $("home").querySelector(`[data-r="${HM.r}"][data-i="${i}"]`);
  if (el) {
    el.classList.add("sel");
    if (HM.r <= 1) $("hm-rows").scrollTop = 0;               // keep the shortcuts in view near the top
    el.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  homeInfo(row.items[i]);
  $("hm-foot").innerHTML = "<span><b>OK</b> Watch / Open</span><span><b>&#9650;&#9660;&#9664;&#9654;</b> Move</span>"
    + "<span><b>*</b> Favorite</span><span><b>0-9</b> Tune</span><span><b>Esc</b> Back to TV</span>";
}
function homeInfo(it) {
  const x = hmText(it);
  $("hmi-title").textContent = x.title; $("hmi-sub").textContent = x.sub; $("hmi-meta").textContent = x.meta; $("hmi-desc").textContent = x.desc;
}
// the details shown above the rows for a tile (Home and Recordings)
function hmText(it) {
  let title = "", sub = "", meta = "", desc = "";
  if (!it) title = "Home";
  else if (it.t === "nav") { title = it.label; sub = it.info; }
  else if (it.t === "chan") {
    const p = it.now;
    title = p ? p.title : it.name; sub = p ? [p.episode, p.subtitle].filter(Boolean).join("  ·  ") : "No information available";
    const e = it.game;
    if (e) { title = e.heading || title; sub = [e.name, e.score, e.detail].filter(Boolean).join("  ·  "); }
    meta = [`${it.number} ${it.label || it.name}`, p && `${fmtTime(p.start)} – ${fmtTime(p.stop)}`, p && p.category].filter(Boolean).join("  ·  ");
    desc = p ? p.descr || "" : "";
  } else if (it.t === "vod") {
    title = it.name; sub = it.sub || "";
    meta = [it.kind === "series" ? "TV Show" : "Movie", it.year, it.pct ? `${it.pct}% watched` : ""].filter(Boolean).join("  ·  ");
  } else if (it.t === "rec") {
    title = it.title; sub = [it.episode, it.subtitle].filter(Boolean).join("  ·  ");
    meta = [`Recorded ${fmtDay(it.start)} ${fmtTime(it.start)}`, `${it.number} ${it.channel_name || ""}`,
            it.watched ? "Watched" : it.position > RESUME_MIN ? `Stopped at ${fmtDur(it.position)}` : ""].filter(Boolean).join("  ·  ");
    desc = it.descr || "";
  } else if (it.t === "game") {
    const p = it.p, e = p.event;
    const [t, s] = spHeading(p);
    title = [sportEmoji(p), t].filter(Boolean).join(" ");
    sub = [s, e && e.score, e && e.detail].filter(Boolean).join("  ·  ");
    meta = [p.ch ? `${p.ch.number} ${p.ch.label || p.ch.name}` : "Event feed", `until ${fmtTime(p.stop)}`].join("  ·  ");
    desc = p.descr || "";
  } else {
    title = it.title; sub = it.subtitle || "";
    meta = [`${fmtDay(it.start)} ${fmtTime(it.start)}`, `${it.number} ${it.channel_name || ""}`,
            it.t === "remind" ? "🔔 Reminder set" : it.t === "fail" ? "Didn't record" : it.status === "recording" ? "● Recording now"
              : it.conflict ? "⚠ Won't get a connection" : "Will be recorded"].join("  ·  ");
    // a clash with other recordings, or a recording waiting for a connection, says why
    desc = it.conflict || ((it.t === "fail" || it.status === "recording") && it.error ? it.error : it.descr || "");
  }
  return { title, sub, meta, desc };
}

// ---- picking
function homeAct(it) {
  if (!it) return;
  if (it.t === "nav") { it.act(); return; }
  if (it.t === "chan") {
    const i = S.byNum.get(it.number);
    if (i === undefined) { toast(`Channel ${it.number} is not available`); return; }
    closeHome(); if (S.idx !== i || S.mode !== "live") tune(i); return;
  }
  if (it.t === "vod") {
    if (it.kind !== "series") { odMovie(it.id); return; }
    if (!it.resume) { openOD("series", it.id); return; }
    openDialog(it.name, it.sub || "", [
      { label: `Resume ${it.sub || "episode"}`, act: () => playVod(it.resume) },
      { label: "All episodes", act: () => { closeDialog(); openOD("series", it.id); } },
      { label: "Close", act: closeDialog }]);
    return;
  }
  if (it.t === "rec") { recDialog(it, () => homeLoad()); return; }
  if (it.t === "game") { const g = spGame(it.p, "", true); if (g.act) g.act(); return; }
  const ch = S.channels[S.byNum.get(it.number)];
  const entries = [];
  if (ch && it.start <= now() && now() < it.stop) entries.push({ label: "Watch now", act: () => { closeDialog(); closeHome(); tune(S.byNum.get(it.number)); } });
  if (it.t === "sched") {
    entries.push({ label: it.status === "recording" ? "Stop recording" : "Cancel recording", act: async () => {
      try { await api(`/api/recordings/${it.id}`, { method: "DELETE" }); toast(it.status === "recording" ? "Recording stopped" : "Recording cancelled"); }
      catch (e) { toast(e.message); }
      closeDialog(); G.cache.clear(); homeLoad();
    } });
  } else {
    entries.push({ label: "🔕 Cancel reminder", act: async () => {
      try { await api(`/api/reminders/${it.id}`, { method: "DELETE" }); toast(`Reminder cancelled: ${it.title}`); } catch (e) { toast(e.message); }
      closeDialog(); await loadReminders(); G.cache.clear(); homeLoad();
    } });
  }
  entries.push({ label: "Close", act: closeDialog });
  openDialog(it.title, `${it.number} ${it.channel_name || ""}\n${fmtDay(it.start)} ${fmtTime(it.start)} – ${fmtTime(it.stop)}`, entries);
  $("dlg-img").classList.add("art");
  if (it.t === "sched") recArt($("dlg-img"), it); else loadArt($("dlg-img"), `${it.number}/${it.start}`, `/api/art/${it.number}/${it.start}`);
}

function homeKey(k) {
  const row = HM.rows[HM.r];
  if (!row) { if (k === "Back") closeHome(); return; }
  const i = HM.cols[row.id] || 0, it = row.items[i];
  switch (k) {
    case "ArrowUp": if (HM.r > 0) { HM.r--; homeSelect(); } break;
    case "ArrowDown": if (HM.r < HM.rows.length - 1) { HM.r++; homeSelect(); } break;
    case "ArrowLeft": if (i > 0) { HM.cols[row.id] = i - 1; homeSelect(); } break;
    case "ArrowRight": if (i < row.items.length - 1) { HM.cols[row.id] = i + 1; homeSelect(); } break;
    case "ChannelUp": HM.cols[row.id] = 0; homeSelect(); break;
    case "ChannelDown": HM.cols[row.id] = row.items.length - 1; homeSelect(); break;
    case "Enter": homeAct(it); break;
    case "*":
      if (it && it.t === "chan" && S.byNum.has(it.number)) toggleFav(S.channels[S.byNum.get(it.number)]).then(() => homeLoad());
      break;
    case "Back": closeHome(); break;
    case "g": G.open(); break;
    case "v": G.open(true); break;
    case "o": openOD(); break;
    case "t": openSports(); break;
    case "/": openSearch(); break;
    case "d": openDvr(); break;
    case "m": openMenu(); break;
    case "u": toggleMute(); break;
    case "VolUp": setVolume(video.volume + 0.05); break;
    case "VolDown": setVolume(video.volume - 0.05); break;
    default:
      if (/^[0-9]$/.test(k) && S.mode === "live") { closeHome(); digit(k); }
  }
}
function homeClock() {
  const t = $("hm-tclock"); if (!t) return;
  const d = new Date();
  t.textContent = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  $("hm-tdate").textContent = d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}
setInterval(() => { if (top() === "home") homeClock(); }, 20000);
$("hm-topnav").addEventListener("click", (e) => {
  const el = e.target.closest("[data-r]"); if (!el) return;
  HM.r = +el.dataset.r; HM.cols[HM.rows[HM.r].id] = +el.dataset.i;
  homeSelect(); homeAct(HM.rows[HM.r].items[+el.dataset.i]);
});
$("hm-rows").addEventListener("click", (e) => {
  const el = e.target.closest("[data-r]"); if (!el) return;
  HM.r = +el.dataset.r; HM.cols[HM.rows[HM.r].id] = +el.dataset.i;
  homeSelect(); homeAct(HM.rows[HM.r].items[+el.dataset.i]);
});
// a picture that won't load: show the tile's name instead
$("hm-rows").addEventListener("error", (e) => {
  const img = e.target;
  if (img.tagName !== "IMG") return;
  const tile = img.closest(".hm-tile");
  img.remove();
  if (tile && img.classList.contains("pic")) tile.classList.remove("art");
  if (tile && img.classList.contains("logo") && tile.dataset.num && !tile.querySelector(".big"))
    tile.insertAdjacentHTML("afterbegin", `<div class="big">${esc(tile.dataset.num)}</div>`);
}, true);
