// Admin tools (loaded only when signed in): select + delete photos, make cover, reorder albums, frame the cover.
const csrf = document.querySelector('meta[name="csrf-token"]')?.content ?? "";
const post = (url, body) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(body),
  });
const $ = (id) => document.getElementById(id);

// ---- Select photos: delete them, or add/remove them as album highlights ----------------------------
const grid = $("grid");
const hdata = $("highlight-data") ? JSON.parse($("highlight-data").textContent) : null;

if (grid) {
  const toggle = $("sel-toggle"), del = $("sel-delete"), star = $("sel-star"), unstar = $("sel-unstar");
  toggle.hidden = false;
  let selecting = false;
  const chosen = () => [...grid.querySelectorAll(".tile.selected")];

  const refresh = () => {
    const n = chosen().length;
    del.hidden = !selecting;
    del.disabled = n === 0;
    del.textContent = n ? `Delete ${n}` : "Delete";
    for (const b of [star, unstar]) {
      b.hidden = !selecting || !hdata;
      b.disabled = n === 0;
    }
    toggle.textContent = selecting ? "Done" : "Select";
  };

  toggle.addEventListener("click", () => {
    selecting = !selecting;
    grid.classList.toggle("selecting", selecting);
    if (!selecting) chosen().forEach((t) => t.classList.remove("selected"));
    refresh();
  });

  // Capture phase, so the lightbox never opens while selecting.
  grid.addEventListener("click", (e) => {
    const tile = e.target.closest("a.tile");
    if (!selecting || !tile) return;
    e.preventDefault();
    e.stopPropagation();
    tile.classList.toggle("selected");
    refresh();
  }, true);

  del.addEventListener("click", async () => {
    const tiles = chosen();
    if (!tiles.length || !confirm(`Delete ${tiles.length} photo${tiles.length === 1 ? "" : "s"}? This can't be undone.`)) return;
    const r = await post("/admin/photos/delete", { ids: tiles.map((t) => Number(t.dataset.photoId)) });
    if (!r.ok) return alert("Could not delete. Try again.");
    const gone = new Set(tiles.map((t) => Number(t.dataset.photoId)));
    hdata.slides = hdata.slides.filter((sl) => !gone.has(sl.id));
    tiles.forEach((t) => t.remove());
    refresh();
  });

  // Highlight / un-highlight the selected photos (in album order). Reloads so the framing dialog sees the new set.
  const highlight = (button, field) => button.addEventListener("click", async () => {
    const ids = chosen().map((t) => Number(t.dataset.photoId));
    const r = await post(`/admin/albums/${hdata.slug}/highlights`, { [field]: ids });
    if (r.ok) return location.reload();
    const message = (await r.json().catch(() => ({}))).error;
    alert(message || "Could not update the highlights.");
  });
  if (hdata) {
    highlight(star, "add");
    highlight(unstar, "remove");
  }
  refresh();
}

// ---- Reorder albums by dragging the cards -----------------------------------------------------
const list = document.querySelector(".album-list");
if (list) {
  let dragged = null;
  list.addEventListener("dragstart", (e) => {
    dragged = e.target.closest(".album-card");
    if (!dragged) return;
    dragged.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
  });
  list.addEventListener("dragover", (e) => {
    if (!dragged) return;
    e.preventDefault();
    const over = e.target.closest(".album-card");
    if (!over || over === dragged) return;
    const r = over.getBoundingClientRect();
    over.parentNode.insertBefore(dragged, e.clientX - r.left > r.width / 2 ? over.nextSibling : over);
  });
  list.addEventListener("dragend", async () => {
    if (!dragged) return;
    dragged.classList.remove("dragging");
    dragged = null;
    const slugs = [...list.querySelectorAll(".album-card")].map((c) => c.dataset.slug);
    const r = await post("/admin/albums/order", { slugs });
    if (!r.ok) alert("Could not save the new order.");
  });
}

