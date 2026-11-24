// ---------------------------------------------------------------- On Demand (movies and shows from the playlists)
// One screen, laid out like the guide: live TV keeps playing in the corner, the highlighted title's details
// beside it, then tabs, a category column and a poster grid. The Movies and TV Shows tabs each have their
// categories; Continue Watching, My List and Search cover both. A TV show opens in the same frame with its
// seasons on the left and episodes on the right (OD.show).
const OD_TABS = [["movie", "Movies"], ["series", "TV Shows"], ["continue", "Continue Watching"], ["list", "My List"], ["search", "Search"]];
const OD_SHORT = { series: "Shows", continue: "Continue" };
const OD_LISTS = 4;                     // tabs before this one show a list; Search acts on OK
const OD_KIND = { movie: "Movies", series: "TV Shows" };
const OD_PAGE = 160;
const OD = { tab: 0, focus: 0, zone: "cats",      // zone: tabs | cats | grid, and seasons | eps inside a show
             counts: {}, cats: [], cat: 0, items: [], total: 0, sel: 0, seq: 0, loaded: false, loading: false,
             show: null, season: 0, ep: 0, details: new Map(), infoTimer: null, catTimer: null, tabTimer: null };
const RESUME_MIN = 30;                  // seconds in before "Resume" is offered (same as the server)
const fmtLen = (s) => { s = Math.round((s || 0) / 60); return !s ? "" : s < 60 ? `${s} min` : `${s / 60 | 0}h ${s % 60}m`; };
const vodPath = (id) => encodeURIComponent(id);
const fmtRate = (k) => !k ? "" : k >= 1000 ? `${(k / 1000).toFixed(1)} Mbps` : `${k} kbps`;   // the provider's bitrate, kbit/s
// a version's file: its type and size. Your own files' size is known; a provider's is its bitrate × length ("≈")
const fmtFile = (v) => {
  const bytes = v.size || (v.bitrate && v.duration ? v.bitrate * 125 * v.duration : 0);
  const size = !bytes ? "" : `${v.size ? "" : "≈"}${bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`}`;
  return [(v.ext || "").toUpperCase(), size].filter(Boolean).join(" ");
};
// a version's picture and sound as ffmpeg read them (played once, or a library file): "4K HEVC Dolby Vision · 5.1 DD+".
// By width: a widescreen film is 3840×1600 or 1920×800, still 4K and 1080p
const fmtAv = (av) => {
  if (!av) return "";
  const res = av.w >= 3200 ? "4K" : av.w >= 1800 ? "1080p" : av.w >= 1200 ? "720p" : av.w ? "SD" : "";
  const vc = { hevc: "HEVC", h264: "H.264", av1: "AV1", vp9: "VP9", mpeg2video: "MPEG-2", mpeg4: "MPEG-4" }[av.vcodec] || "";
  const ac = { eac3: "DD+", ac3: "DD", truehd: "TrueHD", dts: "DTS", aac: "AAC", mp3: "MP3", opus: "Opus", flac: "FLAC" }[av.acodec] || "";
  return [[res, vc, av.hdr].filter(Boolean).join(" "), [av.layout, ac].filter(Boolean).join(" ")].filter(Boolean).join(" · ");
};
// Pick a version: one row per version. Its name with small tags (your copy, the one you opened), what's known of
// it underneath, and the resolution and HDR as badges on the right (dashed: only what the file name says)
// a version's name: its label ("EN", "4K-AMZ"), else "Your copy" for one of your own files, else where it is
const pvName = (v, none = "No label") => v.tag || (v.mine ? "Your copy" : v.where) || none;
// a version's own progress (the server sends it per version): { text, pct, watched }, or null when it has none
function pvProgress(v) {
  if (v.watched) return { text: "Watched", pct: 0, watched: true };
  if (!(v.position > RESUME_MIN)) return null;
  const dur = v.duration || v.pdur;
  return { text: `Stopped at ${fmtDur(v.position)}`, pct: dur ? Math.min(1, v.position / dur) : 0, watched: false };
}
function pvRow(v, label, lenDiffers) {
  const av = v.av || v.hint, guess = !v.av && !!v.hint;
  const res = !av ? "" : av.w >= 3200 ? "4K" : av.w >= 1800 ? "1080p" : av.w >= 1200 ? "720p" : av.w ? "SD" : "";
  const vc = av && ({ hevc: "HEVC", h264: "H.264", av1: "AV1", vp9: "VP9", mpeg2video: "MPEG-2", mpeg4: "MPEG-4" }[av.vcodec] || "");
  const ac = av && ({ eac3: "DD+", ac3: "DD", truehd: "TrueHD", dts: "DTS", aac: "AAC", mp3: "MP3", opus: "Opus", flac: "FLAC" }[av.acodec] || "");
  const meta = [av && [vc, av.bits >= 10 && !av.hdr ? `${av.bits}-bit` : ""].filter(Boolean).join(" "),
    av && [av.layout === "stereo" ? "Stereo" : av.layout === "mono" ? "Mono" : av.layout, ac].filter(Boolean).join(" "),
    fmtRate(v.bitrate), v.len_unknown ? "length unknown" : lenDiffers && fmtLen(v.duration), fmtFile(v),
    v.where && `from ${v.where}`].filter(Boolean);
  // "Your copy" as a tag only when the name is a label (otherwise the name already says it)
  const prog = pvProgress(v);
  const tags = (v.mine && v.tag ? '<span class="pv-tag">Your copy</span>' : "") + (v.current ? '<span class="pv-tag now">Current</span>' : "")
    + (prog ? `<span class="pv-tag ${prog.watched ? "seen" : "prog"}">${esc(prog.text)}</span>` : "");
  // where this version was stopped: a bar along the row's foot
  const bar = prog && prog.pct > 0 ? `<span class="pv-bar"><i style="width:${(prog.pct * 100).toFixed(1)}%"></i></span>` : "";
  const badge = (text, cls) => `<span class="pv-b ${cls}${guess ? " guess" : ""}">${esc(text)}</span>`;
  const badges = (res ? badge(res, res === "4K" ? "k4" : "") : "") + (av && av.hdr ? badge(av.hdr, "hdr") : "");
  return `<div class="pv-main"><div class="pv-name"><span class="pv-label">${esc(label)}</span>${tags}</div>`
    + `<div class="pv-meta">${esc(meta.join("  ·  ")) || (v.checking ? "" : esc(v.why || "No details yet"))}`
    + `${v.checking ? `<span class="pv-reading">${meta.length ? " · " : ""}Reading…</span>` : ""}`
    + `${!v.checking && v.why && meta.length ? `<span class="pv-why"> · ${esc(v.why)}</span>` : ""}${guess ? `<span class="pv-guess"> · ${v.mine ? "from the file name" : "from its label"}</span>` : ""}</div></div>`
    + (badges ? `<div class="pv-badges">${badges}</div>` : "") + bar;
}
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
  hideBanner(); closeMenu(); closeDvr(); closeSearch(); closeSports(); $("playback").classList.add("hidden");
  if (top() === "guide") G.close();
  if (top() === "home") homeCover(); else closeHome();     // from Home: Back comes back to it
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
  if (top() === "home") homeReveal();
  else if (top() !== "guide") video.classList.remove("pip");
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
               ...(d.lib_new ? [{ key: "*libnew", name: "New in My Library", count: d.lib_new }] : []),
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
  if (S.layer.includes("home")) homeLoad();        // Home's Continue Watching row too
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
    const short = OD_SHORT[id];         // phones: "Continue" in place of "Continue Watching", so the row fits
    const name = short ? `<span class="od-tl">${label}</span><span class="od-ts">${short}</span>` : label;
    const text = `${name}${n[id] !== undefined ? ` <small>${(n[id] || 0).toLocaleString()}</small>` : ""}`;
    return `<div class="od-tab${i === OD.tab ? " sel" : ""}${i === OD.focus && OD.zone === "tabs" ? " focus" : ""}" data-t="${i}">${text}</div>`;
  }).join("");
  // a narrow screen scrolls the tabs sideways: keep the current one in view
  const cur = $("od-tabs").querySelector(".od-tab.focus") || $("od-tabs").querySelector(".od-tab.sel");
  if (cur && $("od-tabs").scrollWidth > $("od-tabs").clientWidth) cur.scrollIntoView({ block: "nearest", inline: "nearest" });
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
  + (t.poster ? `<img loading="lazy" alt="" referrerpolicy="no-referrer" src="${esc(safePic(t.poster))}">` : "")
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
      [e && fmtLen(e.duration), e && fmtAv(e.av), e && e.av && fmtRate(e.av.kbps), e && fmtFile(e), e && e.aired && `First aired ${e.aired}`,
       e && e.id === s.next && !e.watched && "Next up for you", !e && s.actors].filter(Boolean).join(" · "));
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
// a movie's picture, sound, bitrate and file, as far as they're known: "4K HEVC HDR10 · 5.1 DD+ · 16.4 Mbps · MKV 42.1 GB"
// (not read yet: what its file name or label says, e.g. "1080p H.264")
const odStats = (d) => [fmtAv(d.av || d.hint), fmtRate(d.bitrate), fmtFile(d)].filter(Boolean).join(" · ");
function odFill(t, d) {
  const x = d || t, movie = t.kind === "movie";
  let seen = "";
  if (t.upnext) seen = `Up next: ${t.ep}`;
  else if (t.sub) seen = `Stopped in ${t.sub}`;
  else if (d && movie && d.watched) seen = "Watched";
  else if (d && movie && d.position > RESUME_MIN) seen = `Watched ${Math.round(d.position / 60)} min${d.duration ? ` of ${Math.round(d.duration / 60)}` : ""}`;
  odSetInfo(x.name,
    [odLabel(x), x.year, movie ? fmtLen(x.duration) : d && d.seasons.length ? plural(d.seasons.length, "season") : "", odRating(x.rating), (d && d.genre) || x.category].filter(Boolean).join(" · "),
    [movie ? "Movie" : "TV Show", d && movie && odStats(d), d && `from ${d.source_name}`, d && d.in_list && "In My List", seen].filter(Boolean).join(" · "),
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
// off Continue Watching without marking it watched (a show: every episode part-way through)
async function odForget(id) {
  closeDialog();
  try { await api("/api/vod/forget", { method: "POST", body: { id } }); } catch (e) { toast(e.message); return; }
  toast("Removed from Continue Watching");
  odRefresh();
}
const odForgetEntry = (id) => ({ label: "Remove from Continue Watching", act: () => odForget(id) });
// a show in Continue Watching (On Demand or Home): straight back into the episode
function odResumeDialog(t, allEpisodes) {
  // an Up next tile: you finished the episode before it, so this one plays from the start
  const ep = t.ep || t.sub || "episode";
  openDialog(t.name, t.sub || "", [
    { label: `${t.upnext ? "Play" : "Resume"} ${ep}`, act: () => playVod(t.resume) },
    { label: "All episodes", act: () => { closeDialog(); allEpisodes(); } },
    { label: `Mark ${ep} as watched`, act: () => odMark(t.resume, true) },
    odForgetEntry(t.id),
    { label: "Close", act: closeDialog }]);
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
  if (resume) entries.push(odForgetEntry(d.id));
  const others = (d.versions || []).filter((v) => !v.current);
  if (others.length) entries.push({ label: `Other versions (${others.map((v) => pvName(v, "no label")).join(", ")})`, act: () => odVersions(d) });
  if (d.category && !here) entries.push({ label: `More in ${d.category}`, act: () => { closeDialog(); odGoCategory(d.category); } });
  entries.push({ label: "Close", act: closeDialog });
  // not started in this copy, but in another one: say which (the most recent)
  const other = resume || d.watched ? null : others.filter((v) => !v.watched && v.position > RESUME_MIN)
    .sort((a, b) => (b.stopped || 0) - (a.stopped || 0))[0];
  openDialog(d.name, `${["Movie", odLabel(d), d.year, fmtLen(d.duration), fmtAv(d.av || d.hint), fmtRate(d.bitrate), d.genre || d.category].filter(Boolean).join(" · ")}\nFrom ${d.source_name}`
    + (resume ? ` · You stopped at ${fmtDur(d.position)}` : d.watched ? " · Watched"
       : other ? ` · You stopped ${pvName(other, "another version")} at ${fmtDur(other.position)}` : ""), entries);
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
// where `at` seconds into a copy `from` long falls in a copy `to` long. Only a 24 fps film against a 25 fps
// (PAL) copy of it is scaled: the same frames run 4% faster, so lengths differ by 24/25. Any other difference
// (another cut, credits left off) keeps the seconds as they are.
function odSamePoint(at, from, to) {
  const r = from > 0 && to > 0 ? to / from : 1;
  return (r >= 0.95 && r <= 0.97) || (r >= 1 / 0.97 && r <= 1 / 0.95) ? Math.round(at * r) : at;
}
// Pick a version needs a connection that this screen's own playback holds (live TV in the corner, or a movie):
// let go of it for the read. -> a function that picks up again where it was, or null when there's nothing to let
// go of here (an event feed: no channel to go back to). Picking up never overrides something started meanwhile.
function pvHold() {
  const v = S.vod;
  if (S.mode === "vod" && v && !v.done) {
    const wasPaused = v.paused;
    clearTimeout(v.parkTimer);
    if (!v.parked) { v.base = vodPos(); v.parked = true; }
    v.paused = true; vodSave(); destroyPlayer(); showVodBar();
    return () => {
      if (S.vod !== v || !v.parked) return;
      v.paused = wasPaused;
      if (wasPaused) showVodBar(); else vodStart(v.base);
    };
  }
  if (S.mode === "live" && !S.feedCh && S.idx >= 0 && S.player) {
    const idx = S.idx, ch = S.channels[idx];
    destroyPlayer();
    showStatic(ch ? ch.number : "", "Paused", "Reading the versions' details. It comes back on by itself.");
    return () => { if (S.mode === "live" && S.idx === idx && !S.feedCh && !S.player) { hideStatic(); tune(idx); } };
  }
  return null;
}
// the same film or show under the playlist's other labels (languages, 4K…)
function odVersions(d) {
  // a movie: straight into it, from where this version was stopped (else wherever that version was left)
  let at = d.kind !== "series" && !d.watched && d.position > RESUME_MIN ? d.position : undefined;
  const here = () => (d.versions || []).find((x) => x.current);
  // where the version you were watching stopped, as the same point in v (-1: none, or v is that version)
  const carry = (v) => {
    if (v.current || at === undefined) return -1;
    const h = here();
    return odSamePoint(at, (h && h.duration) || d.duration, v.duration);
  };
  // A version picked that was started (here, or in the one you were watching) or watched: ask where it
  // starts. Its row stays on top (not selectable), the choices under it; Back goes back to the versions.
  let ask = null, note = "Pick a version · checking each one…";
  const setSub = (t) => { if (t) note = t; $("dlg-sub").textContent = ask ? "Where do you want to start?" : note; };
  const go = (v, label) => {
    if (d.kind === "series") { closeDialog(); closePanels(); openOD("series", v.id); return; }
    if (pvProgress(v) || carry(v) >= 0) {
      ask = { v, label }; dlg.back = askEnd; setSub();
      redraw(d.versions || []);
      return;
    }
    playVod(v.id, 0);
  };
  function askEnd() {
    const id = ask && ask.v.id;
    ask = null; dlg.back = null; setSub();
    redraw(d.versions || [], id);
  }
  // "Mark this copy as not watched": only this version's progress goes, back to the versions
  async function unwatch(v) {
    try { await api("/api/vod/watched", { method: "POST", body: { id: v.id, watched: false } }); } catch (e) { toast(e.message); return; }
    toast(`${pvName(v, "This copy")} marked as not watched`);
    d.versions = (d.versions || []).map((x) => (x.id === v.id ? { ...x, position: 0, watched: false, stopped: 0 } : x));
    if (v.current) { at = undefined; d.position = 0; d.watched = false; }
    OD.details.delete(d.id);
    if (open()) { if (ask) askEnd(); else redraw(d.versions); }      // Back pressed meanwhile: just the fresh tags
    odRefresh();
  }
  const askRows = () => {
    const v = (d.versions || []).find((x) => x.id === ask.v.id) || ask.v, own = pvProgress(v);
    const rows = [{ label: ask.label, cls: "pv hdr pv-fixed", html: pvRow(v, ask.label, false), act: () => {} }];
    const ownAt = own && !own.watched ? v.position : -1;
    if (ownAt >= 0) rows.push({ label: `Resume from ${fmtDur(ownAt)}`, cls: "pv-act", act: () => playVod(v.id, ownAt) });
    const c = carry(v);
    if (c >= 0 && (ownAt < 0 || Math.abs(c - ownAt) > 60))
      rows.push({ label: `Continue from ${fmtDur(c)} (where ${pvName(here() || {}, "this version")} stopped)`, cls: "pv-act", act: () => playVod(v.id, c) });
    rows.push({ label: "Play from the beginning", cls: "pv-act", act: () => playVod(v.id, 0) });
    if (own) rows.push({ label: "Mark this copy as not watched", cls: "pv-act", act: () => unwatch(v) });
    rows.push({ label: "Back", cls: "pv-close", act: askEnd });
    return rows;
  };
  const entries = (vs) => {
    if (ask) return askRows();
    // lengths only when they differ (another cut, a PAL copy): otherwise they'd just repeat
    const lens = vs.map((v) => v.duration).filter(Boolean);
    const lenDiffers = lens.length > 1 && Math.max(...lens) - Math.min(...lens) > 120;
    return [...vs.map((v, i, all) => {          // a provider can list the same label twice: number them, and say which is newer
      const name = (x) => pvName(x);
      const same = all.filter((x) => name(x) === name(v)), n = same.length > 1 ? ` (${same.indexOf(v) + 1})` : "";
      const added = same.length > 1 && v.added
        ? `added ${new Date(v.added * 1000).toLocaleDateString(undefined, { month: "short", year: "numeric" })}` : "";
      return { label: [`${name(v)}${n}`, added].filter(Boolean).join(" · "), cls: "pv",
               html: pvRow(v, [`${name(v)}${n}`, added].filter(Boolean).join(" · "), lenDiffers),
               act: () => go(v, [`${name(v)}${n}`, added].filter(Boolean).join(" · ")) };
    }),
    // Reading each version's picture and sound. "Only when I ask": always offered (read the ones not read yet,
    // else read them all again). Automatically: only when some couldn't be read (no connection free, slow provider)
    ...readEntry(vs),
    { label: "Close", cls: "pv-close", act: closeDialog }];
  };
  const peekMode = () => (S.settings || {}).vod_details || "auto";       // auto | ask | off (Setup › Settings)
  const readEntry = (vs) => {
    if (reading || d.kind === "series" || peekMode() === "off") return [];
    const failed = vs.some((v) => !v.av && v.why), unread = vs.some((v) => !v.av && (v.why || peekMode() === "ask"));
    if (unread) return [{ label: failed ? "Read video quality again" : "Read video quality", cls: "pv-again", act: () => readAll(true) }];
    return peekMode() === "ask" ? [{ label: "Read video quality again", cls: "pv-again", act: () => readAll(true, true) }] : [];
  };
  let reading = true;
  openDialog(`${d.name}${d.year ? ` (${d.year})` : ""}`, note, entries(d.versions || []));
  $("dialog").classList.add("pv-dlg");          // wider, for the details (openDialog takes it off again)
  // start on the version playing (the one you opened)
  const cur = (d.versions || []).findIndex((v) => v.current);
  if (cur > 0) fillList($("dlg-list"), dlg.entries, cur);
  let shown = dlg.entries;
  const open = () => top() === "dialog" && dlg.entries === shown;      // not closed, no other dialog meanwhile
  // pick: the version to highlight (back from "where to start")
  function redraw(vs, pick) {
    const rows = [...$("dlg-list").children];
    const sel = Math.max(0, rows.findIndex((li) => li.classList.contains("sel")));
    if (ask) {
      // where to start: the same choice stays highlighted (the version's row on top can't be)
      const wasAsk = rows[0] && rows[0].classList.contains("pv-fixed");
      d.versions = vs;
      shown = dlg.entries = entries(vs);
      fillList($("dlg-list"), dlg.entries, wasAsk ? Math.min(Math.max(1, sel), dlg.entries.length - 1) : 1);
      return;
    }
    // the highlight stays on the same version when the list comes back in another order; on Close or
    // Read details again, it stays that many rows from the end
    const was = pick ? (d.versions || []).find((v) => v.id === pick)
      : rows[0] && rows[0].classList.contains("pv-fixed") ? null : (d.versions || [])[sel];
    d.versions = vs;
    const at = was ? vs.findIndex((v) => v.id === was.id) : -1;
    shown = dlg.entries = entries(vs);
    const fromEnd = rows.length - 1 - sel;
    fillList($("dlg-list"), dlg.entries, at >= 0 ? at : was ? 0 : Math.max(0, dlg.entries.length - 1 - fromEnd));
  };
  const mark = (id, more) => d.versions.map((x) => (x.id === id ? { ...x, ...more } : x));
  // a look at each version not read yet: resolution, HDR, sound. One at a time (a provider allows few
  // connections), each row saying "Reading…" until it comes back, or why it couldn't be read
  // When this screen's own playback holds the playlist's connection, it's paused for the reads and picked up
  // again once they're done (or the dialog is closed).
  // again: every version, the ones read already too (what each file says now); else the ones not read yet
  async function readAll(force, again = false) {
    reading = true;
    let resume = null;
    const peek = (v) => api(`/api/vod/peek/${vodPath(v.id)}?client=${encodeURIComponent(CID)}${force ? "&force=1" : ""}${again ? "&again=1" : ""}`)
      .catch((e) => ({ id: v.id, why: e.message }));
    setSub("Pick a version · reading each one…");
    try {
      for (const v of (d.versions || []).filter((x) => again || !x.av)) {
        if (!open()) return;
        redraw(mark(v.id, { checking: true, why: "" }));
        let got = await peek(v);
        if (got && got.mine && !resume && open()) {
          resume = pvHold();
          if (resume) {
            setSub("Pick a version · paused what's playing to read each one…");
            await api("/api/stop", { method: "POST", body: { client: CID } }).catch(() => {});
            await new Promise((r) => setTimeout(r, 1500));      // some providers take a moment to see it closed
            if (!open()) return;
            got = await peek(v);
          }
        }
        if (!open()) return;
        redraw(mark(v.id, { ...(got || {}), checking: false }));
      }
    } finally {
      reading = false;
      if (resume) resume();
    }
    if (!open()) return;
    redraw(d.versions);
    setSub("Pick a version");
  }
  (async () => {
    // the provider's bitrate of each version: fetched once per version, then kept
    const vs = await api(`/api/vod/title/${vodPath(d.id)}/versions`).catch(() => null);
    if (!open()) return;
    if (vs && vs.length) redraw(vs);
    if (d.kind !== "series" && peekMode() === "auto") await readAll(false);
    else { reading = false; if (open()) { redraw(d.versions || []); setSub("Pick a version"); } }
  })();
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
  if (t.resume) { odResumeDialog(t, () => odOpenShow(t.id)); return; }     // Continue Watching
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
  const v = S.vod; if (!v || v.done || v.it.catchup) return;     // catch-up shows aren't resumed later
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
  subsTick();                       // takes the subtitles off
}
// `whole`: full screen even in multiview (otherwise, in multiview, it plays in a small picture: mvVodTarget)
async function playVod(id, start, back, whole = false) {
  if (back === undefined) back = S.layer.includes("od");      // go back to On Demand afterwards
  closeDialog();
  toast("Loading…", 6000);
  let it;
  try { it = await api(`/api/vod/play/${vodPath(id)}`); } catch (e) { toast(`Can't play this: ${e.message}`, 4000); return; }
  if (start === undefined) start = it.position > RESUME_MIN ? it.position : 0;
  closeOD(); closePanels(); closeMenu(); closeDvr();
  if (top() === "guide") G.close();
  const tile = !whole && mvVodTarget();
  if (tile) {
    closeHome();
    mvVodPlay(tile, it, start);
    mvFocus(tile.slot);
    toast(`${start > 0 ? `Resuming ${it.title} from ${fmtDur(start)}` : it.title} · Back: watch it full screen`, 4000);
    return;
  }
  vodLeave(); mvStop();
  recLeave(); S.mode = "vod"; hideBanner();
  S.vod = { it, dur: it.duration || 0, base: start, back, retries: 0, target: null, parked: false, paused: false, done: false };
  avInit(S.vod);                    // the audio track and subtitles picked last time for this movie or show
  toast(start > 0 ? `Resuming from ${fmtDur(start)}` : it.catchup ? `↺ ${it.title}` : it.title, 2500);
  vodStart(start);
  avLoad(S.vod);
}
function vodStart(at) {
  const v = S.vod; if (!v) return;
  clearTimeout(v.parkTimer); clearTimeout(v.seekTimer); clearTimeout(v.endTimer);
  at = Math.max(0, v.dur ? Math.min(at, v.dur - 5) : at);
  if (v.it.catchup) at = Math.floor(at / 60) * 60;      // the archive starts on the minute
  v.base = at; v.target = null; v.parked = false; v.paused = false;
  destroyPlayer(); hideStatic();
  const key = `v:${v.it.id}`, mode = h264Keys.has(key) ? "h264" : "";
  const url = (hls) => `${location.origin}/api/vod/${hls ? "hls" : "stream"}/${vodPath(v.it.id)}?ss=${at.toFixed(1)}&client=${CID}`
    + (mode && !hls ? `&mode=${mode}` : "") + (v.audio > 0 ? `&a=${v.audio}` : "");
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
    const codecErr = codecFallback(p, key, mode, () => S.vod === v && vodStart(vodPos()));   // where it is now, not where it began
    p.on(mpegts.Events.ERROR, (type, detail) => {
      if (S.player === p) { console.warn("player error", type, detail); if (!codecErr(type)) fail(); }
    });
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
  const v = S.vod; if (!v || v.checking) return;       // one check at a time: an error and the end can both fire
  const pos = vodPos();
  v.checking = true;
  stoppedFromSetup("", `${v.it.title} was stopped in Setup › Tuners. Press Back.`).then((stopped) => {
    v.checking = false;
    if (S.vod !== v) return;
    if (stopped) { v.base = pos; v.parked = true; } else vodRetry(v, pos);
  });
}
function vodRetry(v, pos) {
  if (v.retries < 3) {
    v.retries++; toast("Reconnecting…");
    v.base = pos; v.parked = true; destroyPlayer();
    v.seekTimer = setTimeout(() => { if (S.vod === v) vodStart(pos); }, 1500);
  } else {
    v.base = pos; v.parked = true; destroyPlayer();
    showStatic("", "Can't play this title", `${v.it.title}: no video from the provider. Press Back.`);
    explainBusy({ vod_id: v.it.id }, "", () => S.vod === v && v.parked);
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
  if (!v.it.catchup) api("/api/vod/watched", { method: "POST", body: { id: v.it.id, watched: true } }).catch(() => {});
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
// holding ◀ / ▶ (or pressing again and again) skips further each step the longer it goes on:
// 10s / 30s at first, then 1 minute, then 3, then 10. Key repeats are taken about 8 times a second.
function vodSkipStep(v, delta) {
  const t = Date.now(), dir = Math.sign(delta);
  if (v.target === null || !v.run || v.run.dir !== dir) v.run = { dir, start: t, last: 0 };
  if (t - v.run.last < 120) return 0;                  // key repeat faster than this: wait for the next one
  v.run.last = t;
  const held = t - v.run.start;
  const step = held < 1500 ? Math.abs(delta) : held < 4000 ? 60 : held < 8000 ? 180 : 600;
  return dir * Math.max(Math.abs(delta), step);
}
function vodSeek(delta, hold = true) {
  const v = S.vod; if (!v || v.done) return;
  if (hold) { delta = vodSkipStep(v, delta); if (!delta) return; }
  vodSeekTo(vodPos() + delta, delta);
}
// jump to a point in the title (the bar's drag lands here too); dir rounds catch-up to the minute
function vodSeekTo(t, delta = 0) {
  const v = S.vod; if (!v || v.done) return;
  t = Math.max(0, t);
  // catch-up: whole minutes (◀ ▶ = 1 minute), as the archive is asked for by the minute
  if (v.it.catchup) t = Math.max(0, Math.floor((delta ? vodPos() + Math.sign(delta) * Math.max(60, Math.abs(delta)) : t) / 60) * 60);
  if (v.dur) t = Math.min(t, Math.max(0, v.dur - 10));
  if (v.it.catchup && v.it.stop > now()) t = Math.min(t, Math.max(0, now() - v.it.start - 90));    // not past what has aired
  v.target = t;
  showVodBar();
  clearTimeout(v.seekTimer); v.seekTimer = setTimeout(() => { if (S.vod === v) vodStart(t); }, 600);   // several presses = one jump
}
let vodBarTimer;
function drawVodBar() {
  const v = S.vod; if (!v) return;
  const pos = vodPos();
  $("pb-title").textContent = (v.it.catchup ? "↺ " : "") + v.it.title + (v.it.sub ? ` · ${v.it.sub}` : "");
  if (!pbScrub) $("pb-progress").style.width = v.dur ? `${Math.min(100, pos / v.dur * 100)}%` : "0";     // the drag sets it meanwhile
  $("pb-time").textContent = `${fmtDur(pos)}${v.dur ? ` / ${fmtDur(v.dur)}` : ""}${v.target !== null ? "   Skipping…" : v.paused ? "   ❚❚ Paused" : v.parked || video.readyState < 3 ? "   Loading…" : ""}`
    + (v.it.catchup ? "      ◀ ▶ 1 min (hold: faster)   OK Pause   Back Live TV" : "      ◀ 10s   ▶ 30s (hold: faster)   OK Pause   Back Stop")
    + (avHas(v) ? "   M Audio & subtitles" : "");
}
function showVodBar() {
  hideBanner(); drawVodBar();
  $("playback").classList.remove("hidden");
  clearTimeout(vodBarTimer);
  vodBarTimer = setTimeout(function hide() {          // stays up while paused, dragged or pointed at
    const v = S.vod;
    if (v && !v.paused && pbHeld()) { vodBarTimer = setTimeout(hide, 1500); return; }
    if (!v || !v.paused) $("playback").classList.add("hidden");
  }, 5000);
}
// click or drag the progress bar to go to that point: On Demand, catch-up and recordings.
// While dragging, the bar and a time tip follow the pointer; letting go makes the jump.
let pbScrub = null;      // { at } seconds under the pointer while dragging
function pbLength() {
  if (S.mode === "vod" && S.vod && !S.vod.done) return S.vod.dur || 0;
  if (S.mode === "playback") { const d = video.duration; return isFinite(d) ? d : 0; }
  return 0;
}
function pbScrubAt(e) {
  const d = pbLength(), r = $("pb-seek").getBoundingClientRect();
  const f = Math.min(1, Math.max(0, (e.clientX - r.left) / (r.width || 1)));
  pbScrub = { at: f * d };
  if (S.mode === "vod") showVodBar(); else showPlaybackBar();          // keeps the bar up while dragging
  $("pb-progress").style.width = `${f * 100}%`;
  const tip = $("pb-tip");
  tip.textContent = `${fmtDur(pbScrub.at)} / ${fmtDur(d)}`;
  tip.style.left = `${f * 100}%`; tip.classList.remove("hidden");
}
function pbScrubEnd(e, go) {
  const el = $("pb-seek");
  if (!el.classList.contains("drag")) return;
  el.classList.remove("drag"); $("pb-tip").classList.add("hidden");
  try { el.releasePointerCapture(e.pointerId); } catch (_) {}
  const s = pbScrub; pbScrub = null;
  if (!go || !s) { if (S.mode === "vod") drawVodBar(); return; }
  if (S.mode === "vod") vodSeekTo(s.at);
  else if (S.mode === "playback") { video.currentTime = Math.min(s.at, Math.max(0, pbLength() - 1)); showPlaybackBar(); }
}
$("pb-seek").addEventListener("pointerdown", (e) => {
  if (!pbLength() || (e.pointerType === "mouse" && e.button !== 0)) return;
  e.preventDefault(); e.stopPropagation();
  const el = $("pb-seek");
  el.classList.add("drag");
  try { el.setPointerCapture(e.pointerId); } catch (_) {}
  pbScrubAt(e);
});
$("pb-seek").addEventListener("pointermove", (e) => { if ($("pb-seek").classList.contains("drag")) pbScrubAt(e); });
$("pb-seek").addEventListener("pointerup", (e) => pbScrubEnd(e, true));
$("pb-seek").addEventListener("pointercancel", (e) => pbScrubEnd(e, false));
$("pb-seek").addEventListener("click", (e) => e.stopPropagation());     // not a tap on the picture
let vodTicks = 0;
setInterval(() => {
  const v = S.vod; if (!v || S.mode !== "vod" || v.done) return;
  if (!$("playback").classList.contains("hidden") && !pbScrub) drawVodBar();
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
    case "ChannelUp": vodSeek(-300, false); break;
    case "ChannelDown": vodSeek(300, false); break;
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
