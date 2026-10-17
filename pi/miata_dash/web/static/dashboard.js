// Polls /api/state and updates the gauges. A gauge goes grey when its
// signal is missing or older than STALE_AFTER seconds.
(function () {
  const POLL_MS = 100;
  const els = {};
  for (const g of window.GAUGES) {
    const root = document.getElementById("g-" + g.id);
    els[g.id] = { root, num: root.querySelector(".num"), fill: root.querySelector(".fill") };
  }

  function render(state) {
    for (const g of window.GAUGES) {
      const el = els[g.id];
      const r = state[g.signal];
      const fresh = r && typeof r.value === "number" && r.age <= window.STALE_AFTER;
      el.root.classList.toggle("stale", !fresh);
      if (!fresh) {
        el.num.textContent = "--";
        el.fill.style.width = "0%";
        continue;
      }
      el.num.textContent = r.value.toFixed(g.decimals);
      const pct = (100 * (r.value - g.minimum)) / (g.maximum - g.minimum);
      el.fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
    }
  }

  async function poll() {
    try {
      const resp = await fetch("/api/state", { cache: "no-store" });
      if (resp.ok) render(await resp.json());
    } catch (e) {
      render({}); // backend down: show everything as stale
    }
    setTimeout(poll, POLL_MS);
  }

  poll();
})();
