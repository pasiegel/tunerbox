// ---------------------------------------------------------------- On Demand (movies and shows from the playlists)
// One screen, laid out like the guide: live TV keeps playing in the corner, the highlighted title's details
// beside it, then tabs, a category column and a poster grid. The Movies and TV Shows tabs each have their
// categories; Continue Watching, My List and Search cover both. A TV show opens in the same frame with its
// seasons on the left and episodes on the right (OD.show).
const OD_TABS = [["movie", "Movies"], ["series", "TV Shows"], ["continue", "Continue Watching"], ["list", "My List"], ["search", "Search"]];
const OD_LISTS = 4;                     // tabs before this one show a list; Search acts on OK
const OD_KIND = { movie: "Movies", series: "TV Shows" };
const OD_PAGE = 160;
const OD = { tab: 0, focus: 0, zone: "cats",      // zone: tabs | cats | grid, and seasons | eps inside a show
             counts: {}, cats: [], cat: 0, items: [], total: 0, sel: 0, seq: 0, loaded: false, loading: false,
             show: null, season: 0, ep: 0, details: new Map(), infoTimer: null, catTimer: null, tabTimer: null };
const RESUME_MIN = 30;                  // seconds in before "Resume" is offered (same as the server)
const fmtLen = (s) => { s = Math.round((s || 0) / 60); return !s ? "" : s < 60 ? `${s} min` : `${s / 60 | 0}h ${s % 60}m`; };
const vodPath = (id) => encodeURIComponent(id);
const odTabId = () => OD_TABS[OD.tab][0];
const odHasCats = () => OD.tab < 2;                  // the Movies and TV Shows tabs
const odKind = () => OD_TABS[OD.tab][0];             // "movie" / "series" on those tabs
const odItem = () => OD.items[OD.sel];
const odEps = () => (OD.show ? OD.show.episodes.filter((e) => e.season === (OD.show.seasons[OD.season] || {}).season) : []);
const odRating = (r) => (!r ? "" : isNaN(+r) ? r : +r > 0 ? `★ ${(+r).toFixed(1)}` : "");

// openOD() opens where you left it; openOD("movie" / "series") on that tab; showId opens that show's episodes
async function openOD(kind, showId) {
  let c;
  try { c = await api("/api/vod"); } catch (e) { toast(e.message); return; }
  if (!c.movie.total && !c.series.total && !c.movie.list && !c.series.list) {
    toast(c.enabled ? "On Demand is still being read from your playlist. Try again when the scan is done."
                    : "On Demand is off. Turn it on in Setup › Playlists.", 5000);
    return;
  }
  OD.counts = c; OD.details.clear();
  hideBanner(); closeMenu(); closePanels(); closeDvr(); $("playback").classList.add("hidden");
  if (top() === "guide") G.close();
  if (top() !== "od") S.layer.push("od");
  $("od").classList.remove("hidden"); video.classList.add("pip");
  const want = kind === "series" ? 1 : kind === "movie" ? 0 : null;
  if (showId) {
    if (!OD.loaded || OD.tab !== 1) { OD.loaded = true; OD.tab = OD.focus = 1; OD.cats = []; OD.items = []; }
    OD.show = null; await odOpenShow(showId); return;
  }
  if (!OD.loaded || (want !== null && want !== OD.tab)) {
    OD.loaded = true; OD.show = null; OD.zone = "cats";
    OD.tab = OD.focus = want !== null ? want : !c.movie.total && c.series.total ? 1 : 0;
    await odLoadTab();
  } else if (OD.show) await odOpenShow(OD.show.id, true);          // back from an episode: fresh watched marks
  else { odRender(); await odLoadItems(true); }
}
function closeOD() {
  OD.seq++; clearTimeout(OD.infoTimer); clearTimeout(OD.catTimer);
  if (!S.layer.includes("od")) return;
  S.layer = S.layer.filter((l) => l !== "od");
  $("od").classList.add("hidden");
  if (top() !== "guide") video.classList.remove("pip");
}

