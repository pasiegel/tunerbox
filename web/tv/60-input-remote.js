// ---------------------------------------------------------------- input / remote mapping
// Every button (keyboard key, TV-remote key, gamepad button) has an input id, and
// bindings map input ids -> actions. Defaults < server-wide defaults < this device.
const ACTIONS = [
  ["ArrowUp", "Up"], ["ArrowDown", "Down"], ["ArrowLeft", "Left"], ["ArrowRight", "Right"],
  ["Enter", "OK / Select"], ["Back", "Back / Exit"],
  ["ChannelUp", "Channel up / Page up"], ["ChannelDown", "Channel down / Page down"],
  ["g", "Guide"], ["i", "Info"], ["m", "Menu"], ["l", "Last channel"],
  ["*", "Favorite: add / remove"], ["v", "Favorites guide / show all"],
  ["r", "Record"], ["d", "Recordings (DVR)"], ["/", "Search"], ["t", "Sports & PPV"],
  ["VolUp", "Volume up"], ["VolDown", "Volume down"], ["u", "Mute"],
  [" ", "Play / Pause"], ["s", "Stop playback"], ["f", "Full screen"], ["Power", "Power on / off"],
  ...[1, 2, 3, 4, 5, 6, 7, 8, 9, 0].map((n) => [String(n), `Number ${n}`]),
];
const ACTION_LABEL = Object.fromEntries(ACTIONS);
const DEFAULT_BINDINGS = {
  ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight",
  Enter: "Enter", Select: "Enter",
  Escape: "Back", Backspace: "Back", BrowserBack: "Back", GoBack: "Back", Exit: "Back",
  PageUp: "ChannelUp", ChannelUp: "ChannelUp", MediaTrackNext: "ChannelUp",
  PageDown: "ChannelDown", ChannelDown: "ChannelDown", MediaTrackPrevious: "ChannelDown",
  g: "g", Guide: "g", BrowserHome: "g", ColorF1Green: "g",
  i: "i", Info: "i", ColorF3Blue: "i",
  m: "m", ContextMenu: "m", Settings: "m",
  l: "l", LastNumberRedial: "l", ColorF2Yellow: "l",
  r: "r", MediaRecord: "r", ColorF0Red: "r",
  d: "d", DVR: "d",
  "*": "*", a: "*", Favorites: "v", v: "v",
  "/": "/", BrowserSearch: "/", Find: "/", F3: "/", t: "t",
  "+": "VolUp", "=": "VolUp", AudioVolumeUp: "VolUp",
  "-": "VolDown", "_": "VolDown", AudioVolumeDown: "VolDown",
  u: "u", AudioVolumeMute: "u",
  " ": " ", MediaPlayPause: " ", MediaPlay: " ", MediaPause: " ",
  s: "s", MediaStop: "s",
  f: "f", F11: "f", Power: "Power", PowerOff: "Power", p: "Power",
  ...Object.fromEntries([0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => [String(n), String(n)])),
  // standard-layout gamepads (Xbox / PlayStation / many Bluetooth remotes)
  "pad:b12": "ArrowUp", "pad:b13": "ArrowDown", "pad:b14": "ArrowLeft", "pad:b15": "ArrowRight",
  "pad:a1-": "ArrowUp", "pad:a1+": "ArrowDown", "pad:a0-": "ArrowLeft", "pad:a0+": "ArrowRight",
  "pad:b0": "Enter", "pad:b1": "Back", "pad:b2": "i", "pad:b3": "g",
  "pad:b4": "ChannelDown", "pad:b5": "ChannelUp", "pad:b6": "VolDown", "pad:b7": "VolUp",
  "pad:b8": "l", "pad:b9": "m",
};
let serverBindings = {};
const localBindings = () => { try { return JSON.parse(localStorage.getItem("tb.keys") || "{}"); } catch (_) { return {}; } };
const bindings = () => ({ ...DEFAULT_BINDINGS, ...serverBindings, ...localBindings() });
function setLocalBinding(id, action) {
  const b = localBindings(); b[id] = action; localStorage.setItem("tb.keys", JSON.stringify(b));
}
async function loadServerBindings() {
  try { S.settings = await api("/api/settings"); serverBindings = S.settings.keymap || {}; } catch (_) {}
}

