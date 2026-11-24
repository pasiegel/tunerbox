// ---------------------------------------------------------------- sports & events: the Basic layout
// Tabs (On Now, Coming Up, Sports Channels, Events), each one list: the screen as it was before the
// Enhanced layout (40-search-sports.js), and still the default (Setup › Settings › Sports › layout).
// openSports / closeSports / sportsKey pick between the two; the helpers both use live in 40-search-sports.js.
// tab id, label, and the list it shows (a key of SPB.data, fetched on its own: /api/sports?tab=id)
const SPB_TABS = [["now", "On Now", "on_now"], ["up", "Coming Up", "upcoming"], ["ch", "Sports Channels", "channels"], ["ev", "Events", "groups"]];
const SPB = { tab: 0, data: null, group: null, items: [], poll: null, seq: 0 };

async function openSportsBasic() {
  hideBanner(); closeMenu(); closeSearch();
  if (top() !== "sports") S.layer.push("sports");
  $("sports-basic").classList.remove("hidden");
  SPB.group = null;
  // The tab it opens on is fetched first and shown right away; the others follow in the background.
  const seq = ++SPB.seq, first = SPB.tab;
  SPB.data = {}; SPB.errors = {}; SPB.moved = false;
  renderSportsBasic(0);
  for (const t of [first, ...SPB_TABS.map((_, i) => i).filter((i) => i !== first)]) {
    const [id, , key] = SPB_TABS[t];
    try { Object.assign(SPB.data, await api(`/api/sports?tab=${id}`)); }
    catch (e) { if (seq === SPB.seq) SPB.errors[key] = e.message; }
    if (seq !== SPB.seq) return;                      // closed or opened again meanwhile
    if (!(key in SPB.data) && !SPB.errors[key]) SPB.errors[key] = "Couldn't load this list";
    // only the list on screen is redrawn (from the top when it just arrived); the others just get their count
    if (t === SPB.tab && !SPB.group) renderSportsBasic(0); else renderSportsBasic();
  }
  // nothing on the tab it opened on: show the first one that has something (unless you've moved already)
  const counts = spbCounts();
  if (!SPB.moved && SPB.tab === first && !counts[first]) { const t = counts.findIndex((n) => n > 0); if (t >= 0) { SPB.tab = t; renderSportsBasic(0); } }
}
function closeSportsBasic() {
  SPB.seq++;
  S.layer = S.layer.filter((l) => l !== "sports");
  $("sports-basic").classList.add("hidden");
  clearInterval(SPB.poll); SPB.poll = null;
}
// how many rows each tab has; null while its list is still on the way
const spbCounts = () => SPB_TABS.map(([, , key]) => (SPB.data && SPB.data[key] ? SPB.data[key].length : null));

