// ParayaBuck api.js — fetch helper with control token, no-store, error decoding.
// Token lives only in-page (meta tag) — never persisted to localStorage/sessionStorage/URL.
(function (global) {
  "use strict";
  const meta = document.querySelector('meta[name="parayabuck-control-token"]');
  let controlToken = meta ? meta.content : "";
  let controlHeaders = { "X-ParayaBuck-Control-Token": controlToken };

  async function refreshControlToken() {
    const resp = await fetch("/api/control-token", { cache: "no-store" });
    if (!resp.ok) throw new Error("Yerel kontrol oturumu yenilenemedi");
    const data = await resp.json();
    controlToken = data.control_token;
    controlHeaders = { "X-ParayaBuck-Control-Token": controlToken };
    if (meta) meta.content = controlToken;
  }

  // A request that gets no response at all is reported in Turkish, not as the browser's raw network error text.
  async function send(url, init) {
    try {
      return await fetch(url, init);
    } catch (err) {
      if (err && err.name === "AbortError") throw new Error("Sunucu zamanında yanıt vermedi");
      throw new Error("Sunucuya ulaşılamadı; bağlantıyı kontrol edin");
    }
  }

  async function controlFetch(url, options) {
    options = options || {};
    // always add no-store for control-plane
    const headers = Object.assign({}, options.headers || {}, controlHeaders);
    let response = await send(url, Object.assign({}, options, { headers, cache: "no-store" }));
    if (response.status !== 403) return response;
    let detail = "";
    try {
      const clone = response.clone();
      const pj = await clone.json();
      detail = pj.detail || "";
    } catch (_) {
      return response;
    }
    if (detail !== "Invalid local control token") return response;
    await refreshControlToken();
    const retryHeaders = Object.assign({}, options.headers || {}, controlHeaders);
    response = await send(url, Object.assign({}, options, { headers: retryHeaders, cache: "no-store" }));
    return response;
  }

  async function responsePayload(response) {
    const ct = response.headers.get("content-type") || "";
    if (ct.includes("application/json")) return response.json();
    const txt = (await response.text()).trim();
    return { detail: txt || "Sunucu hat\u0131 (" + response.status + ")" };
  }

  // FastAPI 422 responses carry `detail` as an array of {loc, msg, ...};
  // joining the messages keeps the operator from seeing "[object Object]".
  function detailText(payload, fallback) {
    const detail = payload ? payload.detail : undefined;
    if (Array.isArray(detail)) {
      const parts = detail.map(function (item) {
        if (item && typeof item === "object") {
          const loc = Array.isArray(item.loc) ? item.loc.filter(function (p) { return p !== "body"; }).join(".") : "";
          // Pydantic prefixes custom validator messages with "Value error, "; the operator reads only the text.
          const msg = item.msg !== undefined ? String(item.msg).replace(/^Value error, /, "") : JSON.stringify(item);
          return loc ? loc + ": " + msg : msg;
        }
        return String(item);
      }).filter(Boolean);
      return parts.length ? parts.join("; ") : fallback;
    }
    if (detail && typeof detail === "object") return JSON.stringify(detail);
    return detail ? String(detail) : fallback;
  }

  async function readJson(r) {
    if (!r.ok) {
      const payload = await responsePayload(r);
      const err = new Error(detailText(payload, "\u0130stek ba\u015far\u0131s\u0131z"));
      err.status = r.status;
      err.payload = payload;
      throw err;
    }
    return r.json();
  }

  // Reads give up after READ_TIMEOUT_MS: a request that never answers must not hold a poll's busy flag forever.
  const READ_TIMEOUT_MS = 15000;

  async function fetchJson(url, opts) {
    if (opts && opts.control) return readJson(await controlFetch(url, opts));
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const timer = controller ? global.setTimeout(function () { controller.abort(); }, READ_TIMEOUT_MS) : null;
    try {
      return await readJson(await send(url, { cache: "no-store", signal: controller ? controller.signal : undefined }));
    } finally {
      if (timer !== null) global.clearTimeout(timer);
    }
  }

  global.PBApi = { controlFetch, responsePayload, detailText, fetchJson };
})(window);
