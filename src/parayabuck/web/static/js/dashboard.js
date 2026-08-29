// ParayaBuck dashboard.js — polls /api/dashboard/overview + status/account + chart.
// Depends on PBApi, PBWebSocket, PBControls. Token never stored in localStorage/sessionStorage.
(function (global) {
  "use strict";
  document.addEventListener("DOMContentLoaded", function(){
    if (!document.getElementById("equityValue")) return;
    const api = global.PBApi;
    // formatting
    const fmtPct = function(v){ return (v===undefined||v===null||!Number.isFinite(Number(v))) ? "—" : Number(v).toFixed(1)+"%"; };
    const fmtNum = function(v, d){ d=d===undefined?2:d; return (v===undefined||v===null||!Number.isFinite(Number(v))) ? "—" : Number(v).toLocaleString("en-US",{maximumFractionDigits:d}); };
    const fmtVol = function(v){ return (v===undefined||v===null||!Number.isFinite(Number(v))) ? "—" : (Number(v)*100).toFixed(2)+"%"; };
    const fmtDateTime = function(iso){ if(!iso) return "—"; const d=new Date(iso); return Number.isNaN(d.getTime())? iso : d.toLocaleString("tr-TR",{hour12:false}); };
    const fmtDate = function(iso){ if(!iso) return "—"; const d=new Date(iso); return Number.isNaN(d.getTime())? iso : d.toLocaleDateString("tr-TR"); };
    const formatCurrency = function(v){ if(v===undefined||v===null) return "$0.00"; return (v>=0?"$":"-$")+Math.abs(v).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2}); };
    const formatPnl = function(v){ if(v===undefined||v===null) return "$0.00"; const s=v>0?"+$":(v<0?"-$":"$"); return s+Math.abs(v).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2}); };
    const setText = function(id, val){ const el=document.getElementById(id); if(el) el.textContent=val; };
    const STRATEGY_STATUS_LABELS = { active:"Aktif", warming:"Is\u0131n\u0131yor", halted:"Durdu (g\u00fcvenlik)", scanner_issue:"Taray\u0131c\u0131 sorunu", disabled:"Devre d\u0131\u015f\u0131", stopped:"Motor durdu", starting:"Ba\u015flat\u0131l\u0131yor", stopping:"Durduruluyor", error:"Hata", stop_failed:"Durdurma hatas\u0131", unknown:"Bilinmiyor", unavailable:"Yok" };
    const CATCH_UP_LABELS = { submitted:"Uyguland\u0131", sell_phase_submitted:"Uyguland\u0131 (SELL faz\u0131)", catch_up_superseded:"Gerek yok: karar mevcut", duplicate_decision:"Gerek yok: kopya \u00f6nlendi", already_invested:"Gerek yok: pozisyon var", warming_up:"Bekliyor: \u0131s\u0131nma", incomplete_strategy_batch:"Bekliyor: eksik parti", rebalance_in_progress:"Bekliyor: rebalance s\u00fcr\u00fcyor", rebalance_blocked:"Bekliyor: rebalance kilitli", no_eligible_bars:"Bekliyor: bar yok", not_scheduled:"Bekliyor: s\u0131radaki periyot", not_attempted:"Haz\u0131r de\u011fil" };
    function formatCatchUp(catchUp){
      const status=(catchUp && catchUp.status) || "not_attempted";
      if (status.indexOf("blocked:")===0) return {label:"Engelli: "+status.slice("blocked:".length), tooltip: status};
      const label=CATCH_UP_LABELS[status]||status||"—";
      const tooltip=catchUp && catchUp.at ? "Son deneme: "+fmtDateTime(catchUp.at) : (catchUp && catchUp.next_boundary_after ? "S\u0131radaki s\u0131n\u0131r: "+fmtDateTime(catchUp.next_boundary_after) : status||"");
      return {label:label, tooltip:tooltip};
    }
    // Engine state UI (topbar)
    const modeChip = document.getElementById("modeChip");
    const modeText = document.getElementById("modeText");
    const statusDot = document.getElementById("statusDot");
    const statusText = document.getElementById("statusText");
    const marketStatusText = document.getElementById("marketStatusText");
    const resetSafetyBtn = document.getElementById("resetSafetyBtn");
    const riskEpochBtn = document.getElementById("riskEpochBtn");
    const startBtn = document.getElementById("startBtn");
    const stopBtn = document.getElementById("stopBtn");
    let isPaperMode = true;
    let accountSuffix = "";
    function updateEngineStateUI(state, paper){
      isPaperMode = paper;
      if (global.PBControls && global.PBControls.setPaper) global.PBControls.setPaper(paper);
      if (modeChip){
        modeChip.className = "mode-chip " + (paper ? "paper" : "real");
        if (modeText) modeText.textContent = (paper ? "Paper demo" : "Ger\u00e7ek para") + (accountSuffix ? " \u00b7 \u2026"+accountSuffix : "");
      }
      if (statusDot) statusDot.className = "status-dot " + state;
      const labels = { running:"\u00c7al\u0131\u015f\u0131yor (Canl\u0131)", stopped:"Durdu", starting:"Ba\u015flat\u0131l\u0131yor...", warming_up:"Veriler haz\u0131rlan\u0131yor...", degraded:"K\u0131s\u0131tl\u0131 \u00e7al\u0131\u015f\u0131yor", halted:"G\u00fcvenlik nedeniyle durdu", stopping:"Durduruluyor...", stop_failed:"Durdurma hatas\u0131", error:"Hata" };
      if (statusText) statusText.textContent = labels[state] || state;
      if (resetSafetyBtn) resetSafetyBtn.hidden = state !== "halted" && state !== "degraded";
      if (riskEpochBtn) riskEpochBtn.hidden = ["running","warming_up","degraded","halted"].indexOf(state)===-1;
      if (["running","warming_up","degraded","halted"].indexOf(state)!==-1){ if(startBtn) startBtn.disabled=true; if(stopBtn) stopBtn.disabled=false; }
      else if (state==="stopped"||state==="error"){ if(startBtn) startBtn.disabled=false; if(stopBtn) stopBtn.disabled=true; }
      else if (state==="starting"||state==="stopping"){ if(startBtn) startBtn.disabled=true; if(stopBtn) stopBtn.disabled = state==="stopping"; }
      else if (state==="stop_failed"){ if(startBtn) startBtn.disabled=true; if(stopBtn) stopBtn.disabled=true; }
    }
    function renderSystemStatus(statusData){
      updateEngineStateUI(statusData.state, statusData.paper);
      const hasRuntime = !!statusData.runtime;
      const markets = (statusData.runtime && statusData.runtime.markets) || {};
      const equity = markets.equity || {};
      const crypto = markets.crypto || {};
      const marketLabel = function(market){
        if (!hasRuntime){ if(statusData.state==="starting") return "ba\u015flat\u0131l\u0131yor"; if(statusData.state==="error") return "hata"; if(statusData.state==="stopped") return "kapal\u0131"; return "durum bekleniyor"; }
        if (market.entry_halt_reason) return "halt: "+market.entry_halt_reason;
        if (market.stream_healthy===false) return "stream hatas\u0131";
        if (market.ready===false) return "haz\u0131r de\u011fil";
        if (Number.isFinite(market.ready_symbol_count) && Number.isFinite(market.symbol_count) && market.ready_symbol_count < market.symbol_count) return market.ready_symbol_count+"/"+market.symbol_count+" haz\u0131r";
        return "haz\u0131r";
      };
      const globalHealth = hasRuntime ? statusData.runtime.state + (statusData.runtime.healthy ? "" : " (degraded)") : statusData.state;
      const strategyStatus = statusData.strategy_status || "";
      const strategyLabel = strategyStatus.indexOf("configured:")===0 ? "STRATEJ\u0130 "+strategyStatus.slice("configured:".length) : strategyStatus==="configuration_error" ? "STRATEJ\u0130 AYAR HATASI" : strategyStatus || "STRATEJ\u0130 DEVRE DI\u015eI";
      const scanner = statusData.scanner || {};
      const equityScanner = scanner.equity_status || scanner.status || "not_run";
      const cryptoScanner = scanner.crypto_status || "not_run";
      const strategyMeta = statusData.strategy_metadata || {};
      const fmtStrategy = function(meta){
        if(!meta) return null;
        if(meta.configuration_error) return meta.name+" (ayar hatas\u0131)";
        const extra=[ "Sinyal "+meta.signal_timeframe, "Rebalance "+meta.rebalance_cadence, meta.dynamic_universe_enabled?"Dinamik evren":"Statik evren", "Aday "+(meta.candidate_pool_size||0), "\u0130zlenen "+(meta.observation_set_size||0)];
        return (meta.display_name||meta.name)+" \u00b7 "+extra.join(" \u00b7 ");
      };
      const strategyMetaText=[ strategyMeta.equity ? "E: "+fmtStrategy(strategyMeta.equity) : null, strategyMeta.crypto ? "C: "+fmtStrategy(strategyMeta.crypto) : null ].filter(Boolean).join("  |  ");
      if (marketStatusText) marketStatusText.textContent = "Global "+globalHealth+" | "+strategyLabel+" | Equity "+(statusData.active_equity_symbol_count||0)+" \u00b7 "+marketLabel(equity)+" | Crypto "+(statusData.active_crypto_symbol_count||0)+" \u00b7 "+marketLabel(crypto)+" | Aday "+(statusData.candidate_universe_size||0)+" \u00b7 Scanner E "+equityScanner+" \u00b7 C "+cryptoScanner+(strategyMetaText ? "\n"+strategyMetaText : "")+(statusData.engine_error ? " | Hata: "+statusData.engine_error : "");
    }
    let statusInFlight=false;
    async function refreshSystemStatus(){
      if(statusInFlight) return; statusInFlight=true;
      try { const r=await fetch("/api/status",{cache:"no-store"}); if(!r.ok) throw new Error("Durum al\u0131namad\u0131 ("+r.status+")"); renderSystemStatus(await r.json()); } catch(e){ console.error("Status refresh error:",e); } finally { statusInFlight=false; }
    }
    global.PBStatus = { refresh: refreshSystemStatus, render: renderSystemStatus };

    // Account + chart
    const equityValue = document.getElementById("equityValue");
    const cashValue = document.getElementById("cashValue");
    const dailyPnlValue = document.getElementById("dailyPnlValue");
    const realizedPnlValue = document.getElementById("realizedPnlValue");
    const feesValue = document.getElementById("feesValue");
    const unrealizedPnlValue = document.getElementById("unrealizedPnlValue");
    const drawdownValue = document.getElementById("drawdownValue");
    const activePositionsBadge = document.getElementById("activePositionsBadge");
    const positionsTableBody = document.getElementById("positionsTableBody");
    let equityHistoryScope = "paper:unknown";
    const equityHistoryKey = function(scope){ return "parayabuck-equity-history-v2:"+scope; };
    const loadEquityHistory = function(scope){
      try {
        const stored = JSON.parse(sessionStorage.getItem(equityHistoryKey(scope)) || "{}");
        const values = Array.isArray(stored.values) ? stored.values.map(Number) : [];
        const times = Array.isArray(stored.times) ? stored.times.map(String) : [];
        const snaps = Array.isArray(stored.snapshots) ? stored.snapshots.map(String) : [];
        if (values.length===times.length && values.length===snaps.length && values.every(Number.isFinite)) return {values: values.slice(-30), times: times.slice(-30), snapshots: snaps.slice(-30)};
      } catch(_){}
      return {values:[], times:[], snapshots:[]};
    };
    const storedHist = loadEquityHistory(equityHistoryScope);
    let equityHistory = storedHist.values;
    let timeHistory = storedHist.times;
    let snapshotHistory = storedHist.snapshots;
    let equityChart = null;
    try {
      const ctx = document.getElementById("equityChart");
      if (ctx && window.Chart) {
        equityChart = new Chart(ctx.getContext("2d"), {
          type:"line",
          data:{labels: timeHistory, datasets:[{label:"Net Portf\u00f6y (Equity)", data: equityHistory, borderColor:"#6366F1", backgroundColor:"rgba(99,102,241,0.1)", borderWidth:2, fill:true, tension:0.4, pointRadius:3, pointBackgroundColor:"#38BDF8"}]},
          options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{grid:{color:"rgba(255,255,255,0.04)"}, ticks:{color:"#94A3B8", font:{size:10}}}, y:{grid:{color:"rgba(255,255,255,0.04)"}, ticks:{color:"#94A3B8", font:{size:10}, callback:function(v){return "$"+v.toLocaleString();}}}}}
        });
      }
    } catch(_){}
    function updateAccountUI(data){
      if(!data) return;
      accountSuffix = data.account_suffix || accountSuffix;
      const nextScope = (isPaperMode ? "paper" : "live") + ":" + (accountSuffix || "unknown");
      if (nextScope !== equityHistoryScope){
        equityHistoryScope = nextScope;
        const stored = loadEquityHistory(equityHistoryScope);
        equityHistory = stored.values; timeHistory = stored.times; snapshotHistory = stored.snapshots;
        if (equityChart){ equityChart.data.labels=timeHistory; equityChart.data.datasets[0].data=equityHistory; equityChart.update(); }
      }
      if(equityValue) equityValue.textContent=formatCurrency(data.equity);
      if(cashValue) cashValue.textContent=formatCurrency(data.cash);
      if(dailyPnlValue){ dailyPnlValue.textContent=formatPnl(data.daily_pnl); dailyPnlValue.className="metric-value "+(data.daily_pnl>0?"positive":(data.daily_pnl<0?"negative":"")); }
      if(realizedPnlValue){ realizedPnlValue.textContent=formatPnl(data.realized_pnl); realizedPnlValue.className="metric-value "+(data.realized_pnl>0?"positive":(data.realized_pnl<0?"negative":"")); }
      if(unrealizedPnlValue){ unrealizedPnlValue.textContent=formatPnl(data.unrealized_pnl); if(feesValue) feesValue.textContent=formatPnl(-(data.fees||0)); unrealizedPnlValue.className="metric-value "+(data.unrealized_pnl>0?"positive":(data.unrealized_pnl<0?"negative":"")); }
      if(drawdownValue) drawdownValue.textContent=formatCurrency(data.drawdown);
      const positions=data.positions||[];
      if(activePositionsBadge) activePositionsBadge.textContent=positions.length+" a\u00e7\u0131k";
      if(positionsTableBody){
        if(!positions.length){ positionsTableBody.innerHTML='<tr><td colspan="9" class="empty-state"><span class="empty-icon">—</span>Hen\u00fcz a\u00e7\u0131k pozisyon bulunmuyor</td></tr>'; }
        else {
          const rows=positions.map(function(p){
            const row=document.createElement("tr");
            const ownershipTag=p.ownership_state==="managed" ? " (M)" : p.ownership_state==="mixed" ? " (M+E)" : " (external)";
            const qtyLabel= p.managed_qty!=null && p.external_qty!=null ? p.total_qty+" = "+p.managed_qty+"M + "+p.external_qty+"E" : String(p.qty);
            const vals=[ p.symbol+ownershipTag, p.market||"-", p.direction||"-", qtyLabel, "$"+Number(p.avg_price).toFixed(2), formatPnl(p.unrealized_pnl), p.manual_intervention_required ? "MANUAL" : (p.protection_state||"-"), (p.stop_price ? "$"+Number(p.stop_price).toFixed(4) : "-")+" / "+(p.target_price ? "$"+Number(p.target_price).toFixed(4):"-"), (p.stop_order_state||"-")+" / "+(p.target_exit_state||"-") ];
            vals.forEach(function(v, idx){
              const c=document.createElement("td"); c.textContent=String(v);
              if(idx===0) c.className="position-symbol";
              if(idx===2) c.className= p.direction==="LONG" ? "badge-long" : "badge-short";
              if(idx===5) c.className= p.unrealized_pnl>0 ? "positive" : (p.unrealized_pnl<0 ? "negative" : "");
              if(idx===6 && p.manual_intervention_required) c.className="negative";
              row.appendChild(c);
            });
            return row;
          });
          positionsTableBody.replaceChildren(...rows);
        }
      }
      if(data.equity>0 && equityChart){
        const snapId=data.snapshot_at || new Date().toISOString();
        if (snapshotHistory[snapshotHistory.length-1]===snapId) return;
        const snapDate=new Date(snapId);
        const nowTime=Number.isNaN(snapDate.getTime()) ? new Date().toLocaleTimeString() : snapDate.toLocaleTimeString();
        timeHistory.push(nowTime); equityHistory.push(data.equity); snapshotHistory.push(snapId);
        if(timeHistory.length>30){ timeHistory.shift(); equityHistory.shift(); snapshotHistory.shift(); }
        try { sessionStorage.setItem(equityHistoryKey(equityHistoryScope), JSON.stringify({times:timeHistory, values:equityHistory, snapshots:snapshotHistory})); } catch(_){}
        equityChart.update();
      }
    }
    global.PBAccount = { update: updateAccountUI };

    // Dashboard overview panels
    function renderModeBadge(mode){
      if(!mode) return; const b=document.getElementById("modeBadge"); if(!b) return;
      b.className="mode-badge "+(mode.badge||"paper");
      if(mode.badge==="paper") b.textContent="PAPER"; else if(mode.badge==="live") b.textContent="LIVE"; else b.textContent="LIVE (devre d\u0131\u015f\u0131)";
      b.title="runtime.mode="+(mode.runtime_mode||"paper")+" \u00b7 live.enabled="+(mode.live_enabled===true);
    }
    function renderHoldings(holdings, bodyId, isCrypto){
      const tbody=document.getElementById(bodyId); if(!tbody) return; tbody.replaceChildren();
      if(!holdings.length){ const r=document.createElement("tr"); const c=document.createElement("td"); c.colSpan=isCrypto?7:5; c.className="empty-state"; const ic=document.createElement("span"); ic.className="empty-icon"; ic.textContent="—"; c.append(ic, document.createTextNode("Pozisyon yok")); r.appendChild(c); tbody.appendChild(r); return; }
      holdings.forEach(function(h){
        const row=document.createElement("tr");
        const reason=h.reason||h.hypothesis||"—";
        const vals=isCrypto ? [h.symbol, fmtNum(h.qty,4), fmtVol(h.realized_vol_22d), h.rank ? "#"+h.rank+"/"+(h.of_qualified||"?") : "—", fmtPct(h.target_weight_pct), fmtPct(h.current_weight_pct), reason] : [h.symbol, fmtNum(h.qty,4), fmtPct(h.target_weight_pct), fmtPct(h.current_weight_pct), reason];
        vals.forEach(function(v){ const td=document.createElement("td"); td.textContent=String(v); row.appendChild(td); }); tbody.appendChild(row);
      });
    }
    function renderStrategyCard(card, ids, isCrypto){
      if(!card||card.unavailable){ setText(ids.name,"Panel yok"); return; }
      setText(ids.name, (card.strategy && card.strategy.display_name) || card.name || "—");
      const badge=document.getElementById(ids.badge);
      if(badge){ badge.className="strategy-chip "+(card.status||"unknown"); badge.textContent=STRATEGY_STATUS_LABELS[card.status]||card.status||"—"; }
      setText(ids.signal, (card.strategy && card.strategy.signal_timeframe)||"—");
      setText(ids.rebalance, (card.strategy && card.strategy.rebalance_cadence)||"—");
      if(ids.lookback) setText(ids.lookback,"22 g\u00fcn");
      setText(ids.pool, card.eligible_pool===undefined||card.eligible_pool===null?"—":String(card.eligible_pool));
      setText(ids.obs, card.observation_set_size===undefined||card.observation_set_size===null?"—":String(card.observation_set_size));
      setText(ids.positions, String(card.positions_count||0));
      setText(ids.allocation, fmtPct(card.current_allocation_pct)+" / "+fmtPct(card.maximum_allocation_pct));
      setText(ids.nextRebalance, fmtDateTime(card.next_rebalance_at));
      const cu=formatCatchUp(card.startup_catch_up); setText(ids.catchUp, cu.label); const el=document.getElementById(ids.catchUp); if(el) el.title=cu.tooltip;
      renderHoldings(card.holdings||[], ids.body, isCrypto);
    }
    function renderRisk(risk){
      if(!risk||risk.unavailable) return;
      setText("riskGross", fmtPct(risk.gross_exposure_pct)+" / "+fmtPct(risk.gross_exposure_limit_pct));
      setText("riskCash", fmtPct(risk.cash_pct)+" / "+fmtPct(risk.cash_buffer_limit_pct));
      setText("riskEquity", fmtPct(risk.equity_exposure_pct));
      setText("riskCrypto", fmtPct(risk.crypto_exposure_pct)+" / "+fmtPct(risk.crypto_exposure_limit_pct));
      setText("riskDrawdown", formatCurrency(risk.drawdown)+" / "+fmtPct(risk.max_drawdown_pct));
      setText("riskDailyLoss", formatPnl(risk.daily_pnl)+" / "+fmtPct(risk.daily_loss_limit_pct));
      const latch=risk.entry_risk_latch||{}; const reasons=Object.entries(latch).filter(function(e){return e[1];}).map(function(e){return e[0]+": "+e[1];}).join(", "); setText("riskEntryLatch", reasons||"a\u00e7\u0131k");
      const epoch=risk.risk_epoch; setText("riskEpoch", epoch ? fmtDate(epoch.session_date)+" \u00b7 taban $"+fmtNum(epoch.day_start_equity) : "—");
      const badge=document.getElementById("riskCircuitBadge"); if(badge){ badge.textContent=risk.circuit_breaker ? ("KES\u0130C\u0130: "+risk.circuit_breaker) : "risk haz\u0131r"; badge.className="count-badge"+(risk.circuit_breaker?" risk-critical":""); }
      const box=document.getElementById("riskIncidents"); if(!box) return; box.replaceChildren(); const incidents=risk.safety_incidents||[]; if(!incidents.length){ const ok=document.createElement("div"); ok.className="risk-incident ok"; ok.textContent="Aktif g\u00fcvenlik olay\u0131 yok"; box.appendChild(ok); return; } incidents.forEach(function(i){ const d=document.createElement("div"); d.className="risk-incident"; d.textContent=i.reason+" ("+fmtDateTime(i.activated_at)+")"; box.appendChild(d); });
    }
    function renderHealth(health){
      if(!health||health.unavailable) return;
      const badge=document.getElementById("healthBadge");
      if(badge){ badge.textContent=health.healthy===true ? "sa\u011fl\u0131kl\u0131" : (health.healthy===false ? "degrade" : (health.engine_state||"—")); badge.className="count-badge"+(health.healthy===false?" risk-critical":""); }
      const queue=health.queue||{}; setText("healthQueue", "boyut "+(queue.size||0)+" \u00b7 ya\u015f "+(queue.age_seconds===undefined||queue.age_seconds===null?"—":Math.round(queue.age_seconds)+"s"));
      const markets=health.markets||{}; const eq=markets.equity||{}; const cr=markets.crypto||{}; const dataOk=(eq.stream_healthy!==false && cr.stream_healthy!==false); setText("healthData", dataOk ? "g\u00fcncel" : "gecikme/gap var");
      const fresh=health.account_freshness||{}; setText("healthAccount", (fresh.source||"—")+(fresh.age_seconds===undefined||fresh.age_seconds===null?"":" \u00b7 "+Math.round(fresh.age_seconds)+"s")+(fresh.maximum_age_for_entry_seconds?" (limit "+fresh.maximum_age_for_entry_seconds+"s)":""));
      setText("healthHeartbeat", fmtDateTime(health.last_heartbeat));
      setText("healthRestarts", String(health.restart_count||0));
      const db=health.database||{}; setText("healthDatabase", (db.exists?"a\u00e7\u0131k":"yok")+(db.size_bytes?" \u00b7 "+fmtNum(db.size_bytes/1024,0)+" KB":""));
      const scanner=health.scanner||{}; const scStatus=Object.entries(scanner.status||{}).map(function(e){return e[0]+": "+e[1];}).join(", "); setText("healthScanner", scStatus||"—");
      const rec=health.reconciliation||{}; setText("healthReconciliation", rec.failed?"HATA":(rec.last_at?fmtDateTime(rec.last_at):"—"));
      const workers=health.workers||{}; const tbody=document.getElementById("workersBody"); if(tbody){ tbody.replaceChildren(); const names=Object.keys(workers); if(!names.length){ const r=document.createElement("tr"); const c=document.createElement("td"); c.colSpan=5; c.className="empty-state"; const ic=document.createElement("span"); ic.className="empty-icon"; ic.textContent="—"; c.append(ic, document.createTextNode("Çal\u0131\u015fan bilgisi yok")); r.appendChild(c); tbody.appendChild(r); } else { names.sort().forEach(function(n){ const w=workers[n]||{}; const r=document.createElement("tr"); [n, w.alive?"canl\u0131":"durdu", fmtDateTime(w.last_heartbeat), String(w.restart_count||0), w.last_error||"—"].forEach(function(v){ const td=document.createElement("td"); td.textContent=String(v); r.appendChild(td); }); tbody.appendChild(r); }); }}
    }
    function renderExecution(execution){
      if(!execution||execution.unavailable) return;
      const badge=document.getElementById("executionBadge"); if(badge) badge.textContent=(execution.open_orders||[]).length+" a\u00e7\u0131k \u00b7 "+(execution.recent_fills||[]).length+" dolum";
      const oBody=document.getElementById("openOrdersBody");
      if(oBody){ oBody.replaceChildren(); const orders=execution.open_orders||[]; if(!orders.length){ const r=document.createElement("tr"); const c=document.createElement("td"); c.colSpan=6; c.className="empty-state"; const ic=document.createElement("span"); ic.className="empty-icon"; ic.textContent="—"; c.append(ic, document.createTextNode("A\u00e7\u0131k emir yok")); r.appendChild(c); oBody.appendChild(r);} else { orders.forEach(function(o){ const r=document.createElement("tr"); const lin=o.lineage||{}; [o.symbol, o.side||"—", fmtNum(o.quantity,4), o.reference_price===undefined||o.reference_price===null?"—":"$"+fmtNum(o.reference_price,4), o.status||"—", lin.strategy ? lin.strategy+" \u00b7 "+fmtDate(lin.decision_timestamp) : "—"].forEach(function(v){ const td=document.createElement("td"); td.textContent=String(v); r.appendChild(td); }); oBody.appendChild(r); }); }}
      const fBody=document.getElementById("recentFillsBody");
      if(fBody){ fBody.replaceChildren(); const fills=execution.recent_fills||[]; if(!fills.length){ const r=document.createElement("tr"); const c=document.createElement("td"); c.colSpan=7; c.className="empty-state"; const ic=document.createElement("span"); ic.className="empty-icon"; ic.textContent="—"; c.append(ic, document.createTextNode("Dolum yok")); r.appendChild(c); fBody.appendChild(r);} else { fills.forEach(function(f){ const r=document.createElement("tr"); const slip=f.slippage_bps; [f.symbol, f.side||"—", fmtNum(f.quantity,4), "$"+fmtNum(f.price,4), slip===undefined||slip===null?"—":fmtNum(slip,2)+" bps", "$"+fmtNum(f.fee,4), fmtDateTime(f.occurred_at)].forEach(function(v){ const td=document.createElement("td"); td.textContent=String(v); r.appendChild(td); }); fBody.appendChild(r); }); }}
    }
    function renderUniverse(universe){
      if(!universe||universe.unavailable) return;
      function renderMarket(market, ids){
        if(!market) return;
        setText(ids.broad, fmtNum(market.broad_discovered_count,0));
        setText(ids.eligible, fmtNum(market.eligible_pool_size,0));
        setText(ids.observed, fmtNum(market.observation_set_size,0));
        setText(ids.lastScan, fmtDateTime(market.last_refresh_at));
        setText(ids.scanner, market.scanner_status||"—");
        setText(ids.nextScan, fmtDateTime(market.next_refresh_at));
        const box=document.getElementById(ids.symbols);
        if(box){ box.replaceChildren(); const syms=market.observation_set||[]; syms.slice(0,120).forEach(function(s){ const ch=document.createElement("span"); ch.textContent=s; box.appendChild(ch); }); if(syms.length>120){ const more=document.createElement("span"); more.textContent="+"+(syms.length-120)+" daha…"; box.appendChild(more); }}
      }
      renderMarket(universe.equity, {broad:"eqBroad", eligible:"eqEligible", observed:"eqObserved", lastScan:"eqLastScan", scanner:"eqScanner", nextScan:"eqNextScan", symbols:"eqSymbols"});
      renderMarket(universe.crypto, {broad:"crBroad", eligible:"crEligible", observed:"crObserved", lastScan:"crLastScan", scanner:"crScanner", nextScan:"crNextScan", symbols:"crSymbols"});
    }
    function renderDashboard(data){
      if(!data) return;
      renderModeBadge(data.mode);
      setText("grossExposureValue", formatCurrency(data.account && data.account.gross_exposure));
      const stale=data.account && data.account.stale; setText("grossExposureSub", stale ? "eski veri ("+(data.account.source||"")+")" : "Toplam pozisyon b\u00fcy\u00fckl\u00fc\u011f\u00fc");
      const total=data.account && data.account.total_pnl; setText("totalPnlValue", formatPnl(total)); const el=document.getElementById("totalPnlValue"); if(el) el.className="metric-value "+(total>0?"positive":(total<0?"negative":""));
      renderStrategyCard(data.equity, {name:"equityStrategyName", badge:"equityStatusBadge", signal:"equitySignal", rebalance:"equityRebalance", pool:"equityPool", obs:"equityObs", positions:"equityPositions", allocation:"equityAllocation", nextRebalance:"equityNextRebalance", catchUp:"equityStartupCatchUp", body:"equityHoldingsBody"}, false);
      renderStrategyCard(data.crypto, {name:"cryptoStrategyName", badge:"cryptoStatusBadge", signal:"cryptoSignal", rebalance:"cryptoRebalance", lookback:"cryptoLookback", pool:"cryptoPool", obs:"cryptoObs", positions:"cryptoPositions", allocation:"cryptoAllocation", nextRebalance:"cryptoNextRebalance", catchUp:"cryptoStartupCatchUp", body:"cryptoHoldingsBody"}, true);
      renderRisk(data.risk); renderHealth(data.health); renderExecution(data.execution); renderUniverse(data.universe);
    }
    let dashInFlight=false;
    async function refreshDashboard(){
      if(dashInFlight) return; dashInFlight=true;
      try { const r=await fetch("/api/dashboard/overview",{cache:"no-store"}); if(!r.ok) throw new Error("Panel verisi al\u0131namad\u0131 ("+r.status+")"); renderDashboard(await r.json()); } catch(e){ console.error("Dashboard refresh error:",e);} finally { dashInFlight=false; }
    }
    // init
    refreshSystemStatus();
    window.setInterval(refreshSystemStatus, 2000);
    if (global.PBWebSocket) global.PBWebSocket.connectStream(function(payload){ if(payload.account) updateAccountUI(payload.account); if(payload.status) updateEngineStateUI(payload.status.state, payload.status.paper); });
    fetch("/api/account",{cache:"no-store"}).then(function(r){ return r.ok ? r.json() : null; }).then(function(d){ if(d) updateAccountUI(d); }).catch(function(){});
    refreshDashboard();
    window.setInterval(refreshDashboard, 5000);
    global.PBDashboard = { refresh: refreshDashboard, render: renderDashboard };
  });
})(window);
