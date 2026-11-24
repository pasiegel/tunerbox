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
// the dialog / menu entries for it ([] when it isn't in On Demand)
function odProgramEntries(m, p) {
  if (!m) return [];
  if (m.show) return [{ label: "▶ Find this show in On Demand", act: () => { closeDialog(); closeMenu(); if (top() === "guide") G.close(); openOD("series", m.show); } }];
  const what = m.kind === "episode" ? `this episode (${(m.sub || "").split(" · ")[0]})` : "this movie";
  const at = odSpot(m, p), out = [{ label: `▶ Watch ${what} On Demand from the beginning`, act: () => odFromProgram(m, 0) }];
  if (at > 60) out.push({ label: `▶ Continue On Demand from here (about ${fmtDur(at)} in)`, act: () => odFromProgram(m, at) });
  if (m.position > RESUME_MIN && Math.abs(m.position - at) > 120)
    out.push({ label: `▶ Resume On Demand where you stopped (${fmtDur(m.position)})`, act: () => odFromProgram(m, m.position) });
  return out;
}
