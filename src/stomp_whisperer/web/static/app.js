// Polls the backend for the pedal's connection state and drives the UI.

const POLL_MS = 2000;

const statusEl = document.getElementById("status");
const statusText = document.getElementById("status-text");
const modal = document.getElementById("connect-modal");
const modalTitle = document.getElementById("modal-title");
const modalBody = document.getElementById("modal-body");

const MESSAGES = {
  connected: { status: "Pedal connected" },
  disconnected: {
    status: "Pedal not connected",
    title: "Connect your pedal",
    body: "Can't find the <strong>Zoom MS-50G+</strong>. Plug it in via USB and power it on; " +
          "this window will close by itself as soon as it's detected.",
  },
  offline: {
    status: "Server offline",
    title: "Can't reach StompWhisperer",
    body: "The local server isn't responding. Start it with <code>stomp-whisperer serve</code> " +
          "and this page will reconnect by itself.",
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
