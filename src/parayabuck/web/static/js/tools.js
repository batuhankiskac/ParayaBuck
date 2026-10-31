// ParayaBuck tools.js — backtest runner, raw operations state and the live engine console.
// Engine controls, status and settings come from the shared shell (ui.js).
(function (global) {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    if (!document.getElementById("backtestRunBtn")) return;
    const PB = global.PB;
    const api = global.PBApi;
    const fmt = PB.fmt;
    const el = PB.el;
    const byId = function (id) { return document.getElementById(id); };

    // ---------- console ----------
    const LEVEL_RANK = { debug: 0, info: 1, warning: 2, error: 3, critical: 4 };
    let minLevel = 0;
    const logView = byId("terminalLogView");
    function applyFilter(line) {
      line.hidden = (LEVEL_RANK[line.dataset.level] || 1) < minLevel;
    }
    function appendLog(message, level, at) {
      const lv = String(level || "info").toLowerCase();
      const stick = logView.scrollTop + logView.clientHeight >= logView.scrollHeight - 24;
      const line = el("div", { className: "log-line " + lv }, [
        el("span", { className: "t", text: fmt.time(at) }),
        el("span", { className: "lv", text: lv.toUpperCase() }),
        el("span", { className: "m", text: message })
      ]);
      line.dataset.level = lv;
      applyFilter(line);
      logView.appendChild(line);
      while (logView.children.length > 1000) logView.firstElementChild.remove();
      if (stick) logView.scrollTop = logView.scrollHeight;
    }
    document.querySelectorAll(".filter-chip[data-level]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        document.querySelectorAll(".filter-chip[data-level]").forEach(function (c) { c.classList.toggle("active", c === chip); });
        minLevel = chip.dataset.level === "all" ? 0 : LEVEL_RANK[chip.dataset.level];
        Array.prototype.forEach.call(logView.children, applyFilter);
      });
    });
    byId("clearLogsBtn").addEventListener("click", function () { logView.replaceChildren(); });
    appendLog("Konsol bağlandı; motor logları burada akacak.", "info");
    if (global.PBWebSocket) {
      global.PBWebSocket.connectLogs(function (payload) { appendLog(payload.message, payload.level, payload.time); });
    }

    // ---------- raw operations state ----------
    PB.onStatus(function (data) {
      const universe = data.universe || {};
      const operations = data.operations || {};
      const scanner = data.scanner || {};
      const raw = {
        mode: data.paper === false ? "LIVE" : "PAPER",
        engine_state: data.state,
        strategies: (data.runtime && data.runtime.strategies) || [],
        active_universe: {
          equity: (universe.equity && universe.equity.symbols) || []
        },
        pending_orders: operations.pending_orders || [],
        recent_fills: operations.recent_fills || [],
        last_decision: operations.last_decision || null,
        scanner_status: { equity: scanner.equity_status || null },
        last_reconciliation: (data.runtime && data.runtime.last_reconciliation_at) || null,
        risk_status: (data.runtime && data.runtime.circuit_breaker_reason) || "ready"
      };
      byId("operationsDetail").textContent = JSON.stringify(raw, null, 2);
    });
    byId("copyOpsBtn").addEventListener("click", async function () {
      try {
        await navigator.clipboard.writeText(byId("operationsDetail").textContent);
        PB.toast("Ham durum panoya kopyalandı.", "success");
      } catch (_) {
        PB.toast("Panoya kopyalanamadı.", "error");
      }
    });

    // ---------- backtest ----------
    const STATE_LABELS = { idle: ["beklemede", "badge"], running: ["çalışıyor", "badge warn"], done: ["tamamlandı", "badge ok"], error: ["hata", "badge bad"] };
    let lastState = null;
    function renderSummary(data) {
      const box = byId("backtestSummary");
      if (data.error) {
        box.hidden = false;
        box.replaceChildren(el("div", { className: "result-error", text: "Hata: " + data.error }));
        return;
      }
      const s = data.summary;
      if (!s) { box.hidden = true; return; }
      const pct = function (key) { return fmt.signedPct(Number(s[key] || 0) * 100, 2); };
      const tone = function (key) { return fmt.tone(s[key]); };
      const period = data.period;
      const items = [
        ["Dönem", period ? period.start + " – " + period.end + " (" + period.days + " gün)" : "—", ""],
        ["Toplam getiri", pct("total_return"), tone("total_return")],
        ["Yıllık (CAGR)", pct("cagr"), tone("cagr")],
        ["Kıyas (" + (s.benchmark_symbol || "—") + ")", pct("benchmark_return"), ""],
        ["En büyük düşüş", fmt.pct(Math.abs(Number(s.max_drawdown || 0)) * 100, 2), "neg"],
        ["Sharpe", fmt.num(s.sharpe, 2), ""],
        ["İşlem sayısı", fmt.num(s.number_of_trades, 0), ""],
        ["Kazanma oranı", fmt.pct(Number(s.win_rate || 0) * 100, 1), ""],
        ["Bitiş bakiyesi", fmt.money(s.ending_equity), ""]
      ];
      box.hidden = false;
      box.replaceChildren(el("div", { className: "result-grid" }, items.map(function (it) {
        return el("div", { className: "result" }, [el("small", { text: it[0] }), el("strong", { className: it[2], text: it[1] })]);
      })));
    }
    function renderProgress(lines) {
      const box = byId("backtestProgress");
      if (!lines.length) return;
      box.replaceChildren.apply(box, lines.map(function (text) {
        return el("div", { className: "log-line plain" }, [el("span", { className: "m", text: text })]);
      }));
      box.scrollTop = box.scrollHeight;
    }
    async function refreshBacktest() {
      try {
        const data = await api.fetchJson("/api/backtest/status");
        const label = STATE_LABELS[data.state] || [data.state, "badge"];
        PB.setText("backtestStateBadge", label[0], label[1]);
        byId("backtestRunBtn").disabled = data.state === "running";
        renderProgress(data.progress || []);
        if (data.state === "done" || data.state === "error") renderSummary(data);
        else if (data.state === "running") byId("backtestSummary").hidden = true;
        if (lastState === "running" && data.state === "done") PB.toast("Backtest tamamlandı.", "success");
        if (lastState === "running" && data.state === "error") PB.toast("Backtest hata ile bitti.", "error");
        lastState = data.state;
      } catch (e) {
        console.error("backtest status", e);
      }
    }
    byId("backtestRunBtn").addEventListener("click", async function () {
      let parameters = {};
      const rawParameters = byId("btParameters").value.trim();
      try {
        if (rawParameters) parameters = JSON.parse(rawParameters);
        if (!parameters || Array.isArray(parameters) || typeof parameters !== "object") throw new Error();
      } catch (_) {
        PB.toast("Parametreler geçerli bir JSON nesnesi olmalı.", "warning");
        return;
      }
      const days = parseInt(byId("btDays").value, 10);
      const payload = {
        market: "equity",
        strategy: byId("btStrategy").value.trim(),
        parameters: parameters,
        years: parseInt(byId("btYears").value, 10) || 3,
        days: Number.isNaN(days) ? 0 : days,
        symbols: byId("btSymbols").value.trim()
      };
      const btn = byId("backtestRunBtn");
      btn.disabled = true;
      try {
        const res = await api.controlFetch("/api/backtest/run", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
        });
        const data = await api.responsePayload(res);
        if (!res.ok) throw new Error(api.detailText(data, "Backtest başlatılamadı"));
        PB.toast("Backtest başladı (" + data.run_id + ").", "success");
        lastState = "running";
        await refreshBacktest();
      } catch (err) {
        PB.toast("Backtest başlatılamadı: " + err.message, "error");
        btn.disabled = false;
      }
    });

    refreshBacktest();
    global.setInterval(refreshBacktest, 3000);
  });
})(window);