function inputId(ev) {
  if (ev.key && ev.key !== "Unidentified" && ev.key !== "Process") return ev.key.length === 1 ? ev.key.toLowerCase() : ev.key;
  if (ev.code) return "code:" + ev.code;
  return "kc:" + ev.keyCode;
}
function prettyInput(id) {
  const names = { ArrowUp: "▲", ArrowDown: "▼", ArrowLeft: "◀", ArrowRight: "▶", " ": "Space", Escape: "Esc" };
  if (names[id]) return names[id];
  const pad = /^pad:b(\d+)$/.exec(id);
  if (pad) return `Pad ${["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Start", "LS", "RS", "D▲", "D▼", "D◀", "D▶", "Home"][+pad[1]] || "btn " + pad[1]}`;
  const ax = /^pad:a(\d+)([+-])$/.exec(id);
  if (ax) return `Stick ${ax[1] % 2 ? (ax[2] === "-" ? "▲" : "▼") : (ax[2] === "-" ? "◀" : "▶")}`;
  return id.length === 1 ? id.toUpperCase() : id;
}

// learn mode: the next button pressed gets bound to `learning`
let learning = null, learnTimer = null;
function startLearn(action) {
  learning = action;
  $("learn").classList.remove("hidden");
  $("learn-action").textContent = ACTION_LABEL[action];
  let left = 10;
  $("learn-count").textContent = left;
  clearInterval(learnTimer);
  learnTimer = setInterval(() => { left -= 1; $("learn-count").textContent = left; if (left <= 0) stopLearn(); }, 1000);
}
function stopLearn() { learning = null; clearInterval(learnTimer); $("learn").classList.add("hidden"); }

