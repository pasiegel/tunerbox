// Colour theme: Setup › Settings › Screens › Theme for every screen; a TV screen can pick its own in
// Menu › Theme (kept in that browser). Loaded first in <head>, so the page never flashes the wrong colours.
(function () {
  var LIST = [["midnight", "Midnight"], ["classic", "Classic Blue"], ["retro", "Retro 90s"],
              ["contrast", "High Contrast"], ["light", "Light"]];
  var ids = LIST.map(function (t) { return t[0]; });
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch (e) {} }
  function apply(t) { document.documentElement.setAttribute("data-theme", ids.indexOf(t) >= 0 ? t : "midnight"); }
  apply(get("tb.theme") || get("tb.themeServer"));
  window.tbTheme = {
    list: LIST,
    label: function (t) { for (var i = 0; i < LIST.length; i++) if (LIST[i][0] === t) return LIST[i][1]; return "Midnight"; },
    // the server's choice (from /api/settings): used unless this screen picked its own
    server: function (t) { set("tb.themeServer", t); apply(get("tb.theme") || t); },
    own: function () { return get("tb.theme") || ""; },
    // this screen's own choice ("" = follow Setup)
    pick: function (t) { set("tb.theme", t); apply(t || get("tb.themeServer")); },
  };
})();
