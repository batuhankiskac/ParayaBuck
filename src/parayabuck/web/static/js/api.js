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

  async function controlFetch(url, options) {
    options = options || {};
    // always add no-store for control-plane
    const headers = Object.assign({}, options.headers || {}, controlHeaders);
    let response = await fetch(url, Object.assign({}, options, { headers, cache: "no-store" }));
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
    response = await fetch(url, Object.assign({}, options, { headers: retryHeaders, cache: "no-store" }));
    return response;
  }

  async function responsePayload(response) {
    const ct = response.headers.get("content-type") || "";
    if (ct.includes("application/json")) return response.json();
    const txt = (await response.text()).trim();
    return { detail: txt || "Sunucu hat\u0131 (" + response.status + ")" };
  }

  async function fetchJson(url, opts) {
    const r = opts && opts.control ? await controlFetch(url, opts) : await fetch(url, { cache: "no-store" });
    if (!r.ok) {
      const payload = await responsePayload(r);
      const err = new Error(payload.detail || "istek ba\u015far\u0131s\u0131z");
      err.status = r.status;
      err.payload = payload;
      throw err;
    }
    return r.json();
  }

  global.PBApi = { controlFetch, responsePayload, fetchJson, refreshControlToken, getControlToken: function(){return controlToken;} };
})(window);
