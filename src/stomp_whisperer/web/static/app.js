// Polls the backend for the pedal's connection state and drives the UI:
// the patch list on the left and the selected patch's effect chain on the right.

const POLL_MS = 2000;
const EFFECT_INFO_POLL_MS = 1500;

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
  sandbox: { status: "Sandbox (no pedal)" },
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
let detailTimer = null;

// ---------- small DOM helper ----------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null) continue;
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
    const effectNames = patch.effects.map((fx) => fx.name ?? fx.id).join(" → ");
    const count = isEmpty ? "empty" : `${patch.effect_count} fx`;
    const button = el("button", {
      type: "button",
      class: "slot",
      dataset: { slot: patch.slot },
      "aria-current": patch.slot === selectedSlot ? "true" : "false",
      title: effectNames,
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
  clearTimeout(detailTimer);
  slotsEl.replaceChildren();
  setListState("Waiting for the pedal…");
  showDetailMessage("Select a patch to see its effect chain.");
}

// ---------- patch detail ----------

function showDetailMessage(text) {
  detailEl.replaceChildren(el("p", { class: "detail-empty" }, text));
}

function paramDetails(param) {
  return [param.explanation, param.range && `Range: ${param.range}`].filter(Boolean).join("\n");
}

// What the pedal shows for a raw value.
function displayOf(param, value) {
  return param.labels?.[value] ?? String(value);
}

// A raw value from an effect whose info hasn't been read yet.
function renderRawParam(param, index) {
  return el("div", { class: param.value === 0 ? "param is-zero" : "param" },
    el("dt", {}, `P${index + 1}`),
    el("dd", {}, param.display));
}

// Few positions (Mode, Ratio…): shown like a switch with the active option lit.
// `onEdit(value)` makes the options clickable (sandbox only).
function renderSwitch(param, onEdit) {
  const items = param.options.map((option, value) =>
    el("li", { class: value === param.value ? "is-active" : "" },
      onEdit ? el("button", { type: "button", onclick: () => choose(value) }, option) : option));
  const list = el("ul", { class: "switch-options", "aria-label": `${param.name}: ${param.display}` }, ...items);

  function choose(value) {
    if (value === param.value) return;
    param.value = value;
    items.forEach((item, i) => item.classList.toggle("is-active", i === value));
    list.setAttribute("aria-label", `${param.name}: ${displayOf(param, value)}`);
    onEdit(value);
  }

  return el("div", { class: "switch", title: paramDetails(param) || null },
    el("span", { class: "switch-label" }, param.name), list);
}

function renderKnob(param, onEdit) {
  const knob = el("amp-knob", {
    label: param.name,
    min: "0",
    max: String(param.max),
    step: "1",
    value: String(param.value),
    mark: param.default == null ? null : String(param.default),
    center: param.center == null ? null : String(param.center),
    readonly: onEdit ? null : "",
    title: [paramDetails(param), param.default == null ? null : "Dot: default value"]
      .filter(Boolean).join("\n") || null,
  });
  knob.displayText = param.display;
  if (onEdit) {
    let committed = param.value;
    knob.addEventListener("input", () => { knob.displayText = displayOf(param, knob.value); });
    knob.addEventListener("change", () => {
      if (knob.value === committed) return;
      committed = knob.value;
      onEdit(knob.value);
    });
  }
  return knob;
}

// Graphic EQ bands: centred (-N…+N) parameters named after a frequency.
const BAND_NAME = /^\d+(\.\d+)?\s*k?Hz$/i;
const MIN_EQ_BANDS = 3;

function isEqBand(param) {
  return param.center != null && BAND_NAME.test(param.name ?? "");
}

// Faders like a graphic EQ: 0 in the middle, a bar up or down to the value,
// and a line joining the bands to show the curve. `onEdit(band, value)` makes the
// faders draggable and keyboard-operable (sandbox only).
function renderEq(bands, onEdit) {
  const curve = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  curve.setAttribute("class", "eq-curve");
  curve.setAttribute("viewBox", "0 0 100 100");
  curve.setAttribute("preserveAspectRatio", "none");
  curve.setAttribute("aria-hidden", "true");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  line.setAttribute("vector-effect", "non-scaling-stroke");
  curve.append(line);
  const drawCurve = () => line.setAttribute("points", bands.map((band, i) => {
    const x = ((i + 0.5) / bands.length) * 100;
    const y = (1 - band.value / band.max) * 100;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" "));
  drawCurve();

  const valueCells = bands.map((band) => el("li", {}, band.display));

  const faders = bands.map((band, i) => {
    const describe = () => [paramDetails(band), `Value: ${band.display}`].filter(Boolean).join("\n");
    const fader = el("li", {
      class: "eq-band",
      title: describe(),
      "aria-label": onEdit ? band.name : `${band.name}: ${band.display}`,
      style: `--level: ${band.value / band.max}; --zero: ${band.center / band.max}`,
    });
    const slot = el("span", { class: "eq-slot" }, el("span", { class: "eq-fill" }), el("span", { class: "eq-thumb" }));
    fader.append(slot);
    if (!onEdit) return fader;

    const set = (value) => {
      value = Math.min(band.max, Math.max(0, Math.round(value)));
      if (value === band.value) return;
      band.value = value;
      band.display = displayOf(band, value);
      fader.style.setProperty("--level", value / band.max);
      fader.setAttribute("aria-valuenow", value);
      fader.setAttribute("aria-valuetext", band.display);
      fader.title = describe();
      valueCells[i].textContent = band.display;
      drawCurve();
    };
    let committed = band.value;
    const commit = () => {
      if (band.value === committed) return;
      committed = band.value;
      onEdit(band, band.value);
    };

    fader.classList.add("is-editable");
    Object.assign(fader, { tabIndex: 0 });
    fader.setAttribute("role", "slider");
    fader.setAttribute("aria-valuemin", 0);
    fader.setAttribute("aria-valuemax", band.max);
    fader.setAttribute("aria-valuenow", band.value);
    fader.setAttribute("aria-valuetext", band.display);

    const fromPointer = (event) => {
      const rect = slot.getBoundingClientRect();
      set((1 - (event.clientY - rect.top) / rect.height) * band.max);
    };
    fader.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      fader.focus();
      fader.setPointerCapture(event.pointerId);
      fromPointer(event);
      const up = () => {
        fader.removeEventListener("pointermove", fromPointer);
        fader.removeEventListener("pointerup", up);
        fader.removeEventListener("pointercancel", up);
        commit();
      };
      fader.addEventListener("pointermove", fromPointer);
      fader.addEventListener("pointerup", up);
      fader.addEventListener("pointercancel", up);
    });
    fader.addEventListener("keydown", (event) => {
      const moves = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 3, PageDown: -3 };
      if (event.key in moves) set(band.value + moves[event.key]);
      else if (event.key === "Home") set(0);
      else if (event.key === "End") set(band.max);
      else return;
      event.preventDefault();
      commit();
    });
    return fader;
  });

  return el("div", { class: "eq", role: "group", "aria-label": "Graphic EQ" },
    el("ol", { class: "eq-row eq-values", "aria-hidden": "true" }, ...valueCells),
    el("div", { class: "eq-stage" }, curve, el("ol", { class: "eq-row eq-bands" }, ...faders)),
    el("ol", { class: "eq-row eq-freqs", "aria-hidden": "true" },
      ...bands.map((band) => el("li", {}, band.name))));
}

