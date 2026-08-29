// ParayaBuck research.js — client-side filtering for strategy/market/status/tag.
// Fetches runs via /api/research/runs (primary) with fallback to legacy /api/research/trials etc.
// Handles both `runs` (manifest+summary) and `trials` shapes; all textContent (no innerHTML).
(function (global) {
  "use strict";
  document.addEventListener("DOMContentLoaded", function(){
    const body = document.getElementById("researchBody");
    if (!body) return;
    const stratSel = document.getElementById("filterStrategy");
    const marketSel = document.getElementById("filterMarket");
    const statusSel = document.getElementById("filterStatus");
    const tagInput = document.getElementById("filterTag");
    const countBadge = document.getElementById("researchCountBadge");
    let trials = [];

    function fmtPct(v){ return (v===undefined||v===null||!Number.isFinite(Number(v))) ? "—" : (Number(v)*100).toFixed(2)+"%"; }
    function fmtNum(v){ return (v===undefined||v===null||!Number.isFinite(Number(v))) ? "—" : Number(v).toFixed(2); }
    function fmtDate(iso){ if(!iso) return "—"; const d=new Date(iso); return Number.isNaN(d.getTime())? iso : d.toLocaleDateString("tr-TR"); }

    async function loadTrials(){
      const endpoints = ["/api/research/runs?limit=500", "/api/research/trials", "/api/research/summary", "/research/trials.jsonl"];
      for (const url of endpoints){
        try {
          const r = await fetch(url, {cache:"no-store"});
          if (!r.ok) continue;
          const ct = r.headers.get("content-type")||"";
          if (ct.includes("application/json")){
            const data = await r.json();
            if (data && Array.isArray(data.runs)) return data.runs;
            if (Array.isArray(data)) return data;
            if (Array.isArray(data.trials)) return data.trials;
            if (Array.isArray(data.summary)) return data.summary;
            if (Array.isArray(data.results)) return data.results;
            if (data && typeof data === "object" && data.run_id) return [data];
          } else {
            const text = await r.text();
            const lines = text.trim().split("\n").filter(Boolean);
            return lines.map(function(l){ try { return JSON.parse(l); } catch(_){ return null; }}).filter(Boolean);
          }
        } catch(_){ continue; }
      }
      return [];
    }

    function populateStrategyFilter(){
      const uniq = Array.from(new Set(trials.map(function(t){ return t.strategy||t.strategy_name||t.spec_strategy||""; }).filter(Boolean))).sort();
      uniq.forEach(function(name){
        const opt=document.createElement("option");
        opt.value=name; opt.textContent=name;
        stratSel.appendChild(opt);
      });
    }

    function matches(t){
      const strat = (t.strategy||t.strategy_name||t.spec_strategy||"").toString();
      const market = (t.market||t.product||t.universe||"").toString().toLowerCase();
      const status = (t.status||t.state||"").toString().toLowerCase();
      const tag = (t.tag||t.label||t.tags||t.trial_tag||"").toString().toLowerCase();
      const fStrat = stratSel.value;
      const fMarket = marketSel.value.toLowerCase();
      const fStatus = statusSel.value.toLowerCase();
      const fTag = tagInput.value.trim().toLowerCase();
      if (fStrat && strat !== fStrat) return false;
      if (fMarket && market.indexOf(fMarket)===-1) return false;
      if (fStatus && status !== fStatus) return false;
      if (fTag && tag.indexOf(fTag)===-1 && strat.toLowerCase().indexOf(fTag)===-1) return false;
      return true;
    }

    function render(){
      const filtered = trials.filter(matches);
      if (countBadge) countBadge.textContent = filtered.length+" / "+trials.length;
      body.replaceChildren();
      if (!filtered.length){
        const r=document.createElement("tr");
        const c=document.createElement("td");
        c.colSpan=8; c.className="empty-state";
        c.textContent = trials.length ? "Filtreye uyan deneme yok" : "Deneme bulunamad\u0131";
        r.appendChild(c); body.appendChild(r); return;
      }
      filtered.slice(0,200).forEach(function(t){
        const r=document.createElement("tr");
        const vals=[
          t.strategy||t.strategy_name||t.spec_strategy||"—",
          t.market||t.product||"—",
          t.status||t.state||"ok",
          (function(){ const v=t.tag||t.label||t.tags; if(Array.isArray(v)) return v.join(", "); return v||"—"; })(),
          fmtNum(t.sharpe||t.sharpe_ratio||t.best_sharpe),
          fmtPct(t.total_return||t.return||t.cagr||t.best_cagr),
          fmtPct(t.max_drawdown||t.max_dd),
          fmtDate(t.created_at||t.created||t.at||t.timestamp)
        ];
        vals.forEach(function(v){
          const td=document.createElement("td");
          td.textContent=String(v);
          r.appendChild(td);
        });
        body.appendChild(r);
      });
      if (filtered.length>200){
        const r=document.createElement("tr");
        const c=document.createElement("td");
        c.colSpan=8; c.className="empty-state";
        c.textContent="+"+(filtered.length-200)+" daha…";
        r.appendChild(c); body.appendChild(r);
      }
    }

    [stratSel, marketSel, statusSel].forEach(function(el){ if(el) el.addEventListener("change", render); });
    if (tagInput) tagInput.addEventListener("input", render);

    loadTrials().then(function(data){
      trials = Array.isArray(data) ? data : [];
      populateStrategyFilter();
      render();
    });
  });
})(window);
