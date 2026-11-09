// ---------------------------------------------------------------- guide
const G = {
  rows: 8, top: 0, row: 0, win: 0, span: 7200, focusT: 0,
  cache: new Map(), cacheT: 0, loading: new Set(),
  favOnly: false, list: [],       // rows = indexes into S.channels (every channel, or favorites only)

  build() {
    this.list = [];
    S.channels.forEach((c, i) => { if (!this.favOnly || S.favs.has(c.number)) this.list.push(i); });
  },
  ch(row) { return S.channels[this.list[row]]; },
  rowOf(idx) { return this.list.indexOf(idx); },

  open(favs = false) {
    if (!S.channels.length) { toast("No channels yet - open Menu › Channel Setup"); return; }
    if (favs && !S.channels.some((c) => S.favs.has(c.number))) {
      toast("No favorites yet: highlight a channel in the guide and press * (or Menu › Add to Favorites)", 5000);
      favs = false;
    }
    if (Date.now() - this.cacheT > 5 * 60000) { this.cache.clear(); this.cacheT = Date.now(); }
    if (top() !== "guide") S.layer.push("guide");
    hideBanner(); $("playback").classList.add("hidden");
    $("guide").classList.remove("hidden");
    video.classList.add("pip");
    this.rows = window.innerHeight < window.innerWidth ? 8 : 9;
    this.span = isPhone() && window.innerHeight > window.innerWidth ? 5400 : 7200;   // phone portrait: 90 min, wider cells
    this.win = Math.floor(now() / HALF) * HALF;
    this.focusT = now();
    this.favOnly = favs; this.build();
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
  close() {
    S.layer = S.layer.filter((l) => l !== "guide");
    $("guide").classList.add("hidden");
    video.classList.remove("pip");
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

    $("g-day").textContent = (this.favOnly ? "★ " : "") + fmtDay(this.win);
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
        const isSel = i === this.row && sel && p.start === sel.start;
        const c = ["g-p", p.empty ? "empty" : (p.category || ""), (p.start <= n0 && n0 < p.stop) ? "now" : "", isSel ? "sel" : ""].join(" ");
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
    rowsEl.innerHTML = html;
    this.info(sel);
  },
  info(p) {
    const ch = this.ch(this.row);
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
      this.close();
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

