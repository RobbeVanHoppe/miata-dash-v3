// Polls /api/state and renders the active layout (digital, retro, motorsport).
// A gauge goes grey when all of its signals are missing or older than staleAfter.
(function () {
  const POLL_MS = 100;
  const D = window.DASH;
  const SVG = "http://www.w3.org/2000/svg";

  // Latest fresh numeric value of a gauge, trying its fallback signals in order.
  function valueOf(g, state) {
    for (const name of g.signals) {
      const r = state[name];
      if (r && typeof r.value === "number" && r.age <= D.staleAfter) return r.value;
    }
    return null;
  }

  function fmt(g, v) {
    return v === null ? "--" : v.toFixed(g.decimals);
  }

  function fraction(g, v) {
    return Math.max(0, Math.min(1, (v - g.minimum) / (g.maximum - g.minimum)));
  }

  // "DR" while speed comes from dead reckoning (GPS lost, IMU bridging).
  function speedBadge(state) {
    const m = state["ins.mode"];
    return m && m.value === "dr" && m.age <= D.staleAfter ? "DR" : "";
  }

  // ---- digital -------------------------------------------------------------
  function digital() {
    const els = D.gauges.map((g) => {
      const root = document.getElementById("g-" + g.id);
      return { g, root, num: root.querySelector(".num"), fill: root.querySelector(".fill"),
               badge: root.querySelector(".badge") };
    });
    return (state) => {
      for (const e of els) {
        const v = valueOf(e.g, state);
        e.root.classList.toggle("stale", v === null);
        e.num.textContent = fmt(e.g, v);
        e.fill.style.width = v === null ? "0%" : 100 * fraction(e.g, v) + "%";
        if (e.g.id === "speed") e.badge.textContent = speedBadge(state);
      }
    };
  }

  // ---- retro (round dials) -------------------------------------------------
  const START = 135, SWEEP = 270; // degrees, SVG coordinates (y down): 7:30 → 4:30

  function polar(r, deg) {
    const a = (deg * Math.PI) / 180;
    return [100 + r * Math.cos(a), 100 + r * Math.sin(a)];
  }

  function el(tag, attrs, parent) {
    const n = document.createElementNS(SVG, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  function arc(r, a1, a2) {
    const [x1, y1] = polar(r, a1), [x2, y2] = polar(r, a2);
    const large = a2 - a1 > 180 ? 1 : 0;
    return `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`;
  }

  function niceStep(g) {
    if (g.dial_step) return g.dial_step;
    const raw = (g.maximum - g.minimum) / 6;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw);
  }

  function buildDial(root, g) {
    const svg = el("svg", { viewBox: "0 0 200 200", class: "dial-svg" }, root);
    el("circle", { cx: 100, cy: 100, r: 96, class: "dial-face" }, svg);
    const angle = (v) => START + SWEEP * fraction(g, v);
    if (g.id === "rpm" && D.shiftRpm < g.maximum) {
      el("path", { d: arc(84, angle(D.shiftRpm), START + SWEEP), class: "dial-red" }, svg);
    }
    const step = niceStep(g);
    for (let v = g.minimum; v <= g.maximum + 1e-9; v += step / 2) {
      const major = Math.abs(((v - g.minimum) / step) % 1) < 1e-6;
      const a = angle(v);
      const [x1, y1] = polar(88, a), [x2, y2] = polar(major ? 74 : 80, a);
      el("line", { x1, y1, x2, y2, class: major ? "tick major" : "tick" }, svg);
      if (major) {
        const [tx, ty] = polar(60, a);
        const t = el("text", { x: tx, y: ty, class: "dial-num" }, svg);
        t.textContent = +(v / g.dial_label_div).toFixed(1);
      }
    }
    const label = el("text", { x: 100, y: 140, class: "dial-label" }, svg);
    label.textContent = g.id === "rpm" ? "×1000 r/min" : `${g.label} ${g.unit}`.trim();
    const readout = el("text", { x: 100, y: 162, class: "dial-readout" }, svg);
    const needle = el("g", { class: "needle" }, svg);
    el("line", { x1: 100 - 14, y1: 100, x2: 100 + 82, y2: 100, class: "needle-line" }, needle);
    el("circle", { cx: 100, cy: 100, r: 7, class: "needle-cap" }, svg);
    return { g, root, angle, needle, readout };
  }

  function retro() {
    const dials = D.gauges.map((g) => buildDial(document.getElementById("g-" + g.id), g));
    return (state) => {
      for (const d of dials) {
        const v = valueOf(d.g, state);
        d.root.classList.toggle("stale", v === null);
        const a = d.angle(v === null ? d.g.minimum : v);
        d.needle.style.transform = `rotate(${a}deg)`; // pivot set in CSS (view-box 100,100)
        let text = fmt(d.g, v);
        if (d.g.id === "speed" && speedBadge(state)) text += " DR";
        d.readout.textContent = text;
      }
    };
  }

  // ---- motorsport ------------------------------------------------------------
  const SEGMENTS = 15, SHIFT_SPAN = 3000; // lights cover the last 3000 rpm before shift

  function motorsport() {
    const bar = document.getElementById("shiftbar");
    const segs = [];
    for (let i = 0; i < SEGMENTS; i++) {
      const s = document.createElement("div");
      s.className = "seg " + (i < 6 ? "green" : i < 11 ? "yellow" : "red");
      bar.appendChild(s);
      segs.push(s);
    }
    const items = D.gauges.map((g) => {
      const root = document.getElementById("g-" + g.id);
      return { g, root, num: root.querySelector(".num"), badge: root.querySelector(".badge") };
    });
    const rpmGauge = D.gauges.find((g) => g.id === "rpm");
    return (state) => {
      for (const it of items) {
        const v = valueOf(it.g, state);
        it.root.classList.toggle("stale", v === null);
        it.num.textContent = fmt(it.g, v);
        if (it.badge && it.g.id === "speed") it.badge.textContent = speedBadge(state);
      }
      const rpm = rpmGauge ? valueOf(rpmGauge, state) : null;
      const lit = rpm === null ? 0 : Math.round(SEGMENTS * (rpm - (D.shiftRpm - SHIFT_SPAN)) / SHIFT_SPAN);
      segs.forEach((s, i) => s.classList.toggle("on", i < lit));
      bar.classList.toggle("shift", rpm !== null && rpm >= D.shiftRpm);
    };
  }

  // ---- loop ------------------------------------------------------------------
  const render = { digital, retro, motorsport }[D.layout]();

  async function poll() {
    try {
      const resp = await fetch("/api/state", { cache: "no-store" });
      if (resp.ok) render(await resp.json());
    } catch (e) {
      render({}); // backend down: everything stale
    }
    setTimeout(poll, POLL_MS);
  }

  document.getElementById("layout-cycle").addEventListener("click", async () => {
    await fetch("/api/layout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step: 1 }),
    });
    location.reload();
  });

  poll();
})();