// `onEdit(param, value)` is set in the sandbox; each param carries its storage `index`.
function renderControls(params, onEdit) {
  const bands = params.filter(isEqBand);
  const others = bands.length >= MIN_EQ_BANDS ? params.filter((p) => !isEqBand(p)) : params;
  return [
    bands.length >= MIN_EQ_BANDS ? renderEq(bands, onEdit) : null,
    el("div", { class: "controls", "aria-label": "Parameters" },
      ...others.map((param) => renderControl(param, onEdit && ((value) => onEdit(param, value))))),
  ];
}

function renderControl(param, onEdit) {
  return param.options ? renderSwitch(param, onEdit) : renderKnob(param, onEdit);
}

// ---------- sandbox editing ----------
// Changes live only in the server's memory; nothing is ever sent to the pedal.

let effectCatalog = [];

async function loadEffectCatalog() {
  try {
    effectCatalog = await getJson("/api/effects");
  } catch {
    effectCatalog = [];
  }
}

async function sendJson(method, url, body) {
  const response = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
  }
  return data;
}

// Applies an edit to the selected patch. Structural edits (`rerender`) redraw the
// detail and put the focus back on the control named by `focusKey`.
async function editPatch(method, path, body, { rerender = true, focusKey = null } = {}) {
  const slot = selectedSlot;
  let patch;
  let failure = null;
  try {
    patch = await sendJson(method, `/api/patches/${slot}${path}`, body);
  } catch (error) {
    failure = error;
    patch = await getJson(`/api/patches/${slot}`).catch(() => null);  // show what's really there
  }
  if (selectedSlot !== slot || !patch) return;
  if (rerender || failure) {
    const scrollTop = detailEl.scrollTop;
    renderDetail(patch);
    detailEl.scrollTop = scrollTop;
    if (focusKey) detailEl.querySelector(`[data-focus-key="${focusKey}"]`)?.focus();
  }
  if (failure) {
    const note = detailEl.querySelector(".edit-error");
    note.textContent = `Couldn't apply the change: ${failure.message}`;
    note.hidden = false;
  }
  getJson("/api/patches").then(renderList).catch(() => {});
}

