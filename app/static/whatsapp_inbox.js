(() => {
  const form = document.getElementById("wa-compose");
  if (!form) return;
  const fileInput = document.getElementById("wa-file");
  const attachBtn = document.getElementById("wa-attach");
  const micBtn = document.getElementById("wa-mic");
  const textEl = document.getElementById("wa-text");
  const hint = document.getElementById("wa-hint");
  const sendBtn = form.querySelector("button[type=submit]");
  const msgs = document.querySelector(".wa-msgs");
  if (msgs) msgs.scrollTop = msgs.scrollHeight;

  let recorder = null;
  let chunks = [];
  let recTimer = null;
  let recStarted = 0;
  let pendingFile = null;
  let pendingVoice = false;
  let sending = false;

  function setHint(text) {
    if (hint) hint.textContent = text || "";
  }

  function setFile(file, voice) {
    pendingFile = file || null;
    pendingVoice = Boolean(voice);
    if (fileInput) fileInput.value = "";
    if (!pendingFile) {
      setHint("");
      return;
    }
    const mb = (pendingFile.size / (1024 * 1024)).toFixed(1);
    setHint((pendingVoice ? "Audio listo" : "Adjunto") + `: ${pendingFile.name} (${mb} MB)`);
  }

  attachBtn?.addEventListener("click", () => fileInput?.click());
  fileInput?.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    setFile(file || null, false);
  });

  function pickMime() {
    const options = ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/webm"];
    return options.find((type) => window.MediaRecorder && MediaRecorder.isTypeSupported(type)) || "";
  }

  function stopTimer() {
    if (recTimer) clearInterval(recTimer);
    recTimer = null;
  }

  async function toggleRecord() {
    if (recorder && recorder.state === "recording") {
      recorder.stop();
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setHint("Este navegador no permite grabar audio.");
      return;
    }
    const mime = pickMime();
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      setHint("No pude usar el micrófono. Permití el acceso e intentá de nuevo.");
      return;
    }
    chunks = [];
    recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    recStarted = Date.now();
    micBtn.classList.add("rec");
    micBtn.textContent = "■";
    micBtn.title = "Detener grabación";
    setHint("Grabando… 0:00");
    recTimer = setInterval(() => {
      const sec = Math.floor((Date.now() - recStarted) / 1000);
      setHint(`Grabando… ${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`);
    }, 250);
    recorder.addEventListener("dataavailable", (ev) => {
      if (ev.data && ev.data.size) chunks.push(ev.data);
    });
    recorder.addEventListener("stop", () => {
      stopTimer();
      stream.getTracks().forEach((track) => track.stop());
      micBtn.classList.remove("rec");
      micBtn.textContent = "🎤";
      micBtn.title = "Grabar audio";
      const type = recorder.mimeType || mime || "audio/webm";
      recorder = null;
      const blob = new Blob(chunks, { type });
      if (!blob.size) {
        setHint("La grabación quedó vacía.");
        return;
      }
      const ext = type.includes("ogg") ? "ogg" : "webm";
      setFile(new File([blob], `nota-de-voz.${ext}`, { type }), true);
      form.requestSubmit();
    });
    recorder.start();
  }

  micBtn?.addEventListener("click", () => {
    toggleRecord().catch(() => setHint("No se pudo grabar el audio."));
  });

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (sending) return;
    if (recorder && recorder.state === "recording") {
      setHint("Terminá la grabación antes de enviar.");
      return;
    }
    const text = (textEl?.value || "").trim();
    const file = pendingFile || (fileInput && fileInput.files && fileInput.files[0]) || null;
    if (!text && !file) {
      setHint("Escribí un texto o adjuntá un archivo.");
      return;
    }
    sending = true;
    if (sendBtn) sendBtn.disabled = true;
    setHint(file ? "Enviando archivo…" : "Enviando…");
    const fd = new FormData();
    fd.append("body", text);
    if (file) fd.append("media", file, file.name || "archivo");
    if (pendingVoice) fd.append("voice", "1");
    try {
      const res = await fetch(form.action, { method: "POST", body: fd, redirect: "follow" });
      if (!res.ok) throw new Error("No se pudo enviar");
      window.location.href = res.url || form.action;
      return;
    } catch (err) {
      setHint(err.message || "No se pudo enviar.");
      sending = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  });
})();
