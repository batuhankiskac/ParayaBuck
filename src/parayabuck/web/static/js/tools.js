// Technical operations and research tools page.
document.addEventListener('DOMContentLoaded', () => {
    let controlToken = document.querySelector('meta[name="parayabuck-control-token"]').content;
    const byId = (id) => document.getElementById(id);
    const controlFetch = async (url, options = {}) => {
        const request = {
            ...options,
            headers: { ...(options.headers || {}), 'X-ParayaBuck-Control-Token': controlToken }
        };
        let response = await fetch(url, request);
        if (response.status !== 403) return response;
        const payload = await response.clone().json().catch(() => ({}));
        if (payload.detail !== 'Invalid local control token') return response;
        const tokenResponse = await fetch('/api/control-token', { cache: 'no-store' });
        if (!tokenResponse.ok) return response;
        controlToken = (await tokenResponse.json()).control_token;
        return fetch(url, {
            ...options,
            headers: { ...(options.headers || {}), 'X-ParayaBuck-Control-Token': controlToken }
        });
    };
    const responsePayload = async (response) => {
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) return response.json();
        return { detail: (await response.text()).trim() || `Sunucu hatası (${response.status})` };
    };
    const appendLog = (message, level = 'info') => {
        const container = byId('terminalLogView');
        const line = document.createElement('div');
        line.className = `log-line ${level.toLowerCase()}`;
        const time = document.createElement('span');
        time.className = 'log-time';
        time.textContent = `[${new Date().toLocaleTimeString()}]`;
        const copy = document.createElement('span');
        copy.className = 'log-msg';
        copy.textContent = ` ${message}`;
        line.append(time, copy);
        container.appendChild(line);
        while (container.children.length > 500) container.firstElementChild.remove();
        container.scrollTop = container.scrollHeight;
    };
    const notify = (message, level = 'info') => {
        appendLog(message, level);
        const toast = byId('statusToast');
        toast.textContent = message;
        toast.className = `status-toast ${level}`;
        toast.hidden = false;
        window.clearTimeout(notify.timer);
        notify.timer = window.setTimeout(() => { toast.hidden = true; }, 4500);
    };
    const stateLabels = {
        running: 'Çalışıyor', starting: 'Başlatılıyor', stopping: 'Durduruluyor',
        halted: 'Güvenlik durdurdu', error: 'Hata', stopped: 'Durdu', stop_failed: 'Durdurma hatası'
    };
    const renderStatus = (data) => {
        const state = data.state || 'stopped';
        byId('statusText').textContent = stateLabels[state] || state;
        byId('statusDot').className = `status-dot ${state}`;
        byId('startBtn').disabled = ['running', 'starting', 'stopping', 'halted'].includes(state);
        byId('stopBtn').disabled = !['running', 'starting', 'stopping', 'halted', 'stop_failed'].includes(state);
        const paper = data.paper !== false;
        byId('modeText').textContent = paper ? 'Paper demo' : 'Gerçek para';
        byId('modeBadge').textContent = paper ? 'PAPER' : 'LIVE';
        byId('modeBadge').className = `mode-badge ${paper ? 'paper' : 'live'}`;
        const equity = data.markets?.equity || {};
        const crypto = data.markets?.crypto || {};
        byId('marketStatusText').textContent =
            `Global ${state} | Equity ${equity.state || 'bilinmiyor'} | Crypto ${crypto.state || 'bilinmiyor'}`;
        const scanner = data.scanner || {};
        const universe = data.universe || {};
        const operations = data.operations || {};
        byId('operationsDetail').textContent = JSON.stringify({
            mode: paper ? 'PAPER' : 'LIVE',
            strategies: data.runtime?.strategies || [],
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
            scanner_status: {
                equity: scanner.equity_status || null,
                crypto: scanner.crypto_status || null
            },
            last_reconciliation: data.runtime?.last_reconciliation_at || null,
            risk_status: data.runtime?.circuit_breaker_reason || 'ready'
        }, null, 2);
    };
    let statusInFlight = false;
    const refreshStatus = async () => {
        if (statusInFlight) return;
        statusInFlight = true;
        try {
            const response = await fetch('/api/status');
            if (response.ok) renderStatus(await response.json());
        } catch (error) {
            console.error('Tools status refresh error:', error);
        } finally {
            statusInFlight = false;
        }
    };

    const backtestLabels = { idle: 'beklemede', running: 'çalışıyor', done: 'tamamlandı', error: 'hata' };
    const renderBacktestSummary = (summary) => {
        const container = byId('backtestSummary');
        if (!summary) { container.hidden = true; return; }
        const pct = (key) => `${(Number(summary[key] || 0) * 100).toFixed(2)}%`;
        container.hidden = false;
        container.innerHTML = `<div class="backtest-summary-grid">
            <span><strong>Getiri</strong>${pct('total_return')}</span>
            <span><strong>CAGR</strong>${pct('cagr')}</span>
            <span><strong>Benchmark</strong>${pct('benchmark_return')} (${summary.benchmark_symbol || '-'})</span>
            <span><strong>MaxDD</strong>${pct('max_drawdown')}</span>
            <span><strong>Sharpe</strong>${Number(summary.sharpe || 0).toFixed(2)}</span>
            <span><strong>İşlem</strong>${summary.total_trades || 0}</span>
            <span><strong>Kazanma</strong>${pct('win_rate')}</span>
            <span><strong>Bitiş</strong>$${Number(summary.ending_equity || 0).toLocaleString()}</span>
        </div>`;
    };
    const renderProgress = (lines) => {
        const container = byId('backtestProgress');
        container.replaceChildren(...lines.map((text) => {
            const line = document.createElement('div');
            line.className = 'log-line info';
            const message = document.createElement('span');
            message.className = 'log-msg';
            message.textContent = text;
            line.appendChild(message);
            return line;
        }));
        container.scrollTop = container.scrollHeight;
    };
    const refreshBacktest = async () => {
        try {
            const response = await fetch('/api/backtest/status');
            if (!response.ok) return;
            const data = await response.json();
            byId('backtestStateBadge').textContent = backtestLabels[data.state] || data.state;
            byId('backtestRunBtn').disabled = data.state === 'running';
            renderProgress(data.progress || []);
            if (data.state === 'done' || data.state === 'error') {
                renderBacktestSummary(data.summary);
                if (data.error) {
                    const summary = byId('backtestSummary');
                    summary.hidden = false;
                    summary.textContent = `Hata: ${data.error}`;
                    summary.classList.add('negative');
                }
            } else if (data.state === 'running') {
                byId('backtestSummary').hidden = true;
            }
        } catch (error) {
            console.error('Backtest status refresh error:', error);
        }
    };
    byId('backtestRunBtn').addEventListener('click', async () => {
        let parameters = {};
        const rawParameters = byId('btParameters').value.trim();
        try {
            if (rawParameters) parameters = JSON.parse(rawParameters);
            if (!parameters || Array.isArray(parameters) || typeof parameters !== 'object') throw new Error();
        } catch (_) {
            notify('Parametreler geçerli bir JSON nesnesi olmalıdır.', 'warning');
            return;
        }
        const days = parseInt(byId('btDays').value, 10);
        const payload = {
            market: byId('btMarket').value,
            strategy: byId('btStrategy').value.trim(),
            parameters,
            years: parseInt(byId('btYears').value, 10) || 3,
            days: Number.isNaN(days) ? 90 : days,
            max_symbols: parseInt(byId('btMaxSymbols').value, 10) || 0,
            symbols: byId('btSymbols').value.trim()
        };
        try {
            const response = await controlFetch('/api/backtest/run', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
            });
            const data = await responsePayload(response);
            if (!response.ok) throw new Error(data.detail || 'Backtest başlatılamadı');
            notify(`Backtest başladı: ${data.run_id}`);
            await refreshBacktest();
        } catch (error) {
            notify(`Backtest başlatılamadı: ${error.message}`, 'warning');
        }
    });

    const engineAction = async (action) => {
        try {
            const response = await controlFetch(`/api/engine/${action}`, { method: 'POST' });
            const data = await responsePayload(response);
            if (!response.ok) throw new Error(data.detail || 'Motor isteği başarısız');
            notify(action === 'start' ? 'Motor başlatma isteği gönderildi.' : 'Motor durduruldu.', action === 'start' ? 'info' : 'warning');
            await refreshStatus();
        } catch (error) {
            notify(`Hata: ${error.message}`, 'warning');
        }
    };
    byId('startBtn').addEventListener('click', () => engineAction('start'));
    byId('stopBtn').addEventListener('click', () => engineAction('stop'));
    byId('clearLogsBtn').addEventListener('click', () => { byId('terminalLogView').innerHTML = ''; });

    const connectLogs = async () => {
        try {
            const response = await controlFetch('/api/ws-ticket', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'logs' })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Log bileti alınamadı');
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const socket = new WebSocket(`${protocol}//${location.host}/ws/logs?ticket=${encodeURIComponent(data.ticket)}`);
            socket.onmessage = (event) => {
                const payload = JSON.parse(event.data);
                appendLog(payload.message, payload.level || 'info');
            };
            socket.onclose = () => window.setTimeout(connectLogs, 2000);
        } catch (_) {
            window.setTimeout(connectLogs, 2000);
        }
    };

    refreshStatus();
    refreshBacktest();
    connectLogs();
    window.setInterval(refreshStatus, 2000);
    window.setInterval(refreshBacktest, 3000);
});
