// ParayaBuck ops.js — ops summary panel (backend-authoritative).
// Reuses /api/ops/status — shows Runtime, Trading state, Heartbeat, health checks,
// incidents, recent events, decision explanation, stuck execution, alerts health.
// No JS health recalculation: renders whatever backend reports, distinguishing BLOCKED vs CRASHED/CRITICAL.
(function (global) {
  "use strict";
  document.addEventListener("DOMContentLoaded", function(){
    const panel = document.getElementById("opsSummaryPanel");
    if (!panel) return;
    const fmtDT = function(iso){ if(!iso) return "—"; const d=new Date(iso); return Number.isNaN(d.getTime())? iso : d.toLocaleString("tr-TR",{hour12:false}); };
    const fmtAge = function(sec){ if(sec===undefined||sec===null) return "—"; if(sec<60) return Math.round(sec)+"s"; if(sec<3600) return Math.round(sec/60)+"dk"; return (sec/3600).toFixed(1)+"sa"; };

    function checkClass(status){
      if(status==="OK") return "ok";
      if(status==="BLOCKED") return "blocked";
      if(status==="CRITICAL"||status==="CRASHED") return "critical";
      if(status==="DEGRADED") return "degraded";
      return "unknown";
    }
    function renderOps(data){
      // overall
      const overallEl = document.getElementById("opsOverall");
      const tradingEl = document.getElementById("opsTradingState");
      const heartbeatEl = document.getElementById("opsHeartbeat");
      const snapshotAgeEl = document.getElementById("opsSnapshotAge");
      if (overallEl) { overallEl.textContent = data.overall_health || "UNKNOWN"; overallEl.className = "ops-badge "+checkClass(data.overall_health); }
      if (tradingEl) { tradingEl.textContent = data.trading_state || "UNKNOWN"; tradingEl.className = "ops-badge "+checkClass(data.trading_state); }
      if (heartbeatEl) heartbeatEl.textContent = fmtDT(data.as_of);
      if (snapshotAgeEl) snapshotAgeEl.textContent = data.snapshot_age_seconds===undefined||data.snapshot_age_seconds===null ? "—" : fmtAge(data.snapshot_age_seconds);
      // Runtime / heartbeat details from checks
      const checksBox = document.getElementById("opsChecks");
      if (checksBox){
        checksBox.replaceChildren();
        const checks = data.checks || [];
        if (!checks.length){ const d=document.createElement("div"); d.className="empty-state"; d.textContent="Check yok"; checksBox.appendChild(d); }
        else {
          checks.forEach(function(c){
            const row=document.createElement("div");
            row.className="ops-check "+checkClass(c.status);
            const head=document.createElement("div");
            head.className="ops-check-head";
            const code=document.createElement("strong");
            code.textContent=c.code;
            const status=document.createElement("span");
            status.className="ops-check-status "+checkClass(c.status);
            status.textContent=c.status;
            head.append(code, status);
            const summary=document.createElement("div");
            summary.className="ops-check-summary";
            summary.textContent=c.summary || "—";
            row.append(head, summary);
            if (c.action){ const act=document.createElement("div"); act.className="ops-check-action"; act.textContent="→ "+c.action; row.appendChild(act); }
            // Backend-provided distinction: BLOCKED vs CRITICAL/CRASHED — render verbatim, no recalculation
            if (c.observed !== undefined && c.observed !== null){
              const obs=document.createElement("pre");
              obs.className="ops-check-observed";
              try { obs.textContent = typeof c.observed==="string" ? c.observed : JSON.stringify(c.observed, null, 2); } catch(_){ obs.textContent=String(c.observed); }
              row.appendChild(obs);
            }
            checksBox.appendChild(row);
          });
        }
      }
      // Incidents / events / execution / alerts are already in checks (risk, execution, alerts).
      // Also render recent_events / decision explanation if backend provides them (forward-compat).
      const eventsBox=document.getElementById("opsRecentEvents");
      if (eventsBox){
        const events=data.recent_events || data.events || [];
        eventsBox.replaceChildren();
        if (!events.length){ eventsBox.textContent="Son olay yok"; eventsBox.className="empty-state"; }
        else {
          events.slice(0,10).forEach(function(ev){
            const div=document.createElement("div");
            div.className="ops-event";
            div.textContent=(ev.type||ev.code||"event")+" · "+fmtDT(ev.at||ev.time||ev.timestamp)+(ev.message ? " — "+ev.message : "");
            eventsBox.appendChild(div);
          });
        }
      }
      const decisionBox=document.getElementById("opsDecisionExplanation");
      if (decisionBox){
        const exp=data.decision_explanation || data.explanations;
        if (!exp) { decisionBox.textContent="Karar a\u00e7\u0131klamas\u0131 yok"; decisionBox.className="empty-state"; }
        else if (typeof exp==="string"){ decisionBox.textContent=exp; }
        else { try { decisionBox.textContent=JSON.stringify(exp,null,2); } catch(_){ decisionBox.textContent=String(exp); } }
      }
    }

    let inFlight=false;
    async function refreshOps(){
      if(inFlight) return; inFlight=true;
      try {
        const r=await fetch("/api/ops/status",{cache:"no-store"});
        if(!r.ok) throw new Error("ops status "+r.status);
        const data=await r.json();
        renderOps(data);
      } catch(e){ console.error("ops refresh",e); const el=document.getElementById("opsChecks"); if(el) el.textContent="Ops durumu al\u0131namad\u0131: "+e.message; } finally { inFlight=false; }
    }
    refreshOps();
    window.setInterval(refreshOps, 5000);
    global.PBOps = { refresh: refreshOps };
  });
})(window);
