// Radio page: talks to /api/radio. The server owns all state; this page only
// renders what it returns, so the knob and the touchscreen stay in sync.
(function () {
  const $ = (id) => document.getElementById(id);
  let current = null;

  async function call(body) {
    const opts = body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : {};
    const resp = await fetch("/api/radio", opts);
    const data = await resp.json();
    if (!resp.ok) {
      $("status").textContent = data.error || "error";
      return null;
    }
    render(data);
    return data;
  }

  function chip(text, onClick, onDelete) {
    const c = document.createElement("span");
    c.className = "chip";
    c.textContent = text;
    c.addEventListener("click", onClick);
    if (onDelete) {
      const x = document.createElement("button");
      x.className = "chip-x";
      x.textContent = "×";
      x.addEventListener("click", (e) => {
        e.stopPropagation();
        onDelete();
      });
      c.appendChild(x);
    }
    return c;
  }

  function render(s) {
    current = s;
    if (!s.bands) return;
    const band = s.bands.find((b) => b.id === s.band);
    $("freq").textContent = s.freq_mhz.toFixed(band.step_khz < 100 ? 3 : 1);
    $("band-name").textContent = band.name;
    $("play").classList.toggle("on", s.playing);
    $("squelch-row").hidden = band.mode === "wbfm";
    $("squelch").value = s.squelch;
    $("squelch-val").textContent = s.squelch;
    $("status").textContent = !s.available
      ? "rtl_fm / aplay not installed (see README)"
      : s.error || (s.playing ? "Playing" : "Stopped");

    const bands = $("bands");
    bands.replaceChildren(
      ...s.bands.map((b) => {
        const btn = document.createElement("button");
        btn.textContent = b.name;
        btn.className = "band-btn" + (b.id === s.band ? " active" : "");
        btn.addEventListener("click", () => call({ action: "tune", band: b.id }));
        return btn;
      })
    );

    const presets = $("presets");
    presets.replaceChildren(
      ...(s.presets || []).map((p) =>
        chip(
          `${p.freq_mhz} ${p.band === "fm" ? "" : p.band}`.trim(),
          () => call({ action: "tune", band: p.band, freq_mhz: p.freq_mhz }),
          () => call({ action: "delete_preset", band: p.band, freq_mhz: p.freq_mhz })
        )
      )
    );
  }

  document.querySelectorAll("[data-action]").forEach((b) =>
    b.addEventListener("click", () =>
      call({ action: b.dataset.action, direction: Number(b.dataset.direction || 0) })
    )
  );
  $("play").addEventListener("click", () => call({ action: "tune" }));
  $("squelch").addEventListener("change", (e) => call({ action: "squelch", level: Number(e.target.value) }));
  $("scan").addEventListener("click", async () => {
    $("status").textContent = "Scanning FM band…";
    const data = await call({ action: "scan" });
    if (!data) return;
    const list = $("stations");
    list.replaceChildren(
      ...(data.stations.length
        ? data.stations.map((f) => chip(f.toFixed(1), () => call({ action: "tune", band: "fm", freq_mhz: f })))
        : [Object.assign(document.createElement("span"), { className: "muted", textContent: "Nothing found" })])
    );
  });

  call();
  setInterval(() => call(), 2000); // pick up changes made with the knob
})();
