(() => {
  const bootEl = document.getElementById("sim-boot");
  const msgsEl = document.getElementById("sim-msgs");
  const form = document.getElementById("sim-form");
  const textEl = document.getElementById("sim-text");
  const nameEl = document.getElementById("sim-name");
  const phoneEl = document.getElementById("sim-phone-input");
  const engineEl = document.getElementById("sim-engine");
  const presenceEl = document.getElementById("sim-presence");
  const statusTime = document.getElementById("sim-status-time");
  const sendBtn = form.querySelector("button[type=submit]");
  const TICK =
    '<svg class="sim-ticks" viewBox="0 0 16 11" aria-hidden="true"><path d="M11.07.22 4.9 6.7 2.7 4.48 1.28 5.9l3.62 3.66L12.5 1.64z"/><path d="M14.72.22 8.55 6.7l-.7-.72 1.42-1.42"/></svg>';
  let sending = false;

  function nowClock() {
    return new Date().toLocaleTimeString("es-BO", { hour: "2-digit", minute: "2-digit" });
  }
  if (statusTime) statusTime.textContent = nowClock();

  function boot() {
    try {
      return JSON.parse(bootEl.textContent || "{}");
    } catch {
      return { messages: [], engine: "", step: "idle", phone: "" };
    }
  }

  function phone() {
    return (phoneEl.value || "").replace(/\D/g, "") || "59170009999";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function clock(iso) {
    const m = String(iso || "").match(/(\d{1,2}):(\d{2})/);
    if (m) return `${m[1].padStart(2, "0")}:${m[2]}`;
    return nowClock();
  }

  function bubbleHtml(m) {
    if (m.direction !== "inbound" && m.msg_type === "catalog" && m.rich) {
      return catalogHtml(m);
    }
    if (m.direction !== "inbound" && m.msg_type === "product_card" && m.rich) {
      return productCardHtml(m);
    }
    if (m.direction !== "inbound" && m.msg_type === "choices" && m.rich) {
      return choicesHtml(m);
    }
    const mine = m.direction === "inbound";
    let media = "";
    if (m.media_url) {
      if (m.msg_type === "audio") {
        media = `<audio controls preload="metadata" src="${escapeHtml(m.media_url)}"></audio>`;
      } else if (m.msg_type === "video") {
        media = `<video controls preload="metadata" src="${escapeHtml(m.media_url)}"></video>`;
      } else if (m.msg_type === "document") {
        media = `<a class="sim-file" href="${escapeHtml(m.media_url)}" target="_blank" rel="noopener">📎 Archivo</a>`;
      } else {
        media = `<img class="sim-img" src="${escapeHtml(m.media_url)}" alt="">`;
      }
    }
    const body = m.body ? `<div class="sim-text">${escapeHtml(m.body)}</div>` : "";
    const ticks = mine ? TICK : "";
    return `<div class="sim-bubble ${m.direction} ${m.source || ""}">
      ${media}${body}
      <div class="sim-foot"><span>${escapeHtml(clock(m.created_at))}</span>${ticks}</div>
    </div>`;
  }

  function catalogHtml(m) {
    const rich = m.rich || {};
    const items = rich.items || [];
    const cards = items
      .map((it) => {
        const img = it.image
          ? `<img src="${escapeHtml(it.image)}" alt="">`
          : `<div class="sim-ph">${escapeHtml(String(it.name || "?").slice(0, 2))}</div>`;
        const cat = it.category ? ` · ${escapeHtml(it.category)}` : "";
        return `<button type="button" class="sim-card" data-pick="sku:${escapeHtml(it.sku)}">
          ${img}
          <span class="sim-card-n">${it.n}</span>
          <strong>${escapeHtml(it.name)}</strong>
          <em>${escapeHtml(it.price)} ${escapeHtml(it.currency || "")}</em>
          <small>Stock ${escapeHtml(String(it.stock))}${cat}</small>
        </button>`;
      })
      .join("");
    const cover = rich.cover
      ? `<img class="sim-img" src="${escapeHtml(rich.cover)}" alt="">`
      : "";
    return `<div class="sim-bubble outbound bot sim-wide">
      ${cover}
      <div class="sim-text">${escapeHtml(rich.text || "Catálogo")}</div>
      <div class="sim-cards">${cards}</div>
      <div class="sim-foot"><span>${escapeHtml(clock(m.created_at))}</span></div>
    </div>`;
  }

  function productCardHtml(m) {
    const rich = m.rich || {};
    const img = rich.image
      ? `<img class="sim-img" src="${escapeHtml(rich.image)}" alt="">`
      : "";
    const btns = (rich.buttons || [])
      .map(
        (b) =>
          `<button type="button" class="sim-qty" data-pick="${escapeHtml(b.id)}">${escapeHtml(
            b.title
          )}</button>`
      )
      .join("");
    return `<div class="sim-bubble outbound bot sim-wide">
      ${img}
      <div class="sim-text">${escapeHtml(rich.text || "")}</div>
      <div class="sim-btns">${btns}</div>
      <div class="sim-foot"><span>${escapeHtml(clock(m.created_at))}</span></div>
    </div>`;
  }

  function choicesHtml(m) {
    const rich = m.rich || {};
    const items = rich.items || [];
    const rows = items
      .map((it) => {
        const desc = it.description
          ? `<small>${escapeHtml(it.description)}</small>`
          : "";
        return `<button type="button" class="sim-choice" data-pick="${escapeHtml(
          String(it.id || "")
        )}"><strong>${escapeHtml(it.title || "")}</strong>${desc}</button>`;
      })
      .join("");
    return `<div class="sim-bubble outbound bot sim-wide">
      <div class="sim-text">${escapeHtml(rich.text || "")}</div>
      <div class="sim-choices">${rows}</div>
      <div class="sim-foot"><span>${escapeHtml(clock(m.created_at))}</span></div>
    </div>`;
  }

  function emptyState() {
    return `<div class="sim-day">HOY</div>
      <div class="sim-lock">Los mensajes están cifrados de extremo a extremo. Escribí <strong>hola</strong> para ver cómo te respondería el bot en WhatsApp.</div>`;
  }

  function render(data, { typing } = {}) {
    const messages = data.messages || [];
    let html = messages.length ? `<div class="sim-day">HOY</div>` : emptyState();
    html += messages.map(bubbleHtml).join("");
    if (typing) {
      html += `<div class="sim-typing" id="sim-typing"><i></i><i></i><i></i></div>`;
    }
    msgsEl.innerHTML = html;
    msgsEl.scrollTop = msgsEl.scrollHeight;
    if (engineEl) {
      engineEl.textContent = `Motor: ${data.engine || "—"} · paso: ${data.step || "idle"}`;
    }
  }

  async function post(url, extra) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone: phone(),
        name: nameEl.value || "Cliente de prueba",
        ...extra,
      }),
    });
    if (res.status === 401) {
      window.location.href = "/login";
      return null;
    }
    if (!res.ok) {
      const err = await res.text();
      throw new Error(err || "Error del simulador");
    }
    return res.json();
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async function send(text, opts) {
    const asImage = Boolean(opts && opts.image);
    const body = (text || "").trim() || (asImage ? "📷 comprobante" : "");
    if (!body || sending) return;
    sending = true;
    sendBtn.disabled = true;
    if (presenceEl) presenceEl.textContent = "escribiendo…";
    const optimistic = (bootData.messages || []).concat([
      {
        direction: "inbound",
        source: "customer",
        body,
        msg_type: asImage ? "image" : "text",
        created_at: nowClock(),
      },
    ]);
    render({ ...bootData, messages: optimistic }, { typing: true });
    textEl.value = "";
    try {
      await sleep(650);
      const data = await post("/whatsapp/simulador/mensaje", { text: body, image: asImage });
      if (data) {
        bootData = data;
        render(data);
      }
    } catch (err) {
      msgsEl.insertAdjacentHTML(
        "beforeend",
        bubbleHtml({
          direction: "outbound",
          source: "bot",
          body: `No pude obtener la respuesta: ${err.message || err}`,
          created_at: nowClock(),
        })
      );
      msgsEl.scrollTop = msgsEl.scrollHeight;
    } finally {
      sending = false;
      sendBtn.disabled = false;
      if (presenceEl) presenceEl.textContent = "en línea";
      textEl.focus();
    }
  }

  const fileEl = document.getElementById("sim-file");
  const attachBtn = document.getElementById("sim-attach");
  const micBtn = document.getElementById("sim-mic");

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const pending = fileEl && fileEl.files && fileEl.files[0];
    if (pending) {
      sendFile(pending);
      return;
    }
    send(textEl.value);
  });
  document.getElementById("sim-chips").addEventListener("click", (ev) => {
    const proof = ev.target.closest("#sim-proof");
    if (proof) {
      send("comprobante", { image: true });
      return;
    }
    const btn = ev.target.closest("[data-chip]");
    if (!btn) return;
    send(btn.dataset.chip);
  });
  attachBtn?.addEventListener("click", () => fileEl?.click());
  fileEl?.addEventListener("change", () => {
    const file = fileEl.files && fileEl.files[0];
    if (file) sendFile(file);
    fileEl.value = "";
  });

  async function sendFile(file) {
    if (!file || sending) return;
    sending = true;
    sendBtn.disabled = true;
    if (presenceEl) presenceEl.textContent = "enviando…";
    try {
      await sleep(250);
      const fd = new FormData();
      fd.append("phone", phone());
      fd.append("name", nameEl.value || "Cliente de prueba");
      fd.append("text", (textEl.value || "").trim());
      fd.append("media", file, file.name || "archivo");
      const res = await fetch("/whatsapp/simulador/media", { method: "POST", body: fd });
      if (res.status === 401) {
        window.location.href = "/login";
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data.detail || data.message || "No se pudo enviar el archivo";
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      bootData = data;
      textEl.value = "";
      if (fileEl) fileEl.value = "";
      render(data);
    } catch (err) {
      msgsEl.insertAdjacentHTML(
        "beforeend",
        bubbleHtml({
          direction: "outbound",
          source: "bot",
          body: `No pude enviar el archivo: ${err.message || err}`,
          created_at: nowClock(),
        })
      );
      msgsEl.scrollTop = msgsEl.scrollHeight;
    } finally {
      sending = false;
      sendBtn.disabled = false;
      if (presenceEl) presenceEl.textContent = "en línea";
    }
  }

  let recorder = null;
  micBtn?.addEventListener("click", async () => {
    if (recorder && recorder.state === "recording") {
      recorder.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      if (presenceEl) presenceEl.textContent = "sin micrófono";
      return;
    }
    const mime = ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/webm"].find(
      (type) => window.MediaRecorder && MediaRecorder.isTypeSupported(type)
    );
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      if (presenceEl) presenceEl.textContent = "micrófono bloqueado";
      return;
    }
    const chunks = [];
    recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    micBtn.classList.add("rec");
    recorder.addEventListener("dataavailable", (ev) => {
      if (ev.data && ev.data.size) chunks.push(ev.data);
    });
    recorder.addEventListener("stop", () => {
      stream.getTracks().forEach((track) => track.stop());
      micBtn.classList.remove("rec");
      const type = recorder.mimeType || mime || "audio/webm";
      recorder = null;
      const blob = new Blob(chunks, { type });
      if (!blob.size) return;
      const ext = type.includes("ogg") ? "ogg" : "webm";
      sendFile(new File([blob], `nota-de-voz.${ext}`, { type }));
    });
    recorder.start();
  });
  msgsEl.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-pick]");
    if (!btn || sending) return;
    send(btn.dataset.pick);
  });
  document.getElementById("sim-reset").addEventListener("click", async () => {
    if (!confirm("¿Borrar este chat de prueba y reiniciar el paso del bot?")) return;
    const data = await post("/whatsapp/simulador/reset", {});
    if (data) {
      bootData = data;
      render(data);
    }
  });
  phoneEl.addEventListener("change", async () => {
    const res = await fetch(`/whatsapp/simulador/estado?phone=${encodeURIComponent(phone())}`);
    if (res.ok) {
      bootData = await res.json();
      render(bootData);
    }
  });

  let bootData = boot();
  render(bootData);
  textEl.focus();
})();