// ---- loading
async function odLoadTab(keep) {
  const seq = ++OD.seq;
  if (!keep) { OD.cat = 0; OD.sel = 0; OD.cats = []; OD.items = []; OD.total = 0; OD.loading = true; }
  odRender();
  if (odHasCats()) {
    let d;
    try { d = await api(`/api/vod/categories?kind=${odKind()}`); } catch (e) { toast(e.message); return; }
    if (seq !== OD.seq) return;
    OD.cats = [{ key: null, name: `All ${OD_KIND[odKind()]}`, count: d.total },
               ...(d.recent ? [{ key: "*recent", name: "Recently Added", count: d.recent }] : []),
               ...d.cats.map((x) => ({ key: x.name, name: x.name || "Other", count: x.count }))];
    OD.cat = Math.min(OD.cat, OD.cats.length - 1);
  } else if (OD.zone === "cats") OD.zone = "grid";
  odRenderSide();
  await odLoadItems(keep);
}
async function odLoadItems(keep, append) {
  const seq = append ? OD.seq : ++OD.seq;
  const tab = odHasCats() ? "" : odTabId();       // Continue Watching / My List: movies and shows together
  const cat = odHasCats() && OD.cats[OD.cat] ? OD.cats[OD.cat].key : null;
  const off = append ? OD.items.length : 0;
  const limit = append ? OD_PAGE : Math.max(OD_PAGE, keep ? OD.items.length : 0);
  OD.loading = true;
  let d;
  try {
    d = await api(`/api/vod/titles?${tab ? `tab=${tab}` : `kind=${odKind()}`}${cat !== null ? `&cat=${encodeURIComponent(cat)}` : ""}&offset=${off}&limit=${limit}`);
  } catch (e) { if (seq === OD.seq) { OD.loading = false; toast(e.message); } return; }
  if (seq !== OD.seq) return;
  OD.loading = false;
  OD.items = append ? OD.items.concat(d.items) : d.items; OD.total = d.total;
  OD.sel = keep || append ? Math.min(OD.sel, Math.max(0, OD.items.length - 1)) : 0;
  if (!OD.items.length && OD.zone === "grid") OD.zone = odHasCats() ? "cats" : "tabs";
  if (!OD.show) { odRenderGrid(append ? off : 0); odSelect(); }
}
async function odRefresh() {
  OD.details.clear();
  try { OD.counts = await api("/api/vod"); } catch (_) {}
  if (!S.layer.includes("od")) return;
  if (OD.show) await odOpenShow(OD.show.id, true); else { odRenderHead(); await odLoadItems(true); }
}
async function odOpenShow(id, keep) {
  let d;
  try { d = await api(`/api/vod/title/${vodPath(id)}`); } catch (e) { toast(e.message); return false; }
  if (d.kind !== "series") return false;
  if (!d.episodes.length) { toast("No episodes are listed for this show"); if (keep) { OD.show = null; odRender(); } return false; }
  OD.show = d;
  if (keep) {
    OD.season = Math.min(OD.season, d.seasons.length - 1);
    OD.ep = Math.min(OD.ep, Math.max(0, odEps().length - 1));
  } else {                                 // start on the episode that's next for you
    const e = d.episodes.find((x) => x.id === d.next) || d.episodes[0];
    OD.season = Math.max(0, d.seasons.findIndex((s) => s.season === e.season));
    OD.ep = Math.max(0, odEps().indexOf(e)); OD.zone = "eps";
  }
  odRender();
  return true;
}
function odCloseShow() {
  OD.show = null; OD.zone = OD.items.length ? "grid" : odHasCats() ? "cats" : "tabs";
  odRender();
  if (OD.cats.length || !odHasCats()) odLoadItems(true); else odLoadTab();      // came straight from Search
}

// search the movies and shows, in the search panel
function odSearch() {
  openSearch(true);
}

// ---- drawing
function odRender() { odRenderHead(); odRenderSide(); if (OD.show) odRenderEps(); else odRenderGrid(0); odSelect(); }
function odRenderHead() {
  const s = OD.show;
  $("od-label").textContent = s ? "Seasons" : odHasCats() ? "Categories" : "On Demand";
  if (s) {
    const se = s.seasons[OD.season] || { season: 1, count: 0, watched: 0 };
    $("od-tabs").innerHTML = `<div class="od-crumb"><span>Season ${se.season} <small>${plural(se.count, "episode")}${se.watched ? ` · ${se.watched} watched` : ""}</small></span>`
      + `<small>TV Shows${s.category ? ` › ${esc(s.category)}` : ""}</small></div>`;
    return;
  }
  const m = OD.counts.movie || {}, sr = OD.counts.series || {};
  const n = { movie: m.total, series: sr.total, continue: (m.continue || 0) + (sr.continue || 0), list: (m.list || 0) + (sr.list || 0) };
  $("od-tabs").innerHTML = OD_TABS.map(([id, label], i) => {
    const text = `${label}${n[id] !== undefined ? ` <small>${(n[id] || 0).toLocaleString()}</small>` : ""}`;
    return `<div class="od-tab${i === OD.tab ? " sel" : ""}${i === OD.focus && OD.zone === "tabs" ? " focus" : ""}" data-t="${i}">${text}</div>`;
  }).join("");
}
function odRenderSide() {
  const rows = OD.show
    ? OD.show.seasons.map((s) => ({ name: `Season ${s.season}`, count: `${s.count} ep` }))
    : odHasCats() ? OD.cats.map((c) => ({ name: c.name, count: c.count.toLocaleString() })) : [];
  $("od-side").innerHTML = rows.map((r, i) => `<li data-i="${i}"><span>${esc(r.name)}</span><small>${r.count}</small></li>`).join("");
  $("od-side").classList.toggle("hidden", !rows.length);
}
// the provider's label ("EN", "4K-FR", "NF") is only worth a badge when more than one is shown
const odManyTags = () => ((OD.counts && OD.counts.tags) || 0) > 1;
const odLabel = (x) => [x.tag, x.country].filter(Boolean).join(" · ");
const odTone = (name) => { let h = 0; for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 8; return h; };     // a title keeps its color
const odTile = (t, i) => `<div class="od-tile t${odTone(t.name)}${t.poster ? " art" : ""}" data-i="${i}">`
  + (t.poster ? `<img loading="lazy" alt="" referrerpolicy="no-referrer" src="${esc(t.poster)}">` : "")
  + (t.new ? '<span class="flag">NEW</span>' : t.watched ? '<span class="flag cc">WATCHED</span>' : "")
  + (t.tag && odManyTags() ? `<span class="od-lang">${esc(t.tag)}</span>` : "")
  + `<div class="tx"><div class="n">${esc(t.name)}</div><div class="y">${esc(t.sub || t.year || "")}</div></div>`
  + (t.pct ? `<div class="bar"><i style="width:${t.pct}%"></i></div>` : "") + "</div>";
