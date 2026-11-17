// Admin tools (loaded only when signed in): select + delete photos, make cover, reorder albums, frame the cover.
const csrf = document.querySelector('meta[name="csrf-token"]')?.content ?? "";
const post = (url, body) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(body),
  });
const $ = (id) => document.getElementById(id);

// ---- Select photos: delete them, or make one the cover ----------------------------------------
const grid = $("grid");
const coverData = $("cover-data");

if (grid) {
  const toggle = $("sel-toggle"), del = $("sel-delete"), mk = $("sel-cover");
  toggle.hidden = false;
  let selecting = false;
  const chosen = () => [...grid.querySelectorAll(".tile.selected")];

  const refresh = () => {
    const n = chosen().length;
    del.hidden = !selecting;
    del.disabled = n === 0;
    del.textContent = n ? `Delete ${n}` : "Delete";
    mk.hidden = !selecting || !coverData;
    mk.disabled = n !== 1;
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
    tiles.forEach((t) => t.remove());
    refresh();
  });

  mk.addEventListener("click", async () => {
    const [tile] = chosen();
    const r = await post(`/admin/albums/${coverData.dataset.slug}/cover`, { photo_id: Number(tile.dataset.photoId) });
    if (r.ok) location.reload();
    else alert("Could not set the cover.");
  });
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

// ---- Frame the cover: drag the photo around inside the card, or show it uncropped -------------
if (coverData) {
  const btn = $("cover-edit");
  btn.hidden = false;
  const d = coverData.dataset;
  const iw = Number(d.w), ih = Number(d.h);

  btn.addEventListener("click", () => {
    const pos = { x: Number(d.x), y: Number(d.y) };
    const dlg = document.createElement("dialog");
    dlg.className = "cover-dialog";
    dlg.innerHTML = `
      <div class="cover"><img alt="" draggable="false"></div>
      <label><input type="checkbox" id="cover-full"> Show the whole photo (no cropping)</label>
      <div class="row"><button type="button" id="cover-cancel">Cancel</button>
        <button type="button" id="cover-save" class="btn btn-primary">Save</button></div>`;
    document.body.append(dlg);
    const frame = dlg.querySelector(".cover"), img = dlg.querySelector("img"), full = dlg.querySelector("#cover-full");
    img.src = d.src;
    full.checked = d.fit === "full";
    frame.style.setProperty("--ar", (iw / ih).toFixed(4));

    const paint = () => {
      frame.classList.toggle("cover--full", full.checked);
      frame.style.setProperty("--x", `${pos.x}%`);
      frame.style.setProperty("--y", `${pos.y}%`);
    };
    full.addEventListener("change", paint);

    let drag = null;
    frame.addEventListener("pointerdown", (e) => {
      if (full.checked) return;
      drag = { x: e.clientX, y: e.clientY, px: pos.x, py: pos.y };
      frame.setPointerCapture(e.pointerId);
    });
    frame.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const fw = frame.clientWidth, fh = frame.clientHeight;
      const scale = Math.max(fw / iw, fh / ih);       // how object-fit: cover scales the photo
      const overX = iw * scale - fw, overY = ih * scale - fh;  // how much sticks out each way
      const clamp = (v) => Math.min(100, Math.max(0, v));
      if (overX > 0.5) pos.x = clamp(drag.px - ((e.clientX - drag.x) / overX) * 100);
      if (overY > 0.5) pos.y = clamp(drag.py - ((e.clientY - drag.y) / overY) * 100);
      paint();
    });
    frame.addEventListener("pointerup", () => (drag = null));

    const close = () => { dlg.close(); dlg.remove(); };
    dlg.querySelector("#cover-cancel").addEventListener("click", close);
    dlg.addEventListener("cancel", () => dlg.remove());
    dlg.querySelector("#cover-save").addEventListener("click", async () => {
      const r = await post(`/admin/albums/${d.slug}/crop`, { x: pos.x, y: pos.y, fit: full.checked ? "full" : "crop" });
      if (!r.ok) return alert("Could not save.");
      Object.assign(d, { x: pos.x, y: pos.y, fit: full.checked ? "full" : "crop" });
      close();
    });
    paint();
    dlg.showModal();
  });
}
