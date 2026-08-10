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

    const responsePayload = async (response) => {
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) return response.json();
        const message = (await response.text()).trim();
        return { detail: message || `Sunucu hatası (${response.status})` };
    };
    // DOM Elements
    const modeChip = document.getElementById('modeChip');
    const modeIcon = document.getElementById('modeIcon');
    const modeText = document.getElementById('modeText');

    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    const marketStatusText = document.getElementById('marketStatusText');
    const operationsDetail = document.getElementById('operationsDetail');

    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const resetSafetyBtn = document.getElementById('resetSafetyBtn');
    const settingsBtn = document.getElementById('settingsBtn');

    // Stat Values
    const equityValue = document.getElementById('equityValue');
    const cashValue = document.getElementById('cashValue');
    const dailyPnlValue = document.getElementById('dailyPnlValue');
    const realizedPnlValue = document.getElementById('realizedPnlValue');
    const feesValue = document.getElementById('feesValue');
    const unrealizedPnlValue = document.getElementById('unrealizedPnlValue');
    const drawdownValue = document.getElementById('drawdownValue');

    const activePositionsBadge = document.getElementById('activePositionsBadge');
    const positionsTableBody = document.getElementById('positionsTableBody');
    const terminalLogView = document.getElementById('terminalLogView');
    const clearLogsBtn = document.getElementById('clearLogsBtn');

    // Backtest Panel Elements
    const backtestRunBtn = document.getElementById('backtestRunBtn');
    const backtestStateBadge = document.getElementById('backtestStateBadge');
    const backtestSummary = document.getElementById('backtestSummary');
    const backtestProgress = document.getElementById('backtestProgress');
    const btStrategy = document.getElementById('btStrategy');
    const btMarket = document.getElementById('btMarket');
    const btYears = document.getElementById('btYears');
    const btDays = document.getElementById('btDays');
    const btMaxSymbols = document.getElementById('btMaxSymbols');
    const btSymbols = document.getElementById('btSymbols');
    const btParameters = document.getElementById('btParameters');

    // Settings Modal Elements
    const settingsModal = document.getElementById('settingsModal');
    const closeSettingsBtn = document.getElementById('closeSettingsBtn');
    const cancelSettingsBtn = document.getElementById('cancelSettingsBtn');
    const settingsForm = document.getElementById('settingsForm');
    const apiKeyInput = document.getElementById('apiKey');
    const secretKeyInput = document.getElementById('secretKey');
    const paperModeCheck = document.getElementById('paperModeCheck');
    const equityTradingCheck = document.getElementById('equityTradingCheck');
    const cryptoTradingCheck = document.getElementById('cryptoTradingCheck');
    const liveTradingCheck = document.getElementById('liveTradingCheck');
    const maxDailyLossInput = document.getElementById('maxDailyLoss');
    const maxDrawdownInput = document.getElementById('maxDrawdown');
    const timeframeInput = document.getElementById('timeframe');
    const hostInput = document.getElementById('host');
    const portInput = document.getElementById('port');
    const openBrowserInput = document.getElementById('openBrowser');
    const logLevelInput = document.getElementById('logLevel');

    let isPaperMode = true;
    let accountSuffix = '';
    let equityHistoryScope = 'paper:unknown';
    const equityHistoryKey = (scope) => `parayabuck-equity-history-v2:${scope}`;
    const loadEquityHistory = (scope) => {
        try {
            const stored = JSON.parse(sessionStorage.getItem(equityHistoryKey(scope)) || '{}');
            const values = Array.isArray(stored.values) ? stored.values.map(Number) : [];
            const times = Array.isArray(stored.times) ? stored.times.map(String) : [];
            const snapshots = Array.isArray(stored.snapshots) ? stored.snapshots.map(String) : [];
            if (
                values.length === times.length
                && values.length === snapshots.length
                && values.every(Number.isFinite)
            ) {
                return {
                    values: values.slice(-30),
                    times: times.slice(-30),
                    snapshots: snapshots.slice(-30)
                };
            }
        } catch (_) {
            // A corrupt browser cache should never prevent the terminal loading.
        }
        return { values: [], times: [], snapshots: [] };
    };
    const storedEquityHistory = loadEquityHistory(equityHistoryScope);
    let equityHistory = storedEquityHistory.values;
    let timeHistory = storedEquityHistory.times;
    let snapshotHistory = storedEquityHistory.snapshots;
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
            modeIcon.textContent = '⌄';
            modeText.textContent = `Paper demo${accountSuffix ? ` · …${accountSuffix}` : ''}`;
        } else {
            modeChip.className = 'mode-chip real';
            modeIcon.textContent = '⌄';
            modeText.textContent = `Gerçek para${accountSuffix ? ` · …${accountSuffix}` : ''}`;
        }

        // Status Dot & Text
        statusDot.className = 'status-dot ' + state;
        const stateLabels = {
            'running': 'Çalışıyor (Canlı)',
            'stopped': 'Durdu',
            'starting': 'Başlatılıyor...',
            'warming_up': 'Veriler hazırlanıyor...',
            'degraded': 'Kısıtlı çalışıyor',
            'halted': 'Güvenlik nedeniyle durdu',
            'stopping': 'Durduruluyor...',
            'stop_failed': 'Durdurma hatası',
            'error': 'Hata'
        };
        statusText.textContent = stateLabels[state] || state;
        resetSafetyBtn.hidden = state !== 'halted' && state !== 'degraded';

        // Buttons state
        if (['running', 'warming_up', 'degraded', 'halted'].includes(state)) {
            startBtn.disabled = true;
            stopBtn.disabled = false;
        } else if (state === 'stopped' || state === 'error') {
            startBtn.disabled = false;
            stopBtn.disabled = true;
        } else if (state === 'starting' || state === 'stopping') {
            startBtn.disabled = true;
            stopBtn.disabled = state === 'stopping';
        } else if (state === 'stop_failed') {
            startBtn.disabled = true;
            stopBtn.disabled = true;
        }
    };

    const renderSystemStatus = (statusData) => {
        updateEngineStateUI(statusData.state, statusData.paper);
        const hasRuntime = Boolean(statusData.runtime);
        const markets = (statusData.runtime && statusData.runtime.markets) || {};
        const equity = markets.equity || {};
        const crypto = markets.crypto || {};
        const marketLabel = (market) => {
            if (!hasRuntime) {
                if (statusData.state === 'starting') return 'başlatılıyor';
                if (statusData.state === 'error') return 'hata';
                if (statusData.state === 'stopped') return 'kapalı';
                return 'durum bekleniyor';
            }
            if (market.entry_halt_reason) return `halt: ${market.entry_halt_reason}`;
            if (market.stream_healthy === false) return 'stream hatası';
            if (market.ready === false) return 'hazır değil';
            if (
                Number.isFinite(market.ready_symbol_count)
                && Number.isFinite(market.symbol_count)
                && market.ready_symbol_count < market.symbol_count
            ) {
                return `${market.ready_symbol_count}/${market.symbol_count} hazır`;
            }
            return 'hazır';
        };
        const globalHealth = hasRuntime
            ? `${statusData.runtime.state}${statusData.runtime.healthy ? '' : ' (degraded)'}`
            : statusData.state;
        const strategyStatus = statusData.strategy_status || '';
        const strategyLabel = strategyStatus.startsWith('configured:')
            ? `STRATEJİ ${strategyStatus.slice('configured:'.length)}`
            : strategyStatus === 'configuration_error'
              ? 'STRATEJİ AYAR HATASI'
              : strategyStatus || 'STRATEJİ DEVRE DIŞI';
        const scanner = statusData.scanner || {};
        const equityScanner = scanner.equity_status || scanner.status || 'not_run';
        const cryptoScanner = scanner.crypto_status || 'not_run';
        marketStatusText.textContent =
            `Global ${globalHealth} | ` +
            `${strategyLabel} | ` +
            `Equity ${statusData.active_equity_symbol_count ?? 0} · ` +
            `${marketLabel(equity)} | ` +
            `Crypto ${statusData.active_crypto_symbol_count ?? 0} · ` +
            `${marketLabel(crypto)} | ` +
            `Aday ${statusData.candidate_universe_size ?? 0} · ` +
            `Scanner E ${equityScanner} · C ${cryptoScanner}` +
            (statusData.engine_error ? ` | Hata: ${statusData.engine_error}` : '');
        const universe = statusData.universe || {};
        const operations = statusData.operations || {};
        operationsDetail.textContent = JSON.stringify({
            mode: statusData.paper ? 'PAPER' : 'LIVE',
            strategies: statusData.runtime?.strategies || [],
            active_universe: {
                equity: universe.equity?.symbols || [],
                crypto: universe.crypto?.symbols || []
            },
            universe_scores: {
                equity: universe.equity?.scores || {},
                crypto: universe.crypto?.scores || {}
            },
            pending_orders: operations.pending_orders || [],
            recent_fills: operations.recent_fills || [],
            last_decision: operations.last_decision || null,
            last_scanner_refresh: scanner.last_run_at || null,
            next_scanner_refresh: scanner.next_refresh_at || null,
            scanner_status: {
                equity: scanner.equity_status || null,
                crypto: scanner.crypto_status || null,
                snapshot_equity: scanner.equity_snapshot_status || null,
                snapshot_crypto: scanner.crypto_snapshot_status || null,
                errors: scanner.refresh_errors || {}
            },
            scanner_counts: {
                equity_candidates: scanner.equity_candidate_count || 0,
                equity_shortlist: scanner.equity_shortlist_count || 0,
                crypto_candidates: scanner.crypto_candidate_count || 0
            },
            warming_symbols: {
                equity: equity.warming_symbols || [],
                crypto: crypto.warming_symbols || []
            },
            managed_pins: {
                equity: equity.managed_pins || [],
                crypto: crypto.managed_pins || []
            },
            last_reconciliation: statusData.runtime?.last_reconciliation_at || null,
            risk_status: statusData.runtime?.circuit_breaker_reason || 'ready'
        }, null, 2);
    };

    let statusRefreshInFlight = false;
    const refreshSystemStatus = async () => {
        if (statusRefreshInFlight) return;
        statusRefreshInFlight = true;
        try {
            const statusRes = await fetch('/api/status');
            if (!statusRes.ok) throw new Error(`Durum alınamadı (${statusRes.status})`);
            renderSystemStatus(await statusRes.json());
        } catch (err) {
            console.error("Status refresh error:", err);
        } finally {
            statusRefreshInFlight = false;
        }
    };

    // Update Account Snapshot
    const updateAccountUI = (data) => {
        if (!data) return;
        accountSuffix = data.account_suffix || accountSuffix;
        const nextHistoryScope = `${isPaperMode ? 'paper' : 'live'}:${accountSuffix || 'unknown'}`;
        if (nextHistoryScope !== equityHistoryScope) {
            equityHistoryScope = nextHistoryScope;
            const stored = loadEquityHistory(equityHistoryScope);
            equityHistory = stored.values;
            timeHistory = stored.times;
            snapshotHistory = stored.snapshots;
            equityChart.data.labels = timeHistory;
            equityChart.data.datasets[0].data = equityHistory;
            equityChart.update();
        }

        equityValue.textContent = formatCurrency(data.equity);
        cashValue.textContent = formatCurrency(data.cash);

        // Daily PnL
        dailyPnlValue.textContent = formatPnl(data.daily_pnl);
        dailyPnlValue.className = 'metric-value ' + (data.daily_pnl > 0 ? 'positive' : (data.daily_pnl < 0 ? 'negative' : ''));

        // Realized PnL
        realizedPnlValue.textContent = formatPnl(data.realized_pnl);
        realizedPnlValue.className = 'metric-value ' + (data.realized_pnl > 0 ? 'positive' : (data.realized_pnl < 0 ? 'negative' : ''));

        // Unrealized PnL
        unrealizedPnlValue.textContent = formatPnl(data.unrealized_pnl);
        feesValue.textContent = formatPnl(-(data.fees || 0));
        unrealizedPnlValue.className = 'metric-value ' + (data.unrealized_pnl > 0 ? 'positive' : (data.unrealized_pnl < 0 ? 'negative' : ''));

        drawdownValue.textContent = formatCurrency(data.drawdown);

        // Update Positions Table
        const positions = data.positions || [];
        activePositionsBadge.textContent = `${positions.length} açık`;

        if (positions.length === 0) {
            positionsTableBody.innerHTML = `<tr><td colspan="9" class="empty-state"><span class="empty-icon">—</span>Henüz açık pozisyon bulunmuyor</td></tr>`;
        } else {
            const rows = positions.map((position) => {
                const row = document.createElement('tr');
                const ownershipTag = position.ownership_state === 'managed'
                    ? ' (M)'
                    : position.ownership_state === 'mixed'
                        ? ' (M+E)'
                        : ' (external)';
                const qtyLabel = position.managed_qty != null && position.external_qty != null
                    ? `${position.total_qty} = ${position.managed_qty}M + ${position.external_qty}E`
                    : String(position.qty);
                const values = [
                    `${position.symbol}${ownershipTag}`,
                    position.market || '-',
                    position.direction || '-',
                    qtyLabel,
                    `$${Number(position.avg_price).toFixed(2)}`,
                    formatPnl(position.unrealized_pnl),
                    position.manual_intervention_required
                        ? 'MANUAL'
                        : (position.protection_state || '-'),
                    `${position.stop_price ? '$' + Number(position.stop_price).toFixed(4) : '-'} / ${position.target_price ? '$' + Number(position.target_price).toFixed(4) : '-'}`,
                    `${position.stop_order_state || '-'} / ${position.target_exit_state || '-'}`
                ];
                values.forEach((value, index) => {
                    const cell = document.createElement('td');
                    cell.textContent = String(value);
                    if (index === 0) cell.className = 'position-symbol';
                    if (index === 2) {
                        cell.className = position.direction === 'LONG' ? 'badge-long' : 'badge-short';
                    }
                    if (index === 5) {
                        cell.className = position.unrealized_pnl > 0
                            ? 'positive'
                            : (position.unrealized_pnl < 0 ? 'negative' : '');
                    }
                    if (index === 6 && position.manual_intervention_required) {
                        cell.className = 'negative';
                    }
                    row.appendChild(cell);
                });
                return row;
            });
            positionsTableBody.replaceChildren(...rows);
        }

        // Push to Chart
        if (data.equity > 0) {
            const snapshotId = data.snapshot_at || new Date().toISOString();
            if (snapshotHistory[snapshotHistory.length - 1] === snapshotId) {
                return;
            }
            const snapshotDate = new Date(snapshotId);
            const nowTime = Number.isNaN(snapshotDate.getTime())
                ? new Date().toLocaleTimeString()
                : snapshotDate.toLocaleTimeString();
            timeHistory.push(nowTime);
            equityHistory.push(data.equity);
            snapshotHistory.push(snapshotId);

            if (timeHistory.length > 30) {
                timeHistory.shift();
                equityHistory.shift();
                snapshotHistory.shift();
            }

            sessionStorage.setItem(equityHistoryKey(equityHistoryScope), JSON.stringify({
                times: timeHistory,
                values: equityHistory,
                snapshots: snapshotHistory
            }));
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
        while (terminalLogView.children.length > 500) {
            terminalLogView.firstElementChild.remove();
        }
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
            const data = await responsePayload(res);
            if (!res.ok) throw new Error(data.detail || 'Motor durdurulamadı');
            appendLog("Motor durduruldu.", "warning");
        } catch (err) {
            alert("Hata: " + err.message);
        }
    });

    resetSafetyBtn.addEventListener('click', async () => {
        if (!confirm('Broker koruması ve veri bütünlüğü doğrulandıktan sonra kilit sıfırlansın mı?')) {
            return;
        }
        try {
            resetSafetyBtn.disabled = true;
            const res = await controlFetch('/api/engine/reset-safety-lock', { method: 'POST' });
            const data = await responsePayload(res);
            if (!res.ok) throw new Error(data.detail || 'Güvenlik kilidi sıfırlanamadı');
            appendLog('Güvenlik kilidi doğrulama sonrası sıfırlandı.', 'warning');
            await refreshSystemStatus();
        } catch (err) {
            alert('Hata: ' + err.message);
        } finally {
            resetSafetyBtn.disabled = false;
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
    modeChip.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            modeChip.click();
        }
    });

    // Settings Modal
    const setSettingsOpen = (open) => {
        settingsModal.classList.toggle('active', open);
        settingsModal.setAttribute('aria-hidden', String(!open));
        document.body.classList.toggle('modal-open', open);
        settingsBtn.setAttribute('aria-expanded', String(open));
        if (open) {
            window.setTimeout(() => closeSettingsBtn.focus(), 180);
        } else {
            settingsBtn.focus();
        }
    };

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
            equityTradingCheck.checked = data.equity_trading_enabled !== false;
            cryptoTradingCheck.checked = data.crypto_trading_enabled === true;
            liveTradingCheck.checked = data.live_trading_enabled === true;
            maxDailyLossInput.value = data.daily_loss_limit_pct || 1.5;
            maxDrawdownInput.value = data.max_drawdown_pct || 10;
            timeframeInput.value = data.timeframe || '5m';
            hostInput.value = data.host || '127.0.0.1';
            portInput.value = data.port || 8000;
            openBrowserInput.checked = data.open_browser !== false;
            logLevelInput.value = data.log_level || 'INFO';
            setSettingsOpen(true);
        } catch (err) {
            alert("Ayarlar yüklenemedi: " + err.message);
        }
    };

    settingsBtn.addEventListener('click', openSettings);
    closeSettingsBtn.addEventListener('click', () => setSettingsOpen(false));
    cancelSettingsBtn.addEventListener('click', () => setSettingsOpen(false));
    settingsModal.addEventListener('click', (event) => {
        if (event.target === settingsModal) setSettingsOpen(false);
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && settingsModal.classList.contains('active')) {
            setSettingsOpen(false);
        }
    });

    settingsForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const submitBtn = settingsForm.querySelector('button[type="submit"]');
        const payload = {
            api_key: apiKeyInput.value.trim(),
            secret_key: secretKeyInput.value.trim(),
            paper_mode: paperModeCheck.checked,
            equity_trading_enabled: equityTradingCheck.checked,
            crypto_trading_enabled: cryptoTradingCheck.checked,
            live_trading_enabled: liveTradingCheck.checked,
            daily_loss_limit_pct: parseFloat(maxDailyLossInput.value),
            max_drawdown_pct: parseFloat(maxDrawdownInput.value),
            execution_timeframe: timeframeInput.value,
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
            setSettingsOpen(false);
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
            submitBtn.textContent = 'Kaydet ve uygula';
        }
    });

    // Backtest (tek tık)
    const backtestStateLabels = {
        idle: 'beklemede',
        running: 'çalışıyor',
        done: 'tamamlandı',
        error: 'hata'
    };
    const renderBacktestSummary = (summary) => {
        if (!summary) {
            backtestSummary.hidden = true;
            return;
        }
        const pct = (key) => {
            const value = Number(summary[key] || 0);
            return (value * 100).toFixed(2) + '%';
        };
        backtestSummary.hidden = false;
        backtestSummary.innerHTML =
            `<div class="backtest-summary-grid">
                <span><strong>Getiri</strong>${pct('total_return')}</span>
                <span><strong>CAGR</strong>${pct('cagr')}</span>
                <span><strong>Benchmark</strong>${pct('benchmark_return')} (${summary.benchmark_symbol || '-'})</span>
                <span><strong>MaxDD</strong>${pct('max_drawdown')}</span>
                <span><strong>Sharpe</strong>${Number(summary.sharpe || 0).toFixed(2)}</span>
                <span><strong>İşlem</strong>${summary.total_trades || 0}</span>
                <span><strong>Kazanma</strong>${pct('win_rate')}</span>
                <span><strong>Bitiş</strong>$${Number(summary.ending_equity || 0).toLocaleString()}</span>
                <span><strong>Periyot</strong>${summary.start_date ? summary.start_date.slice(0, 10) : '-'} → ${summary.end_date ? summary.end_date.slice(0, 10) : '-'}</span>
                <span><strong>Kapanış</strong>${summary.open_positions > 0 ? summary.open_positions + ' açık' : 'hepsi realize'}</span>
            </div>`;
    };
    const renderBacktestProgress = (lines) => {
        const container = backtestProgress;
        container.innerHTML = '';
        for (const line of lines) {
            const div = document.createElement('div');
            div.className = 'log-line info';
            const message = document.createElement('span');
            message.className = 'log-msg';
            message.textContent = line;
            div.appendChild(message);
            container.appendChild(div);
        }
        container.scrollTop = container.scrollHeight;
    };
    const refreshBacktestStatus = async () => {
        let response;
        try {
            response = await fetch('/api/backtest/status');
        } catch (_) {
            return;
        }
        if (!response.ok) return;
        const data = await response.json();
        backtestStateBadge.textContent = backtestStateLabels[data.state] || data.state;
        backtestRunBtn.disabled = data.state === 'running';
        renderBacktestProgress(data.progress || []);
        if (data.state === 'done' || data.state === 'error') {
            renderBacktestSummary(data.summary);
            if (data.error) {
                backtestSummary.hidden = false;
                backtestSummary.innerHTML = `<span class="negative">Hata: ${data.error}</span>`;
            }
            appendLog(`Backtest ${data.state}: reports/${data.run_id}`, data.state === 'error' ? 'warning' : 'info');
        } else if (data.state === 'running') {
            backtestSummary.hidden = true;
        }
    };

    backtestRunBtn.addEventListener('click', async () => {
        const daysValue = parseInt(btDays.value, 10);
        let parameters = {};
        if (btParameters.value.trim()) {
            try {
                parameters = JSON.parse(btParameters.value);
            } catch (_) {
                alert('Parametreler geçerli bir JSON nesnesi olmalıdır.');
                return;
            }
            if (!parameters || Array.isArray(parameters) || typeof parameters !== 'object') {
                alert('Parametreler bir JSON nesnesi olmalıdır.');
                return;
            }
        }
        const payload = {
            market: btMarket.value,
            strategy: btStrategy.value.trim(),
            parameters,
            years: parseInt(btYears.value, 10) || 3,
            days: Number.isNaN(daysValue) ? 90 : daysValue,
            max_symbols: parseInt(btMaxSymbols.value, 10) || 0,
            symbols: btSymbols.value.trim()
        };
        try {
            const res = await controlFetch('/api/backtest/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await responsePayload(res);
            if (!res.ok) throw new Error(data.detail || 'Backtest başlatılamadı');
            appendLog(`Backtest başladı: ${data.run_id} (${payload.market}, ${payload.days || payload.years * 365} gün)`, 'info');
            await refreshBacktestStatus();
        } catch (err) {
            alert('Hata: ' + err.message);
        }
    });

    // Initial Fetch & Start WebSockets
    const init = async () => {
        try {
            await refreshSystemStatus();
            window.setInterval(refreshSystemStatus, 2000);
            initWebSockets();
            await refreshBacktestStatus();
            window.setInterval(refreshBacktestStatus, 3000);
            const accountRes = await fetch('/api/account');
            if (!accountRes.ok) throw new Error(`Hesap alınamadı (${accountRes.status})`);
            const accountData = await accountRes.json();
            updateAccountUI(accountData);
        } catch (err) {
            console.error("Initialization error:", err);
        }
    };

    init();
});
