// ParayaBuck websocket.js — ticket-fetch + WS connect with back-off.
// Uses PBApi.controlFetch, never persists ticket or token to storage/URL fragment.
(function (global) {
  "use strict";
  function reconnectDelay(attempt) {
    const base = Math.min(30000, 1000 * (Math.pow(2, Math.min(attempt, 5))));
    return base * (0.8 + Math.random() * 0.4);
  }
  async function fetchTicket(kind) {
    const resp = await global.PBApi.controlFetch("/api/ws-ticket", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: kind })
    });
    const payload = await resp.json();
    if (!resp.ok) throw new Error(global.PBApi.detailText(payload, "ticket al\u0131namad\u0131"));
    return payload.ticket;
  }
  function connectStream(onUpdate) {
    let ws = null;
    let attempts = 0;
    const loc = window.location;
    const proto = loc.protocol === "https:" ? "wss:" : "ws:";
    async function connect() {
      if (ws && [WebSocket.OPEN, WebSocket.CONNECTING].indexOf(ws.readyState) !== -1) return;
      let ticket;
      try { ticket = await fetchTicket("stream"); } catch (_) {
        setTimeout(connect, reconnectDelay(attempts++));
        return;
      }
      ws = new WebSocket(proto + "//" + loc.host + "/ws/stream?ticket=" + encodeURIComponent(ticket));
      ws.onopen = function(){ attempts = 0; };
      ws.onmessage = function(event){
        try {
          const payload = JSON.parse(event.data);
          onUpdate(payload);
        } catch (e) { console.error("Data WS error", e); }
      };
      ws.onclose = function(){
        ws = null;
        setTimeout(connect, reconnectDelay(attempts++));
      };
    }
    connect();
  }
  function connectLogs(onLog) {
    let ws = null;
    async function connect() {
      try {
        const ticket = await fetchTicket("logs");
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(proto + "//" + location.host + "/ws/logs?ticket=" + encodeURIComponent(ticket));
        ws.onmessage = function(event){
          try { onLog(JSON.parse(event.data)); } catch(_){}
        };
        ws.onclose = function(){ setTimeout(connect, 2000); };
      } catch(_) { setTimeout(connect, 2000); }
    }
    connect();
  }
  global.PBWebSocket = { connectStream: connectStream, connectLogs: connectLogs };
})(window);
