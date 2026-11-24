// ---------------------------------------------------------------- reminders
// "Remind me" on an upcoming program (guide, search, Sports Coming Up). Reminders live on the server, so
// one set on any screen pops up on every screen; each screen times its own pop-up. When it pops up:
// OK switches to the channel, Back dismisses; it goes away by itself after a couple of minutes.
// Games of the favorite teams (Settings › Sports) pop up the same way without being set: RM.games
// holds their Sports-screen rows (r.game), and OK plays them like picking the game on that screen.
const REMIND_LEAD = 60;          // pop up this many seconds before the start
const REMIND_SHOW = 150 * 1000;  // ... and stay up this long
const RM = { list: [], games: [], shown: null, hideTimer: null };
const rmSeen = () => { try { return new Set(JSON.parse(localStorage.getItem("tb.reminded") || "[]")); } catch (_) { return new Set(); } };
const rmKey = (r) => r.key || (r.game ? `game:${r.game.event.id}` : `${r.number}:${r.start}`);

// a favorite team's game as a reminder: on its channel if it has one, otherwise on its event feed
const gameReminder = (p) => ({ game: p, start: p.start, stop: p.stop, title: p.event.name,
  subtitle: [p.heading, p.event.score, p.event.detail].filter(Boolean).join(" · "),
  number: p.ch ? p.ch.number : null, channel_name: p.ch ? p.ch.label || p.ch.name : (p.feed ? p.feed.name : "") });

// A favorite team's game that turns close late on (or goes to overtime) pops up again, once each:
// "Pittsburgh Panthers 56 – 54 Oklahoma Sooners", with the clock. The server marks it (event.close / .ot).
function closeAlert(p) {
  const e = p.event, t = e.teams || [], n0 = now();
  return { ...gameReminder(p), key: `${e.ot ? "ot" : "close"}:${e.id}`, kind: `⭐ ${e.ot ? "Overtime" : "Close game"}`,
           title: t.length === 2 ? `${t[0].name} ${t[0].score} – ${t[1].score} ${t[1].name}` : e.name,
           when: [e.detail, p.heading].filter(Boolean).join(" · "), start: Math.min(p.start, n0), stop: Math.max(p.stop, n0 + 600) };
}
async function loadGameAlerts() {
  try {
    const games = await api("/api/sports/alerts");
    // close-game pop-ups can be turned off (Setup › Settings › Sports); an older server has no setting: on
    const closeOn = !S.settings || S.settings.sports_close_alerts !== false;
    RM.games = [...games.map(gameReminder), ...(closeOn ? games.filter((p) => p.event && (p.event.close || p.event.ot)).map(closeAlert) : [])];
  } catch (_) {}
}
async function loadReminders() {
  try { RM.list = await api("/api/reminders"); } catch (_) { return; }
  // settings changed in Setup since (the close-game pop-up setting): picked up every few minutes
  try { S.settings = { ...S.settings, ...(await api("/api/settings")) }; } catch (_) {}
  await loadGameAlerts();
  checkReminders();
}
const findReminder = (number, start) => RM.list.find((r) => r.number === number && r.start === start);

async function toggleReminder(ch, p) {
  const r = findReminder(ch.number, p.start);
  try {
    if (r) { await api(`/api/reminders/${r.id}`, { method: "DELETE" }); toast(`Reminder cancelled: ${p.title}`); }
    else {
      await api("/api/reminders", { method: "POST", body: { number: ch.number, start: p.start } });
      toast(`🔔 Reminder set: ${p.title}, ${fmtDay(p.start)} ${fmtTime(p.start)}`);
    }
  } catch (e) { toast(`Can't set a reminder: ${e.message}`); }
  await loadReminders();
  G.forget(ch.number); if (top() === "guide") G.render();
}

function checkReminders() {
  if (RM.shown || S.mode === "off") return;
  const n0 = now(), seen = rmSeen();
  const due = [...RM.list, ...RM.games].find((r) => !seen.has(rmKey(r)) && r.start - REMIND_LEAD <= n0 && n0 < r.stop);
  if (due) showReminder(due);
}

function showReminder(r) {
  const seen = rmSeen(); seen.add(rmKey(r));
  try { localStorage.setItem("tb.reminded", JSON.stringify([...seen].slice(-100))); } catch (_) {}
  const ch = curCh();
  if (S.mode === "live" && ch && (ch.feed ? r.game && ch.feed === (r.game.feed || {}).key : ch.number === r.number)) return;     // already watching it
  RM.shown = r;
  const n0 = now();
  $("rm-kind").textContent = r.kind || (r.game ? `⭐ ${r.game.event.fav.join(" & ")}` : "🔔 Reminder");
  $("rm-when").textContent = r.when ? r.when : r.start > n0 ? `Starts at ${fmtTime(r.start)}` : r.start > n0 - 120 ? "Starting now" : `On now · started ${fmtTime(r.start)}`;
  $("rm-title").textContent = r.title;
  $("rm-sub").textContent = r.subtitle || "";
  $("rm-ch").textContent = r.number != null ? `${r.number} ${r.channel_name || ""}` : r.channel_name || "";
  $("reminder").classList.remove("hidden");
  clearTimeout(RM.hideTimer); RM.hideTimer = setTimeout(hideReminder, REMIND_SHOW);
}
function hideReminder() {
  clearTimeout(RM.hideTimer);
  RM.shown = null; $("reminder").classList.add("hidden");
  setTimeout(checkReminders, 500);    // another one due at the same time
}
function watchReminder() {
  const r = RM.shown; if (!r) return;
  hideReminder();
  closeOverlays();
  if (S.mode === "playback") { $("playback").classList.add("hidden"); recLeave(); destroyPlayer(); }   // leave the recording
  if (r.game) pickGame({ ...r.game, start: Math.min(r.game.start, now()) });     // counts as on now: watch, not record
  else tuneNum(r.number);
}
// OK / Back answer the pop-up while it's up; every other button works as usual underneath
function reminderKey(k) {
  if (!RM.shown) return false;
  if (k === "Enter") { watchReminder(); return true; }
  if (k === "Back") { hideReminder(); return true; }
  return false;
}
$("reminder").addEventListener("click", (e) => { if (e.target.closest(".rm-close")) hideReminder(); else watchReminder(); });

setInterval(checkReminders, 10000);
setInterval(loadReminders, 5 * 60000);   // pick up reminders set on other screens / the phone
// favorite teams' games: often enough to catch one turning close (the server refreshes scores every few minutes)
setInterval(() => loadGameAlerts().then(checkReminders), 60000);      // [] at once when no teams are picked
