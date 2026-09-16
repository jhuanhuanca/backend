(() => {
  const form = document.getElementById("wa-compose");
  if (!form) return;
  const fileInput = document.getElementById("wa-file");
  const voiceInput = document.getElementById("wa-voice");
  const attachBtn = document.getElementById("wa-attach");
  const micBtn = document.getElementById("wa-mic");
  const hint = document.getElementById("wa-hint");
  const msgs = document.querySelector(".wa-msgs");
  if (msgs) msgs.scrollTop = msgs.scrollHeight;

  let recorder = null;
  let chunks = [];
  let recTimer = null;
  let recStarted = 0;

  function setHint(text) {
    if (hint) hint.textContent = text || "";
  }

  attachBtn?.addEventListener("click", () => fileInput?.click());
  fileInput?.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    voiceInput.value = "";
    setHint(file ? `Adjunto: ${file.name}` : "");
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
      const file = new File([blob], `nota-de-voz.${ext}`, { type });
      const transfer = new DataTransfer();
      transfer.items.add(file);
      fileInput.files = transfer.files;
      voiceInput.value = "1";
      setHint("Audio listo. Enviando…");
      form.requestSubmit();
    });
    recorder.start();
  }

  micBtn?.addEventListener("click", () => {
    toggleRecord().catch(() => setHint("No se pudo grabar el audio."));
  });

  form.addEventListener("submit", (ev) => {
    const text = (form.querySelector("#wa-text")?.value || "").trim();
    const file = fileInput && fileInput.files && fileInput.files[0];
    if (recorder && recorder.state === "recording") {
      ev.preventDefault();
      setHint("Terminá la grabación antes de enviar.");
      return;
    }
    if (!text && !file) {
      ev.preventDefault();
      setHint("Escribí un texto o adjuntá un archivo.");
    }
  });
})();
