// Tells the page it runs inside the Windows app (nothing else is exposed to the page).
const { contextBridge } = require("electron");
contextBridge.exposeInMainWorld("oiDesktop", { platform: "windows", version: "1.20.41" });
