// Polls the backend for the pedal's connection state and drives the UI.

const POLL_MS = 2000;

const statusEl = document.getElementById("status");
const statusText = document.getElementById("status-text");
const modal = document.getElementById("connect-modal");
const modalTitle = document.getElementById("modal-title");
const modalBody = document.getElementById("modal-body");

const MESSAGES = {
  connected: { status: "Pedal conectado" },
  disconnected: {
    status: "Pedal no conectado",
    title: "Conecta tu pedal",
    body: "No encuentro el <strong>Zoom MS-50G+</strong>. Conéctalo por USB y enciéndelo; " +
          "esta ventana se cerrará sola en cuanto lo detecte.",
  },
  offline: {
    status: "Sin servidor",
    title: "No hay conexión con StompWhisperer",
    body: "El servidor local no responde. Arráncalo con <code>stomp-whisperer serve</code> " +
          "y esta página se reconectará sola.",
  },
};

let lastState = null;

function render(state, port) {
  if (state === lastState) return;
  lastState = state;

  const message = MESSAGES[state];
  statusEl.dataset.state = state;
  document.body.dataset.pedal = state;
  statusText.textContent = message.status;
  statusEl.title = port ?? "";

  if (state === "connected") {
    modal.hidden = true;
  } else {
    modalTitle.textContent = message.title;
    modalBody.innerHTML = message.body;
    modal.hidden = false;
  }
}

async function poll() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const { connected, port } = await response.json();
    render(connected ? "connected" : "disconnected", port);
  } catch {
    render("offline");
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

poll();
