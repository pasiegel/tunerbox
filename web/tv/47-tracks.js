// ---------------------------------------------------------------- audio tracks and subtitles
// Titles from your own libraries: Menu › This title › Audio / Subtitles while one plays. The choice is kept
// per movie or show (an episode picks the same language as the last one). Subtitles are timed lines from
// the server, drawn here over the picture by the title's position, so they stay in step after a skip.
const avKey = (it) => `tb.av.${it.title_id || it.id}`;
function avPrefs(it) { try { return JSON.parse(localStorage.getItem(avKey(it)) || "{}") || {}; } catch (_) { return {}; } }
function avSave(v) {
  if (v.it.catchup) return;
  const s = v.sub && { label: v.sub.label, lang: v.sub.lang, forced: !!v.sub.forced };
  try { localStorage.setItem(avKey(v.it), JSON.stringify({ a: v.audio || 0, s: s || null })); } catch (_) {}
}
// before the first stream: the audio track picked last time for this movie or show
function avInit(v) {
  const p = avPrefs(v.it);
  v.audio = v.it.catchup ? 0 : (p.a | 0); v.subPick = v.it.catchup ? null : p.s || null;
  v.tracks = null; v.sub = null; v.cues = null;
}
// then, while it starts: what the title has (and the subtitles picked last time, if it has them)
async function avLoad(v) {
  if (v.it.catchup) return;
  let t;
  try { t = await api(`/api/vod/tracks/${vodPath(v.it.id)}`); } catch (_) { return; }
  if (S.vod !== v) return;
  v.tracks = t;
  if (v.audio && v.audio >= t.audio.length) { v.audio = 0; vodStart(vodPos()); }      // this episode has fewer
  const p = v.subPick;
  if (p) {
    const s = t.subs.find((x) => !x.picture && x.label === p.label)
      || t.subs.find((x) => !x.picture && p.lang && x.lang === p.lang && !!x.forced === !!p.forced);
    if (s) subsOn(v, s, true);
  }
  if ($("playback") && !$("playback").classList.contains("hidden")) drawVodBar();
}
const avHas = (v) => !!(v && v.tracks && (v.tracks.audio.length > 1 || v.tracks.subs.length));
function avAudioLabel() {
  const v = S.vod, a = v && v.tracks && v.tracks.audio[v.audio || 0];
  return a ? a.label : "";
}
function pickAudio() {
  const v = S.vod; if (!v || !v.tracks) return;
  openDialog("Audio", v.it.title, v.tracks.audio.map((a) => ({
    label: `${a.n === (v.audio || 0) ? "✓ " : ""}${a.label}`,
    act: () => {
      closeDialog();
      if (a.n === (v.audio || 0) || S.vod !== v) return;
      v.audio = a.n; avSave(v);
      toast(`Audio: ${a.label}`, 2500);
      vodStart(vodPos());                     // the same spot, with that track
    } })));
}
function pickSubs() {
  const v = S.vod; if (!v || !v.tracks) return;
  const cur = v.sub && v.sub.id;
  const entries = [{ label: `${cur ? "" : "✓ "}Off`, act: () => { closeDialog(); subsOff(v); avSave(v); } }];
  for (const s of v.tracks.subs) {
    entries.push({ label: `${s.id === cur ? "✓ " : ""}${s.label}${s.picture ? " (picture subtitles: not available yet)" : ""}`,
      act: () => {
        closeDialog();
        if (s.picture) { toast("Picture subtitles (PGS, VobSub) can't be shown yet. Pick a text track if there is one.", 4500); return; }
        subsOn(v, s); avSave(v);
      } });
  }
  openDialog("Subtitles", v.it.title, entries);
}
function subsOff(v) { v.sub = null; v.cues = null; subsTick(); }
async function subsOn(v, s, quiet) {
  v.sub = s; v.cues = null; subsTick();
  const slow = setTimeout(() => { if (S.vod === v && v.sub === s && !v.cues) toast("Loading subtitles… (the first time takes a minute for a long film)", 6000); }, quiet ? 4000 : 800);
  let d;
  try { d = await api(`/api/vod/subs/${vodPath(v.it.id)}/${encodeURIComponent(s.id)}`); }
  catch (e) { clearTimeout(slow); if (S.vod === v && v.sub === s) { v.sub = null; if (!quiet) toast(`Can't show those subtitles: ${e.message}`, 4000); } return; }
  clearTimeout(slow);
  if (S.vod !== v || v.sub !== s) return;
  v.cues = d.cues || [];
  if (!quiet) toast(`Subtitles: ${s.label}`, 2000);
  subsTick();
}
// the lines on screen at the title's position (several when they overlap)
function subsTick() {
  const el = $("vsubs"), v = S.vod;
  let html = "";
  if (v && v.cues && S.mode === "vod" && v.target === null && !v.parked && !v.done) {
    const c = v.cues, t = vodPos();
    let lo = 0, hi = c.length - 1, i = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (c[m][0] <= t) { i = m; lo = m + 1; } else hi = m - 1; }
    const on = [];
    for (let j = i; j >= 0 && j > i - 6; j--) if (c[j][1] > t) on.unshift(c[j][2]);
    html = on.map((x) => esc(x).replace(/&lt;(\/?)i&gt;/g, "<$1i>").replace(/\n/g, "<br>")).join("<br>");
  }
  if (el.dataset.h !== html) { el.dataset.h = html; el.innerHTML = html ? `<span>${html}</span>` : ""; }
  el.classList.toggle("hidden", !html);
}
video.addEventListener("timeupdate", subsTick);
setInterval(() => { if (S.vod && S.vod.cues) subsTick(); }, 250);
