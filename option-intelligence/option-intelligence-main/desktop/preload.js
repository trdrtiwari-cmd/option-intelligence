// Tells the page it runs inside the Windows app; the connection page can also change the site address.
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("oiDesktop", {
  platform: "windows", version: "1.20.42",
  setUrl: url => ipcRenderer.invoke("oi:set-url", url),
  resetUrl: () => ipcRenderer.invoke("oi:reset-url"),
});
