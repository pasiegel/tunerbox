// ---------------------------------------------------------------- reminders
// "Remind me" on an upcoming program (guide, search, Sports Coming Up). Reminders live on the server, so
// one set on any screen pops up on every screen; each screen times its own pop-up. When it pops up:
// OK switches to the channel, Back dismisses; it goes away by itself after a couple of minutes.
const REMIND_LEAD = 60;          // pop up this many seconds before the start
const REMIND_SHOW = 150 * 1000;  // ... and stay up this long
const RM = { list: [], shown: null, hideTimer: null };
const rmSeen = () => { try { return new Set(JSON.parse(localStorage.getItem("tb.reminded") || "[]")); } catch (_) { return new Set(); } };
const rmKey = (r) => `${r.number}:${r.start}`;

async function loadReminders() {
  try { RM.list = await api("/api/reminders"); } catch (_) { return; }
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
  const due = RM.list.find((r) => !seen.has(rmKey(r)) && r.start - REMIND_LEAD <= n0 && n0 < r.stop);
  if (due) showReminder(due);
}

function showReminder(r) {
  const seen = rmSeen(); seen.add(rmKey(r));
  try { localStorage.setItem("tb.reminded", JSON.stringify([...seen].slice(-100))); } catch (_) {}
  const ch = curCh();
  if (S.mode === "live" && ch && !ch.feed && ch.number === r.number) return;     // already watching it
  RM.shown = r;
  const n0 = now();
  $("rm-when").textContent = r.start > n0 ? `Starts at ${fmtTime(r.start)}` : r.start > n0 - 120 ? "Starting now" : `On now · started ${fmtTime(r.start)}`;
  $("rm-title").textContent = r.title;
  $("rm-sub").textContent = r.subtitle || "";
  $("rm-ch").textContent = `${r.number} ${r.channel_name || ""}`;
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
  if (S.mode === "playback") { $("playback").classList.add("hidden"); S.rec = null; destroyPlayer(); }   // leave the recording
  tuneNum(r.number);
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
