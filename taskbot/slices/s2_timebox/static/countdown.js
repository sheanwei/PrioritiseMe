// Pure countdown maths for the Timeboxes panel (FR-20). No DOM, so it can be
// unit-tested under node (see tests/test_countdown_js.py).
//
// The browser clock may be off from the server's. Each list response carries
// `server_now_ms`; we estimate the offset at the midpoint of the request, so
// the error is bounded by half the round trip (milliseconds on localhost).

/** Offset to add to the client clock to get server time, in ms. */
export function clockSkewMs(serverNowMs, sentAtMs, receivedAtMs) {
  return serverNowMs - (sentAtMs + receivedAtMs) / 2;
}

/** Milliseconds left until `endMs` (server time), given the client clock and skew. */
export function remainingMs(endMs, clientNowMs, skewMs) {
  return endMs - (clientNowMs + skewMs);
}

/** "mm:ss" or "h:mm:ss", rounded up so "0:00" only shows once time is really up. */
export function formatRemaining(ms) {
  if (!(ms > 0)) return "0:00";
  const total = Math.ceil(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}
