// Shared source for the two "Probably not needed" views (list footer and
// status item), so they poll /candidates once between them.

const listeners = new Set();
let started = false;
let inflight = null;

export function refreshCandidates(api) {
  if (inflight) return inflight;
  inflight = api.get("/s3_resurface/candidates")
    .then((data) => { for (const fn of listeners) fn(null, data.items); })
    .catch((err) => { for (const fn of listeners) fn(err, null); })
    .finally(() => { inflight = null; });
  return inflight;
}

/** fn(err, items) is called after every refresh. */
export function subscribeCandidates(api, fn) {
  listeners.add(fn);
  if (!started) {
    started = true;
    api.on("items:changed", () => refreshCandidates(api));
    setInterval(() => refreshCandidates(api), 60000);
  }
  refreshCandidates(api);
}
