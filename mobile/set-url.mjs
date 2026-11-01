// Points the native app at your hosted site. Usage:  APP_URL=https://your-service.onrender.com npm run android:sync
import { readFileSync, writeFileSync } from "node:fs";
const file = new URL("./capacitor.config.json", import.meta.url);
const cfg = JSON.parse(readFileSync(file, "utf8"));
const url = (process.env.APP_URL || "").trim().replace(/\/+$/, "");
if (url) {
  if (!/^https:\/\/[^/\s]+$/i.test(url)) { console.error("APP_URL must look like https://your-service.onrender.com (https, no path)"); process.exit(1); }
  cfg.server.url = url;
  writeFileSync(file, JSON.stringify(cfg, null, 2) + "\n");
}
if (/YOUR-SERVICE/.test(cfg.server.url)) { console.error("Set APP_URL to your deployed site first, e.g. APP_URL=https://option-intelligence.onrender.com"); process.exit(1); }
console.log("App will load:", cfg.server.url);