function toolButton(label, title, focusKey, onclick, { disabled = false, extraClass = "" } = {}) {
  return el("button", {
    type: "button",
    class: `stomp-tool ${extraClass}`.trim(),
    title,
    "aria-label": title,
    disabled: disabled ? "" : null,
    dataset: { focusKey },
    onclick,
  }, label);
}

function renderPicker(fx) {
  const groups = new Map();
  for (const effect of effectCatalog) {
    const group = effect.group || "Other";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(effect);
  }
  const known = effectCatalog.some((effect) => effect.id === fx.id);
  return el("select", {
    class: "effect-picker",
    "aria-label": `Effect at position ${fx.position}`,
    dataset: { focusKey: `pick-${fx.position}` },
    onchange: (event) => editPatch("PATCH", `/effects/${fx.position}`, { id: event.target.value },
      { focusKey: `pick-${fx.position}` }),
  },
    fx.empty ? el("option", { value: "", disabled: "", selected: "" }, "Choose an effect…") : null,
    !fx.empty && !known ? el("option", { value: fx.id, selected: "" }, `Effect ${fx.id}`) : null,
    ...[...groups].map(([group, effects]) => el("optgroup", { label: group },
      ...effects.map((effect) =>
        el("option", { value: effect.id, selected: effect.id === fx.id ? "" : null }, effect.name)))));
}

// Move / on-off / remove controls on top of an effect.
function renderStompTools(fx, patch) {
  const pos = fx.position;
  const count = patch.chain.length;
  // Keep the focus on the same arrow unless the move made it disabled (chain ends).
  const move = (to) => () => {
    const side = to === 1 ? "right" : to === count ? "left" : to < pos ? "left" : "right";
    editPatch("POST", `/effects/${pos}/move`, { to }, { focusKey: `${side}-${to}` });
  };
  return [
    toolButton("◀", "Move earlier in the chain", `left-${pos}`, move(pos - 1), { disabled: pos === 1 }),
    toolButton("▶", "Move later in the chain", `right-${pos}`, move(pos + 1), { disabled: pos === count }),
    el("button", {
      type: "button",
      class: "stomp-switch",
      "aria-pressed": fx.enabled ? "true" : "false",
      title: fx.empty ? "Choose an effect first" : fx.enabled ? "Switch off" : "Switch on",
      disabled: fx.empty ? "" : null,
      dataset: { focusKey: `toggle-${pos}` },
      onclick: () => editPatch("PATCH", `/effects/${pos}`, { enabled: !fx.enabled },
        { focusKey: `toggle-${pos}` }),
    }, el("span", { class: "stomp-led" }), el("span", { class: "stomp-state" }, fx.enabled ? "On" : "Off")),
    patch.factory ? null : toolButton("✕", "Remove this effect slot", `remove-${pos}`,
      () => editPatch("DELETE", `/effects/${pos}`, null, { focusKey: "add" }),
      { disabled: count === 1, extraClass: "is-remove" }),
  ];
}