// ---- Frame the highlights: drag each photo around inside the card, or show photos uncropped --------
if (hdata && hdata.slides.length) {
  const btn = $("cover-edit");
  btn.hidden = false;

  btn.addEventListener("click", () => {
    const slides = hdata.slides.map((sl) => ({ ...sl }));  // working copies: nothing changes until Save
    let k = 0;
    const dlg = document.createElement("dialog");
    dlg.className = "cover-dialog";
    dlg.innerHTML = `
      <div class="cover"><img class="slide is-active" alt="" draggable="false"></div>
      <div class="nav"${slides.length > 1 ? "" : " hidden"}><button type="button" id="cover-prev" aria-label="Previous">‹</button>
        <span id="cover-count"></span><button type="button" id="cover-next" aria-label="Next">›</button></div>
      <label><input type="checkbox" id="cover-full"> Show whole photos (no cropping)</label>
      <div class="row"><button type="button" id="cover-cancel">Cancel</button>
        <button type="button" id="cover-save" class="btn btn-primary">Save</button></div>`;
    document.body.append(dlg);
    const frame = dlg.querySelector(".cover"), img = dlg.querySelector("img"), full = dlg.querySelector("#cover-full");
    full.checked = hdata.fit === "full";
    frame.style.setProperty("--ar", (slides[0].w / slides[0].h).toFixed(4)); // the card takes its shape from the first photo

    const paint = () => {
      const sl = slides[k];
      if (img.getAttribute("src") !== sl.src) img.src = sl.src;
      img.style.objectPosition = `${sl.x}% ${sl.y}%`;
      frame.classList.toggle("cover--full", full.checked);
      dlg.querySelector("#cover-count").textContent = `${k + 1} / ${slides.length}`;
    };
    full.addEventListener("change", paint);
    dlg.querySelector("#cover-prev").addEventListener("click", () => { k = (k + slides.length - 1) % slides.length; paint(); });
    dlg.querySelector("#cover-next").addEventListener("click", () => { k = (k + 1) % slides.length; paint(); });

    let drag = null;
    frame.addEventListener("pointerdown", (e) => {
      if (full.checked) return;
      drag = { x: e.clientX, y: e.clientY, px: slides[k].x, py: slides[k].y };
      frame.setPointerCapture(e.pointerId);
    });
    frame.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const sl = slides[k];
      const fw = frame.clientWidth, fh = frame.clientHeight;
      const scale = Math.max(fw / sl.w, fh / sl.h);          // how object-fit: cover scales the photo
      const overX = sl.w * scale - fw, overY = sl.h * scale - fh;  // how much sticks out each way
      const clamp = (v) => Math.min(100, Math.max(0, v));
      if (overX > 0.5) sl.x = clamp(drag.px - ((e.clientX - drag.x) / overX) * 100);
      if (overY > 0.5) sl.y = clamp(drag.py - ((e.clientY - drag.y) / overY) * 100);
      paint();
    });
    frame.addEventListener("pointerup", () => (drag = null));

    const close = () => { dlg.close(); dlg.remove(); };
    dlg.querySelector("#cover-cancel").addEventListener("click", close);
    dlg.addEventListener("cancel", () => dlg.remove());
    dlg.querySelector("#cover-save").addEventListener("click", async () => {
      const fit = full.checked ? "full" : "crop";
      const body = hdata.fallback
        ? { fit, x: slides[0].x, y: slides[0].y }
        : { fit, items: slides.map((sl) => ({ photo_id: sl.id, x: sl.x, y: sl.y })) };
      const r = await post(`/admin/albums/${hdata.slug}/crop`, body);
      if (!r.ok) return alert("Could not save.");
      hdata.fit = fit;
      hdata.slides = slides;
      close();
    });
    paint();
    dlg.showModal();
  });
}

// ---- Panel: how the album highlights are displayed --------------------------------------------
const panel = $("panel-form");
if (panel) {
  const preview = document.querySelector(".panel-preview");
  const seconds = panel.elements.seconds, output = $("panel-seconds"), status = $("panel-status");
  const current = () => ({ mode: panel.elements.mode.value, seconds: Number(seconds.value) });

  const apply = () => {  // the preview card follows the controls straight away
    const { mode, seconds: s } = current();
    output.textContent = `${s} s`;
    status.textContent = "";
    if (preview) {
      preview.dataset.mode = mode;
      preview.dataset.seconds = s;
      preview.dispatchEvent(new Event("highlights:changed"));
    }
  };
  panel.addEventListener("input", apply);
  panel.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await post(panel.dataset.save, current());
    status.textContent = r.ok ? "Saved" : "Could not save";
  });
}
