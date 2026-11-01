// Points the native app at your hosted site. Usage:  APP_URL=https://your-service.onrender.com npm run android:sync
import { readFileSync, writeFileSync } from "node:fs";
const file = new URL("./capacitor.config.json", import.meta.url);
const cfg = JSON.parse(readFileSync(file, "utf8"));
let raw = (process.env.APP_URL || "").trim();
if (raw) {
  if (!/^[a-z]+:\/\//i.test(raw)) raw = "https://" + raw;          // allow "my-app.onrender.com"
  let u;
  try { u = new URL(raw); } catch { console.error("APP_URL is not a valid address: " + raw); process.exit(1); }
  if (u.protocol !== "https:") { console.error("APP_URL must start with https:// (got " + u.protocol + ")"); process.exit(1); }
  cfg.server.url = u.origin;                                       // drops any path such as /login
  writeFileSync(file, JSON.stringify(cfg, null, 2) + "\n");
}
if (/YOUR-SERVICE/.test(cfg.server.url)) { console.error("No site address was given. Run the workflow again and fill in the 'Your deployed site' box with your Render address."); process.exit(1); }
console.log("App will load:", cfg.server.url);