function renderEffect(fx, patch) {
  const editable = patch.sandbox;
  const top = editable
    ? el("div", { class: "stomp-top" },
        el("span", { class: "stomp-position" }, `#${fx.position}`),
        fx.group ? el("span", { class: "stomp-group" }, fx.group) : null,
        ...renderStompTools(fx, patch))
    : el("div", { class: "stomp-top" },
        el("span", { class: "stomp-position" }, `#${fx.position}`),
        fx.empty || !fx.group ? null : el("span", { class: "stomp-group" }, fx.group),
        fx.empty ? null : el("span", { class: "stomp-led", title: fx.enabled ? "On" : "Off" }),
        fx.empty ? null : el("span", { class: "stomp-state" }, fx.enabled ? "On" : "Off"));
  const picker = editable && !patch.factory ? renderPicker(fx) : null;

  if (fx.empty) {
    return el("li", { class: "stomp is-empty" }, top,
      picker ?? el("p", { class: "stomp-name" }, "Empty slot"));
  }
  const title = picker
    ? el("div", { class: "stomp-name" }, picker)
    : fx.name
      ? el("p", { class: "stomp-name", title: `Effect ID ${fx.id}` }, fx.name)
      : el("p", { class: "stomp-name" }, "Effect ", el("code", { title: "Effect ID" }, fx.id));
  const pending = fx.info_pending
    ? el("p", { class: "stomp-pending" }, "Reading effect info from the pedal…")
    : null;
  const params = fx.params.map((param, index) => ({ ...param, index }));
  const onEdit = editable
    ? (param, value) => editPatch("PATCH", `/effects/${fx.position}`, { params: { [param.index]: value } },
        { rerender: false })
    : null;
  return el("li", { class: fx.enabled ? "stomp is-on" : "stomp" },
    top,
    title,
    fx.description ? el("p", { class: "stomp-description" }, fx.description) : null,
    pending,
    ...(params.length && params[0].max != null
      ? renderControls(params, onEdit)
      : [el("dl", { class: "params", "aria-label": "Raw parameter values" },
          ...params.map(renderRawParam))]));
}

function renderAddSlot(patch) {
  if (!patch.sandbox || patch.factory || patch.chain.length >= patch.max_effects) return null;
  return el("li", { class: "stomp is-add" },
    el("button", {
      type: "button",
      class: "add-slot",
      dataset: { focusKey: "add" },
      onclick: () => editPatch("POST", "/effects", null, { focusKey: `pick-${patch.chain.length + 1}` }),
    }, "+ Add effect slot"));
}

// User patches in the sandbox get an editable name; Enter or leaving the field saves it.
function renderName(patch) {
  const name = patch.name ?? "(unreadable)";
  if (!patch.sandbox || patch.factory || patch.name == null) return el("h2", {}, name);
  const input = el("input", {
    class: "name-input",
    value: name,
    maxlength: "28",
    spellcheck: "false",
    autocomplete: "off",
    "aria-label": "Patch name",
    title: "Rename this patch (up to two lines of 14 characters on the pedal)",
    dataset: { focusKey: "name" },
    onchange: () => editPatch("PATCH", "", { name: input.value }),
    onkeydown: (event) => {
      if (event.key === "Enter") input.blur();
      else if (event.key === "Escape") {
        input.value = name;
        input.blur();
      }
    },
  });
  return el("h2", {}, input);
}

