// Album cards with several highlight photos cycle between them: fade or slide, at the speed set in the admin panel.
// Reads data-mode ("fade" | "slide" | "off") and data-seconds from .album-list on every step, so changes apply live.
const list = document.querySelector(".album-list");

if (list && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
  const settings = () => ({ mode: list.dataset.mode || "fade", seconds: Math.max(1.5, Number(list.dataset.seconds) || 4) });
  const visible = new Set(); // only cards on screen animate
  const io = new IntersectionObserver((entries) =>
    entries.forEach((e) => (e.isIntersecting ? visible.add(e.target) : visible.delete(e.target))));
  const restarts = new Set();

  // Put a slide back at its resting place (off to the right) without animating the jump.
  const reset = (img) => {
    img.style.transition = "none";
    img.classList.remove("is-left");
    void img.offsetWidth;
    img.style.transition = "";
  };

  for (const cover of list.querySelectorAll(".cover")) {
    const slides = [...cover.querySelectorAll("img.slide")];
    if (slides.length < 2) continue;
    io.observe(cover);
    let current = 0;
    let timer = null;

    const advance = (mode) => {
      const from = slides[current];
      current = (current + 1) % slides.length;
      const to = slides[current];
      if (mode === "slide") {
        if (to.classList.contains("is-left")) reset(to);
        from.classList.remove("is-active");
        from.classList.add("is-left");
        to.classList.add("is-active");
        from.addEventListener("transitionend", function done(e) {
          if (e.propertyName !== "transform") return;
          from.removeEventListener("transitionend", done);
          if (!from.classList.contains("is-active")) reset(from);
        });
      } else {
        from.classList.remove("is-active", "is-left");
        to.classList.add("is-active");
      }
    };

    const schedule = (ms) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const { mode, seconds } = settings();
        list.style.setProperty("--t", `${Math.min(0.8, seconds * 0.3)}s`);
        if (mode !== "off" && visible.has(cover) && !document.hidden) advance(mode);
        schedule(seconds * 1000);
      }, ms);
    };

    // Cards start at different moments, so they don't all change in lockstep.
    schedule(settings().seconds * 1000 * (0.3 + Math.random() * 0.7));
    restarts.add(() => schedule(700));
  }

  // The admin panel's preview announces changed settings.
  list.addEventListener("highlights:changed", () => restarts.forEach((restart) => restart()));
}