function odRenderGrid(from) {
  const g = $("od-grid");
  g.classList.remove("hidden"); $("od-eps").classList.add("hidden");
  if (!OD.items.length) {
    const why = OD.loading ? "Loading…" : odTabId() === "continue" ? "Nothing in progress. Movies and episodes you start show up here."
      : odTabId() === "list" ? "My List is empty. Press * on a movie or show to add it."
      : "Nothing in this category.";
    g.innerHTML = `<div class="od-empty">${esc(why)}</div>`;
    return;
  }
  const html = OD.items.slice(from).map((t, i) => odTile(t, from + i)).join("");
  if (from) g.insertAdjacentHTML("beforeend", html); else { g.innerHTML = html; g.scrollTop = 0; }
}
function odRenderEps() {
  $("od-grid").classList.add("hidden"); $("od-eps").classList.remove("hidden");
  const entries = odEps().map((e) => ({
    html: `<b class="num">E${e.episode}</b><span class="en">${esc(e.name || `Episode ${e.episode}`)}</span><small class="p">${esc(e.plot || "")}</small>`
      + (e.watched ? '<span class="badge done">WATCHED</span>'
         : e.position > RESUME_MIN && e.duration ? `<span class="badge left">${Math.max(1, Math.round((e.duration - e.position) / 60))} MIN LEFT</span>` : ""),
    right: fmtLen(e.duration),
  }));
  fillList($("od-eps"), entries, OD.zone === "eps" ? OD.ep : -1);
}
// move the highlight (no redraw of the posters) and show the details of what it's on
function odSelect() {
  odRenderHead();
  const side = OD.show ? OD.season : OD.cat, sideFocus = OD.zone === "cats" || OD.zone === "seasons";
  [...$("od-side").children].forEach((li, i) => { li.classList.toggle("cur", i === side); li.classList.toggle("focus", i === side && sideFocus); });
  const cur = $("od-side").children[side]; if (cur) cur.scrollIntoView({ block: "nearest" });
  if (OD.show) {
    [...$("od-eps").children].forEach((li, i) => li.classList.toggle("sel", OD.zone === "eps" && i === OD.ep));
    const li = $("od-eps").children[OD.ep]; if (li) li.scrollIntoView({ block: "nearest" });
  } else {
    const old = $("od-grid").querySelector(".od-tile.sel"); if (old) old.classList.remove("sel");
    const tile = $("od-grid").querySelector(`.od-tile[data-i="${OD.sel}"]`);
    if (tile) { if (OD.zone === "grid") tile.classList.add("sel"); tile.scrollIntoView({ block: "nearest" }); }
    // close to the end of what's loaded: fetch the next page
    if (OD.zone === "grid" && !OD.loading && OD.items.length < OD.total && OD.sel >= OD.items.length - odCols() * 3) odLoadItems(false, true);
  }
  odInfo();
  $("od-foot").innerHTML = OD.show
    ? "<span><b>OK</b> Play / Options</span><span><b>&#9664;&#9654;</b> Seasons / Episodes</span><span><b>PgUp/PgDn</b> Page</span><span><b>*</b> My List</span><span><b>Esc</b> Back</span>"
    : `<span><b>OK</b> Play / Options</span><span><b>&#9650;&#9660;&#9664;&#9654;</b> Tabs / Categories / Titles</span><span><b>PgUp/PgDn</b> Page</span><span><b>/</b> Search</span><span><b>*</b> My List</span><span><b>Esc</b> Back to TV</span>`;
}
const odCols = () => Math.max(1, getComputedStyle($("od-grid")).gridTemplateColumns.split(" ").filter(Boolean).length);
function odSetInfo(title, sub, meta, desc, cast) {
  $("odi-title").textContent = title || ""; $("odi-sub").textContent = sub || ""; $("odi-meta").textContent = meta || "";
  $("odi-desc").textContent = desc || ""; $("odi-cast").textContent = cast || "";
}
function odInfo() {
  clearTimeout(OD.infoTimer);
  const s = OD.show;
  if (s) {
    const e = odEps()[OD.ep], n = s.seasons.length;
    odSetInfo(s.name, e ? `Season ${e.season} · Episode ${e.episode}${e.name ? ` · ${e.name}` : ""}` : "",
      ["TV Show", odLabel(s), s.year, plural(n, "season"), s.genre || s.category, `from ${s.source_name}`].filter(Boolean).join(" · "),
      (e && e.plot) || s.plot,
      [e && fmtLen(e.duration), e && e.aired && `First aired ${e.aired}`, e && e.id === s.next && !e.watched && "Next up for you", !e && s.actors].filter(Boolean).join(" · "));
    return;
  }
  if (OD.zone === "tabs" && OD_TABS[OD.focus][0] === "search") {
    odSetInfo("Search On Demand", "Press OK and type a title, a year or an actor", "Movies and TV shows", "Accents don't matter, the words can be in any order, and small typos still find it.");
    return;
  }
  const t = odItem();
  if (!t) { odSetInfo(OD_TABS[OD.tab][1], "", "On Demand", ""); return; }
  odFill(t, OD.details.get(t.id));
  if (OD.details.has(t.id)) return;
  OD.infoTimer = setTimeout(async () => {       // plot, cast and length come with the first look at a title
    let d;
    try { d = await api(`/api/vod/title/${vodPath(t.id)}`); } catch (_) { return; }
    if (OD.details.size > 300) OD.details.clear();
    OD.details.set(t.id, d);
    if (odItem() === t && !OD.show && top() === "od") odFill(t, d);
  }, 400);
}
function odFill(t, d) {
  const x = d || t, movie = t.kind === "movie";
  let seen = "";
  if (t.sub) seen = `Stopped in ${t.sub}`;
  else if (d && movie && d.watched) seen = "Watched";
  else if (d && movie && d.position > RESUME_MIN) seen = `Watched ${Math.round(d.position / 60)} min${d.duration ? ` of ${Math.round(d.duration / 60)}` : ""}`;
  odSetInfo(x.name,
    [odLabel(x), x.year, movie ? fmtLen(x.duration) : d && d.seasons.length ? plural(d.seasons.length, "season") : "", odRating(x.rating), (d && d.genre) || x.category].filter(Boolean).join(" · "),
    [movie ? "Movie" : "TV Show", d && `from ${d.source_name}`, d && d.in_list && "In My List", seen].filter(Boolean).join(" · "),
    d ? d.plot : "", d ? [d.actors, d.director && `Directed by ${d.director}`].filter(Boolean).join(" · ") : "");
}