function renderSandboxNote(patch) {
  if (!patch.sandbox) return null;
  const text = patch.factory
    ? "Factory patch: effects can be switched on/off, reordered and adjusted, but the patch " +
      "can't be renamed and its effects can't be added, removed or replaced."
    : "Rename the patch, add effect slots, pick their effects and adjust them.";
  return el("p", { class: "sandbox-note" },
    el("strong", {}, "Sandbox · "), text,
    " Changes stay in memory only and are lost on Refresh or restart.");
}

function renderDetail(patch) {
  const effects = patch.chain.filter((fx) => !fx.empty);
  const summary = effects.length === 0 ? "No effects"
    : effects.length === 1 ? "Single effect"
    : `Chain of ${effects.length} effects`;

  detailEl.replaceChildren(...[
    el("header", { class: "detail-head" },
      el("p", { class: "detail-slot" }, `Slot ${slotLabel(patch.slot)}`),
      renderName(patch),
      el("p", { class: "detail-summary" }, summary,
        patch.checksum_ok ? null : el("span", { class: "warning" }, " · checksum mismatch")),
      patch.description ? el("p", { class: "detail-description" }, patch.description) : null,
      patch.error ? el("p", { class: "warning" }, patch.error) : null,
      renderSandboxNote(patch),
      el("p", { class: "warning edit-error", role: "alert", hidden: "" })),
    el("ol", { class: "chain", "aria-label": "Effect chain, in signal order" },
      ...patch.chain.map((fx) => renderEffect(fx, patch)), renderAddSlot(patch)),
    effects.some((fx) => fx.info_pending) ? el("p", { class: "detail-note" },
      "The first time, effect names are read from the pedal's own effect files " +
      "(a few seconds each). They're remembered after that.") : null,
  ].filter(Boolean));
}

async function selectSlot(slot, { scroll = false } = {}) {
  clearTimeout(detailTimer);
  selectedSlot = slot;
  history.replaceState(null, "", `#slot-${slot}`);
  for (const button of slotsEl.querySelectorAll(".slot")) {
    const current = Number(button.dataset.slot) === slot;
    button.setAttribute("aria-current", current ? "true" : "false");
    if (current && scroll) button.scrollIntoView({ block: "center" });
  }
  try {
    const patch = await getJson(`/api/patches/${slot}`);
    if (selectedSlot !== slot) return;
    renderDetail(patch);
    // Effect info arrives in the background; refresh until this patch is complete.
    if (patch.chain.some((fx) => fx.info_pending)) {
      detailTimer = setTimeout(() => refreshDetail(slot), EFFECT_INFO_POLL_MS);
    }
  } catch (error) {
    showDetailMessage(`Couldn't load slot ${slot}: ${error.message}`);
  }
}

async function refreshDetail(slot) {
  if (selectedSlot !== slot) return;
  const scrollTop = detailEl.scrollTop;
  await selectSlot(slot);
  detailEl.scrollTop = scrollTop;
}

refreshButton.addEventListener("click", () => loadList(true));

// ---------- connection state ----------

// Sandbox mode serves dumped patches as if the pedal were connected.
const isLive = (state) => state === "connected" || state === "sandbox";

function render(state, port) {
  if (state === lastState) return;
  const previous = lastState;
  lastState = state;

  const message = MESSAGES[state];
  statusEl.dataset.state = state;
  document.body.dataset.pedal = state;
  statusText.textContent = message.status;
  statusEl.title = port ?? "";

  if (isLive(state)) {
    modal.hidden = true;
    // The sandbox's effect picker needs the catalog before the first patch is drawn.
    (state === "sandbox" ? loadEffectCatalog() : Promise.resolve()).then(() => loadList());
  } else {
    modalTitle.textContent = message.title;
    modalBody.innerHTML = message.body;
    modal.hidden = false;
    if (isLive(previous)) clearList();
  }
}

async function poll() {
  try {
    const { connected, port, sandbox } = await getJson("/api/status");
    render(sandbox ? "sandbox" : connected ? "connected" : "disconnected", port);
  } catch {
    render("offline");
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

poll();
