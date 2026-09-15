(() => {
  const body = document.body;
  const slug = body.dataset.slug;
  const wa = body.dataset.wa;
  const currency = body.dataset.currency || "BOB";
  const store = body.dataset.store || "";
  const home = body.dataset.home || (slug ? "/t/" + slug : "/");
  const key = "tienda:" + slug;
  const countEl = document.getElementById("cart-count");
  const linesEl = document.getElementById("cart-lines");
  const totalEl = document.getElementById("cart-total");
  const waLink = document.getElementById("cart-wa");
  const drawer = document.getElementById("drawer");
  const mega = document.getElementById("mega");
  const catMenu = document.getElementById("cat-menu");
  const catToggle = document.getElementById("cat-toggle");
  const searchQ = document.getElementById("search-q");
  const noResults = document.getElementById("no-results");
  const searchForm = document.getElementById("search-form");
  let activeCat = "";

  const COLOR_MAP = {
    negro: "#1a1a1a",
    blanco: "#f4f4f4",
    gris: "#8a8a8a",
    beige: "#d9c7a5",
    rojo: "#c0392b",
    azul: "#284980",
    verde: "#2d8a4e",
    rosa: "#e8a0b0",
    amarillo: "#f8c530",
  };

  function load() {
    try {
      return JSON.parse(localStorage.getItem(key) || "[]");
    } catch {
      return [];
    }
  }
  function save(items) {
    localStorage.setItem(key, JSON.stringify(items));
    render(items);
  }
  function money(n) {
    const v = Number(n);
    return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }
  function render(items) {
    if (!countEl || !linesEl || !totalEl || !waLink) return;
    const qty = items.reduce((n, i) => n + i.qty, 0);
    const total = items.reduce((n, i) => n + i.qty * Number(i.price), 0);
    countEl.textContent = String(qty);
    linesEl.innerHTML =
      items
        .map(
          (i, idx) =>
            `<li>
              <span>${i.name}</span>
              <span class="qty-row">
                <button type="button" data-act="minus" data-i="${idx}">−</button>
                ${i.qty}
                <button type="button" data-act="plus" data-i="${idx}">+</button>
              </span>
              <span>${money(i.qty * Number(i.price))}</span>
            </li>`
        )
        .join("") || "<li>Vacío</li>";
    totalEl.textContent = money(total);
    const textLines = items.map((i) => `- ${i.qty} x ${i.name} (${i.sku})`).join("\n");
    const text = encodeURIComponent(
      `Hola, quiero pedir desde la tienda ${store}:\n${textLines}\nTotal: ${money(total)} ${currency}`
    );
    waLink.href = items.length && wa ? `https://wa.me/${wa}?text=${text}` : "#";
  }

  function addItem(sku, name, price, qty = 1) {
    const items = load();
    const found = items.find((i) => i.sku === sku && i.name === name);
    if (found) found.qty += qty;
    else items.push({ sku, name, price, qty });
    save(items);
    if (drawer) drawer.hidden = false;
  }

  document.querySelectorAll(".shop-add").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (btn.disabled) return;
      addItem(btn.dataset.sku, btn.dataset.name, btn.dataset.price);
    });
  });
  document.getElementById("cart-open")?.addEventListener("click", () => {
    if (drawer) drawer.hidden = false;
  });
  document.getElementById("cart-close")?.addEventListener("click", () => {
    if (drawer) drawer.hidden = true;
  });
  drawer?.addEventListener("click", (ev) => {
    if (ev.target === drawer) drawer.hidden = true;
  });
  linesEl?.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const items = load();
    const i = Number(btn.dataset.i);
    if (btn.dataset.act === "plus") items[i].qty += 1;
    if (btn.dataset.act === "minus") {
      items[i].qty -= 1;
      if (items[i].qty < 1) items.splice(i, 1);
    }
    save(items);
  });

  document.getElementById("mega-open")?.addEventListener("click", () => {
    if (mega) mega.hidden = !mega.hidden;
  });
  document.addEventListener("click", (ev) => {
    if (mega && !ev.target.closest(".bar-nav")) mega.hidden = true;
    if (catMenu && !ev.target.closest(".search-cat")) catMenu.hidden = true;
  });

  catToggle?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (catMenu) catMenu.hidden = !catMenu.hidden;
  });
  catMenu?.addEventListener("click", (ev) => {
    const li = ev.target.closest("li");
    if (!li) return;
    activeCat = li.dataset.cat || "";
    if (catToggle?.firstChild) catToggle.firstChild.nodeValue = (li.textContent || "Todas").trim() + " ";
    catMenu.hidden = true;
    if (!document.querySelector(".card")) {
      window.location.href = home + (activeCat ? "#colecciones" : "#catalogo");
      return;
    }
    applyFilter();
  });

  function applyFilter() {
    const cards = document.querySelectorAll(".card");
    if (!cards.length) return;
    const q = (searchQ?.value || "").trim().toLowerCase();
    let shown = 0;
    cards.forEach((card) => {
      const hay = (card.dataset.name || "").toLowerCase();
      const cat = card.dataset.cat || "";
      const okCat = !activeCat || cat === activeCat;
      const okQ = !q || hay.includes(q);
      const on = okCat && okQ;
      card.hidden = !on;
      if (on) shown += 1;
    });
    document.querySelectorAll("[data-filter]").forEach((el) => {
      el.classList.toggle("is-on", el.dataset.filter === activeCat && !!activeCat);
    });
    if (noResults) noResults.hidden = shown > 0 || cards.length === 0;
  }

  searchForm?.addEventListener("submit", (ev) => {
    if (!document.querySelector(".card")) {
      const q = (searchQ?.value || "").trim();
      if (!q) return;
      ev.preventDefault();
      window.location.href = home + "#catalogo";
      return;
    }
    ev.preventDefault();
    applyFilter();
  });
  searchQ?.addEventListener("input", applyFilter);
  document.querySelectorAll("[data-filter]").forEach((el) => {
    el.addEventListener("click", () => {
      activeCat = activeCat === el.dataset.filter ? "" : el.dataset.filter;
      if (catToggle?.firstChild) catToggle.firstChild.nodeValue = (activeCat || "Todas") + " ";
      applyFilter();
      document.getElementById("catalogo")?.scrollIntoView({ behavior: "smooth" });
    });
  });
  document.getElementById("to-top")?.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  document.querySelectorAll(".is-swatch .opt-btn").forEach((btn) => {
    const hex = COLOR_MAP[(btn.dataset.value || "").toLowerCase()];
    if (hex) btn.style.background = hex;
  });
  document.querySelectorAll(".pdp-opt").forEach((field) => {
    field.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".opt-btn");
      if (!btn) return;
      field.querySelectorAll(".opt-btn").forEach((b) => b.classList.toggle("is-on", b === btn));
    });
  });

  function selectedOptions() {
    return [...document.querySelectorAll(".pdp-opt")]
      .map((field) => {
        const key = field.dataset.opt;
        const on = field.querySelector(".opt-btn.is-on");
        return key && on ? `${key} ${on.dataset.value}` : "";
      })
      .filter(Boolean);
  }

  const qtyInput = document.getElementById("pdp-qty");
  document.getElementById("qty-minus")?.addEventListener("click", () => {
    if (!qtyInput) return;
    qtyInput.value = String(Math.max(1, Number(qtyInput.value || 1) - 1));
  });
  document.getElementById("qty-plus")?.addEventListener("click", () => {
    if (!qtyInput) return;
    qtyInput.value = String(Math.max(1, Number(qtyInput.value || 1) + 1));
  });
  document.getElementById("pdp-add")?.addEventListener("click", (ev) => {
    const btn = ev.currentTarget;
    if (btn.disabled) return;
    const extra = selectedOptions().join(" · ");
    const name = extra ? `${btn.dataset.name} · ${extra}` : btn.dataset.name;
    const qty = Math.max(1, Number(qtyInput?.value || 1));
    addItem(btn.dataset.sku, name, btn.dataset.price, qty);
  });

  const mainImg = document.getElementById("pdp-main");
  document.getElementById("pdp-thumbs")?.addEventListener("click", (ev) => {
    const thumb = ev.target.closest(".pdp-thumb");
    if (!thumb) return;
    document.querySelectorAll(".pdp-thumb").forEach((el) => el.classList.toggle("is-on", el === thumb));
    if (mainImg) mainImg.src = thumb.dataset.src;
  });
  const lightbox = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightbox-img");
  function openLightbox() {
    if (!lightbox || !mainImg) return;
    lightboxImg.src = mainImg.src;
    lightbox.hidden = false;
  }
  document.getElementById("pdp-zoom")?.addEventListener("click", openLightbox);
  mainImg?.addEventListener("click", openLightbox);
  document.getElementById("lightbox-close")?.addEventListener("click", () => {
    if (lightbox) lightbox.hidden = true;
  });
  lightbox?.addEventListener("click", (ev) => {
    if (ev.target === lightbox) lightbox.hidden = true;
  });

  document.getElementById("spec-more")?.addEventListener("click", () => {
    document.querySelectorAll(".spec-extra").forEach((el) => {
      el.hidden = false;
    });
    document.getElementById("spec-more").hidden = true;
  });

  render(load());
})();
