// <amp-knob> — a rotary control styled like an amp / stompbox knob.
//
//   <amp-knob label="Gain" min="0" max="10" value="5" step="0.1"></amp-knob>
//
// Interaction: drag up/down (Shift = fine), mouse wheel, arrow keys,
// PageUp/PageDown, Home/End, double-click to reset to the initial value.
// Fires "input" while moving and "change" when a gesture ends.

const SWEEP = 270;            // degrees of travel, like a real pot
const DRAG_PIXELS = 200;      // pixels of vertical drag for the full range
const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}

class AmpKnob extends HTMLElement {
  connectedCallback() {
    if (this._built) return;
    this._built = true;

    this.min = Number(this.getAttribute("min") ?? 0);
    this.max = Number(this.getAttribute("max") ?? 10);
    this.step = Number(this.getAttribute("step") ?? 0.1);
    this.defaultValue = Number(this.getAttribute("value") ?? this.min);
    this._value = this.defaultValue;

    this._render();
    this._bindEvents();
    this._update();
  }

  get value() { return this._value; }

  set value(v) {
    const clamped = Math.min(this.max, Math.max(this.min, v));
    const snapped = Math.round(clamped / this.step) * this.step;
    if (snapped === this._value) return;
    this._value = snapped;
    this._update();
    this.dispatchEvent(new Event("input", { bubbles: true }));
  }

  get _fraction() { return (this._value - this.min) / (this.max - this.min); }

  _render() {
    const label = this.getAttribute("label") ?? "";
    this.setAttribute("role", "slider");
    this.setAttribute("tabindex", "0");
    this.setAttribute("aria-label", label);
    this.setAttribute("aria-valuemin", this.min);
    this.setAttribute("aria-valuemax", this.max);

    const face = svg("svg", { viewBox: "0 0 100 100", class: "knob-svg", "aria-hidden": "true" });

    // Scale ticks with 0 and 10 at the ends, like an amp panel.
    const ticks = svg("g", { class: "knob-ticks" });
    for (let i = 0; i <= 10; i++) {
      const angle = (-SWEEP / 2 + (SWEEP * i) / 10) * (Math.PI / 180);
      const [sin, cos] = [Math.sin(angle), -Math.cos(angle)];
      const major = i % 5 === 0;
      ticks.append(svg("line", {
        x1: 50 + sin * (major ? 44 : 45.5), y1: 50 + cos * (major ? 44 : 45.5),
        x2: 50 + sin * 49, y2: 50 + cos * 49,
        class: major ? "tick major" : "tick",
      }));
    }
    face.append(ticks);

    // Value arc: a track plus a glowing fill, both starting at 7:30 o'clock.
    const arcAttrs = { cx: 50, cy: 50, r: 40.5, pathLength: 360, transform: "rotate(135 50 50)" };
    face.append(svg("circle", { ...arcAttrs, class: "knob-track", "stroke-dasharray": `${SWEEP} 360` }));
    this._arc = svg("circle", { ...arcAttrs, class: "knob-arc" });
    face.append(this._arc);

    // The knob itself: knurled skirt, cap and pointer rotate together.
    this._rotor = svg("g", { class: "knob-rotor" });
    this._rotor.append(svg("circle", { cx: 50, cy: 50, r: 35, class: "knob-skirt" }));
    const knurl = svg("g", { class: "knob-knurl" });
    for (let i = 0; i < 48; i++) {
      knurl.append(svg("line", { x1: 50, y1: 15.5, x2: 50, y2: 20, transform: `rotate(${i * 7.5} 50 50)` }));
    }
    this._rotor.append(knurl);
    this._rotor.append(svg("circle", { cx: 50, cy: 50, r: 27, class: "knob-cap" }));
    this._rotor.append(svg("line", { x1: 50, y1: 26, x2: 50, y2: 40, class: "knob-pointer" }));
    face.append(this._rotor);
    // Highlight stays put (fixed light source) while the knob turns under it.
    face.append(svg("circle", { cx: 50, cy: 50, r: 27, class: "knob-shine" }));

    this._readout = document.createElement("span");
    this._readout.className = "knob-readout";

    const caption = document.createElement("span");
    caption.className = "knob-label";
    caption.textContent = label;

    this.append(face, this._readout, caption);
  }

  _update() {
    const angle = -SWEEP / 2 + SWEEP * this._fraction;
    this._rotor.setAttribute("transform", `rotate(${angle} 50 50)`);
    this._arc.setAttribute("stroke-dasharray", `${SWEEP * this._fraction} 360`);
    const text = this._value.toFixed(this.step < 1 ? 1 : 0);
    this._readout.textContent = text;
    this.setAttribute("aria-valuenow", this._value);
    this.setAttribute("aria-valuetext", text);
  }

  _commit() {
    this.dispatchEvent(new Event("change", { bubbles: true }));
  }

  _bindEvents() {
    const range = this.max - this.min;

    this.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      this.focus();
      this.setPointerCapture(event.pointerId);
      this.classList.add("is-turning");
      let lastY = event.clientY;

      const move = (e) => {
        const fine = e.shiftKey ? 0.2 : 1;
        this.value = this._value + ((lastY - e.clientY) / DRAG_PIXELS) * range * fine;
        lastY = e.clientY;
      };
      const up = () => {
        this.classList.remove("is-turning");
        this.removeEventListener("pointermove", move);
        this.removeEventListener("pointerup", up);
        this.removeEventListener("pointercancel", up);
        this._commit();
      };
      this.addEventListener("pointermove", move);
      this.addEventListener("pointerup", up);
      this.addEventListener("pointercancel", up);
    });

    this.addEventListener("wheel", (event) => {
      event.preventDefault();
      this.value = this._value + (event.deltaY < 0 ? 1 : -1) * this.step * (event.shiftKey ? 1 : 5);
      this._commit();
    }, { passive: false });

    this.addEventListener("keydown", (event) => {
      const big = range / 10;
      const moves = {
        ArrowUp: this.step, ArrowRight: this.step,
        ArrowDown: -this.step, ArrowLeft: -this.step,
        PageUp: big, PageDown: -big,
      };
      if (event.key in moves) this.value = this._value + moves[event.key];
      else if (event.key === "Home") this.value = this.min;
      else if (event.key === "End") this.value = this.max;
      else return;
      event.preventDefault();
      this._commit();
    });

    this.addEventListener("dblclick", () => {
      this.value = this.defaultValue;
      this._commit();
    });
  }
}

customElements.define("amp-knob", AmpKnob);
