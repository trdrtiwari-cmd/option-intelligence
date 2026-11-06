// Option Intelligence for Windows (v1.20.41).
// A desktop window around the live terminal on Render - the same app as the website and the Android APK, so every website update
// arrives without a new download and there is no local engine (no Python, no database on this PC) to break.
const { app, BrowserWindow, Menu, shell, Notification } = require("electron");
const fs = require("fs");
const path = require("path");

const APP_ID = "com.optionintelligence.desktop";
let APP_URL = "https://option-intelligence-vqtl.onrender.com";
try { APP_URL = JSON.parse(fs.readFileSync(path.join(__dirname, "app-url.json"), "utf8")).url || APP_URL; } catch { /* keep default */ }
const ORIGIN = new URL(APP_URL).origin;

app.setAppUserModelId(APP_ID);          // Windows shows web notifications (signal alerts) as normal toasts only with this
if (!app.requestSingleInstanceLock()) app.quit();

let win = null;
const stateFile = () => path.join(app.getPath("userData"), "window.json");
function loadState() {
  try { return JSON.parse(fs.readFileSync(stateFile(), "utf8")); } catch { return { width: 1400, height: 900 }; }
}
function saveState() {
  if (!win || win.isDestroyed()) return;
  try { fs.writeFileSync(stateFile(), JSON.stringify({ ...win.getNormalBounds(), maximized: win.isMaximized() })); } catch { /* ignore */ }
}

function showOffline(reason) {
  if (!win || win.isDestroyed()) return;
  win.loadFile(path.join(__dirname, "offline.html"), { query: { url: APP_URL, reason: String(reason || "") } });
}

function createWindow() {
  const st = loadState();
  win = new BrowserWindow({
    x: st.x, y: st.y, width: st.width || 1400, height: st.height || 900, minWidth: 360, minHeight: 500,
    title: "Option Intelligence", backgroundColor: "#0b1220", autoHideMenuBar: true, show: false,
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
      backgroundThrottling: false,      // live feed, alerts and the AI view keep running while the window is minimised
      spellcheck: false,
    },
  });
  if (st.maximized) win.maximize();
  win.once("ready-to-show", () => win.show());
  ["resize", "move", "close"].forEach(ev => win.on(ev, saveState));

  // Links to other sites (Upstox login, TradingView, GitHub ...) open in the normal browser; the app's own pages stay here.
  win.webContents.setWindowOpenHandler(({ url }) => {
    try { if (new URL(url).origin === ORIGIN) return { action: "allow" }; } catch { /* fall through */ }
    shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    let o = "";
    try { o = new URL(url).origin; } catch { /* ignore */ }
    if (url.startsWith("file:") || o === ORIGIN) return;
    // the Upstox sign-in must come back to the app, so it stays inside the window; everything else opens in the browser
    if (/upstox\.com/i.test(o)) return;
    e.preventDefault();
    shell.openExternal(url);
  });
  win.webContents.on("did-fail-load", (_e, code, desc, url, isMain) => {
    if (isMain && code !== -3 && !String(url).startsWith("file:")) showOffline(desc || code);   // -3 = aborted by a new navigation
  });
  win.webContents.on("render-process-gone", (_e, d) => showOffline("page stopped: " + (d && d.reason)));
  win.loadURL(APP_URL);
}

function buildMenu() {
  const tpl = [
    { label: "App", submenu: [
      { label: "Terminal", accelerator: "CmdOrCtrl+1", click: () => win && win.loadURL(APP_URL + "/") },
      { label: "Signals", accelerator: "CmdOrCtrl+2", click: () => win && win.loadURL(APP_URL + "/signals") },
      { label: "Diagnostics", accelerator: "CmdOrCtrl+3", click: () => win && win.loadURL(APP_URL + "/diagnostics") },
      { type: "separator" },
      { label: "Open in browser", click: () => shell.openExternal(win ? win.webContents.getURL().replace(/^file:.*/, APP_URL) : APP_URL) },
      { label: "Test notification", click: () => new Notification({ title: "Option Intelligence", body: "Notifications work on this PC." }).show() },
      { type: "separator" }, { role: "quit", label: "Exit" },
    ] },
    { label: "View", submenu: [
      { role: "reload", accelerator: "F5" }, { role: "forceReload" }, { type: "separator" },
      { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" },
      { role: "toggleDevTools", accelerator: "CmdOrCtrl+Shift+I" },
    ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(tpl));
}

app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
app.whenReady().then(() => {
  // the site asks for notification permission for signal alerts - allow it (and nothing else that needs hardware)
  const ses = require("electron").session.defaultSession;
  ses.setPermissionRequestHandler((_wc, perm, cb) => cb(["notifications", "clipboard-sanitized-write", "fullscreen"].includes(perm)));
  buildMenu();
  createWindow();
});
app.on("window-all-closed", () => app.quit());
