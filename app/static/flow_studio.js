(() => {
  const root = document.getElementById("studio");
  if (!root) return;
  const flowId = root.dataset.flowId;
  const NODE_W = 168;
  const NODE_H = 56;

  const TRIGGERS = [
    ["always", "Siempre"],
    ["default", "Si no coincide otra"],
    ["keyword", "Palabras clave"],
    ["regex", "Regex"],
    ["is_digit", "Es un número"],
    ["is_image", "Es una foto"],
    ["found", "Producto encontrado"],
    ["not_found", "Producto no encontrado"],
    ["transition", "Transición IA"],
  ];

  const NODE_HELP = {
    start: "Entrada del chat. Conectalo al primer nodo (casi siempre Esperar texto).",
    message: "Texto que envía el bot. Podés usar {{product_name}} {{qty}} {{order_code}} {{total}} {{currency}}.",
    catalog: "Elegí productos, subí una foto de portada y conectá una salida (casi siempre a Esperar texto).",
    wait_input: "Pausa hasta el próximo mensaje. Acá se configuran las salidas: palabras, número, foto o “si no coincide”.",
    wait_payment: "Espera el comprobante. Salida típica: “Es una foto” hacia Guardar comprobante.",
    match_product: "Busca el producto por número o nombre. Usá salidas encontrado / no encontrado.",
    create_order: "Crea el pedido y manda el QR. Podés cobrar adelanto % + envío local o interior.",
    attach_proof: "Guarda la foto del comprobante. Conectá a un mensaje con {{confirm_detail}}.",
    capture: "Guarda un dato (ciudad, tipo de reunión). value fijo o el último texto del cliente.",
    schedule_fulfillment: "Pregunta envío local, otro departamento o reunión y agenda día/hora.",
    order_status: "Responde el estado del pedido abierto.",
    cancel_order: "Cancela el pedido y libera stock.",
    ai_reply: "Llama al motor-ia. Las salidas de tipo Transición IA usan la clave (buy, human, default).",
    handoff: "Pausa el bot y avisa que atiende una persona.",
    end: "Reinicia el flujo al nodo Inicio.",
  };

  let flow = null;
  let selectedId = null;
  let connectFrom = null;
  let drag = null;

  let catalogProducts = [];
  let catalogLoaded = false;

  const nodesEl = document.getElementById("fs-nodes");
  const edgesEl = document.getElementById("fs-edges");
  const inspector = document.getElementById("fs-inspector");
  const msgEl = document.getElementById("fs-msg");
  const nameEl = document.getElementById("fs-name");
  const statusEl = document.getElementById("fs-status");
  const switchEl = document.getElementById("fs-switch");

  function uid(prefix) {
    return prefix + Math.random().toString(36).slice(2, 9);
  }

  function def() {
    if (!flow.definition) flow.definition = { nodes: [], edges: [] };
    if (!flow.definition.nodes) flow.definition.nodes = [];
    if (!flow.definition.edges) flow.definition.edges = [];
    return flow.definition;
  }

  function defaultConfig(type) {
    if (type === "message" || type === "handoff") return { text: "" };
    if (type === "ai_reply") {
      return { system_hint: "", fallback_transition: "human", min_confidence: 0.65 };
    }
    if (type === "create_order") {
      return { deposit_percent: 50, shipping_local: "20", shipping_interior: "40", skip_schedule: true };
    }
    if (type === "schedule_fulfillment") {
      return {
        modes: "delivery,shipping,meeting",
        office_address: "",
        meeting_link: "",
      };
    }
    if (type === "capture") return { var: "city", value: "" };
    if (type === "catalog") {
      return {
        text: "Este es el catálogo. Tocá un producto o escribí el número.",
        button: "Ver productos",
        skus: "",
        category: "",
        limit: 20,
        image_count: 4,
        cover_url: "",
        send_list: true,
      };
    }
    return {};
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function nodeCenter(id) {
    const n = def().nodes.find((x) => x.id === id);
    if (!n) return null;
    return { x: n.x + NODE_W / 2, y: n.y + 22 };
  }

  function setMsg(text) {
    msgEl.textContent = text || "";
  }

  function nodeName(id) {
    const n = def().nodes.find((x) => x.id === id);
    return n ? n.name || n.type : id;
  }

  function render() {
    const d = def();
    nodesEl.innerHTML = "";
    d.nodes.forEach((node) => {
      const el = document.createElement("div");
      el.className = "fs-node " + node.type + (node.id === selectedId ? " on" : "");
      el.style.left = node.x + "px";
      el.style.top = node.y + "px";
      el.dataset.id = node.id;
      el.innerHTML =
        '<div class="fs-type">' +
        (node.type || "") +
        '</div><div class="fs-title"></div>' +
        '<button type="button" class="fs-port in" data-port="in" title="Entrada"></button>' +
        '<button type="button" class="fs-port out" data-port="out" title="Conectar"></button>';
      el.querySelector(".fs-title").textContent = node.name || node.type;
      el.querySelector(".fs-title").dataset.role = "title";
      el.addEventListener("mousedown", onNodeDown);
      el.querySelector(".fs-port.out").addEventListener("click", (ev) => {
        ev.stopPropagation();
        connectFrom = node.id;
        setMsg("Clic en el nodo destino para conectar");
      });
      el.querySelector(".fs-port.in").addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (connectFrom && connectFrom !== node.id) {
          addEdge(connectFrom, node.id);
          connectFrom = null;
          setMsg("Conectado");
          render();
        }
      });
      nodesEl.appendChild(el);
    });
    drawEdges();
    fillWire();
    renderInspector();
  }

  function drawEdges() {
    const d = def();
    const parts = [
      '<defs><marker id="fs-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#6b7c90"></path></marker></defs>',
    ];
    d.edges.forEach((edge) => {
      const a = nodeCenter(edge.from);
      const b = nodeCenter(edge.to);
      if (!a || !b) return;
      const dx = Math.max(40, Math.abs(b.x - a.x) / 2);
      const dpath = `M ${a.x + NODE_W / 2} ${a.y} C ${a.x + NODE_W / 2 + dx} ${a.y}, ${b.x - NODE_W / 2 - dx} ${b.y}, ${b.x - NODE_W / 2} ${b.y}`;
      const labelX = (a.x + b.x) / 2;
      const labelY = (a.y + b.y) / 2 - 6;
      const label = (edge.trigger_type || "always") + (edge.trigger_key ? ":" + edge.trigger_key : "");
      parts.push(
        `<path d="${dpath}" fill="none" stroke="#6b7c90" stroke-width="2" marker-end="url(#fs-arrow)"></path>`
      );
      parts.push(
        `<text x="${labelX}" y="${labelY}" fill="#9aa7b8" font-size="10" text-anchor="middle">${escapeHtml(
          label.slice(0, 42)
        )}</text>`
      );
    });
    edgesEl.innerHTML = parts.join("");
  }

  function onNodeDown(ev) {
    if (ev.target.closest(".fs-port")) return;
    const id = ev.currentTarget.dataset.id;
    const node = def().nodes.find((n) => n.id === id);
    if (!node) return;
    selectedId = id;
    if (connectFrom && connectFrom !== id) {
      addEdge(connectFrom, id);
      connectFrom = null;
      setMsg("Conectado");
      render();
      return;
    }
    drag = {
      id,
      ox: ev.clientX - node.x,
      oy: ev.clientY - node.y,
      moved: false,
    };
    render();
  }

  window.addEventListener("mousemove", (ev) => {
    if (!drag) return;
    const node = def().nodes.find((n) => n.id === drag.id);
    if (!node) return;
    const nx = Math.max(8, ev.clientX - drag.ox);
    const ny = Math.max(8, ev.clientY - drag.oy);
    if (Math.abs(nx - node.x) > 2 || Math.abs(ny - node.y) > 2) drag.moved = true;
    node.x = nx;
    node.y = ny;
    const el = nodesEl.querySelector('[data-id="' + drag.id + '"]');
    if (el) {
      el.style.left = node.x + "px";
      el.style.top = node.y + "px";
    }
    drawEdges();
  });
  window.addEventListener("mouseup", () => {
    drag = null;
  });

  function addEdge(from, to, triggerType, triggerKey) {
    def().edges.push({
      id: uid("e_"),
      from,
      to,
      trigger_type: triggerType || "always",
      trigger_key: triggerKey || "",
    });
  }

  function addNode(type) {
    const wrap = document.getElementById("fs-canvas-wrap");
    const count = def().nodes.length;
    def().nodes.push({
      id: uid("n_"),
      type,
      name: type,
      x: 40 + (wrap ? wrap.scrollLeft : 0) + (count % 4) * 28,
      y: 40 + (wrap ? wrap.scrollTop : 0) + (count % 5) * 24,
      config: defaultConfig(type),
    });
    selectedId = def().nodes[def().nodes.length - 1].id;
    setMsg("Nodo agregado. Configuralo a la derecha y agregá una salida.");
    render();
    inspector.scrollIntoView({ block: "nearest" });
  }

  function fillWire() {
    const fromEl = document.getElementById("wire-from");
    const toEl = document.getElementById("wire-to");
    if (!fromEl || !toEl) return;
    const nodes = def().nodes;
    const opts = nodes
      .map((n) => `<option value="${n.id}">${escapeHtml(n.name || n.type)}</option>`)
      .join("");
    const prevFrom = fromEl.value;
    const prevTo = toEl.value;
    fromEl.innerHTML = opts;
    toEl.innerHTML = opts;
    if (selectedId && nodes.some((n) => n.id === selectedId)) fromEl.value = selectedId;
    else if (nodes.some((n) => n.id === prevFrom)) fromEl.value = prevFrom;
    if (nodes.some((n) => n.id === prevTo)) toEl.value = prevTo;
    else if (nodes.length > 1) {
      const other = nodes.find((n) => n.id !== fromEl.value);
      if (other) toEl.value = other.id;
    }
  }

  function catalogHtml(node) {
    const cfg = node.config;
    const picked = new Set(
      String(cfg.skus || "")
        .split(",")
        .map((s) => s.trim().toLowerCase())
        .filter(Boolean)
    );
    const cover = cfg.cover_url || "";
    const list = catalogProducts
      .map((p) => {
        const on = !picked.size || picked.has(String(p.sku).toLowerCase());
        const img = p.image_url
          ? `<img src="${escapeHtml(p.image_url)}" alt="">`
          : `<span class="fs-ph">sin foto</span>`;
        return `<div class="fs-cat-item">
          <label>
            <input type="checkbox" data-sku="${escapeHtml(p.sku)}"${on ? " checked" : ""}>
            ${img}
            <span><strong>${escapeHtml(p.name)}</strong><br><em>${escapeHtml(p.sku)}</em></span>
          </label>
          <label class="fs-cat-up">Foto
            <input type="file" accept="image/*" data-product-id="${escapeHtml(p.id)}">
          </label>
        </div>`;
      })
      .join("");
    return `<h2>Catálogo</h2>
      <label>Texto intro<br><textarea id="insp-text"></textarea></label>
      <label>Texto del botón<br><input id="insp-button" maxlength="20"></label>
      <label>Categoría (vacío = todas)<br><input id="insp-category"></label>
      <label>Máx. productos<br><input id="insp-limit" type="number" min="1" max="50"></label>
      <label>Fotos de productos a enviar<br><input id="insp-images" type="number" min="0" max="10"></label>
      <label class="fs-check"><input type="checkbox" id="insp-list"${
        cfg.send_list === false || cfg.send_list === "false" ? "" : " checked"
      }> Enviar lista de productos</label>
      <label>Foto de portada (subir o pegar URL)<br>
        <input id="insp-cover" placeholder="https://… o /uploads/…">
        <input type="file" id="insp-cover-file" accept="image/*">
      </label>
      ${cover ? `<img class="fs-cover-prev" src="${escapeHtml(cover)}" alt="portada">` : ""}
      <p class="muted">Productos del inventario. Desmarcá los que no quieras mostrar. Subí una foto a cada uno o una portada para mandar el catálogo como imagen.</p>
      <div class="fs-cat-list">${
        catalogLoaded
          ? list || "<p class='muted'>No hay productos activos. Cargalos en Inventario.</p>"
          : "<p class='muted'>Cargando productos…</p>"
      }</div>
      <p><a href="/inventario" target="_blank">Abrir inventario</a></p>`;
  }

  function triggerOptions(selected) {
    return TRIGGERS.map(
      ([value, label]) =>
        `<option value="${value}"${value === (selected || "always") ? " selected" : ""}>${escapeHtml(label)}</option>`
    ).join("");
  }

  async function uploadFile(file) {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/flujos/upload", { method: "POST", body: fd });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "No se pudo subir");
    return body.url;
  }

  async function ensureCatalog() {
    if (catalogLoaded) return;
    const res = await fetch("/api/flujos/catalogo");
    catalogLoaded = true;
    if (!res.ok) return;
    const body = await res.json();
    catalogProducts = body.products || [];
  }

  function bindCatalog(node) {
    if (node.type !== "catalog") return;
    if (!catalogLoaded) {
      ensureCatalog().then(() => renderInspector());
    }
    const btn = inspector.querySelector("#insp-button");
    if (btn) {
      btn.value = node.config.button || "Ver productos";
      btn.addEventListener("input", (e) => {
        node.config.button = e.target.value;
      });
    }
    const cat = inspector.querySelector("#insp-category");
    if (cat) {
      cat.value = node.config.category || "";
      cat.addEventListener("input", (e) => {
        node.config.category = e.target.value;
      });
    }
    const lim = inspector.querySelector("#insp-limit");
    if (lim) {
      lim.value = node.config.limit ?? 20;
      lim.addEventListener("input", (e) => {
        node.config.limit = Number(e.target.value);
      });
    }
    const imgs = inspector.querySelector("#insp-images");
    if (imgs) {
      imgs.value = node.config.image_count ?? 4;
      imgs.addEventListener("input", (e) => {
        node.config.image_count = Number(e.target.value);
      });
    }
    const list = inspector.querySelector("#insp-list");
    if (list) {
      list.addEventListener("change", () => {
        node.config.send_list = list.checked;
      });
    }
    const cover = inspector.querySelector("#insp-cover");
    if (cover) {
      cover.value = node.config.cover_url || "";
      cover.addEventListener("input", (e) => {
        node.config.cover_url = e.target.value;
      });
    }
    const coverFile = inspector.querySelector("#insp-cover-file");
    if (coverFile) {
      coverFile.addEventListener("change", async () => {
        if (!coverFile.files[0]) return;
        try {
          setMsg("Subiendo portada…");
          const url = await uploadFile(coverFile.files[0]);
          node.config.cover_url = url;
          setMsg("Portada subida. Guardá el flujo.");
          renderInspector();
        } catch (err) {
          setMsg(err.message || "Error al subir");
        }
      });
    }
    inspector.querySelectorAll(".fs-cat-item [data-sku]").forEach((box) => {
      box.addEventListener("change", () => {
        const skus = Array.from(inspector.querySelectorAll(".fs-cat-item [data-sku]:checked")).map(
          (el) => el.dataset.sku
        );
        const all = inspector.querySelectorAll(".fs-cat-item [data-sku]").length;
        node.config.skus = skus.length === all ? "" : skus.join(",");
      });
    });
    inspector.querySelectorAll("input[data-product-id]").forEach((input) => {
      input.addEventListener("change", async () => {
        if (!input.files[0]) return;
        const fd = new FormData();
        fd.append("file", input.files[0]);
        setMsg("Subiendo foto…");
        const res = await fetch("/api/flujos/productos/" + input.dataset.productId + "/foto", {
          method: "POST",
          body: fd,
        });
        const body = await res.json();
        if (!res.ok) {
          setMsg(body.detail || "Error al subir");
          return;
        }
        const prod = catalogProducts.find((p) => p.id === input.dataset.productId);
        if (prod) prod.image_url = body.url;
        setMsg("Foto de producto guardada.");
        renderInspector();
      });
    });
  }

  function renderInspector() {
    const node = def().nodes.find((n) => n.id === selectedId);
    if (!node) {
      inspector.innerHTML = '<p class="muted">Seleccioná un nodo en el lienzo o agregá uno desde la paleta.</p>';
      return;
    }
    node.config = node.config || {};
    const outs = def().edges.filter((e) => e.from === node.id);
    const others = def().nodes.filter((n) => n.id !== node.id);
    let html = `<h2>Configurar nodo</h2>
      <label>Nombre<br><input id="insp-name" value=""></label>
      <p class="muted">Tipo: ${escapeHtml(node.type)}</p>
      <p class="fs-help">${escapeHtml(NODE_HELP[node.type] || "Conectá salidas para definir qué pasa después.")}</p>`;
    if (node.type === "message" || node.type === "handoff") {
      html += `<label>Texto que envía<br><textarea id="insp-text"></textarea></label>
        <p class="muted">Variables: {{product_name}} {{qty}} {{order_code}} {{total}} {{currency}}</p>`;
    }
    if (node.type === "ai_reply") {
      html += `<label>Hint al motor<br><textarea id="insp-hint"></textarea></label>
        <label>Fallback<br><input id="insp-fallback" value=""></label>
        <label>Min. confianza<br><input id="insp-conf" type="number" min="0" max="1" step="0.05"></label>`;
    }
    if (node.type === "catalog") {
      html += catalogHtml(node);
    }
    if (node.type === "create_order") {
      html += `<label>Adelanto %<br><input id="insp-deposit" type="number" min="1" max="100"></label>
        <label>Envío local (BOB)<br><input id="insp-ship-local"></label>
        <label>Envío otro departamento (BOB)<br><input id="insp-ship-int"></label>
        <label class="fs-check"><input type="checkbox" id="insp-skip-sched"> Ya agendé antes del QR</label>`;
    }
    if (node.type === "schedule_fulfillment") {
      html += `<label>Modos (delivery,shipping,meeting)<br><input id="insp-modes"></label>
        <label>Dirección oficina<br><textarea id="insp-office"></textarea></label>
        <label>Link reunión virtual<br><input id="insp-link"></label>`;
    }
    if (node.type === "capture") {
      html += `<label>Variable<br><input id="insp-var"></label>
        <label>Valor fijo (vacío = último mensaje)<br><input id="insp-value"></label>`;
    }
    html += `<h2>Salidas</h2>
      <p class="muted">Una salida es “si pasa esto, ir a este nodo”.</p>`;
    if (!outs.length) {
      html += `<p class="fs-warn">Este nodo no tiene salidas. Sin eso el flujo se corta acá.</p>`;
    }
    outs.forEach((edge) => {
      html += `<div class="fs-edge-row">
        <select data-edge="${edge.id}" data-field="trigger_type">${triggerOptions(edge.trigger_type)}</select>
        <input data-edge="${edge.id}" data-field="trigger_key" placeholder="palabras / clave" value="">
        <button type="button" class="btn" data-del-edge="${edge.id}">x</button>
      </div>
      <p class="muted">→ ${escapeHtml(nodeName(edge.to))}</p>`;
    });
    html += `<div class="fs-add-edge">
      <label>Conectar con<br>
        <select id="insp-to">
          <option value="">Elegí un nodo…</option>
          ${others
            .map((n) => `<option value="${n.id}">${escapeHtml(n.name || n.type)}</option>`)
            .join("")}
        </select>
      </label>
      <label>Cuando<br><select id="insp-trig">${triggerOptions("always")}</select></label>
      <label>Palabras / clave<br><input id="insp-key" placeholder="hola,menu  ·  o buy"></label>
      <button type="button" class="btn primary" id="insp-link">Agregar salida</button>
    </div>
    <button type="button" class="btn danger" id="insp-del">Eliminar nodo</button>`;
    inspector.innerHTML = html;
    inspector.querySelector("#insp-name").value = node.name || "";
    inspector.querySelector("#insp-name").addEventListener("input", (e) => {
      node.name = e.target.value;
      const title = nodesEl.querySelector('[data-id="' + node.id + '"] .fs-title');
      if (title) title.textContent = node.name || node.type;
    });
    const textEl = inspector.querySelector("#insp-text");
    if (textEl) {
      textEl.value = node.config.text || "";
      textEl.addEventListener("input", (e) => {
        node.config.text = e.target.value;
      });
    }
    bindCatalog(node);
    const dep = inspector.querySelector("#insp-deposit");
    if (dep) {
      dep.value = node.config.deposit_percent ?? 50;
      dep.addEventListener("input", (e) => {
        node.config.deposit_percent = Number(e.target.value);
      });
    }
    const sl = inspector.querySelector("#insp-ship-local");
    if (sl) {
      sl.value = node.config.shipping_local || "20";
      sl.addEventListener("input", (e) => {
        node.config.shipping_local = e.target.value;
      });
    }
    const si = inspector.querySelector("#insp-ship-int");
    if (si) {
      si.value = node.config.shipping_interior || "40";
      si.addEventListener("input", (e) => {
        node.config.shipping_interior = e.target.value;
      });
    }
    const skip = inspector.querySelector("#insp-skip-sched");
    if (skip) {
      skip.checked = node.config.skip_schedule !== false;
      skip.addEventListener("change", () => {
        node.config.skip_schedule = skip.checked;
      });
    }
    const modes = inspector.querySelector("#insp-modes");
    if (modes) {
      modes.value = node.config.modes || "delivery,shipping,meeting";
      modes.addEventListener("input", (e) => {
        node.config.modes = e.target.value;
      });
    }
    const office = inspector.querySelector("#insp-office");
    if (office) {
      office.value = node.config.office_address || "";
      office.addEventListener("input", (e) => {
        node.config.office_address = e.target.value;
      });
    }
    const meet = inspector.querySelector("#insp-link");
    if (meet) {
      meet.value = node.config.meeting_link || "";
      meet.addEventListener("input", (e) => {
        node.config.meeting_link = e.target.value;
      });
    }
    const vname = inspector.querySelector("#insp-var");
    if (vname) {
      vname.value = node.config.var || "";
      vname.addEventListener("input", (e) => {
        node.config.var = e.target.value;
      });
    }
    const vval = inspector.querySelector("#insp-value");
    if (vval) {
      vval.value = node.config.value || "";
      vval.addEventListener("input", (e) => {
        node.config.value = e.target.value;
      });
    }
    const hintEl = inspector.querySelector("#insp-hint");
    if (hintEl) {
      hintEl.value = node.config.system_hint || "";
      hintEl.addEventListener("input", (e) => {
        node.config.system_hint = e.target.value;
      });
    }
    const fb = inspector.querySelector("#insp-fallback");
    if (fb) {
      fb.value = node.config.fallback_transition || "human";
      fb.addEventListener("input", (e) => {
        node.config.fallback_transition = e.target.value;
      });
    }
    const conf = inspector.querySelector("#insp-conf");
    if (conf) {
      conf.value = node.config.min_confidence ?? 0.65;
      conf.addEventListener("input", (e) => {
        node.config.min_confidence = Number(e.target.value);
      });
    }
    inspector.querySelectorAll("[data-edge]").forEach((el) => {
      const edge = def().edges.find((x) => x.id === el.dataset.edge);
      if (!edge) return;
      if (el.dataset.field === "trigger_key") el.value = edge.trigger_key || "";
      el.addEventListener("change", () => {
        edge[el.dataset.field] = el.value;
        drawEdges();
      });
      el.addEventListener("input", () => {
        edge[el.dataset.field] = el.value;
        drawEdges();
      });
    });
    inspector.querySelectorAll("[data-del-edge]").forEach((btn) => {
      btn.addEventListener("click", () => {
        def().edges = def().edges.filter((e) => e.id !== btn.dataset.delEdge);
        render();
      });
    });
    inspector.querySelector("#insp-link").addEventListener("click", () => {
      const to = inspector.querySelector("#insp-to").value;
      if (!to) {
        setMsg("Elegí a qué nodo conectar");
        return;
      }
      addEdge(
        node.id,
        to,
        inspector.querySelector("#insp-trig").value,
        inspector.querySelector("#insp-key").value.trim()
      );
      setMsg("Salida agregada. Guardá el flujo.");
      render();
    });
    inspector.querySelector("#insp-del").addEventListener("click", () => {
      def().nodes = def().nodes.filter((n) => n.id !== node.id);
      def().edges = def().edges.filter((e) => e.from !== node.id && e.to !== node.id);
      selectedId = null;
      render();
    });
  }

  async function save() {
    const res = await fetch("/api/flujos/" + flowId, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: nameEl.value || flow.name,
        description: flow.description || "",
        definition: def(),
      }),
    });
    const body = await res.json();
    if (!res.ok) {
      setMsg((body.errors || [body.detail || "Error"]).join(" · "));
      return false;
    }
    flow = body.flow;
    setMsg("Guardado");
    return true;
  }

  async function publish() {
    const ok = await save();
    if (!ok) return;
    const res = await fetch("/api/flujos/" + flowId + "/publish", { method: "POST" });
    const body = await res.json();
    if (!res.ok) {
      setMsg((body.errors || [body.detail || "Error"]).join(" · "));
      return;
    }
    location.reload();
  }

  async function unpublish() {
    const res = await fetch("/api/flujos/" + flowId + "/unpublish", { method: "POST" });
    if (res.ok) location.reload();
  }

  async function destroyFlow() {
    const published = root.dataset.status === "published";
    const msg = published
      ? "¿Eliminar este flujo? WhatsApp volverá al bot de código."
      : "¿Eliminar este flujo? No se puede deshacer.";
    if (!confirm(msg)) return;
    const res = await fetch("/api/flujos/" + flowId, { method: "DELETE" });
    if (res.ok) {
      location.href = "/flujos?ok=eliminado";
      return;
    }
    const body = await res.json().catch(() => ({}));
    setMsg(body.detail || "No se pudo eliminar");
  }

  async function simulate() {
    const ok = await save();
    if (!ok) return;
    const out = document.getElementById("fs-sim-out");
    out.textContent = "…";
    const res = await fetch("/api/flujos/" + flowId + "/simular", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone: document.getElementById("fs-sim-phone").value,
        text: document.getElementById("fs-sim-text").value,
      }),
    });
    const body = await res.json();
    out.textContent = (body.replies || []).join("\n\n") || JSON.stringify(body, null, 2);
  }

  async function loadSwitcher() {
    if (!switchEl) return;
    const res = await fetch("/api/flujos");
    if (!res.ok) return;
    const body = await res.json();
    switchEl.innerHTML = (body.flows || [])
      .map((item) => {
        const mark = item.id === body.active_id ? " · en uso" : "";
        const sel = item.id === flowId ? " selected" : "";
        return `<option value="${item.id}"${sel}>${escapeHtml(item.name)}${mark}</option>`;
      })
      .join("");
    switchEl.addEventListener("change", () => {
      if (switchEl.value && switchEl.value !== flowId) {
        location.href = "/flujos/" + switchEl.value + "/editar";
      }
    });
  }

  document.querySelectorAll(".fs-pal-item").forEach((btn) => {
    btn.addEventListener("click", () => addNode(btn.dataset.type));
  });
  document.getElementById("fs-save").addEventListener("click", save);
  const pub = document.getElementById("fs-publish");
  if (pub) pub.addEventListener("click", publish);
  const unp = document.getElementById("fs-unpublish");
  if (unp) unp.addEventListener("click", unpublish);
  const delFlow = document.getElementById("fs-delete");
  if (delFlow) delFlow.addEventListener("click", destroyFlow);
  document.getElementById("fs-sim").addEventListener("click", simulate);
  document.getElementById("fs-sim-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter") simulate();
  });
  const wireBtn = document.getElementById("wire-link");
  if (wireBtn) {
    wireBtn.addEventListener("click", () => {
      if (!flow) return;
      const from = document.getElementById("wire-from").value;
      const to = document.getElementById("wire-to").value;
      if (!from || !to || from === to) {
        setMsg("Elegí dos nodos distintos");
        return;
      }
      addEdge(
        from,
        to,
        document.getElementById("wire-trig").value,
        document.getElementById("wire-key").value.trim()
      );
      selectedId = from;
      setMsg("Conectado. Guardá el flujo.");
      render();
    });
  }

  fetch("/api/flujos/" + flowId)
    .then((r) => r.json())
    .then((data) => {
      flow = data;
      render();
      loadSwitcher();
    });
})();
