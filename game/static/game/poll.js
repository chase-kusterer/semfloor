/*
 * Tiny polling helper. Phase 2 keeps the board and console fresh by re-fetching the
 * state snapshot on an interval. In the live-ticker sprint, swap the body of
 * startPolling() for a WebSocket subscription that calls onData(snapshot) on each push
 * — the render functions in each page do not change.
 */
function startPolling(url, onData, intervalMs) {
  async function tick() {
    try {
      const res = await fetch(url, { headers: { "X-Requested-With": "fetch" } });
      if (res.ok) onData(await res.json());
    } catch (e) { /* ignore transient errors; try again next tick */ }
  }
  tick();
  return setInterval(tick, intervalMs || 2500);
}

function money(n) {
  return "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}
