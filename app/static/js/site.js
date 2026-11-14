// Light/dark theme toggle. The saved choice is applied before first paint by a tiny inline
// script in <head>; this wires up the button. Dark is the default.
(() => {
  const root = document.documentElement;
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  const meta = document.querySelector('meta[name="theme-color"]');
  const colours = { dark: "#0a1128", light: "#f5f7fc" };

  const sync = () => {
    const light = root.dataset.theme === "light";
    btn.setAttribute("aria-pressed", String(light));
    btn.title = light ? "Switch to dark theme" : "Switch to light theme";
    if (meta) meta.content = light ? colours.light : colours.dark;
  };

  btn.addEventListener("click", () => {
    const next = root.dataset.theme === "light" ? "dark" : "light";
    root.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch { /* storage blocked: choice lasts this visit only */ }
    sync();
  });
  sync();
})();
