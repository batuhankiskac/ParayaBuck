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

    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const resetSafetyBtn = document.getElementById('resetSafetyBtn');
    const riskEpochBtn = document.getElementById('riskEpochBtn');
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
    let dataReconnectAttempts = 0;

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
        // Risk-epoch reset is available while the engine is up: it is the
        // explicit recovery for a drawdown halt (rebases the peak baseline).
        riskEpochBtn.hidden = !['running', 'warming_up', 'degraded', 'halted'].includes(state);

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
        // Strategy metadata replaces any notion of a global strategy
        // timeframe: each market shows its own strategy, signal timeframe,
        // rebalance cadence and universe sizes.
        const strategyMeta = statusData.strategy_metadata || {};
        const fmtStrategy = (meta) => {
            if (!meta) return null;
            if (meta.configuration_error) return `${meta.name} (ayar hatası)`;
            const extra = [
                `Sinyal ${meta.signal_timeframe}`,
                `Rebalance ${meta.rebalance_cadence}`,
                meta.dynamic_universe_enabled ? 'Dinamik evren' : 'Statik evren',
                `Aday ${meta.candidate_pool_size ?? 0}`,
                `İzlenen ${meta.observation_set_size ?? 0}`,
            ];
            return `${meta.display_name || meta.name} · ${extra.join(' · ')}`;
        };
        const strategyMetaText = [
            strategyMeta.equity ? `E: ${fmtStrategy(strategyMeta.equity)}` : null,
            strategyMeta.crypto ? `C: ${fmtStrategy(strategyMeta.crypto)}` : null,
        ].filter(Boolean).join('  |  ');
        marketStatusText.textContent =
            `Global ${globalHealth} | ` +
            `${strategyLabel} | ` +
            `Equity ${statusData.active_equity_symbol_count ?? 0} · ` +
            `${marketLabel(equity)} | ` +
            `Crypto ${statusData.active_crypto_symbol_count ?? 0} · ` +
            `${marketLabel(crypto)} | ` +
            `Aday ${statusData.candidate_universe_size ?? 0} · ` +
            `Scanner E ${equityScanner} · C ${cryptoScanner}` +
            (strategyMetaText ? `\n${strategyMetaText}` : '') +
            (statusData.engine_error ? ` | Hata: ${statusData.engine_error}` : '');
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

    // Dashboard actions use a compact notification instead of the technical log console.
    const appendLog = (msg, level = 'info') => {
        const toast = document.getElementById('statusToast');
        if (!toast) return;
        toast.textContent = msg;
        toast.className = `status-toast ${level.toLowerCase()}`;
        toast.hidden = false;
        window.clearTimeout(appendLog.toastTimer);
        appendLog.toastTimer = window.setTimeout(() => { toast.hidden = true; }, 4500);
    };

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

        connectData();
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

    riskEpochBtn.addEventListener('click', async () => {
        if (!confirm('Drawdown tabanı doğrulanmış hesap özkaynağına çekilsin ve yeni risk dönemi başlasın mı? (Başka güvenlik kilitleri aktifse önce onları çözün)')) {
            return;
        }
        try {
            riskEpochBtn.disabled = true;
            const res = await controlFetch('/api/safety/risk-epoch', { method: 'POST' });
            const data = await responsePayload(res);
            if (!res.ok) throw new Error(data.detail || 'Risk dönemi başlatılamadı');
            const baseline = data.baseline_equity !== undefined
                ? ` (yeni taban: ${Number(data.baseline_equity).toLocaleString('tr-TR', { maximumFractionDigits: 2 })})`
                : '';
            appendLog('Yeni risk dönemi başlatıldı' + baseline + '.', 'warning');
            await refreshSystemStatus();
        } catch (err) {
            alert('Hata: ' + err.message);
        } finally {
            riskEpochBtn.disabled = false;
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
            refreshLivePrerequisites();
            setSettingsOpen(true);
        } catch (err) {
            alert("Ayarlar yüklenemedi: " + err.message);
        }
    };

    settingsBtn.addEventListener('click', openSettings);

    // Live arming prerequisites checklist (computed server-side without secrets)
    const livePrerequisitesBox = document.getElementById('livePrerequisites');
    const livePrerequisitesList = document.getElementById('livePrerequisitesList');
    const livePrerequisitesSummary = document.getElementById('livePrerequisitesSummary');

    const refreshLivePrerequisites = async () => {
        try {
            const response = await fetch('/api/live/prerequisites');
            if (!response.ok) throw new Error(`Ön koşullar alınamadı (${response.status})`);
            const data = await response.json();
            const items = Array.isArray(data.items) ? data.items : [];
            livePrerequisitesBox.hidden = false;
            livePrerequisitesList.innerHTML = '';
            for (const item of items) {
                const li = document.createElement('li');
                const mark = document.createElement('span');
                if (item.satisfied) {
                    mark.textContent = '✓ ';
                    mark.className = 'positive';
                } else if (item.state === 'unverifiable') {
                    mark.textContent = '? ';
                    mark.className = '';
                } else {
                    mark.textContent = '✗ ';
                    mark.className = 'negative';
                }
                li.appendChild(mark);
                const label = document.createElement('span');
                label.textContent = item.label;
                li.appendChild(label);
                if (!item.satisfied && item.missing) {
                    const detail = document.createElement('small');
                    detail.className = 'settings-note';
                    detail.textContent = ' — ' + item.missing;
                    li.appendChild(detail);
                }
                livePrerequisitesList.appendChild(li);
            }
            livePrerequisitesSummary.textContent = data.all_met
                ? 'Tüm ön koşullar sağlandı.'
                : 'Eksik ön koşullar tamamlanmadan canlı mod arm edilemez.';
        } catch (err) {
            livePrerequisitesBox.hidden = true;
        }
    };
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

    // ------------------------------------------------------------------
    // Trading dashboard (Phase 7): topline, strategy cards, risk,
    // execution, universe and system-health panels.
    // ------------------------------------------------------------------
    const STRATEGY_STATUS_LABELS = {
        active: 'Aktif',
        warming: 'Isınıyor',
        halted: 'Durdu (güvenlik)',
        scanner_issue: 'Tarayıcı sorunu',
        disabled: 'Devre dışı',
        stopped: 'Motor durdu',
        starting: 'Başlatılıyor',
        stopping: 'Durduruluyor',
        error: 'Hata',
        stop_failed: 'Durdurma hatası',
        unknown: 'Bilinmiyor',
        unavailable: 'Yok'
    };

    // Startup catch-up: explains WHY the bot may legitimately have "0 trades".
    // status comes from PortfolioRuntime.startup_catch_up (engine health);
    // blocked:* carries the runtime-level reason verbatim.
    const CATCH_UP_LABELS = {
        submitted: 'Uygulandı',
        sell_phase_submitted: 'Uygulandı (SELL fazı)',
        catch_up_superseded: 'Gerek yok: karar mevcut',
        duplicate_decision: 'Gerek yok: kopya önlendi',
        already_invested: 'Gerek yok: pozisyon var',
        warming_up: 'Bekliyor: ısınma',
        incomplete_strategy_batch: 'Bekliyor: eksik parti',
        rebalance_in_progress: 'Bekliyor: rebalance sürüyor',
        rebalance_blocked: 'Bekliyor: rebalance kilitli',
        no_eligible_bars: 'Bekliyor: bar yok',
        not_scheduled: 'Bekliyor: sıradaki periyot',
        not_attempted: 'Hazır değil'
    };
    const formatCatchUp = (catchUp) => {
        const status = (catchUp && catchUp.status) || 'not_attempted';
        if (status.startsWith('blocked:')) {
            return { label: `Engelli: ${status.slice('blocked:'.length)}`, tooltip: status };
        }
        const label = CATCH_UP_LABELS[status] || status || '—';
        const tooltip = catchUp && catchUp.at
            ? `Son deneme: ${fmtDateTime(catchUp.at)}`
            : (catchUp && catchUp.next_boundary_after
                ? `Sıradaki sınır: ${fmtDateTime(catchUp.next_boundary_after)}`
                : status || '');
        return { label, tooltip };
    };
    const fmtPct = (v) => (v === undefined || v === null || !Number.isFinite(Number(v)))
        ? '—' : Number(v).toFixed(1) + '%';
    const fmtNum = (v, digits = 2) => (v === undefined || v === null || !Number.isFinite(Number(v)))
        ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: digits });
    const fmtVol = (v) => (v === undefined || v === null || !Number.isFinite(Number(v)))
        ? '—' : (Number(v) * 100).toFixed(2) + '%';
    const fmtDateTime = (iso) => {
        if (!iso) return '—';
        const date = new Date(iso);
        return Number.isNaN(date.getTime()) ? iso : date.toLocaleString('tr-TR', { hour12: false });
    };
    const fmtDate = (iso) => {
        if (!iso) return '—';
        const date = new Date(iso);
        return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString('tr-TR');
    };

    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };

    const renderModeBadge = (mode) => {
        if (!mode) return;
        const badge = document.getElementById('modeBadge');
        if (!badge) return;
        badge.className = 'mode-badge ' + (mode.badge || 'paper');
        if (mode.badge === 'paper') badge.textContent = 'PAPER';
        else if (mode.badge === 'live') badge.textContent = 'LIVE';
        else badge.textContent = 'LIVE (devre dışı)';
        badge.title = `runtime.mode=${mode.runtime_mode || 'paper'} · live.enabled=${mode.live_enabled === true}`;
    };

    const renderStrategyCard = (card, ids, isCrypto) => {
        if (!card || card.unavailable) {
            setText(ids.name, 'Panel yok');
            return;
        }
        setText(ids.name, (card.strategy && card.strategy.display_name) || card.name || '—');
        const badge = document.getElementById(ids.badge);
        if (badge) {
            badge.className = 'strategy-chip ' + (card.status || 'unknown');
            badge.textContent = STRATEGY_STATUS_LABELS[card.status] || card.status || '—';
        }
        setText(ids.signal, (card.strategy && card.strategy.signal_timeframe) || '—');
        setText(ids.rebalance, (card.strategy && card.strategy.rebalance_cadence) || '—');
        if (ids.lookback) setText(ids.lookback, '22 gün');
        setText(ids.pool, card.eligible_pool === undefined || card.eligible_pool === null ? '—' : String(card.eligible_pool));
        setText(ids.obs, card.observation_set_size === undefined || card.observation_set_size === null ? '—' : String(card.observation_set_size));
        setText(ids.positions, String(card.positions_count ?? 0));
        setText(ids.allocation, `${fmtPct(card.current_allocation_pct)} / ${fmtPct(card.maximum_allocation_pct)}`);
        setText(ids.nextRebalance, fmtDateTime(card.next_rebalance_at));
        const catchUp = formatCatchUp(card.startup_catch_up);
        setText(ids.catchUp, catchUp.label);
        const catchUpEl = document.getElementById(ids.catchUp);
        if (catchUpEl) catchUpEl.title = catchUp.tooltip;
        renderHoldingsTable(card.holdings || [], ids.body, isCrypto);
    };

    const renderHoldingsTable = (holdings, bodyId, isCrypto) => {
        const tbody = document.getElementById(bodyId);
        if (!tbody) return;
        tbody.replaceChildren();
        if (!holdings.length) {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = isCrypto ? 7 : 5;
            cell.className = 'empty-state';
            const icon = document.createElement('span');
            icon.className = 'empty-icon';
            icon.textContent = '—';
            cell.append(icon, document.createTextNode('Pozisyon yok'));
            row.appendChild(cell);
            tbody.appendChild(row);
            return;
        }
        for (const holding of holdings) {
            const row = document.createElement('tr');
            const reason = holding.reason || holding.hypothesis || '—';
            const values = isCrypto
                ? [
                    holding.symbol,
                    fmtNum(holding.qty, 4),
                    fmtVol(holding.realized_vol_22d),
                    holding.rank ? `#${holding.rank}/${holding.of_qualified ?? '?'}` : '—',
                    fmtPct(holding.target_weight_pct),
                    fmtPct(holding.current_weight_pct),
                    reason
                ]
                : [
                    holding.symbol,
                    fmtNum(holding.qty, 4),
                    fmtPct(holding.target_weight_pct),
                    fmtPct(holding.current_weight_pct),
                    reason
                ];
            values.forEach((value) => {
                const cell = document.createElement('td');
                cell.textContent = String(value);
                row.appendChild(cell);
            });
            tbody.appendChild(row);
        }
    };

    const renderRiskPanel = (risk) => {
        if (!risk || risk.unavailable) return;
        setText('riskGross', `${fmtPct(risk.gross_exposure_pct)} / ${fmtPct(risk.gross_exposure_limit_pct)}`);
        setText('riskCash', `${fmtPct(risk.cash_pct)} / ${fmtPct(risk.cash_buffer_limit_pct)}`);
        setText('riskEquity', fmtPct(risk.equity_exposure_pct));
        setText('riskCrypto', `${fmtPct(risk.crypto_exposure_pct)} / ${fmtPct(risk.crypto_exposure_limit_pct)}`);
        setText('riskDrawdown', `${formatCurrency(risk.drawdown)} / ${fmtPct(risk.max_drawdown_pct)}`);
        setText('riskDailyLoss', `${formatPnl(risk.daily_pnl)} / ${fmtPct(risk.daily_loss_limit_pct)}`);
        const latch = risk.entry_risk_latch || {};
        const latchReasons = Object.entries(latch)
            .filter(([, reason]) => reason)
            .map(([market, reason]) => `${market}: ${reason}`)
            .join(', ');
        setText('riskEntryLatch', latchReasons || 'açık');
        const epoch = risk.risk_epoch;
        setText('riskEpoch', epoch ? fmtDate(epoch.session_date) + ' · taban $' + fmtNum(epoch.day_start_equity) : '—');
        const circuitBadge = document.getElementById('riskCircuitBadge');
        if (circuitBadge) {
            circuitBadge.textContent = risk.circuit_breaker ? ('KESİCİ: ' + risk.circuit_breaker) : 'risk hazır';
            circuitBadge.className = 'count-badge' + (risk.circuit_breaker ? ' risk-critical' : '');
        }
        const incidentsBox = document.getElementById('riskIncidents');
        if (!incidentsBox) return;
        incidentsBox.replaceChildren();
        const incidents = risk.safety_incidents || [];
        if (!incidents.length) {
            const ok = document.createElement('div');
            ok.className = 'risk-incident ok';
            ok.textContent = 'Aktif güvenlik olayı yok';
            incidentsBox.appendChild(ok);
            return;
        }
        for (const incident of incidents) {
            const item = document.createElement('div');
            item.className = 'risk-incident';
            item.textContent = `${incident.reason} (${fmtDateTime(incident.activated_at)})`;
            incidentsBox.appendChild(item);
        }
    };

    const renderHealthPanel = (health) => {
        if (!health || health.unavailable) return;
        const badge = document.getElementById('healthBadge');
        if (badge) {
            badge.textContent = health.healthy === true ? 'sağlıklı' : (health.healthy === false ? 'degrade' : (health.engine_state || '—'));
            badge.className = 'count-badge' + (health.healthy === false ? ' risk-critical' : '');
        }
        const queue = health.queue || {};
        setText('healthQueue', `boyut ${queue.size ?? 0} · yaş ${queue.age_seconds === undefined || queue.age_seconds === null ? '—' : Math.round(queue.age_seconds) + 's'}`);
        const markets = health.markets || {};
        const equityMarket = markets.equity || {};
        const cryptoMarket = markets.crypto || {};
        const dataOk = (equityMarket.stream_healthy !== false && cryptoMarket.stream_healthy !== false);
        setText('healthData', dataOk ? 'güncel' : 'gecikme/gap var');
        const freshness = health.account_freshness || {};
        const maxAge = freshness.maximum_age_for_entry_seconds;
        const ageOk = freshness.stale !== true;
        setText('healthAccount', `${freshness.source || '—'}${freshness.age_seconds === undefined || freshness.age_seconds === null ? '' : ' · ' + Math.round(freshness.age_seconds) + 's'}` + (maxAge ? ` (limit ${maxAge}s)` : ''));
        setText('healthHeartbeat', fmtDateTime(health.last_heartbeat));
        setText('healthRestarts', String(health.restart_count ?? 0));
        const db = health.database || {};
        setText('healthDatabase', `${db.exists ? 'açık' : 'yok'}${db.size_bytes ? ' · ' + fmtNum(db.size_bytes / 1024, 0) + ' KB' : ''}`);
        const scanner = health.scanner || {};
        const scannerStatus = Object.entries(scanner.status || {})
            .map(([market, status]) => `${market}: ${status}`)
            .join(', ');
        setText('healthScanner', scannerStatus || '—');
        const reconciliation = health.reconciliation || {};
        setText('healthReconciliation', reconciliation.failed ? 'HATA' : (reconciliation.last_at ? fmtDateTime(reconciliation.last_at) : '—'));
        renderWorkersTable(health.workers || {});
    };

    const renderWorkersTable = (workers) => {
        const tbody = document.getElementById('workersBody');
        if (!tbody) return;
        tbody.replaceChildren();
        const names = Object.keys(workers);
        if (!names.length) {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 5;
            cell.className = 'empty-state';
            const icon = document.createElement('span');
            icon.className = 'empty-icon';
            icon.textContent = '—';
            cell.append(icon, document.createTextNode('Çalışan bilgisi yok'));
            row.appendChild(cell);
            tbody.appendChild(row);
            return;
        }
        for (const name of names.sort()) {
            const worker = workers[name] || {};
            const row = document.createElement('tr');
            const values = [
                name,
                worker.alive ? 'canlı' : 'durdu',
                fmtDateTime(worker.last_heartbeat),
                String(worker.restart_count ?? 0),
                worker.last_error || '—'
            ];
            values.forEach((value) => {
                const cell = document.createElement('td');
                cell.textContent = String(value);
                row.appendChild(cell);
            });
            tbody.appendChild(row);
        }
    };

    const renderExecutionPanel = (execution) => {
        if (!execution || execution.unavailable) return;
        const badge = document.getElementById('executionBadge');
        if (badge) {
            badge.textContent = `${(execution.open_orders || []).length} açık · ${(execution.recent_fills || []).length} dolum`;
        }
        const ordersBody = document.getElementById('openOrdersBody');
        if (ordersBody) {
            ordersBody.replaceChildren();
            const orders = execution.open_orders || [];
            if (!orders.length) {
                const row = document.createElement('tr');
                const cell = document.createElement('td');
                cell.colSpan = 6;
                cell.className = 'empty-state';
                const icon = document.createElement('span');
                icon.className = 'empty-icon';
                icon.textContent = '—';
                cell.append(icon, document.createTextNode('Açık emir yok'));
                row.appendChild(cell);
                ordersBody.appendChild(row);
            } else {
                for (const order of orders) {
                    const row = document.createElement('tr');
                    const lineage = order.lineage || {};
                    const values = [
                        order.symbol,
                        order.side || '—',
                        fmtNum(order.quantity, 4),
                        order.reference_price === undefined || order.reference_price === null ? '—' : '$' + fmtNum(order.reference_price, 4),
                        order.status || '—',
                        lineage.strategy ? `${lineage.strategy} · ${fmtDate(lineage.decision_timestamp)}` : '—'
                    ];
                    values.forEach((value) => {
                        const cell = document.createElement('td');
                        cell.textContent = String(value);
                        row.appendChild(cell);
                    });
                    ordersBody.appendChild(row);
                }
            }
        }
        const fillsBody = document.getElementById('recentFillsBody');
        if (fillsBody) {
            fillsBody.replaceChildren();
            const fills = execution.recent_fills || [];
            if (!fills.length) {
                const row = document.createElement('tr');
                const cell = document.createElement('td');
                cell.colSpan = 7;
                cell.className = 'empty-state';
                const icon = document.createElement('span');
                icon.className = 'empty-icon';
                icon.textContent = '—';
                cell.append(icon, document.createTextNode('Dolum yok'));
                row.appendChild(cell);
                fillsBody.appendChild(row);
            } else {
                for (const fill of fills) {
                    const row = document.createElement('tr');
                    const slippage = fill.slippage_bps;
                    const values = [
                        fill.symbol,
                        fill.side || '—',
                        fmtNum(fill.quantity, 4),
                        '$' + fmtNum(fill.price, 4),
                        slippage === undefined || slippage === null ? '—' : fmtNum(slippage, 2) + ' bps',
                        '$' + fmtNum(fill.fee, 4),
                        fmtDateTime(fill.occurred_at)
                    ];
                    values.forEach((value) => {
                        const cell = document.createElement('td');
                        cell.textContent = String(value);
                        row.appendChild(cell);
                    });
                    fillsBody.appendChild(row);
                }
            }
        }
    };

    const renderUniversePanel = (universe) => {
        if (!universe || universe.unavailable) return;
        const renderMarket = (market, ids) => {
            if (!market) return;
            setText(ids.broad, fmtNum(market.broad_discovered_count, 0));
            setText(ids.eligible, fmtNum(market.eligible_pool_size, 0));
            setText(ids.observed, fmtNum(market.observation_set_size, 0));
            setText(ids.lastScan, fmtDateTime(market.last_refresh_at));
            setText(ids.scanner, market.scanner_status || '—');
            setText(ids.nextScan, fmtDateTime(market.next_refresh_at));
            const box = document.getElementById(ids.symbols);
            if (box) {
                box.replaceChildren();
                const symbols = market.observation_set || [];
                symbols.slice(0, 120).forEach((symbol) => {
                    const chip = document.createElement('span');
                    chip.textContent = symbol;
                    box.appendChild(chip);
                });
                if (symbols.length > 120) {
                    const more = document.createElement('span');
                    more.textContent = `+${symbols.length - 120} daha…`;
                    box.appendChild(more);
                }
            }
        };
        renderMarket(universe.equity, {
            broad: 'eqBroad', eligible: 'eqEligible', observed: 'eqObserved',
            lastScan: 'eqLastScan', scanner: 'eqScanner', nextScan: 'eqNextScan', symbols: 'eqSymbols'
        });
        renderMarket(universe.crypto, {
            broad: 'crBroad', eligible: 'crEligible', observed: 'crObserved',
            lastScan: 'crLastScan', scanner: 'crScanner', nextScan: 'crNextScan', symbols: 'crSymbols'
        });
    };

    const renderDashboard = (data) => {
        if (!data) return;
        renderModeBadge(data.mode);
        // Account top line
        setText('grossExposureValue', formatCurrency(data.account && data.account.gross_exposure));
        setText('grossExposureSub', data.account && data.account.stale ? `eski veri (${data.account.source})` : 'Toplam pozisyon büyüklüğü');
        const totalPnl = data.account && data.account.total_pnl;
        setText('totalPnlValue', formatPnl(totalPnl));
        const totalPnlEl = document.getElementById('totalPnlValue');
        if (totalPnlEl) {
            totalPnlEl.className = 'metric-value ' + (totalPnl > 0 ? 'positive' : (totalPnl < 0 ? 'negative' : ''));
        }
        setText('engineStatusValue', STRATEGY_STATUS_LABELS[data.engine_state] || data.engine_state || '—');
        setText('engineStatusSub', data.engine_error ? data.engine_error : 'Durum açıklaması');
        setText('lastReconValue', fmtDateTime(data.account && data.account.last_reconciliation_at));
        // Strategy cards
        renderStrategyCard(data.equity, {
            name: 'equityStrategyName', badge: 'equityStatusBadge', signal: 'equitySignal',
            rebalance: 'equityRebalance', pool: 'equityPool', obs: 'equityObs',
            positions: 'equityPositions', allocation: 'equityAllocation',
            nextRebalance: 'equityNextRebalance', catchUp: 'equityStartupCatchUp',
            body: 'equityHoldingsBody'
        }, false);
        renderStrategyCard(data.crypto, {
            name: 'cryptoStrategyName', badge: 'cryptoStatusBadge', signal: 'cryptoSignal',
            rebalance: 'cryptoRebalance', lookback: 'cryptoLookback', pool: 'cryptoPool',
            obs: 'cryptoObs', positions: 'cryptoPositions', allocation: 'cryptoAllocation',
            nextRebalance: 'cryptoNextRebalance', catchUp: 'cryptoStartupCatchUp',
            body: 'cryptoHoldingsBody'
        }, true);
        // Panels
        renderRiskPanel(data.risk);
        renderHealthPanel(data.health);
        renderExecutionPanel(data.execution);
        renderUniversePanel(data.universe);
    };

    let dashboardRefreshInFlight = false;
    const refreshDashboard = async () => {
        if (dashboardRefreshInFlight) return;
        dashboardRefreshInFlight = true;
        try {
            const response = await fetch('/api/dashboard/overview');
            if (!response.ok) throw new Error(`Panel verisi alınamadı (${response.status})`);
            renderDashboard(await response.json());
        } catch (err) {
            console.error('Dashboard refresh error:', err);
        } finally {
            dashboardRefreshInFlight = false;
        }
    };

    // Initial Fetch & Start WebSockets
    const init = async () => {
        try {
            await refreshSystemStatus();
            window.setInterval(refreshSystemStatus, 2000);
            initWebSockets();
            const accountRes = await fetch('/api/account');
            if (!accountRes.ok) throw new Error(`Hesap alınamadı (${accountRes.status})`);
            const accountData = await accountRes.json();
            updateAccountUI(accountData);
            await refreshDashboard();
            window.setInterval(refreshDashboard, 5000);
            if (window.location.hash === '#settings') await openSettings();
        } catch (err) {
            console.error("Initialization error:", err);
        }
    };

    init();
});
