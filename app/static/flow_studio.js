(() => {
  const root = document.getElementById("studio");
  if (!root) return;
  const flowId = root.dataset.flowId;
  const NODE_W = 188;
  const MAX_BUTTONS = 10;

  const NODE_META = {
    start: { label: "Inicio" },
    message: { label: "Mensaje" },
    catalog: { label: "Catálogo" },
    buttons: { label: "Botones" },
    send_image: { label: "Imagen" },
    send_audio: { label: "Audio" },
    send_video: { label: "Video" },
    wait_input: { label: "Esperar respuesta" },
    wait_payment: { label: "Esperar foto de pago" },
    match_product: { label: "Buscar producto" },
    match_image: { label: "Reconocer foto" },
    create_order: { label: "Crear pedido y QR" },
    attach_proof: { label: "Guardar foto de pago" },
    capture: { label: "Guardar lo que dijo" },
    schedule_fulfillment: { label: "Agendar entrega o reunión" },
    schedule_call: { label: "Agendar llamada" },
    order_status: { label: "Estado del pedido" },
    cancel_order: { label: "Cancelar pedido" },
    ai_reply: { label: "Responder con IA" },
    handoff: { label: "Pasar a una persona" },
    end: { label: "Terminar" },
  };

  const TRIGGERS = [
    ["always", "Siempre (al toque)"],
    ["keyword", "Si escribe estas palabras"],
    ["is_digit", "Si manda un número"],
    ["is_image", "Si manda una foto o archivo"],
    ["default", "Si no entendió (otra cosa)"],
    ["found", "Si encontró el producto"],
    ["not_found", "Si no encontró el producto"],
    ["unsure", "Si hay varias opciones parecidas"],
    ["transition", "Según lo que decida la IA"],
    ["regex", "Texto especial (avanzado)"],
  ];

  const NODE_HELP = {
    start: "Punto de partida. Unilo al primer mensaje o al menú.",
    message: "Texto que envía el bot. Podés pegar {{product_name}} {{qty}} {{order_code}} para rellenar solo.",
    catalog: "Muestra los productos del inventario. Después unilo a “Esperar respuesta” para que elijan uno.",
    buttons: "Hasta 3 salen como botones en el chat. Si ponés 4 a 10, WhatsApp abre un menú “Ver opciones”. Escribí el texto y a qué paso va cada una.",
    send_image: "Manda una foto. Subí el archivo o pegá una URL. El texto opcional va debajo de la imagen.",
    send_audio: "Manda un audio. Subí OGG, MP3 o M4A. Podés marcarlo como nota de voz.",
    send_video: "Manda un video MP4. El texto opcional va debajo del video.",
    wait_input: "El chat se pausa. Usalo si el cliente tiene que escribir. Si querés botones para tocar, usá el bloque Botones.",
    wait_payment: "Espera la foto del comprobante. La salida típica es “Si manda una foto” hacia Guardar foto de pago.",
    match_product: "Busca qué producto pidió. Unilo con “Si encontró el producto” y “Si no encontró el producto”.",
    match_image: "Compara la captura del live con las fotos del inventario. Unilo: Esperar respuesta → Si manda una foto → Reconocer foto. Confianza alta vende; si duda, pregunta.",
    create_order: "Crea el pedido y manda los datos de Cobros (cuenta y QR). Podés pisar banco/QR solo en este nodo.",
    attach_proof: "Guarda la foto del pago. Después unilo a un mensaje de “recibido”.",
    capture: "Guarda un dato (ciudad, tipo de reunión) para usarlo más adelante.",
    schedule_fulfillment: "Pregunta si es envío, otra ciudad o reunión y pide día y hora.",
    schedule_call: "Ofrece días y horarios de 30 minutos. Cada persona ocupa un turno; ese horario no se le ofrece a otra.",
    order_status: "Responde cómo va el pedido abierto.",
    cancel_order: "Cancela el pedido y libera el stock.",
    ai_reply: "La IA contesta si no hay una opción clara. Es opcional.",
    handoff: "El bot deja de hablar para que atienda una persona.",
    end: "Termina y vuelve al Inicio en el próximo mensaje.",
  };

  function typeLabel(type) {
    return (NODE_META[type] && NODE_META[type].label) || type;
  }

  function triggerLabel(type) {
    const hit = TRIGGERS.find((row) => row[0] === type);
    return hit ? hit[1] : type || "Siempre";
  }

  function shortKey(key) {
    const raw = String(key || "").trim();
    if (!raw) return "";
    const first = raw.split(",")[0].trim();
    if (raw.length <= 28) return raw;
    return first + (raw.includes(",") ? "…" : "");
  }

  function edgeCaption(edge) {
    const kind = edge.trigger_type || "always";
    const key = shortKey(edge.trigger_key);
    if (kind === "always") return "siempre";
    if (kind === "default") return "si no entendió";
    if (kind === "keyword") {
      const raw = String(edge.trigger_key || "").trim();
      const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
      const last = parts[parts.length - 1] || key;
      if (/^opt\d+$/i.test(parts[0] || "")) return last ? "si toca “" + last + "”" : "si toca un botón";
      return key ? "si dice “" + key + "”" : "si escribe…";
    }
    if (kind === "is_digit") return "si es un número";
    if (kind === "is_image") return "si manda foto";
    if (kind === "found") return "sí, hay producto";
    if (kind === "not_found") return "no hay producto";
    if (kind === "unsure") return "si hay varias opciones";
    if (kind === "transition") return key ? "IA: " + key : "según la IA";
    if (kind === "regex") return key ? "texto: " + key : "texto especial";
    return triggerLabel(kind);
  }

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
    if (typeof flow.definition === "string") {
      try {
        flow.definition = JSON.parse(flow.definition);
      } catch (_err) {
        flow.definition = { nodes: [], edges: [] };
      }
    }
    if (!flow.definition || typeof flow.definition !== "object") {
      flow.definition = { nodes: [], edges: [] };
    }
    if (!Array.isArray(flow.definition.nodes)) flow.definition.nodes = [];
    if (!Array.isArray(flow.definition.edges)) flow.definition.edges = [];
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
    if (type === "match_image") {
      return {
        threshold: 0.85,
        unsure: 0.6,
        confirm_text:
          "Encontré: {{product_name}} — {{product_price}} {{currency}}.\n¿Es este? Si sí, decime la cantidad.",
      };
    }
    if (type === "schedule_fulfillment") {
      return {
        modes: "delivery,shipping,meeting",
        office_address: "",
        meeting_link: "",
      };
    }
    if (type === "schedule_call") {
      return {
        text: "¿Qué día te llamamos?",
        start_hour: 9,
        end_hour: 18,
        days: 7,
        interval_min: 30,
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
    if (type === "buttons") {
      return {
        text: "¿Cómo lo recibís?",
        buttons: [
          { title: "Envío local", to: "" },
          { title: "Otro departamento", to: "" },
          { title: "Reunión / recojo", to: "" },
        ],
      };
    }
    if (type === "send_image" || type === "send_audio" || type === "send_video") {
      return { url: "", caption: "", voice: false };
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
    return n ? n.name || typeLabel(n.type) : id;
  }

  function render() {
    const d = def();
    nodesEl.innerHTML = "";
    d.nodes.forEach((node) => {
      const el = document.createElement("div");
      el.className =
        "fs-node " +
        node.type +
        (node.id === selectedId ? " on" : "") +
        (connectFrom === node.id ? " from" : "") +
        (connectFrom && connectFrom !== node.id ? " drop" : "");
      el.style.left = node.x + "px";
      el.style.top = node.y + "px";
      el.dataset.id = node.id;
      el.innerHTML =
        '<div class="fs-type">' +
        escapeHtml(typeLabel(node.type)) +
        '</div><div class="fs-title"></div>' +
        '<button type="button" class="fs-port in" data-port="in" title="Acá llega la flecha"></button>' +
        '<button type="button" class="fs-port out" data-port="out" title="Clic y después tocá el siguiente paso"></button>';
      el.querySelector(".fs-title").textContent = node.name || typeLabel(node.type);
      el.querySelector(".fs-title").dataset.role = "title";
      el.addEventListener("mousedown", onNodeDown);
      el.querySelector(".fs-port.out").addEventListener("click", (ev) => {
        ev.stopPropagation();
        startConnect(node.id);
      });
      el.querySelector(".fs-port.in").addEventListener("click", (ev) => {
        ev.stopPropagation();
        finishConnect(node.id);
      });
      nodesEl.appendChild(el);
    });
    drawEdges();
    fillWire();
    renderInspector();
    updateConnectBar();
  }

  function startConnect(id) {
    connectFrom = id;
    setMsg("Ahora tocá el siguiente paso (el bloque o el puntito de la izquierda).");
    render();
  }

  function finishConnect(id) {
    if (!connectFrom || connectFrom === id) return;
    addEdge(connectFrom, id);
    connectFrom = null;
    setMsg("Unidos. A la derecha podés decir cuándo seguir (siempre, si escribe…). Guardá el flujo.");
    render();
  }

  function cancelConnect() {
    connectFrom = null;
    setMsg("");
    render();
  }

  function updateConnectBar() {
    const bar = document.getElementById("fs-connect-bar");
    const text = document.getElementById("fs-connect-text");
    if (!bar) return;
    if (!connectFrom) {
      bar.hidden = true;
      return;
    }
    bar.hidden = false;
    if (text) text.textContent = "Uniendo desde «" + nodeName(connectFrom) + "». Tocá el siguiente paso.";
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
      const label = edgeCaption(edge);
      parts.push(
        `<path d="${dpath}" fill="none" stroke="#6b7c90" stroke-width="2" marker-end="url(#fs-arrow)"></path>`
      );
      parts.push(
        `<text x="${labelX}" y="${labelY}" fill="#e8edf5" font-size="11" text-anchor="middle" paint-order="stroke" stroke="#0f141b" stroke-width="4">${escapeHtml(
          label.slice(0, 36)
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
      finishConnect(id);
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
      name: typeLabel(type),
      x: 40 + (wrap ? wrap.scrollLeft : 0) + (count % 4) * 28,
      y: 40 + (wrap ? wrap.scrollTop : 0) + (count % 5) * 24,
      config: defaultConfig(type),
    });
    selectedId = def().nodes[def().nodes.length - 1].id;
    setMsg(
      type === "buttons"
        ? "Bloque Botones: a la derecha escribí la pregunta, el texto de cada botón (máx. 3) y a qué paso va. Guardá el flujo."
        : type === "send_image" || type === "send_audio" || type === "send_video"
          ? "Subí el archivo a la derecha (o pegá una URL) y unilo al paso siguiente. Guardá el flujo."
          : "Paso agregado. Unilo al anterior y, a la derecha, escribí qué dice o cuándo seguir."
    );
    render();
    inspector.scrollIntoView({ block: "nearest" });
  }

  function fillWire() {
    const fromEl = document.getElementById("wire-from");
    const toEl = document.getElementById("wire-to");
    if (!fromEl || !toEl) return;
    const nodes = def().nodes;
    const opts = nodes
      .map((n) => `<option value="${n.id}">${escapeHtml(n.name || typeLabel(n.type))}</option>`)
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

  function mediaKind(type) {
    if (type === "send_image") return "image";
    if (type === "send_audio") return "audio";
    if (type === "send_video") return "video";
    return "";
  }

  function mediaHtml(node) {
    const kind = mediaKind(node.type);
    const url = node.config.url || "";
    const accept = {
      image: "image/jpeg,image/png,image/webp,image/gif,.jpg,.jpeg,.png,.webp",
      audio: "audio/ogg,audio/mpeg,audio/mp4,audio/aac,audio/amr,.ogg,.opus,.mp3,.m4a,.aac,.amr,.wav",
      video: "video/mp4,video/3gpp,.mp4,.3gp",
    }[kind] || "";
    let preview = "";
    if (url) {
      if (kind === "image") {
        preview = `<img class="fs-cover-prev" src="${escapeHtml(url)}" alt="">`;
      } else if (kind === "audio") {
        preview = `<audio class="fs-media-prev" controls src="${escapeHtml(url)}"></audio>`;
      } else {
        preview = `<video class="fs-media-prev" controls src="${escapeHtml(url)}"></video>`;
      }
    }
    const caption =
      kind === "audio"
        ? ""
        : `<label>Texto debajo (opcional)<br><textarea id="insp-caption" placeholder="Mirá esto"></textarea></label>`;
    const voice =
      kind === "audio"
        ? `<label class="fs-check"><input type="checkbox" id="insp-voice"> Mandar como nota de voz</label>`
        : "";
    const hint = {
      image: "JPG, PNG o WEBP. Máx. 5 MB.",
      audio: "OGG, MP3 o M4A. Máx. 16 MB.",
      video: "MP4. Máx. 16 MB.",
    }[kind] || "";
    return `<h2>Archivo</h2>
      <label>Subir
        <input type="file" id="insp-media-file" accept="${accept}">
      </label>
      <label>O pegar URL<br><input id="insp-media-url" placeholder="https://… o /uploads/…"></label>
      ${preview || (url ? "" : `<p class="fs-warn">Todavía no hay archivo. Subilo o pegá una URL.</p>`)}
      ${caption}
      ${voice}
      <p class="muted">${hint} Unilo al siguiente paso con “Siempre”.</p>`;
  }

  function bindMedia(node) {
    const kind = mediaKind(node.type);
    if (!kind) return;
    const urlEl = inspector.querySelector("#insp-media-url");
    if (urlEl) {
      urlEl.value = node.config.url || "";
      urlEl.addEventListener("input", (e) => {
        node.config.url = e.target.value.trim();
      });
    }
    const cap = inspector.querySelector("#insp-caption");
    if (cap) {
      cap.value = node.config.caption || "";
      cap.addEventListener("input", (e) => {
        node.config.caption = e.target.value;
      });
    }
    const voice = inspector.querySelector("#insp-voice");
    if (voice) {
      voice.checked = Boolean(node.config.voice);
      voice.addEventListener("change", () => {
        node.config.voice = voice.checked;
      });
    }
    const fileEl = inspector.querySelector("#insp-media-file");
    if (fileEl) {
      fileEl.addEventListener("change", async () => {
        if (!fileEl.files[0]) return;
        try {
          setMsg("Subiendo archivo…");
          const url = await uploadFile(fileEl.files[0], kind);
          node.config.url = url;
          setMsg("Archivo listo. Guardá el flujo.");
          renderInspector();
        } catch (err) {
          setMsg(err.message || "Error al subir");
        }
      });
    }
  }

  function nodeSelectOptions(others, selected) {
    return (
      `<option value="">Elegí el siguiente paso…</option>` +
      others
        .map(
          (n) =>
            `<option value="${n.id}"${n.id === selected ? " selected" : ""}>${escapeHtml(
              n.name || typeLabel(n.type)
            )}</option>`
        )
        .join("")
    );
  }

  function buttonsHtml(node, others) {
    const cfg = node.config || {};
    let list = Array.isArray(cfg.buttons) ? cfg.buttons.slice(0, MAX_BUTTONS) : [];
    if (!list.length) list = [{ title: "", to: "" }, { title: "", to: "" }];
    const filled = list.filter((b) => b && String(b.title || "").trim()).length;
    const many = filled > 3 || list.length > 3;
    const limit = many ? 24 : 20;
    const rows = list
      .map((b, i) => {
        const ph =
          ["Envío local", "Otro departamento", "Reunión / recojo"][i] || "Opción " + (i + 1);
        const del =
          list.length > 1
            ? `<button type="button" class="btn" data-btn-del="${i}" title="Quitar">x</button>`
            : "";
        return `<div class="fs-btn-row">
        <div class="fs-btn-head"><strong>${many ? "Opción" : "Botón"} ${i + 1}</strong>${del}</div>
        <label>Texto (máx. ${limit} letras)
          <input data-btn-title="${i}" maxlength="${limit}" placeholder="${escapeHtml(ph)}">
        </label>
        <label>Si lo toca, ir a
          <select data-btn-to="${i}">${nodeSelectOptions(others, b.to || "")}</select>
        </label>
      </div>`;
      })
      .join("");
    const add =
      list.length < MAX_BUTTONS
        ? `<button type="button" class="btn" id="insp-add-btn">Agregar opción</button>`
        : `<p class="muted">WhatsApp deja como máximo 10 opciones.</p>`;
    const listBtn = many
      ? `<label>Texto del menú (máx. 20 letras)<br><input id="insp-list-btn" maxlength="20" placeholder="Ver opciones"></label>`
      : "";
    const missing = !list.some((b) => b && String(b.title || "").trim() && b.to);
    return `<h2>Opciones para tocar</h2>
      <label>Pregunta (arriba)<br><textarea id="insp-text" placeholder="¿Cómo lo recibís?"></textarea></label>
      <p class="muted">Hasta <strong>3</strong> salen como botones en el chat. Si agregás más, el cliente toca <strong>Ver opciones</strong> y elige de una lista (máx. 10). No hace falta que escriba 1, 2, 3.</p>
      ${rows}
      ${add}
      ${listBtn}
      <label>Guardar la elección como (opcional)<br><input id="insp-var" placeholder="choice"></label>
      <p class="muted">Después podés usar {{choice}} en un mensaje.</p>
      ${missing ? `<p class="fs-warn">Completá el texto y el siguiente paso de cada opción que quieras usar.</p>` : ""}`;
  }

  function syncButtonEdges(node) {
    const titles = Array.from(inspector.querySelectorAll("[data-btn-title]"));
    const many = titles.length > 3;
    const limit = many ? 24 : 20;
    const buttons = titles.map((titleEl, i) => {
      const toEl = inspector.querySelector(`[data-btn-to="${i}"]`);
      return {
        title: (titleEl.value || "").trim().slice(0, limit),
        to: toEl ? toEl.value : "",
      };
    });
    node.config.buttons = buttons;
    const listBtn = inspector.querySelector("#insp-list-btn");
    if (listBtn) node.config.button = (listBtn.value || "").trim().slice(0, 20);
    const keep = def().edges.filter((e) => {
      if (e.from !== node.id) return true;
      const key = String(e.trigger_key || "").trim();
      return !/^opt\d+(,|$)/.test(key);
    });
    buttons.forEach((b, i) => {
      if (!b.title || !b.to) return;
      keep.push({
        id: uid("e_"),
        from: node.id,
        to: b.to,
        trigger_type: "keyword",
        trigger_key: "opt" + (i + 1) + "," + (i + 1) + "," + b.title,
      });
    });
    def().edges = keep;
    drawEdges();
    fillWire();
  }

  function bindButtons(node) {
    if (node.type !== "buttons") return;
    const titles = inspector.querySelectorAll("[data-btn-title]");
    titles.forEach((titleEl, i) => {
      const toEl = inspector.querySelector(`[data-btn-to="${i}"]`);
      const b = (node.config.buttons || [])[i] || {};
      titleEl.value = b.title || "";
      if (toEl) toEl.value = b.to || "";
      titleEl.addEventListener("input", () => {
        syncButtonEdges(node);
      });
      if (toEl) {
        toEl.addEventListener("change", () => {
          syncButtonEdges(node);
        });
      }
    });
    const listBtn = inspector.querySelector("#insp-list-btn");
    if (listBtn) {
      listBtn.value = node.config.button || "Ver opciones";
      listBtn.addEventListener("input", () => {
        node.config.button = (listBtn.value || "").trim().slice(0, 20);
      });
    }
    const add = inspector.querySelector("#insp-add-btn");
    if (add) {
      add.addEventListener("click", () => {
        syncButtonEdges(node);
        node.config.buttons = node.config.buttons || [];
        if (node.config.buttons.length >= MAX_BUTTONS) return;
        node.config.buttons.push({ title: "", to: "" });
        renderInspector();
      });
    }
    inspector.querySelectorAll("[data-btn-del]").forEach((btn) => {
      btn.addEventListener("click", () => {
        syncButtonEdges(node);
        const i = Number(btn.dataset.btnDel);
        (node.config.buttons || []).splice(i, 1);
        if (!node.config.buttons.length) node.config.buttons.push({ title: "", to: "" });
        renderInspector();
      });
    });
    syncButtonEdges(node);
  }

  function triggerOptions(selected) {
    return TRIGGERS.map(
      ([value, label]) =>
        `<option value="${value}"${value === (selected || "always") ? " selected" : ""}>${escapeHtml(label)}</option>`
    ).join("");
  }

  async function uploadFile(file, kind) {
    const fd = new FormData();
    fd.append("file", file);
    const q = kind ? "?kind=" + encodeURIComponent(kind) : "";
    const res = await fetch("/api/flujos/upload" + q, { method: "POST", body: fd });
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
      inspector.innerHTML = '<p class="muted">Elegí un paso en el mapa o agregá uno desde la izquierda.</p>';
      return;
    }
    node.config = node.config || {};
    const outs = def().edges.filter((e) => e.from === node.id);
    const others = def().nodes.filter((n) => n.id !== node.id);
    let html = `<h2>Este paso</h2>
      <label>Nombre (como lo ves en el mapa)<br><input id="insp-name" value=""></label>
      <p class="fs-kind">${escapeHtml(typeLabel(node.type))}</p>
      <p class="fs-help">${escapeHtml(NODE_HELP[node.type] || "Unilo a otro paso para decir qué pasa después.")}</p>`;
    if (node.type === "message" || node.type === "handoff") {
      html += `<label>Qué escribe el bot<br><textarea id="insp-text" placeholder="Hola, ¿en qué te ayudo?"></textarea></label>
        <p class="muted">Opcional: {{product_name}} {{qty}} {{order_code}} se rellenan solos.</p>`;
    }
    if (node.type === "ai_reply") {
      html += `<label>Indicación para la IA<br><textarea id="insp-hint" placeholder="Ayudá a elegir un producto. No inventes precios."></textarea></label>
        <label>Si no está segura, ir a<br><input id="insp-fallback" value=""></label>
        <label>Qué tan segura tiene que estar (0 a 1)<br><input id="insp-conf" type="number" min="0" max="1" step="0.05"></label>`;
    }
    if (node.type === "catalog") {
      html += catalogHtml(node);
    }
    if (mediaKind(node.type)) {
      html += mediaHtml(node);
    }
    if (node.type === "match_image") {
      html += `<label>Confianza para darlo por encontrado (0 a 1)<br><input id="insp-th" type="number" min="0.5" max="1" step="0.05"></label>
        <label>Por debajo de esto, no lo encontró<br><input id="insp-unsure" type="number" min="0.2" max="0.9" step="0.05"></label>
        <label>Texto si lo reconoce<br><textarea id="insp-confirm" placeholder="Encontré: {{product_name}}"></textarea></label>
        <p class="muted">Unilo así: Esperar respuesta → “Si manda una foto” → este nodo. El catálogo tiene que tener fotos. El motor visual corre en el puerto 8011.</p>`;
    }
    if (node.type === "create_order") {
      const qr = node.config.pay_qr_path || node.config.qr_url || "";
      html += `<label>Adelanto %<br><input id="insp-deposit" type="number" min="1" max="100"></label>
        <label>Envío en la ciudad (BOB)<br><input id="insp-ship-local"></label>
        <label>Envío a otra ciudad (BOB)<br><input id="insp-ship-int"></label>
        <label class="fs-check"><input type="checkbox" id="insp-skip-sched"> Ya pedí fecha/hora antes del QR</label>
        <h2>Cobro</h2>
        <p class="muted">Vacío = usa lo de <a href="/cobros" target="_blank">Cobros</a>. Si llenás un campo, ese valor sale en este nodo.</p>
        <label>Banco<br><input id="insp-bank-name" placeholder="El de Cobros si lo dejás vacío"></label>
        <label>Titular<br><input id="insp-bank-holder"></label>
        <label>Tipo de cuenta<br>
          <select id="insp-bank-type">
            <option value="">El de Cobros</option>
            <option>Caja de ahorro</option>
            <option>Cuenta corriente</option>
            <option>Cuenta fiscal</option>
          </select>
        </label>
        <label>N° de cuenta<br><input id="insp-bank-number"></label>
        <label>CI / NIT<br><input id="insp-bank-doc"></label>
        <label>Nota para el cliente<br><textarea id="insp-pay-note" placeholder="Ej: Pagá el adelanto y el envío."></textarea></label>
        <label>QR de este nodo (opcional)
          <input type="file" id="insp-pay-qr-file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp">
        </label>
        ${qr ? `<p><img class="fs-cover-prev" src="${escapeHtml(qr)}" alt="QR"></p>
          <button type="button" class="btn" id="insp-pay-qr-clear">Quitar QR del nodo</button>` : `<p class="muted">Sin QR en el nodo se manda el de Cobros.</p>`}`;
    }
    if (node.type === "schedule_fulfillment") {
      html += `<label>Qué ofrecer (separado por coma)<br><input id="insp-modes" placeholder="delivery,shipping,meeting"></label>
        <p class="muted">delivery = domicilio · shipping = otra ciudad · meeting = reunión</p>
        <label>Dirección de la oficina<br><textarea id="insp-office"></textarea></label>
        <label>Link de la reunión virtual<br><input id="insp-link" placeholder="https://meet.google.com/…"></label>`;
    }
    if (node.type === "schedule_call") {
      html += `<label>Pregunta<br><textarea id="insp-text" placeholder="¿Qué día te llamamos?"></textarea></label>
        <label>Desde (hora)<br><input id="insp-call-start" type="number" min="0" max="23"></label>
        <label>Hasta (hora)<br><input id="insp-call-end" type="number" min="1" max="24"></label>
        <label>Días a ofrecer<br><input id="insp-call-days" type="number" min="1" max="14"></label>
        <label>Minutos por persona<br><input id="insp-call-interval" type="number" min="15" max="60" step="15"></label>
        <p class="muted">Un turno por persona. Si alguien toma las 10:00, el siguiente libre es 10:30. Aparece en Agenda como Llamada.</p>`;
    }
    if (node.type === "capture") {
      html += `<label>Nombre del dato (ej. city)<br><input id="insp-var"></label>
        <label>Valor fijo (vacío = lo que acaba de escribir)<br><input id="insp-value" placeholder="presencial"></label>`;
    }
    if (node.type === "buttons") {
      html += buttonsHtml(node, others);
    } else {
      html += `<h2>Qué pasa después</h2>
      <p class="muted">Cada flecha es: “si pasa esto, ir a este paso”.</p>`;
      if (!outs.length) {
        html += `<p class="fs-warn">Este paso no tiene siguiente. Sin flecha el chat se corta acá.</p>`;
      }
      outs.forEach((edge) => {
        html += `<div class="fs-edge-card">
        <div class="fs-edge-row">
          <select data-edge="${edge.id}" data-field="trigger_type">${triggerOptions(edge.trigger_type)}</select>
          <input data-edge="${edge.id}" data-field="trigger_key" placeholder="ej. catálogo, 1" value="">
          <button type="button" class="btn" data-del-edge="${edge.id}" title="Quitar esta flecha">x</button>
        </div>
        <p class="muted">Va a <strong>${escapeHtml(nodeName(edge.to))}</strong></p>
      </div>`;
      });
      html += `<div class="fs-add-edge">
      <p class="fs-add-title">Nueva flecha</p>
      <label>Ir a este paso<br>
        <select id="insp-to">
          <option value="">Elegí…</option>
          ${others
            .map((n) => `<option value="${n.id}">${escapeHtml(n.name || typeLabel(n.type))}</option>`)
            .join("")}
        </select>
      </label>
      <label>Seguir cuando<br><select id="insp-trig">${triggerOptions("always")}</select></label>
      <label>Si son palabras, escribílas acá<br><input id="insp-key" placeholder="hola, catálogo, 1"></label>
      <button type="button" class="btn primary" id="insp-add-edge">Agregar flecha</button>
    </div>`;
    }
    html += `<button type="button" class="btn danger" id="insp-del">Eliminar este paso</button>`;
    inspector.innerHTML = html;
    inspector.querySelector("#insp-name").value = node.name || "";
    inspector.querySelector("#insp-name").addEventListener("input", (e) => {
      node.name = e.target.value;
      const title = nodesEl.querySelector('[data-id="' + node.id + '"] .fs-title');
      if (title) title.textContent = node.name || typeLabel(node.type);
    });
    const textEl = inspector.querySelector("#insp-text");
    if (textEl) {
      textEl.value = node.config.text || "";
      textEl.addEventListener("input", (e) => {
        node.config.text = e.target.value;
      });
    }
    bindCatalog(node);
    bindButtons(node);
    bindMedia(node);
    const dep = inspector.querySelector("#insp-deposit");
    if (dep) {
      dep.value = node.config.deposit_percent ?? 50;
      dep.addEventListener("input", (e) => {
        node.config.deposit_percent = Number(e.target.value);
      });
    }
    const th = inspector.querySelector("#insp-th");
    if (th) {
      th.value = node.config.threshold ?? 0.85;
      th.addEventListener("input", (e) => {
        node.config.threshold = Number(e.target.value);
      });
    }
    const uns = inspector.querySelector("#insp-unsure");
    if (uns) {
      uns.value = node.config.unsure ?? 0.6;
      uns.addEventListener("input", (e) => {
        node.config.unsure = Number(e.target.value);
      });
    }
    const confirmEl = inspector.querySelector("#insp-confirm");
    if (confirmEl) {
      confirmEl.value = node.config.confirm_text || "";
      confirmEl.addEventListener("input", (e) => {
        node.config.confirm_text = e.target.value;
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
    const bindPay = (id, key) => {
      const el = inspector.querySelector(id);
      if (!el) return;
      el.value = node.config[key] || "";
      el.addEventListener("input", (e) => {
        node.config[key] = e.target.value;
      });
    };
    bindPay("#insp-bank-name", "bank_name");
    bindPay("#insp-bank-holder", "bank_holder");
    bindPay("#insp-bank-number", "bank_account_number");
    bindPay("#insp-bank-doc", "bank_id_doc");
    bindPay("#insp-pay-note", "pay_instructions");
    const bankType = inspector.querySelector("#insp-bank-type");
    if (bankType) {
      bankType.value = node.config.bank_account_type || "";
      bankType.addEventListener("change", () => {
        node.config.bank_account_type = bankType.value;
      });
    }
    const qrFile = inspector.querySelector("#insp-pay-qr-file");
    if (qrFile) {
      qrFile.addEventListener("change", async () => {
        if (!qrFile.files[0]) return;
        try {
          setMsg("Subiendo QR…");
          const url = await uploadFile(qrFile.files[0], "image");
          node.config.pay_qr_path = url;
          setMsg("QR del nodo listo. Guardá el flujo.");
          renderInspector();
        } catch (err) {
          setMsg(err.message || "Error al subir el QR");
        }
      });
    }
    const qrClear = inspector.querySelector("#insp-pay-qr-clear");
    if (qrClear) {
      qrClear.addEventListener("click", () => {
        node.config.pay_qr_path = "";
        node.config.qr_url = "";
        renderInspector();
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
    const callStart = inspector.querySelector("#insp-call-start");
    if (callStart) {
      callStart.value = node.config.start_hour ?? 9;
      callStart.addEventListener("input", (e) => {
        node.config.start_hour = Number(e.target.value);
      });
    }
    const callEnd = inspector.querySelector("#insp-call-end");
    if (callEnd) {
      callEnd.value = node.config.end_hour ?? 18;
      callEnd.addEventListener("input", (e) => {
        node.config.end_hour = Number(e.target.value);
      });
    }
    const callDays = inspector.querySelector("#insp-call-days");
    if (callDays) {
      callDays.value = node.config.days ?? 7;
      callDays.addEventListener("input", (e) => {
        node.config.days = Number(e.target.value);
      });
    }
    const callInt = inspector.querySelector("#insp-call-interval");
    if (callInt) {
      callInt.value = node.config.interval_min ?? 30;
      callInt.addEventListener("input", (e) => {
        node.config.interval_min = Number(e.target.value);
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
    const linkBtn = inspector.querySelector("#insp-add-edge");
    if (linkBtn) {
      linkBtn.addEventListener("click", () => {
      const to = inspector.querySelector("#insp-to").value;
      if (!to) {
        setMsg("Elegí a qué paso va la flecha");
        return;
      }
      addEdge(
        node.id,
        to,
        inspector.querySelector("#insp-trig").value,
        inspector.querySelector("#insp-key").value.trim()
      );
      setMsg("Flecha agregada. Guardá el flujo.");
      render();
      });
    }
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
  const cancelConnectBtn = document.getElementById("fs-connect-cancel");
  if (cancelConnectBtn) cancelConnectBtn.addEventListener("click", cancelConnect);
  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && connectFrom) cancelConnect();
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
        setMsg("Elegí dos pasos distintos");
        return;
      }
      addEdge(
        from,
        to,
        document.getElementById("wire-trig").value,
        document.getElementById("wire-key").value.trim()
      );
      selectedId = from;
      setMsg("Unidos. Guardá el flujo.");
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
