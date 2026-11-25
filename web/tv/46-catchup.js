// ---------------------------------------------------------------- catch-up TV
// A channel whose playlist keeps an archive (ch.catchup = days, from the provider) can play shows that
// already aired, and the one on now from the beginning. They play in the On Demand player as the item
// "cu-<channel>-<start>": pause, skip and the progress bar work as for a movie.
function cuDays() { return S.channels.reduce((m, c) => Math.max(m, c.catchup || 0), 0); }
// is this show in the channel's archive? (started at least a minute ago, and not too long ago)
function cuAvailable(ch, p, t = now()) {
  return !!(ch && ch.catchup && ch.epg && p && !p.empty && p.start <= t - 60 && p.start >= t - ch.catchup * 86400);
}
function playCatchup(ch, p) {
  if (!cuAvailable(ch, p)) {
    toast(ch && ch.catchup ? "That show is no longer in the channel's archive" : `${ch ? ch.number + " " + ch.name : "This channel"} has no catch-up`, 4000);
    return;
  }
  closeDialog();
  if (top() === "guide") G.close();
  closeHome();
  playVod(`cu-${ch.number}-${p.start}`, 0, false);
}
// S (or Menu › Start this show over): the show on now, from the beginning
async function startOver() {
  const ch = S.mode === "live" && curCh();
  if (!ch || ch.feed) { toast("Tune to a channel first"); return; }
  if (!ch.catchup) { toast(`${ch.number} ${ch.name} has no catch-up, so its shows can't be started over`, 4000); return; }
  let d;
  try { d = await nowNext(ch.number, true); } catch (e) { toast(e.message); return; }
  if (!d.now) { toast("No guide information for this channel, so there's no show to start over"); return; }
  playCatchup(ch, d.now);
}
// "3 DAYS" / "1 DAY": how far back this channel's archive goes
function cuDaysText(ch) { const d = ch.catchup || 0; return `${d} DAY${d === 1 ? "" : "S"}`; }
function cuLive() { const ch = S.mode === "live" && curCh(); return !!(ch && !ch.feed && ch.catchup); }

// ---------------------------------------------------------------- the show in On Demand
// A movie or episode on a channel that's also in On Demand: watch it there from the beginning, or carry on
// from about where the broadcast is. Looked up by the server (/api/vod/for-program), remembered a while.
const odProgCache = new Map();
async function odForProgram(ch, p) {
  if (!vodOn() || !ch || ch.feed || !p || p.empty || !p.title) return null;
  const key = `${ch.number}-${p.start}`, c = odProgCache.get(key);
  if (c && Date.now() - c.t < 5 * 60000) return c.m;
  let m = null;
  try { const d = await api(`/api/vod/for-program/${ch.number}/${p.start}`); m = d && d.match ? { ...d.match, slot: d.slot } : null; }
  catch (_) { return null; }                 // a server without it (or a hiccup): just no On Demand entries
  odProgCache.set(key, { t: Date.now(), m });
  return m;
}
// how far into the On Demand copy the broadcast is now: the time since it started, less the adverts'
// share (a 42-minute episode in an hour's slot is 30 minutes in after 43 minutes on the air)
function odSpot(m, p) {
  const n = now(), slot = Math.max(1, p.stop - p.start), dur = m.duration || 0;
  if (n < p.start || n >= p.stop) return 0;
  let at = n - p.start;
  if (dur && slot > dur) at *= dur / slot;
  return Math.max(0, dur ? Math.min(at, dur - 60) : at);
}
function odFromProgram(m, at) {
  closeDialog(); closeMenu(); closeHome();
  playVod(m.id, at, false);                  // Back / Stop: back to the channel
}
// what can be done with it: open the show, or play from the start, from where the broadcast is, or
// from where you stopped ([] when it isn't in On Demand). The guide's dialog and the on-screen controls word them.
function odChoices(m, p) {
  if (!m) return [];
  if (m.show) return [{ id: "show", act: () => { closeDialog(); closeMenu(); if (top() === "guide") G.close(); openOD("series", m.show); } }];
  const at = odSpot(m, p), out = [{ id: "start", at: 0 }];
  if (at > 60) out.push({ id: "here", at });
  if (m.position > RESUME_MIN && Math.abs(m.position - at) > 120) out.push({ id: "resume", at: m.position });
  return out.map((c) => ({ ...c, act: () => odFromProgram(m, c.at) }));
}
// the guide dialog's entries for it
function odProgramEntries(m, p) {
  const what = m && m.kind === "episode" ? `this episode (${(m.sub || "").split(" · ")[0]})` : "this movie";
  const label = (c) => ({
    show: "▶ Find this show in On Demand",
    start: `▶ Watch ${what} On Demand from the beginning`,
    here: `▶ Continue On Demand from here (about ${fmtDur(c.at)} in)`,
    resume: `▶ Resume On Demand where you stopped (${fmtDur(c.at)})`,
  })[c.id];
  return odChoices(m, p).map((c) => ({ label: label(c), act: c.act }));
}

