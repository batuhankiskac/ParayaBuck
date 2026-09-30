// ParayaBuck dashboard.js — overview page: account, performance chart, plain-language
// bot summary, positions, strategies, risk, trades and system health.
// Read-only: every value comes from the backend; nothing is recalculated for trading.
(function (global) {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    if (!document.getElementById("equityValue")) return;
    const PB = global.PB;
    const api = global.PBApi;
    const fmt = PB.fmt;
    const el = PB.el;
    const setText = PB.setText;
    const byId = function (id) { return document.getElementById(id); };
    const isNum = PB.isNum;

    const view = { account: null, overview: null, status: null, period: "1M", perf: null };

    // ---------- labels ----------
    const STRATEGY_STATUS = {
      active: "Aktif", warming: "Isınıyor", halted: "Güvenlik durdurdu",
      disabled: "Kapalı", stopped: "Motor kapalı", starting: "Başlatılıyor", stopping: "Durduruluyor",
      error: "Hata", stop_failed: "Durdurma hatası", unknown: "Bilinmiyor", unavailable: "Yok"
    };
    const CATCH_UP = {
      submitted: "Uygulandı", sell_phase_submitted: "Uygulandı (satış)", catch_up_superseded: "Gerek yok",
      duplicate_decision: "Gerek yok", already_invested: "Zaten yatırımda", warming_up: "Isınma bekleniyor",
      incomplete_strategy_batch: "Veri bekleniyor", rebalance_in_progress: "Dengeleme sürüyor",
      rebalance_blocked: "Dengeleme kilitli", no_eligible_bars: "Veri yok", not_scheduled: "Sıradaki periyot",
      not_attempted: "Henüz denenmedi"
    };
    const STRATEGY_DESC = {
      equity_leveraged_trend_v1: "QQQ 200 günlük ortalamasının üzerindeyken QLD (2x Nasdaq-100) tutar; piyasa dalgalandıkça payı küçültür, trend bozulunca nakde geçer. Her işlem günü kontrol eder."
    };
    const PERIOD_LABEL = { "1D": "son 1 gün", "1W": "son 1 hafta", "1M": "son 1 ay", "3M": "son 3 ay", "1A": "son 1 yıl", "ALL": "tüm zamanlar" };

    function cadence(value) {
      if (!value) return "—";
      const s = String(value);
      let m = s.match(/^hours:(\d+)$/);
      if (m) return Number(m[1]) === 24 ? "Günde bir" : m[1] + " saatte bir";
      m = s.match(/^(\d+)\s*(h|hour|hours|Hour)$/);
      if (m) return m[1] === "1" ? "Saatlik" : m[1] + " saatlik";
      m = s.match(/^(\d+)\s*(d|day|days|Day)$/);
      if (m) return m[1] === "1" ? "Günlük" : m[1] + " günlük";
      m = s.match(/^(\d+)\s*(m|min|Min)$/);
      if (m) return m[1] + " dakikalık";
      if (/daily|session/i.test(s)) return "Her işlem günü";
      return s;
    }
    function symbolBase(symbol) { return String(symbol || "").split("/")[0].slice(0, 4); }
    function sideBadge(side) {
      const s = String(side || "").toLowerCase();
      if (s === "buy") return el("span", { className: "badge ok", text: "Alış" });
      if (s === "sell") return el("span", { className: "badge bad", text: "Satış" });
      return el("span", { className: "badge", text: side || "—" });
    }
    function cell(content, className) {
      const td = el("td", className ? { className: className } : null);
      if (content instanceof Node) td.appendChild(content); else td.textContent = content === undefined || content === null ? "—" : String(content);
      return td;
    }
    function toneClass(base, v) { const t = fmt.tone(v); return base + (t ? " " + t : ""); }

    // ---------- account / hero ----------
    function renderAccount(acct) {
      if (!acct) return;
      view.account = Object.assign({}, view.account || {}, acct);
      const a = view.account;
      PB.setAccountSuffix(a.account_suffix);
      setText("equityValue", fmt.money(a.equity));
      const base = isNum(a.equity) && isNum(a.daily_pnl) ? Number(a.equity) - Number(a.daily_pnl) : null;
      const dailyPct = base && base > 0 ? (Number(a.daily_pnl) / base) * 100 : null;
      setText("dailyPnlValue", fmt.signedMoney(a.daily_pnl) + (dailyPct !== null ? " (" + fmt.signedPct(dailyPct, 2) + ")" : ""), toneClass("delta", a.daily_pnl));
      setText("cashValue", fmt.money(a.cash));
      if (isNum(a.cash) && isNum(a.equity) && Number(a.equity) > 0) setText("cashSub", "portföyün " + fmt.pct((a.cash / a.equity) * 100));
      const total = isNum(a.total_pnl) ? a.total_pnl : (isNum(a.realized_pnl) || isNum(a.unrealized_pnl) ? Number(a.realized_pnl || 0) + Number(a.unrealized_pnl || 0) : null);
      setText("totalPnlValue", fmt.signedMoney(total), toneClass("kpi-value", total));
      setText("realizedPnlValue", fmt.signedMoney(a.realized_pnl), toneClass("kpi-value", a.realized_pnl));
      setText("unrealizedPnlValue", fmt.signedMoney(a.unrealized_pnl), toneClass("kpi-value", a.unrealized_pnl));
      setText("drawdownValue", fmt.money(a.drawdown), "kpi-value" + (Number(a.drawdown) > 0 ? " neg" : ""));
      if (isNum(a.drawdown) && isNum(a.equity) && Number(a.equity) + Number(a.drawdown) > 0) {
        setText("drawdownSub", fmt.pct((a.drawdown / (Number(a.equity) + Number(a.drawdown))) * 100) + " — en yüksek bakiyeye göre");
      }
      setText("grossExposureValue", fmt.money(a.gross_exposure));
      if (isNum(a.fees)) setText("feesValue", fmt.money(a.fees));

      const fresh = byId("accountFreshness");
      if (fresh) {
        const stale = a.stale === true;
        fresh.className = "freshness" + (stale ? " stale" : "");
        const label = a.source === "unavailable" ? "hesap verisi yok"
          : stale ? "eski veri · " + fmt.duration(a.age_seconds) + " önce"
          : "canlı · " + fmt.time(a.snapshot_at);
        fresh.replaceChildren(el("span", { className: "dot" + (stale ? "" : " running") }), el("span", { text: label }));
      }
      setText("grossExposureSub", a.stale ? "eski veri (" + (a.source || "?") + ")" : "brüt maruziyet");
      if (Array.isArray(a.positions)) renderPositions(a.positions, a.equity);
    }

    function renderPositions(positions, equity) {
      const tbody = byId("positionsTableBody");
      setText("activePositionsBadge", positions.length + " pozisyon");
      if (!positions.length) { PB.emptyRow(tbody, 8, "Henüz açık pozisyon yok"); return; }
      const eq = Number(equity) || 0;
      const rows = positions.slice().sort(function (a, b) { return Number(b.market_value || 0) - Number(a.market_value || 0); }).map(function (p) {
        const crypto = p.market === "crypto";
        const owner = p.ownership_state === "managed" ? "Bot" : p.ownership_state === "mixed" ? "Bot + harici" : "Harici";
        const sym = el("span", { className: "sym" }, [
          el("span", { className: "sym-logo", text: symbolBase(p.symbol) }),
          el("span", null, [p.symbol, el("small", { text: (crypto ? "Kripto" : "Hisse/ETF") + " · " + owner + (p.direction === "SHORT" ? " · açığa satış" : "") })])
        ]);
        const qty = p.managed_qty != null && p.external_qty != null && Number(p.external_qty) > 0
          ? fmt.num(p.total_qty, 6) + " (" + fmt.num(p.managed_qty, 6) + " bot)"
          : fmt.num(p.qty, 6);
        const cost = Number(p.avg_price) * Number(p.qty);
        const pnlPct = cost > 0 && isNum(p.unrealized_pnl) ? (Number(p.unrealized_pnl) / cost) * 100 : null;
        const pnl = el("span", { className: fmt.tone(p.unrealized_pnl), text: fmt.signedMoney(p.unrealized_pnl) + (pnlPct !== null ? " · " + fmt.signedPct(pnlPct, 2) : "") });
        const share = eq > 0 && isNum(p.market_value) ? (Number(p.market_value) / eq) * 100 : null;
        const bar = el("span", { className: "bar" }, [el("span")]);
        bar.firstChild.style.width = Math.min(100, Math.max(0, share || 0)) + "%";
        const shareCell = el("div", { className: "weight-cell" }, [el("span", { text: fmt.pct(share) }), bar]);
        let protection;
        if (p.manual_intervention_required) protection = el("span", { className: "badge bad", text: "Elle müdahale gerekli" });
        else if (p.protection_state) protection = el("span", { className: "badge", text: p.protection_state });
        else protection = el("span", { className: "muted", text: "—" });
        if (p.stop_price || p.target_price) protection.title = "Stop " + fmt.price(p.stop_price) + " · Hedef " + fmt.price(p.target_price);
        return el("tr", null, [
          cell(sym), cell(qty, "r"), cell(fmt.price(p.avg_price), "r"), cell(fmt.price(p.current_price), "r"),
          cell(fmt.money(p.market_value), "r"), cell(pnl, "r"), cell(shareCell, "r"), cell(protection)
        ]);
      });
      tbody.replaceChildren.apply(tbody, rows);
    }

    // ---------- performance chart ----------
    const chartBox = byId("perfChart");
    let chart = null;
    function toReturns(series) {
      const pts = (series || []).map(function (p) { return { t: new Date(p.t).getTime(), raw: Number(p.v) }; })
        .filter(function (p) { return Number.isFinite(p.t) && Number.isFinite(p.raw) && p.raw > 0; })
        .sort(function (a, b) { return a.t - b.t; });
      if (!pts.length) return [];
      const base = pts[0].raw;
      return pts.map(function (p) { return { t: p.t, raw: p.raw, v: (p.raw / base - 1) * 100 }; });
    }
    function renderPerformance(data) {
      view.perf = data;
      const main = toReturns(data.equity);
      const bench = toReturns(data.benchmark);
      const last = main.length ? main[main.length - 1].v : null;
      const benchLast = bench.length ? bench[bench.length - 1].v : null;
      setText("periodReturn", fmt.signedPct(last, 2), toneClass("delta", last));
      setText("periodLabel", PERIOD_LABEL[view.period] || "");
      setText("legendPortfolio", fmt.signedPct(last, 2));
      setText("legendBench", fmt.signedPct(benchLast, 2));

      let note = "";
      if (!data.available) note = "";
      else if (data.benchmark_error) note = "VOO karşılaştırma verisi alınamadı.";
      else if (last !== null && benchLast !== null) {
        const diff = last - benchLast;
        note = Math.abs(diff) < 0.05 ? "Bu dönemde portföy VOO ile başa baş."
          : "Bu dönemde portföy VOO'dan " + fmt.pct(Math.abs(diff), 2).replace("%", "") + " puan " + (diff > 0 ? "daha iyi." : "geride.");
      }
      setText("perfNote", note);

      const options = {
        series: [{ cls: "main", area: true, points: main }, { cls: "bench", points: bench }],
        includeZero: true,
        areaBase: 0,
        emptyText: data.available ? "Bu dönem için yeterli hesap geçmişi yok." : (data.reason || "Hesap geçmişi alınamadı."),
        yFormat: function (v) { return (v > 0 ? "+" : "") + v.toLocaleString("tr-TR", { maximumFractionDigits: Math.abs(v) < 10 ? 1 : 0 }) + "%"; },
        xFormat: function (t, span) {
          const d = new Date(t);
          return span < 2 * 86400000
            ? d.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", hour12: false })
            : d.toLocaleDateString("tr-TR", { day: "numeric", month: "short" });
        },
        tip: function (t, hits) {
          const span = main.length > 1 ? main[main.length - 1].t - main[0].t : 0;
          const d = new Date(t);
          const date = span < 2 * 86400000
            ? d.toLocaleString("tr-TR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })
            : d.toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" });
          const nodes = [el("div", { className: "tip-date", text: date })];
          hits.forEach(function (h) {
            const label = h.cls === "main" ? "Portföy" : "VOO";
            const value = h.cls === "main" ? fmt.money(h.point.raw) + " · " + fmt.signedPct(h.point.v, 2) : fmt.signedPct(h.point.v, 2);
            nodes.push(el("div", { className: "tip-row" }, [el("span", null, [el("i", { className: h.cls === "main" ? "" : "bench" }), label]), el("b", { text: value })]));
          });
          return nodes;
        }
      };
      if (!chart) chart = global.PBChart.line(chartBox, options); else chart.update(options);
    }
    let perfSeq = 0;
    async function loadPerformance() {
      const seq = ++perfSeq;
      const period = view.period;
      try {
        const data = await api.fetchJson("/api/performance?period=" + encodeURIComponent(period));
        if (seq === perfSeq) renderPerformance(data);
      } catch (err) {
        if (seq === perfSeq) renderPerformance({ available: false, reason: "Grafik verisi alınamadı: " + err.message, equity: [], benchmark: [] });
      }
    }
    document.querySelectorAll("#periodSwitch button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll("#periodSwitch button").forEach(function (b) { b.classList.toggle("active", b === btn); });
        view.period = btn.dataset.period;
        loadPerformance();
      });
    });

    // ---------- story + allocation ----------
    function storyItem(tone, iconName, strong, rest) {
      return el("li", null, [
        el("span", { className: "story-icon " + tone }, [PB.icon(iconName)]),
        el("span", null, [strong ? el("strong", { text: strong }) : null, rest ? " " + rest : null])
      ]);
    }
    function holdingsSentence(card) {
      const holdings = (card && card.holdings) || [];
      const names = holdings.map(function (h) { return h.symbol; });
      if (!names.length) return null;
      return names.slice(0, 3).join(", ") + (names.length > 3 ? " +" + (names.length - 3) : "");
    }
    function renderStory() {
      const list = byId("storyList");
      if (!list) return;
      const items = [];
      const status = view.status;
      const ov = view.overview || {};
      const state = status ? status.state : null;
      const breaker = status && status.runtime && status.runtime.circuit_breaker_reason;
      if (!state) items.push(storyItem("", "clock", "Durum yükleniyor…"));
      else if (breaker || state === "halted") items.push(storyItem("bad", "lock", "Güvenlik kilidi devrede.", "Bot yeni alım yapmıyor" + (breaker ? " (" + breaker + ")" : "") + "."));
      else if (state === "running") items.push(storyItem("ok", "activity", "Bot çalışıyor", "ve piyasayı izliyor."));
      else if (state === "warming_up" || state === "starting") items.push(storyItem("warn", "clock", "Bot hazırlanıyor.", "Geçmiş verileri yüklüyor; birazdan karar vermeye başlayacak."));
      else if (state === "degraded") items.push(storyItem("warn", "alert", "Bot kısıtlı çalışıyor.", "Bazı veriler gecikiyor; yeni alımlar durabilir."));
      else if (state === "stopped") items.push(storyItem("", "pause", "Bot kapalı.", "İşlem yapmıyor — üstteki “Başlat” ile çalıştırabilirsiniz."));
      else items.push(storyItem("bad", "alert", "Motor sorunlu:", (status.engine_error || state) + "."));

      [["equity", "trend", "Hisse"]].forEach(function (spec) {
        const card = ov[spec[0]];
        if (!card || card.unavailable) return;
        if (card.enabled === false) { items.push(storyItem("", spec[1], spec[2] + " stratejisi kapalı.")); return; }
        const names = holdingsSentence(card);
        const next = card.next_rebalance_at ? " Sıradaki kontrol " + fmt.relative(card.next_rebalance_at) + "." : "";
        if (names) items.push(storyItem("info", spec[1], spec[2] + ": " + names, "tutuluyor — portföyün " + fmt.pct(card.current_allocation_pct) + "'i." + next));
        else items.push(storyItem("", spec[1], spec[2] + ": pozisyon yok,", "bu kısım nakitte bekliyor." + next));
      });

      const risk = ov.risk;
      if (risk && !risk.unavailable && !risk.circuit_breaker && view.account) {
        const dd = ddPct(view.account);
        items.push(storyItem("ok", "shield", "Risk limitleri içinde.", "Zirveden düşüş " + fmt.pct(dd) + " (sınır " + fmt.pct(risk.max_drawdown_pct, 0) + ")."));
      }
      list.replaceChildren.apply(list, items);
    }
    function ddPct(a) {
      const eq = Number(a && a.equity) || 0;
      const dd = Number(a && a.drawdown) || 0;
      return eq + dd > 0 ? (dd / (eq + dd)) * 100 : 0;
    }
    function renderAllocation(risk) {
      if (!risk || risk.unavailable) return;
      const eq = Math.max(0, Number(risk.equity_exposure_pct) || 0);
      const cash = Math.max(0, 100 - eq);
      const C = 2 * Math.PI * 50;
      let offset = 0;
      [["donutEquity", eq], ["donutCash", cash]].forEach(function (pair) {
        const circle = byId(pair[0]);
        if (!circle) return;
        const len = Math.max(0, Math.min(100, pair[1])) / 100 * C;
        circle.setAttribute("stroke-dasharray", len.toFixed(2) + " " + C.toFixed(2));
        circle.setAttribute("stroke-dashoffset", (-offset).toFixed(2));
        offset += len;
      });
      setText("donutInvested", fmt.pct(eq, 0));
      setText("allocEquity", fmt.pct(eq));
      setText("allocCash", fmt.pct(cash));
    }

    // ---------- strategies ----------
    function renderStrategy(prefix, card) {
      if (!card || card.unavailable) { setText(prefix + "StrategyDesc", "Strateji bilgisi alınamadı."); return; }
      const strategy = card.strategy || {};
      setText(prefix + "StrategyName", strategy.display_name || card.name || "—");
      setText(prefix + "StrategyDesc", STRATEGY_DESC[card.name] || strategy.name || card.name || "—");
      setText(prefix + "StatusBadge", STRATEGY_STATUS[card.status] || card.status || "—", PB.badgeClass(card.status === "disabled" ? "" : card.status));
      setText(prefix + "Signal", cadence(strategy.signal_timeframe));
      setText(prefix + "Rebalance", cadence(strategy.rebalance_cadence));
      setText(prefix + "Obs", isNum(card.observation_set_size) ? String(card.observation_set_size) : "—");
      setText(prefix + "Positions", String(card.positions_count || 0));
      setText(prefix + "Allocation", fmt.pct(card.current_allocation_pct) + " / en fazla " + fmt.pct(card.maximum_allocation_pct, 0));
      const bar = byId(prefix + "AllocBar");
      if (bar) {
        const max = Number(card.maximum_allocation_pct) || 0;
        bar.style.width = (max > 0 ? Math.min(100, (Number(card.current_allocation_pct) || 0) / max * 100) : 0) + "%";
      }
      const next = byId(prefix + "NextRebalance");
      if (next) { next.textContent = card.next_rebalance_at ? fmt.relative(card.next_rebalance_at) : "—"; next.title = fmt.dateTime(card.next_rebalance_at); }
      const cu = card.startup_catch_up || {};
      const status = cu.status || "not_attempted";
      const cuEl = byId(prefix + "StartupCatchUp");
      if (cuEl) {
        // "blocked:<reason>" and "recovered_session:<status>" carry a nested status.
        const label = status.indexOf("blocked:") === 0 ? "Engelli: " + status.slice(8)
          : status.indexOf("recovered_session:") === 0
            ? "Eksik oturum kararı: " + (CATCH_UP[status.slice(19)] || status.slice(19))
            : (CATCH_UP[status] || status);
        cuEl.textContent = label;
        cuEl.title = cu.at ? "Son deneme: " + fmt.dateTime(cu.at) : cu.next_boundary_after ? "Sıradaki sınır: " + fmt.dateTime(cu.next_boundary_after) : status;
      }
      const tbody = byId(prefix + "HoldingsBody");
      const holdings = card.holdings || [];
      if (!holdings.length) { PB.emptyRow(tbody, 5, "Pozisyon yok"); return; }
      tbody.replaceChildren.apply(tbody, holdings.map(function (h) {
        const sym = el("span", { className: "sym" }, [el("span", { className: "sym-logo", text: symbolBase(h.symbol) }), h.symbol]);
        return el("tr", null, [
          cell(sym), cell(fmt.num(h.qty, 4), "r"), cell(fmt.pct(h.target_weight_pct), "r"),
          cell(fmt.pct(h.current_weight_pct), "r"), cell(h.reason || h.hypothesis || "—", "wrap")
        ]);
      }));
    }

    // ---------- risk ----------
    function meter(label, value, limit, opts) {
      opts = opts || {};
      const wrap = el("div");
      const valueText = fmt.pct(value, opts.digits === undefined ? 1 : opts.digits);
      wrap.appendChild(el("div", { className: "meter-top" }, [
        el("span", { text: label }),
        el("b", null, [valueText, el("small", { text: isNum(limit) ? (opts.minimum ? "  · en az " : "  · sınır ") + fmt.pct(limit, 0) : "" })])
      ]));
      const bar = el("div", { className: "meter" });
      const fill = el("span");
      const scale = opts.minimum ? 100 : Math.max(Number(limit) * 1.25 || 100, Number(value) || 0, 1);
      fill.style.width = Math.max(0, Math.min(100, (Number(value) || 0) / scale * 100)) + "%";
      let tone = "";
      if (isNum(limit) && Number(limit) > 0) {
        const ratio = (Number(value) || 0) / Number(limit);
        if (opts.minimum) tone = ratio >= 1 ? "" : ratio >= 0.7 ? "warn" : "bad";
        else tone = ratio >= 1 ? "bad" : ratio >= 0.7 ? "warn" : "";
        const mark = el("i");
        mark.style.left = Math.min(100, Number(limit) / scale * 100) + "%";
        bar.appendChild(mark);
      }
      fill.className = tone;
      bar.insertBefore(fill, bar.firstChild);
      wrap.appendChild(bar);
      if (opts.note) wrap.appendChild(el("div", { className: "meter-note", text: opts.note }));
      return wrap;
    }
    function renderRisk(risk) {
      if (!risk || risk.unavailable) return;
      const a = view.account || {};
      const eq = Number(a.equity) || 0;
      const daily = Number(a.daily_pnl !== undefined ? a.daily_pnl : risk.daily_pnl) || 0;
      const lossPct = eq - daily > 0 ? Math.max(0, -daily) / (eq - daily) * 100 : 0;
      const box = byId("riskMeters");
      if (box) {
        box.replaceChildren(
          meter("Zirveden düşüş", ddPct(a), risk.max_drawdown_pct, { note: "Bu sınır aşılırsa bot tüm yeni alımları durdurur." }),
          meter("Bugünkü kayıp", lossPct, risk.daily_loss_limit_pct, { digits: 2, note: "Günlük kayıp sınırı aşılırsa o gün yeni alım yapılmaz." }),
          meter("Yatırımdaki pay (brüt)", risk.gross_exposure_pct, risk.gross_exposure_limit_pct),
          meter("Nakit tamponu", risk.cash_pct, risk.cash_buffer_limit_pct, { minimum: true })
        );
      }
      setText("riskCircuitBadge", risk.circuit_breaker ? "Kesici: " + risk.circuit_breaker : "Limitler içinde", risk.circuit_breaker ? "badge bad" : "badge ok");
      const latch = Object.entries(risk.entry_risk_latch || {}).filter(function (e) { return e[1]; }).map(function (e) { return e[0] + ": " + e[1]; }).join(", ");
      setText("riskEntryLatch", latch || "Yok — alım serbest", latch ? "neg" : "pos");
      const epoch = risk.risk_epoch;
      setText("riskEpoch", epoch ? fmt.date(epoch.session_date) + " · taban " + fmt.money(epoch.day_start_equity) : "—");
      const cap = risk.buy_capacity || {};
      const ready = !cap.unavailable;
      const reasons = Object.entries(cap.suppression_reasons || {}).concat(Object.entries(cap.halt_reasons || {})).map(function (e) { return e[0] + ": " + e[1]; }).join(", ");
      setText("riskAdmission", ready ? ({ complete: "Tamam", pending: "Bekleniyor", failed: "Başarısız" }[cap.reconciliation_status] || cap.reconciliation_status || "—") + (reasons ? " · " + reasons : "") : "—");
      setText("riskSlots", ready ? cap.positions_count + " / " + cap.effective_slots + " / " + cap.maximum_positions : "—");
      setText("riskWorking", ready ? String(cap.broker_working_order_count) : "—");
      setText("riskGrossHeadroom", ready ? fmt.money(cap.available_gross_buy_headroom) : "—");
      setText("riskCommittedBuy", ready && cap.committed_buy_notional !== null ? fmt.money(cap.committed_buy_notional) : "bilinmiyor");
      setText("riskCashHeadroom", ready ? fmt.money(cap.available_cash_buy_headroom) : "—");
      const incidents = risk.safety_incidents || [];
      const box2 = byId("riskIncidents");
      if (box2) {
        if (!incidents.length) box2.replaceChildren(el("div", { className: "incident ok" }, [PB.icon("check"), el("span", { text: "Aktif güvenlik olayı yok" })]));
        else box2.replaceChildren.apply(box2, incidents.map(function (i) {
          return el("div", { className: "incident" }, [PB.icon("alert"), el("span", { text: i.reason + " · " + fmt.dateTime(i.activated_at) })]);
        }));
      }
    }

    // ---------- trades ----------
    function renderExecution(ex) {
      if (!ex || ex.unavailable) return;
      const fills = ex.recent_fills || [];
      const orders = ex.open_orders || [];
      setText("fillsCount", String(fills.length));
      setText("ordersCount", String(orders.length));
      const fBody = byId("recentFillsBody");
      if (!fills.length) PB.emptyRow(fBody, 7, "Henüz işlem yok");
      else fBody.replaceChildren.apply(fBody, fills.map(function (f) {
        const slip = f.slippage_bps;
        return el("tr", null, [
          cell(fmt.dateTime(f.occurred_at)), cell(el("span", { className: "sym", text: f.symbol })), cell(sideBadge(f.side)),
          cell(fmt.num(f.quantity, 6), "r"), cell(fmt.price(f.price), "r"),
          cell(isNum(slip) ? fmt.num(slip, 1) + " bps" : "—", "r"), cell(fmt.price(f.fee), "r")
        ]);
      }));
      const oBody = byId("openOrdersBody");
      if (!orders.length) PB.emptyRow(oBody, 6, "Bekleyen emir yok");
      else oBody.replaceChildren.apply(oBody, orders.map(function (o) {
        const lin = o.lineage || {};
        return el("tr", null, [
          cell(el("span", { className: "sym", text: o.symbol })), cell(sideBadge(o.side)), cell(fmt.num(o.quantity, 6), "r"),
          cell(fmt.price(o.reference_price), "r"), cell(el("span", { className: "badge", text: o.status || "—" })),
          cell(lin.strategy ? lin.strategy + " · " + fmt.date(lin.decision_timestamp) : "—")
        ]);
      }));
    }
    document.querySelectorAll(".tab[data-tab]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        document.querySelectorAll(".tab[data-tab]").forEach(function (t) { t.classList.toggle("active", t === tab); });
        document.querySelectorAll("[data-panel]").forEach(function (panel) { panel.hidden = panel.dataset.panel !== tab.dataset.tab; });
      });
    });

    // ---------- system ----------
    function renderHealth(h) {
      if (!h || h.unavailable) return;
      setText("healthBadge", h.healthy === true ? "Sağlıklı" : h.healthy === false ? "Sorunlu" : (h.engine_state || "—"), h.healthy === true ? "badge ok" : h.healthy === false ? "badge bad" : "badge");
      const markets = h.markets || {};
      const dataOk = (markets.equity || {}).stream_healthy !== false;
      setText("healthData", dataOk ? "Güncel" : "Gecikme var", dataOk ? "pos" : "neg");
      const fresh = h.account_freshness || {};
      setText("healthAccount", (fresh.source || "—") + (isNum(fresh.age_seconds) ? " · " + fmt.duration(fresh.age_seconds) : ""));
      setText("healthHeartbeat", h.last_heartbeat ? fmt.relative(h.last_heartbeat) : "—");
      setText("healthRestarts", String(h.restart_count || 0));
      const db = h.database || {};
      setText("healthDatabase", (db.exists ? "Açık" : "Yok") + (db.size_bytes ? " · " + fmt.num(db.size_bytes / 1048576, 1) + " MB" : ""));
      const rec = h.reconciliation || {};
      setText("healthReconciliation", rec.failed ? "Hata" : rec.last_at ? fmt.relative(rec.last_at) : "—", rec.failed ? "neg" : "");
      const workers = h.workers || {};
      const names = Object.keys(workers).sort();
      const tbody = byId("workersBody");
      if (!names.length) PB.emptyRow(tbody, 5, "Çalışan bilgisi yok");
      else tbody.replaceChildren.apply(tbody, names.map(function (n) {
        const w = workers[n] || {};
        return el("tr", null, [
          cell(el("span", { className: "mono", text: n })),
          cell(el("span", { className: w.alive ? "badge ok" : "badge bad", text: w.alive ? "Canlı" : "Durdu" })),
          cell(w.last_heartbeat ? fmt.relative(w.last_heartbeat) : "—"), cell(String(w.restart_count || 0), "r"),
          cell(w.last_error || "—", "wrap")
        ]);
      }));
    }
    function chips(boxId, symbols) {
      const box = byId(boxId);
      if (!box) return;
      const list = (symbols || []).slice(0, 80).map(function (s) { return el("span", { className: "chip", text: s }); });
      if ((symbols || []).length > 80) list.push(el("span", { className: "chip", text: "+" + (symbols.length - 80) }));
      box.replaceChildren.apply(box, list);
    }
    function renderUniverse(u) {
      if (!u || u.unavailable) return;
      if (u.equity) {
        const status = u.equity.scanner_status === "static" ? "Sabit liste" : (u.equity.scanner_status || "—");
        setText("eqStatus", status + " · " + fmt.num(u.equity.observation_set_size, 0) + " sembol");
        chips("eqSymbols", u.equity.observation_set);
      }
    }
    function renderOps(data) {
      setText("opsOverall", data.overall_health || "UNKNOWN", PB.badgeClass(data.overall_health));
      setText("opsTradingState", data.trading_state || "UNKNOWN", PB.badgeClass(data.trading_state));
      setText("opsSnapshotAge", "Anlık görüntü yaşı: " + fmt.duration(data.snapshot_age_seconds) + " · " + fmt.dateTime(data.as_of));
      const box = byId("opsChecks");
      const checks = data.checks || [];
      if (!checks.length) box.replaceChildren(el("p", { className: "empty-note", text: "Kontrol yok" }));
      else box.replaceChildren.apply(box, checks.map(function (c) {
        const status = String(c.status || "").toLowerCase();
        const body = el("div", null, [
          el("div", { className: "check-title", text: c.code }),
          el("div", { className: "check-summary", text: c.summary || "—" }),
          c.action ? el("div", { className: "check-action", text: "→ " + c.action }) : null
        ]);
        if (c.observed !== undefined && c.observed !== null && status !== "ok") {
          let text;
          try { text = typeof c.observed === "string" ? c.observed : JSON.stringify(c.observed, null, 2); } catch (_) { text = String(c.observed); }
          body.appendChild(el("pre", { text: text }));
        }
        return el("div", { className: "check" }, [el("span", { className: "check-dot " + status }), body, el("span", { className: PB.badgeClass(status), text: c.status })]);
      }));
      const events = data.recent_events || data.events || [];
      const evBox = byId("opsRecentEvents");
      if (!events.length) evBox.replaceChildren(el("li", { className: "empty-note", text: "Son olay yok" }));
      else evBox.replaceChildren.apply(evBox, events.slice(0, 12).map(function (ev) {
        return el("li", null, [el("time", { text: fmt.dateTime(ev.at || ev.time || ev.timestamp) }), el("span", { text: (ev.type || ev.code || "olay") + (ev.message ? " — " + ev.message : "") })]);
      }));
      const exp = data.decision_explanation || data.explanations;
      let text = "Karar açıklaması yok";
      if (typeof exp === "string") text = exp;
      else if (exp) { try { text = JSON.stringify(exp, null, 2); } catch (_) { text = String(exp); } }
      setText("opsDecisionExplanation", text);
    }

    // ---------- data loading ----------
    function renderOverview(data) {
      if (!data || data.unavailable) return;
      view.overview = data;
      if (data.account) renderAccount(data.account);
      renderStrategy("equity", data.equity);
      renderAllocation(data.risk);
      renderRisk(data.risk);
      renderExecution(data.execution);
      renderHealth(data.health);
      renderUniverse(data.universe);
      renderStory();
    }
    let overviewBusy = false;
    async function refreshOverview() {
      if (overviewBusy) return;
      overviewBusy = true;
      try { renderOverview(await api.fetchJson("/api/dashboard/overview")); }
      catch (e) { console.error("overview", e); }
      finally { overviewBusy = false; }
    }
    let opsBusy = false;
    async function refreshOps() {
      if (opsBusy) return;
      opsBusy = true;
      try { renderOps(await api.fetchJson("/api/ops/status")); }
      catch (e) { const box = byId("opsChecks"); if (box) box.replaceChildren(el("p", { className: "empty-note", text: "Operasyon durumu alınamadı: " + e.message })); }
      finally { opsBusy = false; }
    }

    PB.onStatus(function (status) { view.status = status; renderStory(); });
    PB.onStream(function (payload) { if (payload.account && payload.account.source !== "unavailable") renderAccount(payload.account); });
    api.fetchJson("/api/account").then(renderAccount).catch(function () {});
    refreshOverview();
    refreshOps();
    loadPerformance();
    global.setInterval(refreshOverview, 5000);
    global.setInterval(refreshOps, 10000);
    global.setInterval(loadPerformance, 60000);
  });
})(window);
