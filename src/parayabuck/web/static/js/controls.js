// ParayaBuck controls.js — engine, mode, settings, safety actions.
(function (global) {
  "use strict";
  document.addEventListener("DOMContentLoaded", function(){
    const modeChip = document.getElementById("modeChip");
    const startBtn = document.getElementById("startBtn");
    const stopBtn = document.getElementById("stopBtn");
    const resetSafetyBtn = document.getElementById("resetSafetyBtn");
    const riskEpochBtn = document.getElementById("riskEpochBtn");
    const settingsBtn = document.getElementById("settingsBtn");
    if (!modeChip) return; // not on research/tools without these controls? still safe
    const byId = function(id){ return document.getElementById(id); };
    const api = global.PBApi;
    let isPaperMode = true;

    // expose for other modules
    global.PBControls = global.PBControls || {};
    global.PBControls.getPaper = function(){ return isPaperMode; };
    global.PBControls.setPaper = function(v){ isPaperMode = !!v; };

    function appendLog(msg, level){
      const toast = byId("statusToast");
      if (!toast) return;
      toast.textContent = msg;
      toast.className = "status-toast " + (level||"info").toLowerCase();
      toast.hidden = false;
      window.clearTimeout(appendLog._t);
      appendLog._t = window.setTimeout(function(){ toast.hidden = true; }, 4500);
    }

    if (startBtn) startBtn.addEventListener("click", async function(){
      try {
        const res = await api.controlFetch("/api/engine/start", { method: "POST" });
        const data = await api.responsePayload(res);
        if (!res.ok) throw new Error(data.detail || "Motor ba\u015flat\u0131lamad\u0131");
        appendLog("Motor ba\u015flatma iste\u011fi g\u00f6nderildi.", "info");
      } catch (err) { alert("Hata: " + err.message); }
    });
    if (stopBtn) stopBtn.addEventListener("click", async function(){
      try {
        const res = await api.controlFetch("/api/engine/stop", { method: "POST" });
        const data = await api.responsePayload(res);
        if (!res.ok) throw new Error(data.detail || "Motor durdurulamad\u0131");
        appendLog("Motor durduruldu.", "warning");
      } catch (err) { alert("Hata: " + err.message); }
    });
    if (resetSafetyBtn) resetSafetyBtn.addEventListener("click", async function(){
      if (!confirm("Broker korumas\u0131 ve veri b\u00fct\u00fcnl\u00fc\u011f\u00fc do\u011fruland\u0131ktan sonra kilit s\u0131f\u0131rlans\u0131n m\u0131?")) return;
      try {
        resetSafetyBtn.disabled = true;
        const res = await api.controlFetch("/api/engine/reset-safety-lock", { method: "POST" });
        const data = await api.responsePayload(res);
        if (!res.ok) throw new Error(data.detail || "G\u00fcvenlik kilidi s\u0131f\u0131rlanamad\u0131");
        appendLog("G\u00fcvenlik kilidi do\u011frulama sonras\u0131 s\u0131f\u0131rland\u0131.", "warning");
      } catch (err) { alert("Hata: " + err.message); } finally { resetSafetyBtn.disabled = false; }
    });
    if (riskEpochBtn) riskEpochBtn.addEventListener("click", async function(){
      if (!confirm("Drawdown taban\u0131 do\u011frulanm\u0131\u015f hesap \u00f6zkayna\u011f\u0131na \u00e7ekilsin ve yeni risk d\u00f6nemi ba\u015flas\u0131n m\u0131?")) return;
      try {
        riskEpochBtn.disabled = true;
        const res = await api.controlFetch("/api/safety/risk-epoch", { method: "POST" });
        const data = await api.responsePayload(res);
        if (!res.ok) throw new Error(data.detail || "Risk d\u00f6nemi ba\u015flat\u0131lamad\u0131");
        const base = data.baseline_equity !== undefined ? " (yeni taban: " + Number(data.baseline_equity).toLocaleString("tr-TR", {maximumFractionDigits:2}) + ")" : "";
        appendLog("Yeni risk d\u00f6nemi ba\u015flat\u0131ld\u0131" + base + ".", "warning");
      } catch (err) { alert("Hata: " + err.message); } finally { riskEpochBtn.disabled = false; }
    });
    if (modeChip) modeChip.addEventListener("click", async function(){
      const nextMode = !isPaperMode;
      if (!nextMode) {
        try {
          const reqRes = await api.controlFetch("/api/live/arming/request", { method: "POST" });
          const reqData = await reqRes.json();
          if (!reqRes.ok) throw new Error(reqData.detail || "Live do\u011frulama ba\u015flat\u0131lamad\u0131");
          const phrase = window.prompt("Hesap \u2026" + reqData.account_suffix + " i\u00e7in LIVE ve son d\u00f6rt haneyi yazarak do\u011frulay\u0131n:", "");
          if (!phrase) return;
          const confRes = await api.controlFetch("/api/live/arming/confirm", { method:"POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({phrase: phrase})});
          const confData = await confRes.json();
          if (!confRes.ok) throw new Error(confData.detail || "Live do\u011frulama ba\u015far\u0131s\u0131z");
        } catch(err){ alert("Hata: " + err.message); return; }
      }
      try {
        const res = await api.controlFetch("/api/mode", { method:"POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({paper: nextMode})});
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Mod de\u011fi\u015ftirilemedi");
        isPaperMode = nextMode;
        appendLog("\u0130\u015flem modu de\u011fi\u015ftirildi: " + (nextMode ? "PAPER (Demo)" : "REAL (Ger\u00e7ek Para)"), "info");
        if (global.PBStatus && global.PBStatus.refresh) global.PBStatus.refresh();
      } catch(err){ alert("Hata: " + err.message); }
    });
    modeChip.addEventListener("keydown", function(e){ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); modeChip.click(); }});

    // Settings modal (if present)
    const settingsModal = byId("settingsModal");
    const closeSettingsBtn = byId("closeSettingsBtn");
    const cancelSettingsBtn = byId("cancelSettingsBtn");
    const settingsForm = byId("settingsForm");
    if (settingsModal && settingsBtn) {
      const setOpen = function(open){
        settingsModal.classList.toggle("active", open);
        settingsModal.setAttribute("aria-hidden", String(!open));
        document.body.classList.toggle("modal-open", open);
        settingsBtn.setAttribute("aria-expanded", String(open));
        if (open) setTimeout(function(){ closeSettingsBtn && closeSettingsBtn.focus(); }, 180);
        else settingsBtn.focus();
      };
      const openSettings = async function(){
        try {
          const res = await fetch("/api/settings", { cache:"no-store" });
          const data = await res.json();
          byId("apiKey").value = data.api_key || "";
          byId("secretKey").value = data.secret_key || "";
          if (data.has_credentials){ byId("apiKey").placeholder = "Mevcut anahtar korunacak"; byId("secretKey").placeholder = "Mevcut secret korunacak"; }
          byId("paperModeCheck").checked = data.paper_mode;
          byId("equityTradingCheck").checked = data.equity_trading_enabled !== false;
          byId("cryptoTradingCheck").checked = data.crypto_trading_enabled === true;
          byId("liveTradingCheck").checked = data.live_trading_enabled === true;
          byId("maxDailyLoss").value = data.daily_loss_limit_pct || 1.5;
          byId("maxDrawdown").value = data.max_drawdown_pct || 10;
          byId("timeframe").value = data.timeframe || "5m";
          byId("host").value = data.host || "127.0.0.1";
          byId("port").value = data.port || 8000;
          byId("openBrowser").checked = data.open_browser !== false;
          byId("logLevel").value = data.log_level || "INFO";
          // prerequisites
          const prBox = byId("livePrerequisites");
          const prList = byId("livePrerequisitesList");
          const prSum = byId("livePrerequisitesSummary");
          try {
            const prRes = await fetch("/api/live/prerequisites", { cache:"no-store" });
            if (prRes.ok){
              const pr = await prRes.json();
              prBox.hidden = false; prList.innerHTML = "";
              (pr.items||[]).forEach(function(item){
                const li = document.createElement("li");
                const mark = document.createElement("span");
                if (item.satisfied){ mark.textContent="✓ "; mark.className="positive"; }
                else if (item.state==="unverifiable"){ mark.textContent="? "; }
                else { mark.textContent="✗ "; mark.className="negative"; }
                li.appendChild(mark);
                const lbl = document.createElement("span"); lbl.textContent = item.label; li.appendChild(lbl);
                if (!item.satisfied && item.missing){ const sm=document.createElement("small"); sm.className="settings-note"; sm.textContent=" — "+item.missing; li.appendChild(sm); }
                prList.appendChild(li);
              });
              prSum.textContent = pr.all_met ? "T\u00fcm \u00f6n ko\u015fullar sa\u011fland\u0131." : "Eksik \u00f6n ko\u015fullar tamamlanmadan canl\u0131 mod arm edilemez.";
            }
          } catch(_){ if(prBox) prBox.hidden = true; }
          setOpen(true);
        } catch(err){ alert("Ayarlar y\u00fcklenemedi: " + err.message); }
      };
      settingsBtn.addEventListener("click", openSettings);
      if (closeSettingsBtn) closeSettingsBtn.addEventListener("click", function(){ setOpen(false); });
      if (cancelSettingsBtn) cancelSettingsBtn.addEventListener("click", function(){ setOpen(false); });
      settingsModal.addEventListener("click", function(e){ if(e.target===settingsModal) setOpen(false); });
      document.addEventListener("keydown", function(e){ if(e.key==="Escape" && settingsModal.classList.contains("active")) setOpen(false); });
      if (settingsForm) settingsForm.addEventListener("submit", async function(e){
        e.preventDefault();
        const btn = settingsForm.querySelector('button[type="submit"]');
        const payload = {
          api_key: byId("apiKey").value.trim(),
          secret_key: byId("secretKey").value.trim(),
          paper_mode: byId("paperModeCheck").checked,
          equity_trading_enabled: byId("equityTradingCheck").checked,
          crypto_trading_enabled: byId("cryptoTradingCheck").checked,
          live_trading_enabled: byId("liveTradingCheck").checked,
          daily_loss_limit_pct: parseFloat(byId("maxDailyLoss").value),
          max_drawdown_pct: parseFloat(byId("maxDrawdown").value),
          execution_timeframe: byId("timeframe").value,
          host: byId("host").value.trim(),
          port: parseInt(byId("port").value,10),
          open_browser: byId("openBrowser").checked,
          log_level: byId("logLevel").value
        };
        try {
          btn.disabled = true; btn.textContent = "Kaydediliyor...";
          const res = await api.controlFetch("/api/settings", { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)});
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || "Ayarlar kaydedilemedi");
          setOpen(false);
          appendLog(data.engine_restarted ? "Ayarlar uyguland\u0131; i\u015flem motoru yeniden ba\u015flat\u0131ld\u0131." : "Ayarlar ve API bilgileri g\u00fcncellendi.", "info");
          if (data.app_restart_required) appendLog("Web adresi/portu de\u011fi\u015fikli\u011fi bir sonraki a\u00e7\u0131l\u0131\u015fta etkinle\u015fecek.", "warning");
        } catch(err){ alert("Ayarlar kaydedilemedi: " + err.message); } finally { btn.disabled=false; btn.textContent="Kaydet ve uygula"; }
      });
    }
  });
})(window);