// a choice's short label with its icon, and what's in On Demand (the on-screen controls' On Demand button)
const OD_ICON = {
  start: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15l13-7.5z"/></svg>',
};
const odShortLabel = (c) => ({
  show: `${OD_ICON.play}Find this show`,
  start: `${OD_ICON.start}Start from the beginning`,
  here: `${OD_ICON.play}Continue · ${fmtDur(c.at)} in`,
  resume: `${OD_ICON.play}Resume · ${fmtDur(c.at)}`,
})[c.id];
const odWhat = (m) => (m.show ? "This show is in On Demand" : `This ${m.kind === "episode" ? "episode" : "movie"} is in On Demand`);

// ---------------------------------------------------------------- the same, on the on-screen controls
// An "On Demand" button next to Multiview, only while the show on now is in On Demand; it opens its
// choices above it (70-sleep-screensaver.js sends the clicks here). remoteSync calls rmOdFor.
const RMOD = { tok: 0, choices: [] };
function rmOdHide() {
  RMOD.tok++; RMOD.choices = [];
  $("rm-od").classList.add("hidden"); $("rm-odsep").classList.add("hidden"); rmOdClose();
}
function rmOdClose() { $("rm-odmenu").classList.add("hidden"); $("rm-od").classList.remove("on"); }
async function rmOdFor(ch, p) {
  if (RMOD.key === `${ch && ch.number}-${p && p.start}` && RMOD.choices.length) return;    // already up for it
  rmOdHide(); RMOD.key = "";
  const tok = RMOD.tok;
  if (!ch || ch.feed || !p || !vodOn() || S.mode !== "live" || mvOn()) return;
  const m = await odForProgram(ch, p);
  if (tok !== RMOD.tok || curCh() !== ch || S.mode !== "live") return;
  const choices = odChoices(m, p);
  if (!choices.length) return;
  Object.assign(RMOD, { choices, m, p, key: `${ch.number}-${p.start}` });
  $("rm-od").classList.remove("hidden");
  $("rm-odsep").classList.toggle("hidden", !$("rm-mv").classList.contains("hidden"));   // Multiview's own line does
}
function rmOdToggle() {
  const menu = $("rm-odmenu");
  if (!menu.classList.contains("hidden")) { rmOdClose(); return; }
  RMOD.choices = odChoices(RMOD.m, RMOD.p);         // "Continue · 30:06 in": as of now
  menu.innerHTML = `<div class="rm-odhead">${esc(odWhat(RMOD.m))}</div>`
    + RMOD.choices.map((c, i) => `<button type="button" data-odi="${i}">${odShortLabel(c)}</button>`).join("");
  $("rm-menu").classList.add("hidden");
  menu.classList.remove("hidden"); $("rm-od").classList.add("on");
  if (!isPhone()) {                                 // above the button, kept on screen
    const b = $("rm-od").getBoundingClientRect(), w = menu.offsetWidth;
    menu.style.left = `${Math.max(8, Math.min(b.left, innerWidth - w - 8))}px`;
  } else menu.style.left = "";
}
function rmOdPick(i) { const c = RMOD.choices[i]; rmOdClose(); if (c) c.act(); }
