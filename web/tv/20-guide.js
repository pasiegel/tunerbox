// ---------------------------------------------------------------- guide
// genre filter (C): only channels with a show of that genre on in the hours shown; other shows are dimmed
const GENRES = [["", "All"], ["movies", "Movies"], ["sports", "Sports"], ["news", "News"], ["kids", "Kids"]];
const G = {
  rows: 8, top: 0, row: 0, win: 0, span: 7200, focusT: 0,
  cache: new Map(), cacheT: 0, loading: new Set(),
  favOnly: false, list: [],       // rows = indexes into S.channels (every channel, or favorites only)
  genre: "", genreNums: null, genreKey: "", genreSeq: 0,      // genreNums: channel numbers with that genre on
  filterFocus: false,             // the highlight is on the genre picker (▲ from the first channel)

  build() {
    this.list = [];
    const g = this.genre && this.genreNums && this.genreNums.size ? this.genreNums : null;
    S.channels.forEach((c, i) => { if ((!this.favOnly || S.favs.has(c.number)) && (!g || g.has(c.number))) this.list.push(i); });
    if (!this.list.length && g) this.genreNums = new Set();   // none of them among the favorites: show them all, dimmed
    if (!this.list.length) S.channels.forEach((c, i) => { if (!this.favOnly || S.favs.has(c.number)) this.list.push(i); });
  },
  genreLabel() { return (GENRES.find(([id]) => id === this.genre) || GENRES[0])[1]; },
  // C (or ◀ ▶ on the genre picker): the next / previous genre; the highlighted channel stays if it's still listed
  async cycleGenre(step = 1, announce = true) {
    const i = GENRES.findIndex(([id]) => id === this.genre);
    this.genre = GENRES[(i + step + GENRES.length) % GENRES.length][0];
    this.genreKey = ""; this.genreNums = null;
    await this.refilter(announce);
  },
  // (re)load which channels have the genre on in the hours shown, then rebuild the rows
  async refilter(announce) {
    const seq = ++this.genreSeq, cur = this.list[this.row];
    if (this.genre) {
      const key = `${this.genre}:${this.win}:${this.span}`;
      let nums;
      try { nums = await api(`/api/guide/genre?genre=${this.genre}&start=${this.win}&stop=${this.win + this.span}`); }
      catch (_) { toast("Couldn't filter the guide. Try again in a moment."); return; }
      if (seq !== this.genreSeq || top() !== "guide") return;
      this.genreKey = key; this.genreNums = new Set(nums);
    } else this.genreNums = null;
    this.build();
    const none = this.genre && !this.genreNums.size;
    if (announce) toast(!this.genre ? "All channels" : none ? `No ${this.genreLabel().toLowerCase()} on in these hours` : `${this.genreLabel()}: ${plural(this.list.length, "channel")}`);
    const r = this.list.indexOf(cur);
    this.row = r >= 0 ? r : 0;
    this.top = Math.max(0, Math.min(this.row - 2, this.list.length - this.rows));
    this.render();
  },
  ch(row) { return S.channels[this.list[row]]; },
  rowOf(idx) { return this.list.indexOf(idx); },

  open(favs = false) {
    if (!S.channels.length) { toast("No channels yet. Open Menu › Channel Setup to add a playlist."); return; }
    if (favs && !S.channels.some((c) => S.favs.has(c.number))) {
      toast("No favorites yet: highlight a channel in the guide and press * (or Menu › Add to Favorites)", 5000);
      favs = false;
    }
    if (Date.now() - this.cacheT > 5 * 60000) { this.cache.clear(); this.cacheT = Date.now(); }
    homeCover();
    if (top() !== "guide") S.layer.push("guide");
    hideBanner(); $("playback").classList.add("hidden");
    $("guide").classList.remove("hidden");
    video.classList.add("pip");
    this.rows = window.innerHeight < window.innerWidth ? 8 : 9;
    this.span = isPhone() && window.innerHeight > window.innerWidth ? 5400 : 7200;   // phone portrait: 90 min, wider cells
    this.win = Math.floor(now() / HALF) * HALF;
    this.focusT = now();
    this.favOnly = favs; this.genre = ""; this.genreNums = null; this.genreKey = ""; this.filterFocus = false; this.build();
    // a phone may have changed the favorites: pick that up
    loadFavs().then(() => top() === "guide" && this.refresh());
    this.row = Math.max(0, this.rowOf(S.idx));
    this.top = Math.max(0, Math.min(this.row - 2, this.list.length - this.rows));
    this.render();
  },
  // switch between every channel and favorites only, staying on the same channel if it's in both
  toggleView() {
    const cur = this.list[this.row];
    if (!this.favOnly && !S.channels.some((c) => S.favs.has(c.number))) {
      toast("No favorites yet: highlight a channel and press * to add it", 4000); return;
    }
    this.favOnly = !this.favOnly; this.build();
    const r = this.rowOf(cur);
    this.row = r >= 0 ? r : 0;
    this.top = Math.max(0, Math.min(this.row - 2, this.list.length - this.rows));
    toast(this.favOnly ? "★ Favorites only" : "All channels");
    this.render();
  },
  // favorites changed while the guide is open
  refresh() {
    const cur = this.list[this.row];
    this.build();
    if (!this.list.length) { this.favOnly = false; this.build(); toast("No favorites left - showing all channels"); }
    const r = this.rowOf(cur);
    this.row = r >= 0 ? r : Math.min(this.row, this.list.length - 1);
    this.top = Math.max(0, Math.min(this.top, this.list.length - this.rows));
    if (this.row < this.top) this.top = this.row;
    if (this.row >= this.top + this.rows) this.top = this.row - this.rows + 1;
    this.render();
  },
  // keys while the genre picker is highlighted (true = handled)
  filterKey(k) {
    if (!this.filterFocus) return false;
    if (k === "ArrowLeft" || k === "ArrowRight") { this.cycleGenre(k === "ArrowRight" ? 1 : -1, false); return true; }   // the picker shows it
    if (k === "Enter" || k === "Back") { this.filterFocus = false; this.render(); return true; }
    if (k !== "ArrowUp" && k !== "ArrowDown") this.filterFocus = false;    // paging, record…: back on the channels
    return false;                   // ▲ ▼ go through move(); everything else as usual
  },
  close() {
    this.filterFocus = false;
    S.layer = S.layer.filter((l) => l !== "guide");
    $("guide").classList.add("hidden");
    if (top() === "home") homeReveal();                                // back to Home, TV still in its corner
    else if (!S.layer.includes("home")) video.classList.remove("pip");
  },
  minWin() { return Math.floor(now() / HALF) * HALF; },

  progsFor(num) {
    const e = this.cache.get(num);
    return e ? [...e.progs.values()].sort((a, b) => a.start - b.start) : [];
  },
  async ensure(nums, from, to) {
    const need = new Map();
    for (let c = Math.floor(from / CHUNK) * CHUNK; c < to; c += CHUNK) {
      for (const n of nums) {
        const e = this.cache.get(n);
        if (!e || !e.chunks.has(c)) { if (!need.has(c)) need.set(c, []); need.get(c).push(n); }
      }
    }
    if (!need.size) return false;
    // Only report "changed" when this call actually downloaded something. Requests already in
    // flight are skipped; the call that started them redraws when they land. (Reporting true for
    // a skipped request made render -> ensure -> render spin forever and froze the whole page
    // whenever the server was slow to answer.)
    let fetched = false;
    await Promise.all([...need].map(async ([c, list]) => {
      const key = c + ":" + list.join(",");
      if (this.loading.has(key)) return;
      this.loading.add(key);
      try {
        const data = await api(`/api/guide?start=${c}&stop=${c + CHUNK}&numbers=${list.join(",")}`);
        for (const n of list) {
          if (!this.cache.has(n)) this.cache.set(n, { chunks: new Set(), progs: new Map() });
          const e = this.cache.get(n); e.chunks.add(c);
          for (const p of data[n] || []) e.progs.set(p.start, p);
        }
        fetched = true;
      } finally { this.loading.delete(key); }
    }));
    return fetched;
  },
  forget(num) { this.cache.delete(num); },

  // programs + filler cells covering [win, win+span)
  cells(num) {
    const a = this.win, b = this.win + this.span, out = [];
    const hasData = this.cache.has(num);
    let t = a;
    const fill = (until) => {
      while (t < until) {
        const nx = Math.min(until, (Math.floor(t / HALF) + 1) * HALF);
        out.push({ start: t, stop: nx, title: hasData ? "No Information" : "Loading…", empty: true });
        t = nx;
      }
    };
    for (const p of this.progsFor(num)) {
      if (p.stop <= a || p.start >= b) continue;
      if (p.start > t) fill(p.start);
      out.push(p); t = Math.max(t, p.stop);
    }
    fill(b);
    return out;
  },
  // full program list for navigation (includes outside window)
  rowProgs(num) {
    const ps = this.progsFor(num);
    if (ps.length) return ps;
    return [];
  },
  selected() {
    const ch = this.ch(this.row);
    const t = Math.max(this.focusT, this.win);
    const cells = this.cells(ch.number);
    return cells.find((c) => c.start <= t && t < c.stop) || cells[0];
  },

  render() {
    const rowsEl = $("g-rows");
    const n = this.list.length;
    // prefetch this page + next page
    const pre = [];
    for (let i = Math.max(0, this.top - this.rows); i < Math.min(n, this.top + this.rows * 2); i++) pre.push(this.ch(i).number);
    this.ensure(pre, this.win, this.win + this.span).then((changed) => changed && top() === "guide" && this.render()).catch(() => {});
    // moved to other hours with a genre picked: the channels showing it change too
    if (this.genre && this.genreKey && this.genreKey !== `${this.genre}:${this.win}:${this.span}`) { this.genreKey = ""; this.refilter(); }

    const none = this.genre && this.genreNums && !this.genreNums.size;
    const label = this.filterFocus ? `&#9664; ${esc(this.genre ? this.genreLabel() : "All channels")}${none ? " · none" : ""} &#9654;`
      : esc(this.genreLabel()) + (none ? " · none" : "");
    $("g-day").innerHTML = (this.favOnly ? "★ " : "")
      + (this.genre || this.filterFocus ? `<span class="g-genre${none ? " none" : ""}${this.filterFocus ? " focus" : ""}">${label}</span> ` : "") + esc(fmtDay(this.win));
    const tEl = $("g-times"); tEl.innerHTML = "";
    for (let i = 0; i < this.span / HALF; i++) {
      const d = document.createElement("div");
      d.style.left = `${(i * HALF) / this.span * 100}%`; d.style.width = `${HALF / this.span * 100}%`;
      d.textContent = fmtTime(this.win + i * HALF);
      tEl.appendChild(d);
    }
    const sel = this.selected();
    const n0 = now();
    let html = "";
    for (let i = this.top; i < Math.min(n, this.top + this.rows); i++) {
      const ch = this.ch(i), fav = S.favs.has(ch.number);
      const cls = ["g-row", i === this.row ? "sel" : "", this.list[i] === S.idx ? "playing" : ""].join(" ");
      html += `<div class="${cls}" data-i="${i}"><div class="g-ch"><span class="g-fav${fav ? " on" : ""}" title="${fav ? "Remove from" : "Add to"} Favorites">${fav ? "★" : "☆"}</span><span class="n">${ch.number}</span>`
        + (ch.logo ? `<img src="${esc(ch.logo)}" onerror="this.remove()" alt="">` : "")
        + `<span class="l">${esc(ch.label || ch.name)}</span></div><div class="g-progs">`;
      for (const p of this.cells(ch.number)) {
        const l = Math.max(p.start, this.win), r = Math.min(p.stop, this.win + this.span);
        const isSel = !this.filterFocus && i === this.row && sel && p.start === sel.start;
        const c = ["g-p", p.empty ? "empty" : (p.category || ""), (p.start <= n0 && n0 < p.stop) ? "now" : "", isSel ? "sel" : "",
                   this.genre && !p.empty && p.genre !== this.genre ? "dim" : ""].join(" ");
        const pre = p.start < this.win ? "&#9664; " : "";
        const post = p.stop > this.win + this.span ? " &#9654;" : "";
        const title = p.empty && !ch.epg ? ch.name : p.title;
        const dot = p.rec ? '<span class="dot">&#9679;</span>' : (p.series ? '<span class="dot">&#9679;&#9679;</span>' : "");
        html += `<div class="${c}" data-i="${i}" data-s="${p.start}" style="left:${(l - this.win) / this.span * 100}%;width:${(r - l) / this.span * 100}%">`
          + `${dot}<span class="t">${pre}${esc(title)}${post}</span></div>`;
      }
      if (n0 >= this.win && n0 < this.win + this.span) html += `<div class="g-nowline" style="left:${(n0 - this.win) / this.span * 100}%"></div>`;
      html += "</div></div>";
    }
    rowsEl.style.setProperty("--rows", this.rows);
    rowsEl.innerHTML = html;
    this.info(sel);
  },
  info(p) {
    if (this.filterFocus) {
      showArt($("gi-art"), null);
      $("gi-title").textContent = "Filter by genre"; $("gi-sub").textContent = "◀ ▶ All channels, Movies, Sports, News or Kids";
      $("gi-meta").textContent = "OK or ▼ to go back to the channels";
      $("gi-desc").textContent = "Lists only the channels with a show of that genre on in the hours shown. Shows of other genres are dimmed.";
      return;
    }
    const ch = this.ch(this.row);
    // picture: after a moment, so holding an arrow key doesn't fetch one per cell
    clearTimeout(this.artT);
    const img = $("gi-art");
    if (!p || p.empty || !p.image) showArt(img, null);
    else if (img.dataset.key !== p.image) { showArt(img, null); this.artT = setTimeout(() => showArt(img, ch.number, p), 150); }
    if (!p || p.empty) {
      $("gi-title").textContent = ch.name; $("gi-sub").textContent = p && p.title === "Loading…" ? "Loading…" : "No information available";
      $("gi-meta").textContent = `Channel ${ch.number}`; $("gi-desc").textContent = ""; return;
    }
    $("gi-title").innerHTML = esc(p.title) + " " + flags(p);
    $("gi-sub").textContent = [p.episode, p.subtitle].filter(Boolean).join("  ·  ");
    $("gi-meta").textContent = `${fmtDay(p.start)}  ${fmtTime(p.start)} – ${fmtTime(p.stop)}   ·   ${ch.number} ${ch.label || ch.name}` + (p.category ? `   ·   ${p.category}` : "");
    $("gi-desc").textContent = p.descr || "";
  },
  move(dr) {
    const n = this.list.length;
    // ▲ on the first channel: up to the genre picker; ▲ again wraps round to the last channel
    if (dr === -1 && this.row === 0 && !this.filterFocus) { this.filterFocus = true; this.render(); return; }
    if (this.filterFocus) { this.filterFocus = false; if (dr > 0) { this.render(); return; } }
    this.row = (this.row + dr + n) % n;
    if (this.row < this.top) this.top = this.row;
    if (this.row >= this.top + this.rows) this.top = this.row - this.rows + 1;
    if (Math.abs(dr) > 1) this.top = Math.max(0, Math.min(this.row, n - this.rows));
    this.render();
  },
  page(d) {
    const n = this.list.length;
    if (d > 0 && this.top + this.rows >= n) { this.top = 0; this.row = 0; }
    else if (d < 0 && this.top === 0) { this.top = Math.max(0, n - this.rows); this.row = n - 1; }
    else {
      this.top = Math.max(0, Math.min(n - this.rows, this.top + d * this.rows));
      this.row = Math.max(0, Math.min(n - 1, this.row + d * this.rows));
      if (this.row < this.top || this.row >= this.top + this.rows) this.row = this.top;
    }
    this.render();
  },
  horiz(d) {
    const sel = this.selected(); if (!sel) return;
    if (d > 0) {
      const target = sel.stop;
      if (target >= now() + 14 * 86400) return;
      this.focusT = target;
      while (this.focusT >= this.win + this.span) this.win += HALF;
    } else {
      const target = sel.start - 1;
      if (target < this.minWin() && sel.start <= this.minWin()) return;
      this.focusT = Math.max(target, this.minWin());
      while (this.focusT < this.win && this.win > this.minWin()) this.win -= HALF;
      if (this.focusT < this.win) this.focusT = this.win;
    }
    this.render();
  },
  // touch scrolling: move the visible page / time window, keeping the highlight on screen
  scrollTo(t) {
    t = Math.max(0, Math.min(t, this.list.length - this.rows));
    if (t === this.top) return;
    this.top = t;
    this.row = Math.max(t, Math.min(this.row, t + this.rows - 1));
    this.render();
  },
  timeTo(w) {
    w = Math.max(this.minWin(), Math.min(w, Math.floor((now() + 14 * 86400) / HALF) * HALF - this.span));
    if (w === this.win) return;
    this.win = w;
    this.focusT = Math.max(w, Math.min(this.focusT, w + this.span - 1));
    this.render();
  },
  jump(num) {
    let i = this.list.findIndex((ci) => S.channels[ci].number >= num);
    if (i < 0 && this.favOnly) { this.favOnly = false; this.build(); i = this.list.findIndex((ci) => S.channels[ci].number >= num); }
    if (i < 0) { toast(`Channel ${num} is not available`); return; }
    this.row = i; this.top = Math.max(0, Math.min(i, this.list.length - this.rows)); this.render();
  },
  ok() {
    const p = this.selected(); const ch = this.ch(this.row);
    const n0 = now();
    if (!p || p.empty || (p.start <= n0 && n0 < p.stop)) {
      if (this.list[this.row] !== S.idx || S.mode !== "live") tune(this.list[this.row]);
      closeHome(); this.close();          // watching: full-screen TV, not back to Home
      return;
    }
    programDialog(ch, p);
  },
  fav() { toggleFav(this.ch(this.row)); },
  record() {
    const p = this.selected(); const ch = this.ch(this.row);
    if (!p || p.empty) { toast("No guide information to record"); return; }
    programDialog(ch, p);
  },
};

// touch: tap the day (top left of the guide) for the next genre
$("g-day").addEventListener("click", () => { if (top() === "guide") G.cycleGenre(); });
