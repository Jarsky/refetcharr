// The sign-in page: "create your login" until one exists, then "sign in".
const f = document.getElementById("login"), msg = document.getElementById("msg");
let creating = false;
async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : d.detail?.[0]?.msg || `Request failed (${r.status})`);
  return d;
}
(async () => {
  const s = await (await fetch("/api/auth/state")).json();
  if (s.signed_in) { location.replace("/"); return; }
  creating = !s.set;
  if (creating) {
    document.getElementById("title").textContent = "Create your login";
    document.getElementById("intro").textContent = "Refetcharr has no login yet. Choose the username and password that will protect it (at least 8 characters).";
    document.getElementById("confirm-row").hidden = false;
    f.password.autocomplete = "new-password";
    f.confirm.required = true;
    document.getElementById("go").textContent = "Create login";
  }
})();
f.addEventListener("submit", async (e) => {
  e.preventDefault();
  msg.textContent = "";
  if (creating && f.password.value !== f.confirm.value) { msg.textContent = "The passwords don't match."; return; }
  try {
    await post(creating ? "/api/auth/setup" : "/api/auth/login", { username: f.username.value, password: f.password.value, remember: f.remember.checked });
    location.replace("/");
  } catch (err) { msg.textContent = err.message; f.password.select(); }
});
