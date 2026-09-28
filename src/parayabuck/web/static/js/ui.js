// ParayaBuck ui.js — shared shell: formatting, toasts, dialogs, theme, navigation,
// engine status + controls, mode switching and the settings drawer.
// Depends on PBApi and PBWebSocket. The control token is never persisted.
(function (global) {
  "use strict";

  const api = global.PBApi;
  const byId = function (id) { return document.getElementById(id); };
  const isNum = function (v) { return v !== undefined && v !== null && v !== "" && Number.isFinite(Number(v)); };

  // ---------- formatting ----------
  const moneyFmt = new Intl.NumberFormat("tr-TR", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmt = {
    money: function (v) { return isNum(v) ? moneyFmt.format(Number(v)) : "—"; },
    signedMoney: function (v) {
      if (!isNum(v)) return "—";
      const n = Number(v);
      return (n > 0 ? "+" : n < 0 ? "−" : "") + moneyFmt.format(Math.abs(n));
    },
    price: function (v) {
      if (!isNum(v)) return "—";
      const n = Number(v);
      const digits = Math.abs(n) >= 1000 ? 2 : Math.abs(n) >= 1 ? 2 : 6;
      return new Intl.NumberFormat("tr-TR", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: digits }).format(n);
    },
    num: function (v, digits) {
      if (!isNum(v)) return "—";
      return Number(v).toLocaleString("tr-TR", { maximumFractionDigits: digits === undefined ? 2 : digits });
    },
    pct: function (v, digits) {
      if (!isNum(v)) return "—";
      return "%" + Number(v).toLocaleString("tr-TR", { minimumFractionDigits: digits === undefined ? 1 : digits, maximumFractionDigits: digits === undefined ? 1 : digits });
    },
    signedPct: function (v, digits) {
      if (!isNum(v)) return "—";
      const n = Number(v);
      return (n > 0 ? "+" : n < 0 ? "−" : "") + fmt.pct(Math.abs(n), digits);
    },
    dateTime: function (iso) {
      if (!iso) return "—";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso);
      return d.toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
    },
    date: function (iso) {
      if (!iso) return "—";
      const d = new Date(iso);
      return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString("tr-TR", { day: "numeric", month: "short", year: "numeric" });
    },
    time: function (iso) {
      const d = iso ? new Date(iso) : new Date();
      return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("tr-TR", { hour12: false });
    },
    duration: function (sec) {
      if (!isNum(sec)) return "—";
      const s = Math.abs(Number(sec));
      if (s < 60) return Math.round(s) + " sn";
      if (s < 3600) return Math.round(s / 60) + " dk";
      if (s < 86400) return (s / 3600).toFixed(1).replace(".", ",") + " sa";
      return Math.round(s / 86400) + " gün";
    },
    relative: function (iso) {
      if (!iso) return "—";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso);
      const diff = (d.getTime() - Date.now()) / 1000;
      if (Math.abs(diff) < 45) return "şimdi";
      return diff > 0 ? fmt.duration(diff) + " sonra" : fmt.duration(diff) + " önce";
    },
    tone: function (v) { return !isNum(v) ? "" : Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : ""; }
  };

  // ---------- DOM helpers ----------
  function el(tag, props, children) {
    const node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (key) {
        const val = props[key];
        if (val === undefined || val === null || val === false) return;
        if (key === "text") node.textContent = String(val);
        else if (key === "className") node.className = val;
        else node.setAttribute(key, val === true ? "" : String(val));
      });
    }
    (children || []).forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }
  function icon(name, extra) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon" + (extra ? " " + extra : ""));
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.appendChild(use);
    return svg;
  }
  function setText(id, value, className) {
    const node = byId(id);
    if (!node) return null;
    node.textContent = value === undefined || value === null ? "—" : String(value);
    if (className !== undefined) node.className = className;
    return node;
  }
  function emptyRow(tbody, cols, text) {
    if (!tbody) return;
    const td = el("td", { text: text });
    td.colSpan = cols;
    tbody.replaceChildren(el("tr", { className: "empty" }, [td]));
  }
  function badgeClass(status) {
    const s = String(status || "").toLowerCase();
    if (["ok", "active", "running", "healthy"].indexOf(s) !== -1) return "badge ok";
    if (["blocked", "degraded", "warming", "warming_up", "starting", "stopping"].indexOf(s) !== -1) return "badge warn";
    if (["critical", "crashed", "halted", "error", "stop_failed", "scanner_issue"].indexOf(s) !== -1) return "badge bad";
    return "badge";
  }

  // ---------- toasts ----------
  function toast(message, level) {
    const box = byId("toasts");
    if (!box) return;
    level = level || "info";
    const name = level === "success" ? "check" : level === "warning" || level === "error" ? "alert" : "info";
    const node = el("div", { className: "toast " + level, role: "status" }, [icon(name), el("span", { text: message })]);
    box.appendChild(node);
    window.setTimeout(function () {
      node.classList.add("leaving");
      window.setTimeout(function () { node.remove(); }, 220);
    }, level === "error" ? 7000 : 4500);
  }

  // ---------- dialogs (native <dialog>) ----------
  function openDialog(opts) {
    const dlg = byId("pbDialog");
    if (!dlg || typeof dlg.showModal !== "function") {
      if (opts.input) return Promise.resolve(window.prompt(opts.message || "", "") || null);
      return Promise.resolve(opts.alertOnly ? true : window.confirm(opts.message || ""));
    }
    setText("pbDialogTitle", opts.title || "");
    setText("pbDialogMessage", opts.message || "");
    const field = byId("pbDialogField");
    const input = byId("pbDialogInput");
    field.hidden = !opts.input;
    input.value = "";
    input.placeholder = opts.placeholder || "";
    setText("pbDialogLabel", opts.label || "");
    const ok = byId("pbDialogOk");
    ok.textContent = opts.confirmLabel || "Tamam";
    ok.className = "btn " + (opts.danger ? "danger-solid" : "primary");
    byId("pbDialogCancel").hidden = !!opts.alertOnly;
    return new Promise(function (resolve) {
      const onClose = function () {
        dlg.removeEventListener("close", onClose);
        const accepted = dlg.returnValue === "ok";
        if (opts.input) resolve(accepted ? input.value.trim() || null : null);
        else resolve(accepted);
      };
      dlg.returnValue = "";
      dlg.addEventListener("close", onClose);
      dlg.showModal();
      if (opts.input) input.focus();
    });
  }
  const dialog = {
    confirm: function (opts) { return openDialog(Object.assign({}, opts)); },
    prompt: function (opts) { return openDialog(Object.assign({}, opts, { input: true })); },
    alert: function (opts) { return openDialog(Object.assign({}, opts, { alertOnly: true })); }
  };

  // ---------- theme ----------
  function applyThemeLabel() {
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    const use = byId("themeIcon");
    if (use) use.setAttribute("href", dark ? "#i-sun" : "#i-moon");
    setText("themeLabel", dark ? "Açık tema" : "Koyu tema");
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#090b0f" : "#f4f5f8");
    document.dispatchEvent(new CustomEvent("pb:theme"));
  }
  function toggleTheme() {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { window.localStorage.setItem("pb-theme", next); } catch (_) { /* per-viewer convenience only */ }
    applyThemeLabel();
  }

  // ---------- navigation ----------
  function setupNavigation() {
    const sidebar = byId("sidebar");
    const scrim = byId("sidebarScrim");
    const setMenu = function (open) {
      if (!sidebar) return;
      sidebar.classList.toggle("open", open);
      if (scrim) scrim.classList.toggle("open", open);
    };
    const menuBtn = byId("menuBtn");
    if (menuBtn) menuBtn.addEventListener("click", function () { setMenu(!sidebar.classList.contains("open")); });
    if (scrim) scrim.addEventListener("click", function () { setMenu(false); });
    document.querySelectorAll(".nav-item").forEach(function (link) {
      link.addEventListener("click", function () { setMenu(false); });
    });

    const links = {};
    document.querySelectorAll(".nav-item[data-nav]").forEach(function (link) { links[link.dataset.nav] = link; });
    const markActive = function (name) {
      Object.keys(links).forEach(function (key) { links[key].classList.toggle("active", key === name); });
    };
    const sections = Array.prototype.slice.call(document.querySelectorAll("[data-section]"));
    if (!sections.length) return;
    markActive(sections[0].dataset.section);
    // The active section is the last one whose top has scrolled past the topbar.
    let ticking = false;
    const spy = function () {
      ticking = false;
      const line = 140;
      let current = sections[0];
      sections.forEach(function (section) {
        if (section.getBoundingClientRect().top <= line) current = section;
      });
      const bottom = global.innerHeight + global.scrollY >= document.documentElement.scrollHeight - 4;
      markActive((bottom ? sections[sections.length - 1] : current).dataset.section);
    };
    global.addEventListener("scroll", function () {
      if (!ticking) { ticking = true; global.requestAnimationFrame(spy); }
    }, { passive: true });
    spy();
  }

  // ---------- engine status ----------
  const STATE_LABELS = {
    running: "Çalışıyor", stopped: "Durdu", starting: "Başlatılıyor", warming_up: "Isınıyor",
    degraded: "Kısıtlı çalışıyor", halted: "Güvenlik durdurdu", stopping: "Durduruluyor",
    stop_failed: "Durdurma hatası", error: "Hata"
  };
  const ACTIVE_STATES = ["running", "warming_up", "degraded", "halted"];
  const listeners = { status: [], stream: [] };
  const shell = { paper: true, accountSuffix: "", state: "stopped", status: null };

  function renderMode() {
    const chip = byId("modeChip");
    if (!chip) return;
    chip.className = "pill " + (shell.paper ? "mode-paper" : "mode-live");
    setText("modeText", shell.paper ? "Paper" : "CANLI");
    setText("modeSuffix", shell.accountSuffix ? "· …" + shell.accountSuffix : "");
    chip.title = shell.paper ? "Demo hesap — gerçek para kullanılmıyor. Değiştirmek için tıklayın." : "Gerçek para modu. Paper'a dönmek için tıklayın.";
  }
  function renderEngineState(state, paper) {
    shell.state = state || "stopped";
    if (paper !== undefined && paper !== null) shell.paper = paper !== false;
    renderMode();
    const dot = byId("statusDot");
    if (dot) dot.className = "dot " + shell.state;
    setText("statusText", STATE_LABELS[shell.state] || shell.state);
    const start = byId("startBtn");
    const stop = byId("stopBtn");
    const reset = byId("resetSafetyBtn");
    const epoch = byId("riskEpochBtn");
    const active = ACTIVE_STATES.indexOf(shell.state) !== -1;
    if (reset) reset.hidden = shell.state !== "halted" && shell.state !== "degraded";
    if (epoch) epoch.hidden = !active;
    if (!start || !stop) return;
    if (active) { start.disabled = true; stop.disabled = false; }
    else if (shell.state === "stopped" || shell.state === "error") { start.disabled = false; stop.disabled = true; }
    else if (shell.state === "starting" || shell.state === "stopping") { start.disabled = true; stop.disabled = shell.state === "stopping"; }
    else { start.disabled = true; stop.disabled = true; }
  }
  function marketLabel(status, market) {
    if (!status.runtime) {
      if (status.state === "starting") return "başlatılıyor";
      if (status.state === "error") return "hata";
      return "motor kapalı";
    }
    if (!market) return "kapalı";
    if (market.entry_halt_reason) return "alım durduruldu";
    if (market.stream_healthy === false) return "veri akışı sorunlu";
    if (market.ready === false) return "hazırlanıyor";
    if (isNum(market.ready_symbol_count) && isNum(market.symbol_count) && market.ready_symbol_count < market.symbol_count) {
      return market.ready_symbol_count + "/" + market.symbol_count + " hazır";
    }
    return "hazır";
  }
  function renderStatus(status) {
    shell.status = status;
    renderEngineState(status.state, status.paper);
    const markets = (status.runtime && status.runtime.markets) || {};
    setText("marketStatusText",
      "Hisse: " + marketLabel(status, markets.equity) +
      " · Kripto: " + marketLabel(status, markets.crypto) +
      " · " + fmt.time() + " güncellendi");

    const messages = [];
    let danger = false;
    const breaker = status.runtime && status.runtime.circuit_breaker_reason;
    if (breaker) { messages.push("Güvenlik kilidi devrede (" + breaker + "): yeni alım yapılmıyor. Sorun giderildiyse “Kilidi doğrula” ile açabilirsiniz."); danger = true; }
    if (status.engine_error) { messages.push("Motor hatası: " + status.engine_error); danger = true; }
    const retry = status.engine && status.engine.retry;
    if (retry) messages.push("Motor otomatik yeniden deneniyor (deneme " + retry.attempt + "), yaklaşık " + fmt.duration(retry.retry_in_seconds) + " sonra.");
    if (status.security_warning) messages.push(status.security_warning);
    if (status.has_credentials === false) messages.push("Alpaca API anahtarları girilmemiş. Ayarlar’dan ekleyin.");
    const bar = byId("alertBar");
    if (bar) {
      bar.hidden = messages.length === 0;
      bar.className = "alert-bar" + (danger ? " danger" : "");
      setText("alertText", messages.join(" "));
    }
    listeners.status.forEach(function (fn) { try { fn(status); } catch (e) { console.error(e); } });
  }
  let statusInFlight = false;
  async function refreshStatus() {
    if (statusInFlight) return;
    statusInFlight = true;
    try {
      renderStatus(await api.fetchJson("/api/status"));
    } catch (e) {
      setText("marketStatusText", "Sunucuya ulaşılamıyor — yeniden deneniyor…");
    } finally {
      statusInFlight = false;
    }
  }

  // ---------- engine actions ----------
  async function postControl(url, body) {
    const opts = { method: "POST" };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    const res = await api.controlFetch(url, opts);
    const data = await api.responsePayload(res);
    if (!res.ok) throw new Error(api.detailText(data, "İstek başarısız (" + res.status + ")"));
    return data;
  }
  async function withButton(button, fn) {
    if (button) button.disabled = true;
    try { await fn(); } catch (err) { toast("İşlem başarısız: " + err.message, "error"); } finally {
      if (button) button.disabled = false;
      refreshStatus();
    }
  }
  function setupControls() {
    const start = byId("startBtn");
    const stop = byId("stopBtn");
    const reset = byId("resetSafetyBtn");
    const epoch = byId("riskEpochBtn");
    const chip = byId("modeChip");
    if (start) start.addEventListener("click", function () {
      withButton(start, async function () {
        await postControl("/api/engine/start");
        toast("Motor başlatılıyor…", "success");
      });
    });
    if (stop) stop.addEventListener("click", async function () {
      const ok = await dialog.confirm({ title: "Motor durdurulsun mu?", message: "Bot yeni karar vermeyi ve işlem yapmayı bırakır. Açık pozisyonlar hesapta kalır.", confirmLabel: "Durdur", danger: true });
      if (!ok) return;
      withButton(stop, async function () {
        await postControl("/api/engine/stop");
        toast("Motor durduruldu.", "warning");
      });
    });
    if (reset) reset.addEventListener("click", async function () {
      const ok = await dialog.confirm({ title: "Güvenlik kilidi açılsın mı?", message: "Broker korumaları ve veri bütünlüğü doğrulandıktan sonra kilit sıfırlanır. Emin değilseniz vazgeçin.", confirmLabel: "Doğrula ve aç" });
      if (!ok) return;
      withButton(reset, async function () {
        await postControl("/api/engine/reset-safety-lock");
        toast("Güvenlik kilidi doğrulama sonrası açıldı.", "success");
      });
    });
    if (epoch) epoch.addEventListener("click", async function () {
      const ok = await dialog.confirm({ title: "Yeni risk dönemi başlatılsın mı?", message: "Zirveden düşüş (drawdown) hesabının tabanı mevcut doğrulanmış bakiyeye çekilir.", confirmLabel: "Başlat" });
      if (!ok) return;
      withButton(epoch, async function () {
        const data = await postControl("/api/safety/risk-epoch");
        toast("Yeni risk dönemi başladı" + (isNum(data.baseline_equity) ? " (taban " + fmt.money(data.baseline_equity) + ")" : "") + ".", "success");
      });
    });
    if (chip) chip.addEventListener("click", async function () {
      const toPaper = !shell.paper;
      try {
        if (!toPaper) {
          const req = await postControl("/api/live/arming/request");
          const phrase = await dialog.prompt({
            title: "Gerçek para moduna geçiş",
            message: "Hesap …" + (req.account_suffix || "????") + " üzerinde GERÇEK emirler gönderilecek. Bu işlem geri alınabilir ama canlı paraya dokunur.",
            label: "Onay için LIVE ve hesabın son dört hanesini yazın",
            placeholder: "LIVE " + (req.account_suffix || "1234"),
            confirmLabel: "Canlıya geç",
            danger: true
          });
          if (!phrase) return;
          await postControl("/api/live/arming/confirm", { phrase: phrase });
        } else {
          const ok = await dialog.confirm({ title: "Paper moda dönülsün mü?", message: "Bot demo hesapla çalışmaya devam eder.", confirmLabel: "Paper'a dön" });
          if (!ok) return;
        }
        await postControl("/api/mode", { paper: toPaper });
        shell.paper = toPaper;
        renderMode();
        toast("İşlem modu: " + (toPaper ? "Paper (demo)" : "CANLI (gerçek para)"), toPaper ? "success" : "warning");
        refreshStatus();
      } catch (err) {
        toast("İşlem modu değiştirilemedi: " + err.message, "error");
      }
    });
  }

  // ---------- settings drawer ----------
  function setupSettings() {
    const drawer = byId("settingsDrawer");
    const scrim = byId("settingsScrim");
    const form = byId("settingsForm");
    const openBtn = byId("settingsBtn");
    if (!drawer || !form || !openBtn) return;
    const setOpen = function (open) {
      drawer.classList.toggle("open", open);
      if (scrim) scrim.classList.toggle("open", open);
      drawer.setAttribute("aria-hidden", String(!open));
      document.body.classList.toggle("no-scroll", open);
      openBtn.setAttribute("aria-expanded", String(open));
      if (open) window.setTimeout(function () { byId("apiKey").focus(); }, 250);
    };
    const renderPrerequisites = async function () {
      const box = byId("livePrerequisites");
      const list = byId("livePrerequisitesList");
      try {
        const pr = await api.fetchJson("/api/live/prerequisites");
        list.replaceChildren.apply(list, (pr.items || []).map(function (item) {
          const mark = item.satisfied ? el("span", { className: "mark pos", text: "✓" })
            : item.state === "unverifiable" ? el("span", { className: "mark muted", text: "?" })
            : el("span", { className: "mark neg", text: "✗" });
          return el("li", null, [mark, el("span", null, [
            item.label,
            !item.satisfied && item.missing ? el("small", { className: "muted", text: " — " + item.missing }) : null
          ])]);
        }));
        setText("livePrerequisitesSummary", pr.all_met ? "Tüm ön koşullar sağlandı." : "Eksikler tamamlanmadan canlı mod açılamaz.");
        box.hidden = false;
      } catch (_) {
        box.hidden = true;
      }
    };
    const open = async function () {
      try {
        const data = await api.fetchJson("/api/settings");
        byId("apiKey").value = data.api_key || "";
        byId("secretKey").value = data.secret_key || "";
        if (data.has_credentials) {
          byId("apiKey").placeholder = "Mevcut anahtar korunacak";
          byId("secretKey").placeholder = "Mevcut secret korunacak";
        }
        byId("paperModeCheck").checked = !!data.paper_mode;
        byId("equityTradingCheck").checked = data.equity_trading_enabled !== false;
        byId("cryptoTradingCheck").checked = data.crypto_trading_enabled === true;
        byId("maxDailyLoss").value = data.daily_loss_limit_pct || 1.5;
        byId("maxDrawdown").value = data.max_drawdown_pct || 10;
        byId("timeframe").value = data.timeframe || "5m";
        byId("host").value = data.host || "127.0.0.1";
        byId("port").value = data.port || 8000;
        byId("openBrowser").checked = data.open_browser !== false;
        byId("logLevel").value = data.log_level || "INFO";
        setOpen(true);
        renderPrerequisites();
      } catch (err) {
        toast("Ayarlar yüklenemedi: " + err.message, "error");
      }
    };
    openBtn.addEventListener("click", open);
    byId("closeSettingsBtn").addEventListener("click", function () { setOpen(false); });
    byId("cancelSettingsBtn").addEventListener("click", function () { setOpen(false); });
    if (scrim) scrim.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && drawer.classList.contains("open")) setOpen(false);
    });
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      const btn = byId("saveSettingsBtn");
      const payload = {
        api_key: byId("apiKey").value.trim(),
        secret_key: byId("secretKey").value.trim(),
        paper_mode: byId("paperModeCheck").checked,
        equity_trading_enabled: byId("equityTradingCheck").checked,
        crypto_trading_enabled: byId("cryptoTradingCheck").checked,
        daily_loss_limit_pct: parseFloat(byId("maxDailyLoss").value),
        max_drawdown_pct: parseFloat(byId("maxDrawdown").value),
        execution_timeframe: byId("timeframe").value,
        host: byId("host").value.trim(),
        port: parseInt(byId("port").value, 10),
        open_browser: byId("openBrowser").checked,
        log_level: byId("logLevel").value
      };
      btn.disabled = true;
      btn.textContent = "Kaydediliyor…";
      try {
        const data = await postControl("/api/settings", payload);
        setOpen(false);
        toast(data.engine_restarted ? "Ayarlar uygulandı; motor yeniden başlatıldı." : "Ayarlar kaydedildi.", "success");
        if (data.app_restart_required) toast("Adres/port değişikliği bir sonraki açılışta etkinleşir.", "warning");
        refreshStatus();
      } catch (err) {
        toast("Ayarlar kaydedilemedi: " + err.message, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "Kaydet ve uygula";
      }
    });
    if (global.location.hash === "#settings") open();
  }

  // ---------- public API ----------
  global.PB = {
    fmt: fmt,
    el: el,
    icon: icon,
    setText: setText,
    emptyRow: emptyRow,
    badgeClass: badgeClass,
    isNum: isNum,
    toast: toast,
    dialog: dialog,
    shell: shell,
    onStatus: function (fn) { listeners.status.push(fn); if (shell.status) fn(shell.status); },
    onStream: function (fn) { listeners.stream.push(fn); },
    refreshStatus: refreshStatus,
    setAccountSuffix: function (suffix) {
      if (suffix && suffix !== shell.accountSuffix) { shell.accountSuffix = suffix; renderMode(); }
    }
  };

  document.addEventListener("DOMContentLoaded", function () {
    applyThemeLabel();
    const themeBtn = byId("themeToggle");
    if (themeBtn) themeBtn.addEventListener("click", toggleTheme);
    setupNavigation();
    setupControls();
    setupSettings();
    refreshStatus();
    global.setInterval(refreshStatus, 2000);
    if (global.PBWebSocket) {
      global.PBWebSocket.connectStream(function (payload) {
        if (payload.status) renderEngineState(payload.status.state, payload.status.paper);
        if (payload.account && payload.account.account_suffix) global.PB.setAccountSuffix(payload.account.account_suffix);
        listeners.stream.forEach(function (fn) { try { fn(payload); } catch (e) { console.error(e); } });
      });
    }
  });
})(window);
