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
