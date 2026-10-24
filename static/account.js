(function () {
  const $ = id => document.getElementById(id);
  async function api(url, opts) {
    const res = await fetch(url, Object.assign({ credentials: "same-origin", headers: { "Content-Type": "application/json" } }, opts || {}));
    let data = {}; try { data = await res.json(); } catch { }
    if (res.status === 401) { location.replace("/login?next=/account"); throw new Error("Login required"); }
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed (" + res.status + ")");
    return data;
  }
  function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : "—"; }
  let cache = null;
  async function load() {
    const me = await api("/api/auth/me"), d = await api("/api/userdata");
    cache = d;
    $("profile").textContent = `Signed in as ${me.user.username}${me.user.is_admin ? " (owner)" : ""} · account created ${when(me.user.created_at)}`;
    if (me.storage && me.storage.warning) { $("storageNote").textContent = me.storage.warning; $("storageNote").hidden = false; }
    const keys = Object.keys(d.data).sort(), box = $("savedList"); box.textContent = "";
    if (!keys.length) { box.textContent = "Nothing saved yet — it appears here after your first paper trade or setting change."; return; }
    keys.forEach(k => { const r = d.data[k], row = document.createElement("div"); const n = Array.isArray(r.value) ? r.value.length + " items" : typeof r.value === "object" ? "settings" : String(r.value);
      row.textContent = `${k} — ${n} · saved ${when(r.updated_at)} (v${r.version})`; box.appendChild(row); });
  }
  $("exportBtn").onclick = () => {
    if (!cache) return;
    const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), data: cache.data }, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "option-intelligence-data.json"; document.body.appendChild(a); a.click(); a.remove();
  };
  $("pwForm").addEventListener("submit", async ev => {
    ev.preventDefault(); const m = $("pwMsg"); m.textContent = "";
    if ($("newPw").value !== $("newPw2").value) { m.textContent = "The new passwords do not match."; return; }
    try { const r = await api("/api/auth/password", { method: "POST", body: JSON.stringify({ old_password: $("oldPw").value, new_password: $("newPw").value }) });
      m.textContent = "Password changed. " + (r.note || ""); $("pwForm").reset(); } catch (e) { m.textContent = e.message; }
  });
  $("delForm").addEventListener("submit", async ev => {
    ev.preventDefault(); const m = $("delMsg");
    if (!confirm("Delete this account and all saved data permanently?")) return;
    try { await api("/api/auth/delete", { method: "POST", body: JSON.stringify({ password: $("delPw").value }) });
      try { Object.keys(localStorage).filter(k => k.startsWith("option-intelligence-") || k.startsWith("oi-")).forEach(k => localStorage.removeItem(k)); } catch { }
      location.replace("/login"); } catch (e) { m.textContent = e.message; }
  });
  load().catch(e => { $("profile").textContent = e.message; });
})();
