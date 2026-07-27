// ParayaBuck Modern Web Trading Terminal JavaScript App

document.addEventListener('DOMContentLoaded', () => {
    let controlToken = document.querySelector('meta[name="parayabuck-control-token"]').content;
    let controlHeaders = { 'X-ParayaBuck-Control-Token': controlToken };

    const refreshControlToken = async () => {
        const response = await fetch('/api/control-token', { cache: 'no-store' });
        if (!response.ok) throw new Error('Yerel kontrol oturumu yenilenemedi');
        const data = await response.json();
        controlToken = data.control_token;
        controlHeaders = { 'X-ParayaBuck-Control-Token': controlToken };
    };

    const controlFetch = async (url, options = {}) => {
        const request = {
            ...options,
            headers: { ...(options.headers || {}), ...controlHeaders }
        };
        let response = await fetch(url, request);
        if (response.status !== 403) return response;

        let detail = '';
        try {
            detail = (await response.clone().json()).detail || '';
        } catch (_) {
            return response;
        }
        if (detail !== 'Invalid local control token') return response;

        await refreshControlToken();
        response = await fetch(url, {
            ...options,
            headers: { ...(options.headers || {}), ...controlHeaders }
        });
        return response;
    };
    // DOM Elements
    const modeChip = document.getElementById('modeChip');
    const modeIcon = document.getElementById('modeIcon');
    const modeText = document.getElementById('modeText');

    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');

    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const settingsBtn = document.getElementById('settingsBtn');

    // Stat Values
    const equityValue = document.getElementById('equityValue');
    const cashValue = document.getElementById('cashValue');
    const dailyPnlValue = document.getElementById('dailyPnlValue');
    const realizedPnlValue = document.getElementById('realizedPnlValue');
    const unrealizedPnlValue = document.getElementById('unrealizedPnlValue');
    const drawdownValue = document.getElementById('drawdownValue');

    const activePositionsBadge = document.getElementById('activePositionsBadge');
    const positionsTableBody = document.getElementById('positionsTableBody');
    const terminalLogView = document.getElementById('terminalLogView');
    const clearLogsBtn = document.getElementById('clearLogsBtn');

    // Settings Modal Elements
    const settingsModal = document.getElementById('settingsModal');
    const closeSettingsBtn = document.getElementById('closeSettingsBtn');
    const cancelSettingsBtn = document.getElementById('cancelSettingsBtn');
    const settingsForm = document.getElementById('settingsForm');
    const apiKeyInput = document.getElementById('apiKey');
    const secretKeyInput = document.getElementById('secretKey');
    const paperModeCheck = document.getElementById('paperModeCheck');
    const maxDailyLossInput = document.getElementById('maxDailyLoss');
    const maxDrawdownInput = document.getElementById('maxDrawdown');
    const timeframeInput = document.getElementById('timeframe');
    const hostInput = document.getElementById('host');
    const portInput = document.getElementById('port');
    const openBrowserInput = document.getElementById('openBrowser');
    const logLevelInput = document.getElementById('logLevel');

    let isPaperMode = true;
    let accountSuffix = '';
    let equityHistory = [];
    let timeHistory = [];
    let dataWs = null;
    let logWs = null;
    let dataReconnectAttempts = 0;
    let logReconnectAttempts = 0;

    // Initialize Chart.js
    const ctx = document.getElementById('equityChart').getContext('2d');
    const equityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: timeHistory,
            datasets: [{
                label: 'Net Portföy (Equity)',
                data: equityHistory,
                borderColor: '#6366F1',
                backgroundColor: 'rgba(99, 102, 241, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.4,
                pointRadius: 3,
                pointBackgroundColor: '#38BDF8'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: { color: '#94A3B8', font: { size: 10 } }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: {
                        color: '#94A3B8',
                        font: { size: 10 },
                        callback: (value) => '$' + value.toLocaleString()
                    }
                }
            }
        }
    });

    // Formatting Helpers
    const formatCurrency = (val) => {
        if (val === undefined || val === null) return '$0.00';
        return (val >= 0 ? '$' : '-$') + Math.abs(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    };

    const formatPnl = (val) => {
        if (val === undefined || val === null) return '$0.00';
        const sign = val > 0 ? '+$' : (val < 0 ? '-$' : '$');
        return sign + Math.abs(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    };

    // Update UI Status
    const updateEngineStateUI = (state, paper) => {
        isPaperMode = paper;

        // Mode Chip
        if (paper) {
            modeChip.className = 'mode-chip paper';
            modeIcon.textContent = '📜';
            modeText.textContent = `PAPER DEMO${accountSuffix ? ` · …${accountSuffix}` : ''}`;
        } else {
            modeChip.className = 'mode-chip real';
            modeIcon.textContent = '⚠️';
            modeText.textContent = `REAL GERÇEK PARA${accountSuffix ? ` · …${accountSuffix}` : ''}`;
        }

        // Status Dot & Text
        statusDot.className = 'status-dot ' + state;
        const stateLabels = {
            'running': 'Çalışıyor (Canlı)',
            'stopped': 'Durdu',
            'starting': 'Başlatılıyor...',
            'error': 'Hata'
        };
        statusText.textContent = stateLabels[state] || state;

        // Buttons state
        if (state === 'running') {
            startBtn.disabled = true;
            stopBtn.disabled = false;
        } else if (state === 'stopped' || state === 'error') {
            startBtn.disabled = false;
            stopBtn.disabled = true;
        } else if (state === 'starting') {
            startBtn.disabled = true;
            stopBtn.disabled = false;
        }
    };

    // Update Account Snapshot
    const updateAccountUI = (data) => {
        if (!data) return;
        accountSuffix = data.account_suffix || accountSuffix;

        equityValue.textContent = formatCurrency(data.equity);
        cashValue.textContent = formatCurrency(data.cash);

        // Daily PnL
        dailyPnlValue.textContent = formatPnl(data.daily_pnl);
        dailyPnlValue.className = 'card-value ' + (data.daily_pnl > 0 ? 'positive' : (data.daily_pnl < 0 ? 'negative' : ''));

        // Realized PnL
        realizedPnlValue.textContent = formatPnl(data.realized_pnl);
        realizedPnlValue.className = 'card-value ' + (data.realized_pnl > 0 ? 'positive' : (data.realized_pnl < 0 ? 'negative' : ''));

        // Unrealized PnL
        unrealizedPnlValue.textContent = formatPnl(data.unrealized_pnl);
        unrealizedPnlValue.className = 'card-value ' + (data.unrealized_pnl > 0 ? 'positive' : (data.unrealized_pnl < 0 ? 'negative' : ''));

        drawdownValue.textContent = formatCurrency(data.drawdown);

        // Update Positions Table
        const positions = data.positions || [];
        activePositionsBadge.textContent = `${positions.length} Açık`;

        if (positions.length === 0) {
            positionsTableBody.innerHTML = `<tr><td colspan="5" class="empty-state">Henüz açık pozisyon bulunmuyor</td></tr>`;
        } else {
            positionsTableBody.innerHTML = positions.map(p => `
                <tr>
                    <td><strong>${p.symbol}</strong>${p.ownership === 'external' ? ' <small>(external)</small>' : ''}</td>
                    <td><span class="${p.direction === 'LONG' ? 'badge-long' : 'badge-short'}">${p.direction}</span></td>
                    <td>${p.qty}</td>
                    <td>$${p.avg_price.toFixed(2)}</td>
                    <td class="${p.unrealized_pnl > 0 ? 'positive' : (p.unrealized_pnl < 0 ? 'negative' : '')}">
                        ${formatPnl(p.unrealized_pnl)}
                    </td>
                </tr>
            `).join('');
        }

        // Push to Chart
        if (data.equity > 0) {
            const nowTime = new Date().toLocaleTimeString();
            timeHistory.push(nowTime);
            equityHistory.push(data.equity);

            if (timeHistory.length > 30) {
                timeHistory.shift();
                equityHistory.shift();
            }

            equityChart.update();
        }
    };

    // Log Streamer
    const appendLog = (msg, level = 'info') => {
        const line = document.createElement('div');
        line.className = `log-line ${level.toLowerCase()}`;
        const timeStr = new Date().toLocaleTimeString();
        const timeSpan = document.createElement('span');
        timeSpan.className = 'log-time';
        timeSpan.textContent = `[${timeStr}]`;
        const messageSpan = document.createElement('span');
        messageSpan.className = 'log-msg';
        messageSpan.textContent = ` ${msg}`;
        line.append(timeSpan, messageSpan);
        terminalLogView.appendChild(line);
        terminalLogView.scrollTop = terminalLogView.scrollHeight;
    };

    clearLogsBtn.addEventListener('click', () => {
        terminalLogView.innerHTML = '';
    });

    // WebSocket Stream Connection
    const initWebSockets = () => {
        const loc = window.location;
        const wsProtocol = loc.protocol === 'https:' ? 'wss:' : 'ws:';

        const reconnectDelay = (attempt) => {
            const base = Math.min(30000, 1000 * (2 ** Math.min(attempt, 5)));
            return base * (0.8 + Math.random() * 0.4);
        };

        const websocketTicket = async (kind) => {
            const response = await controlFetch('/api/ws-ticket', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ kind })
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || 'WebSocket ticket alınamadı');
            return payload.ticket;
        };

        const connectData = async () => {
            if (dataWs && [WebSocket.OPEN, WebSocket.CONNECTING].includes(dataWs.readyState)) return;
            let ticket;
            try {
                ticket = await websocketTicket('stream');
            } catch (_error) {
                setTimeout(connectData, reconnectDelay(dataReconnectAttempts++));
                return;
            }
            dataWs = new WebSocket(`${wsProtocol}//${loc.host}/ws/stream?ticket=${encodeURIComponent(ticket)}`);
            dataWs.onopen = () => { dataReconnectAttempts = 0; };
            dataWs.onmessage = (event) => {
                try {
                    const payload = JSON.parse(event.data);
                    if (payload.account) updateAccountUI(payload.account);
                    if (payload.status) updateEngineStateUI(payload.status.state, payload.status.paper);
                } catch (e) {
                    console.error("Data WS error", e);
                }
            };
            dataWs.onclose = () => {
                dataWs = null;
                setTimeout(connectData, reconnectDelay(dataReconnectAttempts++));
            };
        };

        const connectLogs = async () => {
            if (logWs && [WebSocket.OPEN, WebSocket.CONNECTING].includes(logWs.readyState)) return;
            let ticket;
            try {
                ticket = await websocketTicket('logs');
            } catch (_error) {
                setTimeout(connectLogs, reconnectDelay(logReconnectAttempts++));
                return;
            }
            logWs = new WebSocket(`${wsProtocol}//${loc.host}/ws/logs?ticket=${encodeURIComponent(ticket)}`);
            logWs.onopen = () => { logReconnectAttempts = 0; };
            logWs.onmessage = (event) => {
                try {
                    const logData = JSON.parse(event.data);
                    appendLog(logData.message, logData.level || 'info');
                } catch (e) {
                    console.error("Log WS error", e);
                }
            };
            logWs.onclose = () => {
                logWs = null;
                setTimeout(connectLogs, reconnectDelay(logReconnectAttempts++));
            };
        };

        connectData();
        connectLogs();
    };

    // Button Listeners
    startBtn.addEventListener('click', async () => {
        try {
            const res = await controlFetch('/api/engine/start', { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Motor başlatılamadı');
            appendLog("Motor başlatma isteği gönderildi.", "info");
        } catch (err) {
            alert("Hata: " + err.message);
        }
    });

    stopBtn.addEventListener('click', async () => {
        try {
            const res = await controlFetch('/api/engine/stop', { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Motor durdurulamadı');
            appendLog("Motor durduruldu.", "warning");
        } catch (err) {
            alert("Hata: " + err.message);
        }
    });

    modeChip.addEventListener('click', async () => {
        const nextMode = !isPaperMode;
        if (!nextMode) {
            try {
                const requestRes = await controlFetch('/api/live/arming/request', {
                    method: 'POST'
                });
                const requestData = await requestRes.json();
                if (!requestRes.ok) throw new Error(requestData.detail || 'Live doğrulama başlatılamadı');
                const phrase = window.prompt(
                    `Hesap …${requestData.account_suffix} için LIVE ve son dört haneyi yazarak doğrulayın:`,
                    ''
                );
                if (!phrase) return;
                const confirmRes = await controlFetch('/api/live/arming/confirm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ phrase })
                });
                const confirmData = await confirmRes.json();
                if (!confirmRes.ok) throw new Error(confirmData.detail || 'Live doğrulama başarısız');
            } catch (err) {
                alert("Hata: " + err.message);
                return;
            }
        }

        try {
            const res = await controlFetch('/api/mode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paper: nextMode })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Mod değiştirilemedi');
            updateEngineStateUI(data.state || 'stopped', nextMode);
            appendLog(`İşlem modu değiştirildi: ${nextMode ? 'PAPER (Demo)' : 'REAL (Gerçek Para)'}`, "info");
        } catch (err) {
            alert("Hata: " + err.message);
        }
    });

    // Settings Modal
    const openSettings = async () => {
        try {
            const res = await fetch('/api/settings');
            const data = await res.json();
            apiKeyInput.value = data.api_key || '';
            secretKeyInput.value = data.secret_key || '';
            if (data.has_credentials) {
                apiKeyInput.placeholder = 'Mevcut anahtar korunacak';
                secretKeyInput.placeholder = 'Mevcut secret korunacak';
            }
            paperModeCheck.checked = data.paper_mode;
            maxDailyLossInput.value = data.daily_loss_limit_pct || 1.5;
            maxDrawdownInput.value = data.max_drawdown_pct || 10;
            timeframeInput.value = data.timeframe || '5m';
            hostInput.value = data.host || '127.0.0.1';
            portInput.value = data.port || 8000;
            openBrowserInput.checked = data.open_browser !== false;
            logLevelInput.value = data.log_level || 'INFO';
            settingsModal.classList.add('active');
        } catch (err) {
            alert("Ayarlar yüklenemedi: " + err.message);
        }
    };

    settingsBtn.addEventListener('click', openSettings);
    closeSettingsBtn.addEventListener('click', () => settingsModal.classList.remove('active'));
    cancelSettingsBtn.addEventListener('click', () => settingsModal.classList.remove('active'));

    settingsForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const submitBtn = settingsForm.querySelector('button[type="submit"]');
        const payload = {
            api_key: apiKeyInput.value.trim(),
            secret_key: secretKeyInput.value.trim(),
            paper_mode: paperModeCheck.checked,
            daily_loss_limit_pct: parseFloat(maxDailyLossInput.value),
            max_drawdown_pct: parseFloat(maxDrawdownInput.value),
            timeframe: timeframeInput.value,
            host: hostInput.value.trim(),
            port: parseInt(portInput.value, 10),
            open_browser: openBrowserInput.checked,
            log_level: logLevelInput.value
        };

        try {
            submitBtn.disabled = true;
            submitBtn.textContent = 'Kaydediliyor...';
            const res = await controlFetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Ayarlar kaydedilemedi');
            settingsModal.classList.remove('active');
            if (data.engine_restarted) {
                appendLog("Ayarlar uygulandı; işlem motoru yeni ayarlarla yeniden başlatıldı.", "info");
            } else {
                appendLog("Ayarlar ve API bilgileri güncellendi.", "info");
            }
            if (data.app_restart_required) {
                appendLog("Web adresi/portu, tarayıcı veya log seviyesi değişikliği bir sonraki uygulama açılışında etkinleşecek.", "warning");
            }
        } catch (err) {
            alert("Ayarlar kaydedilemedi: " + err.message);
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Kaydet & Uygula';
        }
    });

    // Initial Fetch & Start WebSockets
    const init = async () => {
        try {
            const statusRes = await fetch('/api/status');
            const statusData = await statusRes.json();
            updateEngineStateUI(statusData.state, statusData.paper);

            const accountRes = await fetch('/api/account');
            const accountData = await accountRes.json();
            updateAccountUI(accountData);

            initWebSockets();
        } catch (err) {
            console.error("Initialization error:", err);
        }
    };

    init();
});
