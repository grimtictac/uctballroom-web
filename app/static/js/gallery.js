// PhotoSwipe lightbox for album grids: caption, download, share and deep links (#photo-<id>).
import PhotoSwipeLightbox from "../vendor/photoswipe/photoswipe-lightbox.esm.min.js";

const grid = document.getElementById("grid");

if (grid) {
  const lightbox = new PhotoSwipeLightbox({
    gallery: grid,
    children: "a.tile",
    bgOpacity: 0.96,
    pswpModule: () => import("../vendor/photoswipe/photoswipe.esm.min.js"),
    // Tiles are appended by htmx as you scroll; children are looked up on each click.
  });

  const HASH_RE = /^#photo-(\d+)$/;
  const tileData = (pswp) => pswp.currSlide?.data.element?.dataset ?? {};
  let pushed = false; // did we add a history entry when opening?
  let closingFromPop = false;

  const svg = (path) =>
    `<svg class="pswp__icn" viewBox="0 0 32 32" width="32" height="32" aria-hidden="true"><path d="${path}" fill="currentColor"/></svg>`;

  lightbox.on("uiRegister", () => {
    const ui = lightbox.pswp.ui;

    ui.registerElement({
      name: "caption",
      order: 9,
      isButton: false,
      appendTo: "root",
      onInit: (el, pswp) => {
        el.className = "pswp__custom-caption";
        pswp.on("change", () => {
          const { caption, credit } = tileData(pswp);
          el.replaceChildren();
          for (const [cls, text] of [["caption-text", caption], ["caption-credit", credit && `Photo: ${credit}`]]) {
            if (!text) continue;
            const p = document.createElement("p");
            p.className = cls;
            p.textContent = text;
            el.append(p);
          }
          el.hidden = !el.childNodes.length;
        });
      },
    });

    if (document.body.hasAttribute("data-admin")) {
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content ?? "";
      const send = (url, body) => fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(body),
      });
      const cover = document.getElementById("cover-data");
      const word = (text) => `<span class="pswp__icn" style="font:600 .8rem/32px sans-serif;padding:0 .4rem">${text}</span>`;

      if (cover) {
        ui.registerElement({
          name: "cover-button", order: 5, isButton: true, title: "Make cover", ariaLabel: "Make cover", html: word("Cover"),
          onClick: async () => {
            const id = Number(tileData(lightbox.pswp).photoId);
            const r = await send(`/admin/albums/${cover.dataset.slug}/cover`, { photo_id: id });
            if (r.ok) location.reload();
          },
        });
      }
      ui.registerElement({
        name: "delete-button", order: 4, isButton: true, title: "Delete photo", ariaLabel: "Delete photo", html: word("Delete"),
        onClick: async () => {
          const tile = lightbox.pswp.currSlide?.data.element;
          if (!tile || !confirm("Delete this photo? This can't be undone.")) return;
          const r = await send("/admin/photos/delete", { ids: [Number(tile.dataset.photoId)] });
          if (!r.ok) return alert("Could not delete. Try again.");
          tile.remove();
          lightbox.pswp.close();
        },
      });
    }

    ui.registerElement({
      name: "download-button",
      order: 8,
      isButton: true,
      tagName: "a",
      title: "Download original",
      ariaLabel: "Download original",
      html: svg("M20.5 14.3 17.1 18V10h-2.2v8l-3.4-3.7-1.6 1.5L16 22.2l6.1-6.4zM25 24H7v2h18z"),
      onInit: (el, pswp) => {
        el.target = "_blank";
        el.rel = "noopener";
        el.setAttribute("download", "");
        pswp.on("change", () => { el.href = tileData(pswp).original || "#"; });
      },
    });

    ui.registerElement({
      name: "share-button",
      order: 7,
      isButton: true,
      title: "Share",
      ariaLabel: "Share",
      html: svg("M21 21a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM11 14a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm10-7a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM12.7 15.1l6.6-3.7.8 1.4-6.6 3.7zm0 1.8.8-1.4 6.6 3.7-.8 1.4z"),
      onClick: async (e, el) => {
        const url = new URL(tileData(lightbox.pswp).page, location.origin).href;
        try {
          if (navigator.share) await navigator.share({ url });
          else {
            await navigator.clipboard.writeText(url);
            el.title = "Link copied";
            setTimeout(() => (el.title = "Share"), 2000);
          }
        } catch { /* user cancelled the share sheet */ }
      },
    });
  });

  // --- Deep links -------------------------------------------------------------------------
  const setHash = (pswp, replace) => {
    const id = tileData(pswp).photoId;
    if (!id) return;
    const url = `${location.pathname}${location.search}#photo-${id}`;
    history[replace ? "replaceState" : "pushState"]({ pswp: true }, "", url);
  };

  lightbox.on("afterInit", () => {
    const pswp = lightbox.pswp;
    pushed = !HASH_RE.test(location.hash); // opened from a click, not from a shared link
    setHash(pswp, !pushed);
    pswp.on("change", () => setHash(pswp, true));
  });

  lightbox.on("close", () => {
    if (closingFromPop) return;
    if (pushed) history.back(); // pops our entry; popstate fires with the hash gone
    else history.replaceState(null, "", location.pathname + location.search);
  });

  lightbox.on("destroy", () => { closingFromPop = false; });

  window.addEventListener("popstate", () => {
    const pswp = lightbox.pswp;
    if (pswp && !HASH_RE.test(location.hash)) {
      closingFromPop = true;
      pswp.close();
    }
  });

  lightbox.init();

  // Open a shared link such as /albums/o-week-2027#photo-123.
  const m = HASH_RE.exec(location.hash);
  if (m) {
    const tiles = [...grid.querySelectorAll("a.tile")];
    const index = tiles.findIndex((t) => t.dataset.photoId === m[1]);
    if (index >= 0) lightbox.loadAndOpen(index, { gallery: grid });
    else location.replace(`/photo/${m[1]}`); // not on the first page: show the standalone page
  }
}