// what a game is headed with: the server's league heading and matchup (sports.heading). A phone has
// no room for both, so a scoreboard game is just its teams there (the icon still says the sport).
// A row of On Now / Coming Up. Games from the scoreboard (p.event) carry a score, and may have no
// guide listing: then they play from an event feed (p.feed), or nothing here carries them.
function spbGame(p, badge, until) {
  const e = p.event, score = e && [e.score, e.detail].filter(Boolean).join(" · ");
  const [t, s] = spHeading(p);
  // title, matchup and score are separate pieces, so a phone can stack them (tv.css: li.game)
  const fav = e && e.fav && e.fav.length ? `<span class="sp-fav" title="${esc(e.fav.join(", "))}">★</span>` : "";
  const html = `${badge}<span class="sp-main"><span class="sp-t">${fav}${sportIcon(p)}${esc(t)}</span>${s ? `<small class="sp-s">${esc(s)}</small>` : ""}</span>`
    + (score ? `<small class="score">${esc(score)}</small>` : "");
  const cls = fav ? "game fav" : "game";       // a favorite team's game: marked whether or not it's highlighted
  const chans = gameChans(p).length, feeds = gameFeeds(p), more = chans + feeds.length - 1;
  if (p.ch) {
    const also = more > 1 ? ` + ${more} more` : feeds.length ? ` + ${feeds[0].kind === "ppv" ? "PPV" : "feed"}` : more > 0 ? " + 1 more" : "";
    return { cls, html, right: `${p.ch.number} ${esc(p.ch.label || p.ch.name)}${also}${until ? ` · until ${fmtTime(p.stop)}` : ""}`,
             act: () => (feeds.length || more > 0 ? pickGame(p) : pickProgram(p)) };
  }
  if (feeds.length) return { cls, html, right: esc(feeds[0].group || "Event feed") + (more > 0 ? ` + ${more} more` : ""), act: () => pickGame(p) };
  const on = e.on.length ? `on ${e.on.join(", ")}` : "";
  return { cls: `${cls} nochan`, html, right: esc(on ? `${on} · no channel` : "no channel"),
           act: () => toast(`${e.name}: none of your channels or event feeds has it${on ? ` (it's ${on})` : ""}`, 4000) };
}
function spbItems() {
  const d = SPB.data, n0 = now(), [id, , key] = SPB_TABS[SPB.tab];
  const none = (label) => [{ label }];
  if (!d[key]) return none(SPB.errors[key] || "Loading…");
  if (id === "now") {
    return d.on_now.length ? d.on_now.map((p) =>
      spbGame(p, p.is_live ? '<span class="badge live">LIVE</span>' : `<span class="badge${p.ch || p.feed ? "" : " off"}">${p.ch || p.feed ? "ON" : "SOON"}</span>`, true))
      : none("No sports on right now.");
  }
  if (id === "up") {
    return d.upcoming.length ? d.upcoming.map((p) =>
      spbGame(p, `<span class="badge${p.ch || p.feed ? "" : " off"}">${esc(fmtWhen(p.start))}</span>`))
      : none("No upcoming sports.");
  }
  if (id === "ch") {
    return d.channels.length ? d.channels.map((c) => ({
      html: `<b class="num">${c.number}</b> <span>${esc(c.name)}</span>`,
      right: c.now ? (c.now.is_live ? "LIVE · " : "") + esc(c.now.title) : "",
      act: () => { closePanels(); tuneNum(c.number); },
    })) : none("No sports channels found.");
  }
  // events (PPV, PLE, special events)
  if (!SPB.group) {
    return d.groups.length ? d.groups.map((g) => ({
      html: `<span class="badge ${g.kind}">${g.kind === "ppv" ? "PPV" : "EVENTS"}</span> <span>${esc(g.name)}</span>`,
      right: `${g.live ? `<b class="live-n">${g.live} live</b> · ` : ""}${g.named ? `${g.named} event${g.named === 1 ? "" : "s"}` : `${g.count} feeds`}`,
      act: () => openGroupBasic(g.name),
    })) : none("None of your playlists have event or PPV groups (like ESPN+, PPV or NFL Game Pass).");
  }
  const g = SPB.group, st = SPB.checkState;
  const checking = st && st.running;
  const head = [
    { label: "◀  All event groups", act: () => { SPB.group = null; renderSportsBasic(0); } },
    { label: checking ? `Checking which feeds are live… ${st.done}/${st.total}`
        : g.checkable ? `⟳  Check which feeds are live now${g.checked ? ` (last checked ${ago(g.checked)})` : ""}`
        : "These feeds are from Xtream, which isn't checked automatically. Just pick one to try it.",
      act: () => { if (!checking && g.checkable) startCheckBasic(); } },
  ];
  // empty / ended slots ("NO EVENT STREAMING", "ESPN+ 12") stay folded away unless asked for
  const real = g.feeds.filter((f) => !f.slot || f.status === "ok"), slots = g.feeds.filter((f) => f.slot && f.status !== "ok");
  const shown = real.length && !SPB.showSlots ? real : g.feeds;
  const items = head.concat(shown.map((f) => feedItem({ ...f, kind: g.kind })));
  if (real.length && slots.length) {
    items.push({ label: SPB.showSlots ? "Hide empty and ended slots" : `Show ${slots.length} empty and ended slots`,
                 act: () => { SPB.showSlots = !SPB.showSlots; renderSportsBasic(); } });
  }
  return items;
}
function renderSportsBasic(sel) {
  if (!SPB.data) return;
  const counts = spbCounts();
  $("spb-tabs").innerHTML = SPB_TABS.map(([, label], i) =>
    `<div class="tab${i === SPB.tab ? " sel" : ""}" data-t="${i}">${label} <small>${counts[i] ?? ""}</small></div>`).join("");
  SPB.items = spbItems();
  if (sel === undefined) sel = Math.max(0, selIndex($("spb-list")));
  fillList($("spb-list"), SPB.items, Math.min(sel, SPB.items.length - 1));
  const li = $("spb-list").querySelector("li.sel"); if (li) li.scrollIntoView({ block: "nearest" });
  const g = SPB.group;
  $("spb-status").textContent = g ? `${g.name} · ${g.named ? `${g.named} events · ` : ""}${g.count} feeds${g.live ? ` · ${g.live} live` : ""}` : "";
  $("spb-foot").innerHTML = g
    ? "<b>OK</b> Watch &nbsp; <b>Esc</b> Back to groups &nbsp; Event feeds only have video while the event is on."
    : "<b>◀ ▶</b> Tabs &nbsp; <b>OK</b> Watch / Record &nbsp; <b>Esc</b> Close";
}
async function openGroupBasic(name) {
  try { SPB.group = await api(`/api/sports/group?name=${encodeURIComponent(name)}`); } catch (e) { toast(e.message); return; }
  SPB.checkState = null; SPB.showSlots = false;
  renderSportsBasic(SPB.group.feeds.length ? 2 : 0);
  // feeds checked more than 15 minutes ago (or never): look again, in the background
  if (SPB.group.checkable && now() - (SPB.group.checked || 0) > 900) startCheckBasic();
}
async function startCheckBasic() {
  const g = SPB.group; if (!g) return;
  let st;
  try { st = await api("/api/sports/check", { method: "POST", body: { group: g.name } }); } catch (e) { toast(e.message); return; }
  if (!st.running) { toast("Nothing to check"); return; }
  SPB.checkState = st; renderSportsBasic();
  clearInterval(SPB.poll);
  SPB.poll = setInterval(async () => {
    try { SPB.checkState = await api("/api/sports/check"); } catch (_) { return; }
    if (!SPB.group || SPB.group.name !== g.name) { clearInterval(SPB.poll); return; }
    if (!SPB.checkState.running) {
      clearInterval(SPB.poll); SPB.poll = null;
      const keep = selIndex($("spb-list"));
      try { SPB.group = await api(`/api/sports/group?name=${encodeURIComponent(g.name)}`); } catch (_) {}
      const gi = (SPB.data.groups || []).find((x) => x.name === g.name); if (gi) gi.live = SPB.group.live;
      renderSportsBasic(keep);
      toast(SPB.group.live ? `${SPB.group.live} ${g.name} feed${SPB.group.live === 1 ? " is" : "s are"} live now` : `Nothing live on ${g.name} right now`);
    } else if (top() === "sports") renderSportsBasic();
  }, 1500);
}
function sportsKeyBasic(k) {
  if (k === "Back") { if (SPB.group) { SPB.group = null; renderSportsBasic(0); } else closeSports(); return; }
  if (k === "t") { closeSports(); return; }
  if (k === "ArrowLeft" || k === "ArrowRight") {
    SPB.tab = (SPB.tab + (k === "ArrowRight" ? 1 : -1) + SPB_TABS.length) % SPB_TABS.length;
    SPB.group = null; SPB.moved = true; renderSportsBasic(0); return;
  }
  listNav($("spb-list"), k, (_, i) => { const it = SPB.items[i]; if (it && it.act) it.act(); });
}
$("spb-tabs").addEventListener("click", (e) => {
  const t = e.target.closest(".tab"); if (!t) return;
  SPB.tab = +t.dataset.t; SPB.group = null; SPB.moved = true; renderSportsBasic(0);
});
$("spb-list").addEventListener("click", (e) => {
  const li = e.target.closest("li"); if (!li) return;
  const it = SPB.items[+li.dataset.i]; if (it && it.act) it.act();
});