// ---- picking
async function odSetList(d, on) {
  try { await api("/api/vod/list", { method: "POST", body: { id: d.id, on } }); } catch (e) { toast(e.message); return; }
  d.in_list = on;
  toast(on ? `★ ${d.name} added to My List` : `${d.name} removed from My List`);
  odRefresh();
}
async function odMark(id, watched) {
  closeDialog();
  try { await api("/api/vod/watched", { method: "POST", body: { id, watched } }); } catch (e) { toast(e.message); return; }
  toast(watched ? "Marked as watched" : "Marked as not watched");
  odRefresh();
}
const odListEntry = (d) => ({ label: d.in_list ? "Remove from My List" : "★ Add to My List", act: () => { closeDialog(); odSetList(d, !d.in_list); } });
function odMovieDialog(d) {
  const resume = !d.watched && d.position > RESUME_MIN;
  const here = top() === "od" && !OD.show && OD.tab === 0 && OD.cats[OD.cat] && OD.cats[OD.cat].key === d.category;
  const entries = [];
  if (resume) entries.push({ label: `Resume from ${fmtDur(d.position)}`, act: () => playVod(d.id, d.position) });
  entries.push({ label: resume ? "Play from the beginning" : "Play", act: () => playVod(d.id, 0) });
  entries.push(odListEntry(d));
  entries.push({ label: d.watched ? "Mark as not watched" : "Mark as watched", act: () => odMark(d.id, !d.watched) });
  const others = (d.versions || []).filter((v) => !v.current);
  if (others.length) entries.push({ label: `Other versions (${others.map((v) => v.tag || "no label").join(", ")})`, act: () => odVersions(d) });
  if (d.category && !here) entries.push({ label: `More in ${d.category}`, act: () => { closeDialog(); odGoCategory(d.category); } });
  entries.push({ label: "Close", act: closeDialog });
  openDialog(d.name, `${["Movie", odLabel(d), d.year, fmtLen(d.duration), d.genre || d.category].filter(Boolean).join(" · ")}\nFrom ${d.source_name}`
    + (resume ? ` · You stopped at ${fmtDur(d.position)}` : d.watched ? " · Watched" : ""), entries);
}
function odEpisodeDialog(show, e) {
  const resume = !e.watched && e.position > RESUME_MIN;
  const entries = [];
  if (resume) entries.push({ label: `Resume from ${fmtDur(e.position)}`, act: () => playVod(e.id, e.position) });
  entries.push({ label: resume ? "Play from the beginning" : "Play", act: () => playVod(e.id, 0) });
  entries.push({ label: e.watched ? "Mark as not watched" : "Mark as watched", act: () => odMark(e.id, !e.watched) });
  entries.push(odListEntry(show));
  entries.push({ label: "Close", act: closeDialog });
  openDialog(`${show.name} · S${e.season} E${e.episode}`,
    [e.name, fmtLen(e.duration), resume ? `You stopped at ${fmtDur(e.position)}` : e.watched ? "Watched" : ""].filter(Boolean).join(" · "), entries);
}
// the same film or show under the playlist's other labels (languages, 4K…)
function odVersions(d) {
  const go = (v) => { closeDialog(); if (d.kind === "series") { closePanels(); openOD("series", v.id); } else odMovie(v.id); };
  openDialog(`${d.name}${d.year ? ` (${d.year})` : ""}`, "Pick a version", [
    ...(d.versions || []).map((v, i, all) => {          // a provider can list the same label twice: number them
      const same = all.filter((x) => x.tag === v.tag), n = same.length > 1 ? ` (${same.indexOf(v) + 1})` : "";
      return { label: `${v.tag || "No label"}${n}${v.current ? " · this one" : ""}`, act: () => go(v) };
    }),
    { label: "Close", act: closeDialog }]);
}
// OK on a movie (from the grid or from Search): its options
async function odMovie(id) {
  let d;
  try { d = await api(`/api/vod/title/${vodPath(id)}`); } catch (e) { toast(e.message); return; }
  OD.details.set(id, d);
  odMovieDialog(d);
}
async function odPick(t) {
  if (!t) return;
  if (t.kind !== "series") { await odMovie(t.id); return; }
  if (t.resume) {                           // Continue Watching: straight back into the episode
    openDialog(t.name, t.sub || "", [
      { label: `Resume ${t.sub || "episode"}`, act: () => playVod(t.resume) },
      { label: "All episodes", act: () => { closeDialog(); odOpenShow(t.id); } },
      { label: "Close", act: closeDialog }]);
    return;
  }
  await odOpenShow(t.id);
}
async function odGoCategory(name) {
  await openOD("movie");
  if (!S.layer.includes("od")) return;
  OD.show = null; OD.tab = OD.focus = 0; OD.zone = "cats";
  await odLoadTab();
  const i = OD.cats.findIndex((c) => c.key === name);
  if (i < 0) return;
  OD.cat = i; OD.zone = "grid";
  await odLoadItems();
}
async function odStar() {
  const id = OD.show ? OD.show.id : (odItem() || {}).id;
  if (!id) return;
  let d = OD.show || OD.details.get(id);
  if (!d) { try { d = await api(`/api/vod/title/${vodPath(id)}`); } catch (e) { toast(e.message); return; } }
  odSetList(d, !d.in_list);
}