function dispatch(id, ev) {
  $("remote-last").textContent = `Last button: ${prettyInput(id)}  (${id})`;
  if (wake(true)) return true;
  if (learning) {
    const action = learning; stopLearn();
    setLocalBinding(id, action);
    toast(`${prettyInput(id)} → ${ACTION_LABEL[action]}`);
    if (top() === "remotecfg") renderRemote();
    return true;
  }
  const action = bindings()[id];
  if (S.mode === "off") { powerOn(); return true; }
  // never lock yourself out of the remote setup screen
  const fallback = top() === "remotecfg" && { ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", Enter: "Enter", Escape: "Back" }[id];
  if (!action && !fallback) return false;
  return handleKey(action || fallback, ev);
}

// remote setup panel
let remoteSel = 0;
function remoteEntries() {
  const b = bindings();
  const byAction = {};
  for (const [id, act] of Object.entries(b)) if (act) (byAction[act] = byAction[act] || []).push(id);
  const rows = ACTIONS.map(([act, label]) => ({
    label, right: (byAction[act] || []).filter((id) => !id.startsWith("pad:a")).map(prettyInput).join("  ·  ") || "—",
    act: () => actionDialog(act),
  }));
  rows.push({ label: "Save this mapping as the default for all devices", act: saveRemoteDefault });
  rows.push({ label: "Reset this device to defaults", act: () => { localStorage.removeItem("tb.keys"); toast("Remote mapping reset"); renderRemote(); } });
  rows.push({ label: "Done", act: closeRemote });
  return rows;
}
function renderRemote() {
  fillList($("remote-list"), remoteEntries(), remoteSel);
  const li = $("remote-list").querySelector("li.sel"); if (li) li.scrollIntoView({ block: "nearest" });
}
function openRemote() {
  hideBanner();
  remoteSel = 0; renderRemote();
  S.layer.push("remotecfg"); $("remotecfg").classList.remove("hidden");
}
function closeRemote() { stopLearn(); S.layer = S.layer.filter((l) => l !== "remotecfg"); $("remotecfg").classList.add("hidden"); }
function actionDialog(act) {
  const ids = Object.entries(bindings()).filter(([, a]) => a === act).map(([id]) => id);
  openDialog(ACTION_LABEL[act], ids.length ? `Buttons: ${ids.map(prettyInput).join(", ")}` : "No buttons assigned", [
    { label: "Add a button…", act: () => { closeDialog(); startLearn(act); } },
    { label: "Replace all buttons with a new one…", act: () => { closeDialog(); ids.forEach((id) => setLocalBinding(id, "")); renderRemote(); startLearn(act); } },
    { label: "Remove all buttons", act: () => { ids.forEach((id) => setLocalBinding(id, "")); closeDialog(); renderRemote(); } },
    { label: "Back", act: closeDialog },
  ]);
}
async function saveRemoteDefault() {
  const merged = { ...serverBindings, ...localBindings() };
  await api("/api/settings", { method: "POST", body: { keymap: merged } });
  serverBindings = merged; localStorage.removeItem("tb.keys");
  toast("Saved as the default remote mapping for every device");
}
$("remote-list").addEventListener("click", (e) => {
  const li = e.target.closest("li"); if (!li) return;
  remoteSel = +li.dataset.i; remoteEntries()[remoteSel].act();
});

function handleKey(k, ev) {
  if (k === "Power") { if (S.mode === "off") powerOn(); else powerOff(); return true; }
  if (S.mode === "off") { powerOn(); return true; }
  const layer = top();
  if (/^[0-9]$/.test(k) && !["dialog", "menu", "dvr", "remotecfg", "search", "sports"].includes(layer) && S.mode === "live") { digit(k); return true; }
  if (S.digits && k === "Enter") { commitDigits(); return true; }

  if (layer === "dialog") {
    if (k === "Back") closeDialog(); else listNav($("dlg-list"), k, (_, i) => dlg.entries[i].act());
    return true;
  }
  if (layer === "remotecfg") {
    if (k === "Back") { closeRemote(); return true; }
    listNav($("remote-list"), k, (_, i) => { remoteSel = i; remoteEntries()[i].act(); });
    const items = [...$("remote-list").querySelectorAll("li")];
    const i = items.findIndex((li) => li.classList.contains("sel"));
    if (i >= 0) remoteSel = i;
    return true;
  }
  if (layer === "search") { searchKey(k); return true; }
  if (layer === "sports") { sportsKey(k); return true; }
  if (layer === "menu") {
    if (k === "Back" || k === "m") closeMenu(); else listNav($("menu-list"), k, (_, i) => menuShown[i].act());
    return true;
  }
  if (layer === "dvr") {
    if (k === "Back" || k === "d") closeDvr(); else listNav($("dvr-list"), k, (_, i) => dvrPick(i));
    return true;
  }
  if (layer === "guide") {
    switch (k) {
      case "ArrowUp": G.move(-1); break;
      case "ArrowDown": G.move(1); break;
      case "ArrowLeft": G.horiz(-1); break;
      case "ArrowRight": G.horiz(1); break;
      case "ChannelUp": G.page(-1); break;
      case "ChannelDown": G.page(1); break;
      case "Enter": G.ok(); break;
      case "r": G.record(); break;
      case "*": G.fav(); break;
      case "v": G.toggleView(); break;
      case "g": case "Back": G.close(); break;
      case "m": G.close(); openMenu(); break;
      case "d": G.close(); openDvr(); break;
      case "/": G.close(); openSearch(); break;
      case "t": G.close(); openSports(); break;
      case "u": toggleMute(); break;
      case "VolUp": setVolume(video.volume + 0.05); break;
      case "VolDown": setVolume(video.volume - 0.05); break;
      default: return false;
    }
    return true;
  }
  if (S.mode === "playback") {
    switch (k) {
      case " ": case "Enter": video.paused ? video.play() : video.pause(); showPlaybackBar(); break;
      case "ArrowLeft": video.currentTime = Math.max(0, video.currentTime - 10); showPlaybackBar(); break;
      case "ArrowRight": video.currentTime += 30; showPlaybackBar(); break;
      case "ChannelUp": video.currentTime = Math.max(0, video.currentTime - 300); showPlaybackBar(); break;
      case "ChannelDown": video.currentTime += 300; showPlaybackBar(); break;
      case "Back":
        if (!$("banner").classList.contains("hidden")) hideBanner(); else backToLive();
        break;
      case "s": backToLive(); break;
      case "g": hideBanner(); G.open(); break;
      case "v": hideBanner(); G.open(true); break;
      case "i":
        if (!$("banner").classList.contains("hidden")) { $("banner").classList.toggle("full"); clearTimeout(S.bannerTimer); S.bannerTimer = setTimeout(hideBanner, 12000); }
        else { $("playback").classList.add("hidden"); recInfo(); }
        break;
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
  if (S.mode === "empty") {
    if (k === "Enter") openSetup();
    else if (k === "m") openMenu();
    else if (k === "f") toggleFull();
    else return false;
    return true;
  }
  // live TV
  switch (k) {
    case "ArrowUp": case "ChannelUp": chStep(1); break;
    case "ArrowDown": case "ChannelDown": chStep(-1); break;
    case "g": G.open(); break;
    case "v": G.open(true); break;
    case "*": toggleFav(curCh()); break;
    case "i": case "Enter":
      if (!$("banner").classList.contains("hidden") && k === "i") { $("banner").classList.toggle("full"); clearTimeout(S.bannerTimer); S.bannerTimer = setTimeout(hideBanner, 12000); }
      else showBanner(k === "i");
      break;
    case "Back": if (!$("banner").classList.contains("hidden")) hideBanner(); else if (S.lastIdx >= 0) tune(S.lastIdx); break;
    case "l": if (S.lastIdx >= 0) tune(S.lastIdx); break;
    case "m": openMenu(); break;
    case "d": openDvr(); break;
    case "/": openSearch(); break;
    case "t": openSports(); break;
    case "r": {
      const ch = curCh();
      if (ch && ch.feed) toast("Event feeds can't be recorded (they have no guide listing)");
      else if (ch) nowNext(ch.number, true).then((d) => d.now ? programDialog(ch, d.now) : toast("No guide information to record"));
      break;
    }
    case "u": toggleMute(); break;
    case "VolUp": case "ArrowRight": setVolume(video.volume + 0.05); break;
    case "VolDown": case "ArrowLeft": setVolume(video.volume - 0.05); break;
    case "f": toggleFull(); break;
    default: return false;
  }
  return true;
}
document.addEventListener("keydown", (ev) => {
  if (ev.ctrlKey || ev.altKey || ev.metaKey) return;
  try {
    if (top() === "search" && !learning && searchRawKey(ev)) return;
    if (dispatch(inputId(ev), ev)) ev.preventDefault();
  } catch (e) { report(`key ${ev.key}: ${e && (e.stack || e.message)}`); }
});

// gamepads: edge-triggered buttons + stick directions, with auto-repeat while held
(function gamepads() {
  const held = new Map();   // id -> next repeat time
  const poll = () => {
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    const down = new Set();
    for (const gp of pads) {
      if (!gp) continue;
      gp.buttons.forEach((b, i) => { if (b.pressed || b.value > 0.6) down.add(`pad:b${i}`); });
      gp.axes.slice(0, 4).forEach((v, i) => { if (v < -0.6) down.add(`pad:a${i}-`); else if (v > 0.6) down.add(`pad:a${i}+`); });
    }
    const t = performance.now();
    for (const id of down) {
      if (!held.has(id)) { held.set(id, t + 450); dispatch(id); }
      else if (t >= held.get(id) && !learning && /Arrow|Channel|Vol/.test(bindings()[id] || "")) { held.set(id, t + 140); dispatch(id); }
    }
    for (const id of [...held.keys()]) if (!down.has(id)) held.delete(id);
    requestAnimationFrame(poll);
  };
  window.addEventListener("gamepadconnected", (e) => toast(`Controller connected: ${e.gamepad.id.slice(0, 40)}`));
  requestAnimationFrame(poll);
})();
loadServerBindings();

