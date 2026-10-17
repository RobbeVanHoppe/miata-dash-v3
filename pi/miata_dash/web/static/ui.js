// Shared by every page: tells the server which page is on screen and follows
// navigation requested with the knob (long press = next page, layout change = reload).
(function () {
  const POLL_MS = 250;
  let seq = null;

  async function poll() {
    try {
      const resp = await fetch("/api/ui", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ page: location.pathname }),
      });
      if (resp.ok) {
        const r = await resp.json();
        if (r.navigate && r.navigate !== location.pathname) {
          location.assign(r.navigate);
          return;
        }
        if (seq !== null && r.reload_seq !== seq) {
          location.reload();
          return;
        }
        seq = r.reload_seq;
      }
    } catch (e) {
      // server restarting: keep trying
    }
    setTimeout(poll, POLL_MS);
  }

  poll();
})();