// ---- buttons
// Movies / TV Shows / Continue Watching / My List switch when the highlight rests on them (passing over them on the
// way doesn't load each one); Search acts on OK
function odFocusTab(i) {
  OD.focus = (i + OD_TABS.length) % OD_TABS.length;
  odSelect();
  clearTimeout(OD.tabTimer);
  if (OD.focus < OD_LISTS && OD.focus !== OD.tab) {
    OD.tabTimer = setTimeout(() => { if (OD.zone === "tabs" && OD.focus < OD_LISTS && OD.focus !== OD.tab) odUseTab(OD.focus); }, 450);
  }
}
function odUseTab(i) {
  OD.tab = i; OD.show = null;
  odLoadTab();
}
function odActTab(i) {
  clearTimeout(OD.tabTimer);
  const id = OD_TABS[i][0];
  if (id === "search") { odSearch(); return; }
  OD.focus = i;
  if (i !== OD.tab) { odUseTab(i); OD.zone = odHasCats() ? "cats" : "grid"; }
}
function odSetCat(i) {
  i = Math.max(0, Math.min(OD.cats.length - 1, i));
  if (i === OD.cat) return;
  OD.cat = i; OD.sel = 0; OD.seq++; odSelect();
  clearTimeout(OD.catTimer); OD.catTimer = setTimeout(() => odLoadItems(), 220);     // flicking past categories doesn't load each one
}
function odKey(k) {
  switch (k) {
    case "o": closeOD(); return;
    case "/": odSearch(); return;
    case "m": closeOD(); openMenu(); return;
    case "g": closeOD(); G.open(); return;
    case "d": closeOD(); openDvr(); return;
    case "t": closeOD(); openSports(); return;
    case "*": odStar(); return;
    case "u": toggleMute(); return;
    case "VolUp": setVolume(video.volume + 0.05); return;
    case "VolDown": setVolume(video.volume - 0.05); return;
    case "f": toggleFull(); return;
  }
  if (OD.show) { odShowKey(k); return; }
  if (k === "Back") { if (OD.zone === "tabs" && OD.focus !== OD.tab) { OD.focus = OD.tab; odSelect(); } else closeOD(); return; }
  const n = OD.items.length;
  if (OD.zone === "tabs") {
    if (k === "ArrowLeft") odFocusTab(OD.focus - 1);
    else if (k === "ArrowRight") odFocusTab(OD.focus + 1);
    else if (k === "Enter" && OD.focus >= OD_LISTS) odActTab(OD.focus);
    else if ((k === "ArrowDown" || k === "Enter") && OD.focus < OD_LISTS && OD.focus !== OD.tab) { clearTimeout(OD.tabTimer); odActTab(OD.focus); }
    else if (k === "ArrowDown" || k === "Enter") {
      OD.focus = OD.tab;
      if (odHasCats() && OD.cats.length) OD.zone = "cats"; else if (n) OD.zone = "grid";
      odSelect();
    }
    return;
  }
  if (OD.zone === "cats") {
    if (k === "ArrowUp") { if (OD.cat === 0) { OD.zone = "tabs"; OD.focus = OD.tab; odSelect(); } else odSetCat(OD.cat - 1); }
    else if (k === "ArrowDown") odSetCat(OD.cat + 1);
    else if (k === "ChannelUp") odSetCat(OD.cat - 8);
    else if (k === "ChannelDown") odSetCat(OD.cat + 8);
    else if ((k === "ArrowRight" || k === "Enter") && n) { OD.zone = "grid"; odSelect(); }
    return;
  }
  const cols = odCols(), col = OD.sel % cols;
  let to = OD.sel;
  if (k === "Enter") { odPick(odItem()); return; }
  if (k === "ArrowLeft") { if (col === 0) { if (odHasCats()) { OD.zone = "cats"; odSelect(); } return; } to--; }
  else if (k === "ArrowRight") { if (col === cols - 1 || to + 1 >= n) return; to++; }
  else if (k === "ArrowUp") { if (to - cols < 0) { OD.zone = "tabs"; OD.focus = OD.tab; odSelect(); return; } to -= cols; }
  else if (k === "ArrowDown") { if (Math.floor(to / cols) === Math.floor((n - 1) / cols)) return; to = Math.min(n - 1, to + cols); }
  else if (k === "ChannelUp") to = Math.max(col, to - cols * 2);
  else if (k === "ChannelDown") to = Math.min(n - 1, to + cols * 2);
  else return;
  OD.sel = to; odSelect();
}
function odShowKey(k) {
  const eps = odEps(), ns = OD.show.seasons.length;
  if (k === "Back") { odCloseShow(); return; }
  if (OD.zone !== "eps") {
    const move = (to) => { to = Math.max(0, Math.min(ns - 1, to)); if (to !== OD.season) { OD.season = to; OD.ep = 0; odRenderEps(); odSelect(); } };
    if (k === "ArrowUp") move(OD.season - 1);
    else if (k === "ArrowDown") move(OD.season + 1);
    else if (k === "ChannelUp") move(OD.season - 8);
    else if (k === "ChannelDown") move(OD.season + 8);
    else if ((k === "ArrowRight" || k === "Enter") && eps.length) { OD.zone = "eps"; odSelect(); }
    return;
  }
  if (k === "Enter") { if (eps[OD.ep]) odEpisodeDialog(OD.show, eps[OD.ep]); return; }
  if (k === "ArrowLeft") OD.zone = "seasons";
  else if (k === "ArrowUp") OD.ep = Math.max(0, OD.ep - 1);
  else if (k === "ArrowDown") OD.ep = Math.min(eps.length - 1, OD.ep + 1);
  else if (k === "ChannelUp") OD.ep = Math.max(0, OD.ep - 8);
  else if (k === "ChannelDown") OD.ep = Math.min(eps.length - 1, OD.ep + 8);
  else return;
  odSelect();
}
// mouse / touch
$("od-tabs").addEventListener("click", (e) => { const t = e.target.closest(".od-tab"); if (t) odActTab(+t.dataset.t); });
$("od-side").addEventListener("click", (e) => {
  const li = e.target.closest("li"); if (!li) return;
  if (OD.show) { OD.zone = "seasons"; if (+li.dataset.i !== OD.season) { OD.season = +li.dataset.i; OD.ep = 0; odRenderEps(); } odSelect(); }
  else { OD.zone = "cats"; if (+li.dataset.i === OD.cat) odSelect(); else odSetCat(+li.dataset.i); }
});
$("od-grid").addEventListener("click", (e) => {
  const t = e.target.closest(".od-tile"); if (!t) return;
  OD.zone = "grid"; OD.sel = +t.dataset.i; odSelect(); odPick(odItem());
});
$("od-eps").addEventListener("click", (e) => {
  const li = e.target.closest("li"); if (!li || !OD.show) return;
  OD.zone = "eps"; OD.ep = +li.dataset.i; odSelect();
  const ep = odEps()[OD.ep]; if (ep) odEpisodeDialog(OD.show, ep);
});
$("od-grid").addEventListener("error", (e) => {      // no poster after all: show the name instead
  if (e.target.tagName === "IMG") { e.target.parentElement.classList.remove("art"); e.target.remove(); }
}, true);
$("od-grid").addEventListener("scroll", () => {
  const g = $("od-grid");
  if (!OD.show && !OD.loading && OD.items.length < OD.total && g.scrollTop + g.clientHeight > g.scrollHeight - g.clientHeight) odLoadItems(false, true);
}, { passive: true });

