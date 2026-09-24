// Polls the backend for the pedal's connection state and drives the UI:
// the patch list on the left and the selected patch's effect chain on the right.

const POLL_MS = 2000;

const statusEl = document.getElementById("status");
const statusText = document.getElementById("status-text");
const modal = document.getElementById("connect-modal");
const modalTitle = document.getElementById("modal-title");
const modalBody = document.getElementById("modal-body");
const slotsEl = document.getElementById("slots");
const listState = document.getElementById("list-state");
const refreshButton = document.getElementById("refresh");
const detailEl = document.getElementById("detail");

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
let selectedSlot = null;

// ---------- small DOM helper ----------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((child) => child != null));
  return node;
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ?? `HTTP ${response.status}`);
  return body;
}

// ---------- patch list ----------

function slotLabel(slot) {
  return String(slot).padStart(3, "0");
}

function setListState(text) {
  listState.textContent = text ?? "";
  listState.hidden = !text;
}

function renderList(patches) {
  slotsEl.replaceChildren(...patches.map((patch) => {
    const isEmpty = patch.effect_count === 0;
    const leds = el("span", { class: "chain-leds", "aria-hidden": "true" },
      ...patch.effects.map((fx) => el("i", { class: fx.enabled ? "on" : "" })));
    const count = isEmpty ? "empty" : `${patch.effect_count} fx`;
    const button = el("button", {
      type: "button",
      class: "slot",
      dataset: { slot: patch.slot },
      "aria-current": patch.slot === selectedSlot ? "true" : "false",
      onclick: () => selectSlot(patch.slot),
    },
      el("span", { class: "slot-number" }, slotLabel(patch.slot)),
      el("span", { class: "slot-name" }, patch.name ?? "(unreadable)"),
      leds,
      el("span", { class: "slot-count" }, count),
    );
    if (isEmpty) button.classList.add("is-empty");
    return el("li", {}, button);
  }));
}

async function loadList(refresh = false) {
  refreshButton.disabled = true;
  setListState("Reading patches from the pedal…");
  if (refresh) slotsEl.replaceChildren();
  try {
    const patches = await getJson(`/api/patches${refresh ? "?refresh=true" : ""}`);
    renderList(patches);
    setListState(null);
    const fromHash = Number(location.hash.match(/^#slot-(\d+)$/)?.[1]);
    const initial = selectedSlot ?? (fromHash || null);
    if (initial) selectSlot(initial, { scroll: true });
  } catch (error) {
    setListState(`Couldn't read the patches: ${error.message}`);
  } finally {
    refreshButton.disabled = false;
  }
}

function clearList() {
  slotsEl.replaceChildren();
  setListState("Waiting for the pedal…");
  showDetailMessage("Select a patch to see its effect chain.");
}

// ---------- patch detail ----------

function showDetailMessage(text) {
  detailEl.replaceChildren(el("p", { class: "detail-empty" }, text));
}

function renderEffect(fx) {
  if (fx.empty) {
    return el("li", { class: "stomp is-empty" },
      el("span", { class: "stomp-position" }, `#${fx.position}`),
      el("p", { class: "stomp-name" }, "Empty slot"));
  }
  const params = fx.params.map((value, index) =>
    el("div", { class: value === 0 ? "param is-zero" : "param" },
      el("dt", {}, `P${index + 1}`),
      el("dd", {}, String(value))));
  return el("li", { class: fx.enabled ? "stomp is-on" : "stomp" },
    el("div", { class: "stomp-top" },
      el("span", { class: "stomp-position" }, `#${fx.position}`),
      el("span", { class: "stomp-led", title: fx.enabled ? "On" : "Off" }),
      el("span", { class: "stomp-state" }, fx.enabled ? "On" : "Off")),
    el("p", { class: "stomp-name" }, "Effect ",
      el("code", { title: "Effect ID (names not decoded yet)" }, fx.id)),
    el("dl", { class: "params", "aria-label": "Raw parameter values" }, ...params));
}

function renderDetail(patch) {
  const effects = patch.chain.filter((fx) => !fx.empty);
  const summary = effects.length === 0 ? "No effects"
    : effects.length === 1 ? "Single effect"
    : `Chain of ${effects.length} effects`;

  detailEl.replaceChildren(
    el("header", { class: "detail-head" },
      el("p", { class: "detail-slot" }, `Slot ${slotLabel(patch.slot)}`),
      el("h2", {}, patch.name ?? "(unreadable)"),
      el("p", { class: "detail-summary" }, summary,
        patch.checksum_ok ? null : el("span", { class: "warning" }, " · checksum mismatch")),
      patch.description ? el("p", { class: "detail-description" }, patch.description) : null,
      patch.error ? el("p", { class: "warning" }, patch.error) : null),
    el("ol", { class: "chain", "aria-label": "Effect chain, in signal order" },
      ...patch.chain.map(renderEffect)),
    effects.length ? el("p", { class: "detail-note" },
      "Effect names and parameter labels aren't decoded yet; values are raw.") : null,
  );
}

async function selectSlot(slot, { scroll = false } = {}) {
  selectedSlot = slot;
  history.replaceState(null, "", `#slot-${slot}`);
  for (const button of slotsEl.querySelectorAll(".slot")) {
    const current = Number(button.dataset.slot) === slot;
    button.setAttribute("aria-current", current ? "true" : "false");
    if (current && scroll) button.scrollIntoView({ block: "center" });
  }
  try {
    const patch = await getJson(`/api/patches/${slot}`);
    if (selectedSlot === slot) renderDetail(patch);
  } catch (error) {
    showDetailMessage(`Couldn't load slot ${slot}: ${error.message}`);
  }
}

refreshButton.addEventListener("click", () => loadList(true));

// ---------- connection state ----------

function render(state, port) {
  if (state === lastState) return;
  const previous = lastState;
  lastState = state;

  const message = MESSAGES[state];
  statusEl.dataset.state = state;
  document.body.dataset.pedal = state;
  statusText.textContent = message.status;
  statusEl.title = port ?? "";

  if (state === "connected") {
    modal.hidden = true;
    loadList();
  } else {
    modalTitle.textContent = message.title;
    modalBody.innerHTML = message.body;
    modal.hidden = false;
    if (previous === "connected") clearList();
  }
}

async function poll() {
  try {
    const { connected, port } = await getJson("/api/status");
    render(connected ? "connected" : "disconnected", port);
  } catch {
    render("offline");
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

poll();