// ---------------------------------------------------------------- playing a movie or episode
// S.vod = what's playing. The server streams it from a start time (v.base) like a live channel, so the
// position is v.base + the player's own clock, and skipping asks for a new stream from the new time.
function vodPos() {
  const v = S.vod; if (!v) return 0;
  if (v.target !== null) return v.target;
  return v.parked ? v.base : v.base + (video.currentTime || 0);
}
function vodSave() {
  const v = S.vod; if (!v || v.done) return;
  const pos = vodPos();
  if (pos < 5) return;
  fetch("/api/vod/progress", { method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
                               body: JSON.stringify({ id: v.it.id, position: pos, duration: v.dur }) }).catch(() => {});
}
// something else is about to play (a channel, a recording, power off): remember the spot and let go
function vodLeave() {
  const v = S.vod; if (!v) return;
  vodSave();
  clearTimeout(v.parkTimer); clearTimeout(v.seekTimer); clearTimeout(v.endTimer);
  S.vod = null;
  $("playback").classList.add("hidden");
}
async function playVod(id, start, back) {
  if (back === undefined) back = S.layer.includes("od");      // go back to On Demand afterwards
  closeDialog();
  toast("Loading…", 6000);
  let it;
  try { it = await api(`/api/vod/play/${vodPath(id)}`); } catch (e) { toast(`Can't play this: ${e.message}`, 4000); return; }
  if (start === undefined) start = it.position > RESUME_MIN ? it.position : 0;
  closeOD(); closePanels(); closeMenu(); closeDvr();
  if (top() === "guide") G.close();
  vodLeave();
  S.rec = null; S.mode = "vod"; hideBanner();
  S.vod = { it, dur: it.duration || 0, base: start, back, retries: 0, target: null, parked: false, paused: false, done: false };
  toast(start > 0 ? `Resuming from ${fmtDur(start)}` : it.title, 2500);
  vodStart(start);
}
function vodStart(at) {
  const v = S.vod; if (!v) return;
  clearTimeout(v.parkTimer); clearTimeout(v.seekTimer); clearTimeout(v.endTimer);
  at = Math.max(0, v.dur ? Math.min(at, v.dur - 5) : at);
  v.base = at; v.target = null; v.parked = false; v.paused = false;
  destroyPlayer(); hideStatic();
  const url = (hls) => `${location.origin}/api/vod/${hls ? "hls" : "stream"}/${vodPath(v.it.id)}?ss=${at.toFixed(1)}&client=${CID}`;
  const fail = () => { if (S.vod === v) vodDropped(); };
  const started = (pr, mine) => { if (pr && pr.catch) pr.catch((e) => { if (e && e.name === "NotAllowedError" && mine()) showTapPlay(); }); };
  if (useHls()) {
    const token = {};
    S.player = { destroy() {}, token };
    video.onerror = () => { if (S.player && S.player.token === token) fail(); };
    video.src = url(true);
    started(video.play(), () => S.player && S.player.token === token);
  } else {
    if (!window.mpegts || !mpegts.getFeatureList().mseLivePlayback) { showStatic("", "Playback not supported", "This browser can't play this video"); return; }
    const p = mpegts.createPlayer({ type: "mpegts", isLive: true, url: url(false) },
      { enableWorker: true, lazyLoad: false, enableStashBuffer: true, stashInitialSize: 256 * 1024,
        autoCleanupSourceBuffer: true, liveBufferLatencyChasing: false });
    S.player = p;
    p.attachMediaElement(video);
    p.on(mpegts.Events.ERROR, (type, detail) => { if (S.player === p) { console.warn("player error", type, detail); fail(); } });
    // the stream ended: the end of the title, or the provider dropped the connection (vodEnded tells which)
    p.on(mpegts.Events.LOADING_COMPLETE, () => {
      if (S.player !== p) return;
      v.endTimer = setTimeout(() => { if (S.player === p && S.vod === v) vodEnded(); }, (bufferedAhead() + 1) * 1000);
    });
    p.load();
    started(p.play(), () => S.player === p);
  }
  S.watchdog = setTimeout(() => { if (S.vod === v && !v.parked && video.readyState < 3) fail(); }, 40000);
  showVodBar();
}
function vodDropped() {
  const v = S.vod; if (!v) return;
  const pos = vodPos();
  if (v.retries < 3) {
    v.retries++; toast("Reconnecting…");
    v.base = pos; v.parked = true; destroyPlayer();
    v.seekTimer = setTimeout(() => { if (S.vod === v) vodStart(pos); }, 1500);
  } else {
    v.base = pos; v.parked = true; destroyPlayer();
    showStatic("", "Can't play this title", `${v.it.title}: no video from the provider. Press Back.`);
  }
}
function vodEnded() {
  const v = S.vod; if (!v || v.done) return;
  const pos = vodPos();
  if (v.dur && pos < v.dur - 120) { vodDropped(); return; }      // stopped well short of the end: not the end
  vodFinish();
}
function vodFinish() {
  const v = S.vod; if (!v || v.done) return;
  v.done = true; v.base = v.dur || vodPos(); v.parked = true;
  api("/api/vod/watched", { method: "POST", body: { id: v.it.id, watched: true } }).catch(() => {});
  destroyPlayer(); $("playback").classList.add("hidden");
  const nx = v.it.next;
  if (!nx) { vodStop(); return; }
  openDialog(`Finished: ${v.it.title}`, v.it.sub || "", [
    { label: `Play next: ${nx.label}`, act: () => playVod(nx.id, 0, v.back) },
    { label: v.back ? "Back to the episodes" : "Back to TV", act: () => { closeDialog(); vodStop(); } }]);
}
// Back / Stop: return to the channel you were on, and to the On Demand screen if you started there
function vodStop() {
  const v = S.vod; if (!v) return;
  const back = v.back;
  vodLeave(); hideStatic();
  if (S.channels.length) tune(Math.max(0, S.idx));
  else { destroyPlayer(); S.mode = "empty"; showStatic("", "No channels yet", "Press OK (Enter) or click here to add a playlist"); }
  if (back) openOD();
}
function vodPause() {
  const v = S.vod; if (!v || v.done) return;
  if (v.paused) {
    v.paused = false; clearTimeout(v.parkTimer);
    if (v.parked) { vodStart(v.base); return; }
    video.play().catch(() => {});
  } else {
    v.paused = true; video.pause(); vodSave();
    // a long pause lets go of the provider's connection; Play picks up from the same spot
    v.parkTimer = setTimeout(() => { if (S.vod === v && v.paused) { v.base = vodPos(); v.parked = true; destroyPlayer(); showVodBar(); } }, 60000);
  }
  showVodBar();
}
function vodSeek(delta) {
  const v = S.vod; if (!v || v.done) return;
  let t = Math.max(0, vodPos() + delta);
  if (v.dur) t = Math.min(t, Math.max(0, v.dur - 10));
  v.target = t;
  showVodBar();
  clearTimeout(v.seekTimer); v.seekTimer = setTimeout(() => { if (S.vod === v) vodStart(t); }, 600);   // several presses = one jump
}
let vodBarTimer;
function drawVodBar() {
  const v = S.vod; if (!v) return;
  const pos = vodPos();
  $("pb-title").textContent = v.it.title + (v.it.sub ? ` · ${v.it.sub}` : "");
  $("pb-progress").style.width = v.dur ? `${Math.min(100, pos / v.dur * 100)}%` : "0";
  $("pb-time").textContent = `${fmtDur(pos)}${v.dur ? ` / ${fmtDur(v.dur)}` : ""}${v.target !== null ? "   Skipping…" : v.paused ? "   ❚❚ Paused" : v.parked || video.readyState < 3 ? "   Loading…" : ""}`
    + "      ◀ 10s   ▶ 30s   OK Pause   Back Stop";
}
function showVodBar() {
  hideBanner(); drawVodBar();
  $("playback").classList.remove("hidden");
  clearTimeout(vodBarTimer);
  vodBarTimer = setTimeout(() => { const v = S.vod; if (!v || !v.paused) $("playback").classList.add("hidden"); }, 5000);     // stays up while paused
}
let vodTicks = 0;
setInterval(() => {
  const v = S.vod; if (!v || S.mode !== "vod" || v.done) return;
  if (!$("playback").classList.contains("hidden")) drawVodBar();
  if (v.parked || video.paused || v.target !== null) return;
  if (++vodTicks % 15 === 0) { vodSave(); if (video.readyState >= 3) v.retries = 0; }
  if (v.dur && vodPos() >= v.dur - 1) vodFinish();        // also the only end signal on iPad/iPhone
}, 1000);
window.addEventListener("pagehide", vodSave);
function vodKey(k) {
  switch (k) {
    case " ": case "Enter": vodPause(); break;
    case "ArrowLeft": vodSeek(-10); break;
    case "ArrowRight": vodSeek(30); break;
    case "ChannelUp": vodSeek(-300); break;
    case "ChannelDown": vodSeek(300); break;
    case "Back": case "s": vodStop(); break;
    case "i": if ($("playback").classList.contains("hidden")) showVodBar(); else $("playback").classList.add("hidden"); break;
    case "o": openOD(); break;
    case "g": G.open(); break;
    case "v": G.open(true); break;
    case "/": openSearch(); break;
    case "t": openSports(); break;
    case "d": openDvr(); break;
    case "m": openMenu(); break;
    case "u": toggleMute(); break;
    case "VolUp": setVolume(video.volume + 0.05); break;
    case "VolDown": setVolume(video.volume - 0.05); break;
    case "f": toggleFull(); break;
    default: return false;
  }
  return true;
}

// ---------------------------------------------------------------- On Demand titles in Search
function vodSearchItem(v) {
  const len = fmtLen(v.duration);
  const right = `On Demand${v.kind !== "series" && len ? ` · ${len}` : ""}`;
  const many = v.versions && v.versions.length > 1;
  const sub = esc([odLabel(v), v.year, many ? `${v.versions.length} versions` : v.category].filter(Boolean).join(" · "));
  if (v.kind === "movie") {
    return { html: `<span class="badge movie">MOVIE</span> <span>${esc(v.name)} <small>${sub}</small></span>`, right, act: () => odMovie(v.id) };
  }
  if (v.kind === "series") {
    return { html: `<span class="badge show">SHOW</span> <span>${esc(v.name)} <small>${sub}</small></span>`, right,
             act: () => (many ? odVersions({ ...v, versions: v.versions.map((x, i) => ({ ...x, current: !i })) })
                              : (closePanels(), openOD("series", v.id))) };
  }
  return { html: `<span class="badge show">EPISODE</span> <span>${esc(v.name)} <small>· ${esc(v.sub)}</small></span>`, right,
           act: () => openDialog(v.name, v.sub, [
             { label: "Play", act: () => playVod(v.id) },
             { label: "All episodes", act: () => { closeDialog(); closePanels(); openOD("series", v.title_id); } },
             { label: "Close", act: closeDialog }]) };
}
