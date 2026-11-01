/**
 * 仪表盘面板
 * 统计卡片、实时交易流缩略、最近跟单信号
 */

const DashboardPanel = {
    _refreshTimer: null,

    async render() {
        const el = document.getElementById('panel-dashboard');
        el.innerHTML = `
            <div class="panel-header">
                <h2>📊 仪表盘</h2>
                <div class="header-actions">
                    <button class="btn btn-secondary btn-sm" onclick="DashboardPanel.refresh()">🔄 刷新</button>
                </div>
            </div>
            <div class="stats-grid" id="dash-stats-grid">
                <div class="loading"><div class="spinner"></div>加载中...</div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                <div class="card">
                    <div class="card-header-row">
                        <h3>📈 实时交易流</h3>
                        <div class="card-actions">
                            <button class="btn btn-secondary btn-sm" onclick="App.switchPanel('trades')">查看全部 →</button>
                        </div>
                    </div>
                    <div class="table-wrapper" style="max-height:360px;">
                        <table>
                            <thead><tr><th>时间</th><th>钱包</th><th>市场</th><th>方向</th><th>价格</th><th>金额</th></tr></thead>
                            <tbody id="dash-trades-tbody"></tbody>
                        </table>
                    </div>
                </div>
                <div class="card">
                    <div class="card-header-row">
                        <h3>🎯 最近跟单信号</h3>
                        <div class="card-actions">
                            <button class="btn btn-secondary btn-sm" onclick="App.switchPanel('execution')">查看全部 →</button>
                        </div>
                    </div>
                    <div class="table-wrapper" style="max-height:360px;">
                        <table>
                            <thead><tr><th>时间</th><th>市场</th><th>方向</th><th>价格</th><th>数量</th><th>链</th></tr></thead>
                            <tbody id="dash-signals-tbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- 链路自检：无需等待真实交易即可验证配置是否正确 -->
            <div class="card" style="margin-top:16px;">
                <div class="card-header-row">
                    <h3>🧪 链路自检</h3>
                    <div class="card-actions">
                        <button class="btn btn-secondary btn-sm" onclick="DashboardPanel.loadSelfCheck()">🔄 重新检查</button>
                    </div>
                </div>
                <div id="dash-selfcheck" class="text-muted">检查中...</div>
                <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-top:12px;">
                    <div class="form-group" style="margin:0;width:130px;">
                        <label>模拟金额 (USDC)</label>
                        <input type="number" id="sim-size" value="50" min="1" step="1" style="width:100%;">
                    </div>
                    <div class="form-group" style="margin:0;width:120px;">
                        <label>成交价格</label>
                        <input type="number" id="sim-price" value="0.55" min="0.01" max="0.99" step="0.01" style="width:100%;">
                    </div>
                    <button class="btn btn-primary" id="btn-sim-inject" onclick="DashboardPanel.injectSimTrade()">⚡ 注入一笔模拟交易</button>
                    <button class="btn btn-secondary" onclick="App.switchPanel('execution')">查看执行记录 →</button>
                </div>
                <div id="dash-sim-result" style="margin-top:10px;"></div>
            </div>
        `;
        await this.refresh();
    },

    async refresh() {
        await this.loadStats();
        await this.loadTrades();
        await this.loadSignals();
        await this.loadSelfCheck();
    },

    // ==================== 链路自检 ====================

    async loadSelfCheck() {
        const el = document.getElementById('dash-selfcheck');
        if (!el) return;
        try {
            const st = await API.getSimulateStatus();
            const rows = (st.checks || []).map(c => `
                <div style="display:flex;align-items:center;gap:8px;">
                    <span class="status-dot ${c.ok ? 'connected' : 'disconnected'}"></span>
                    <span style="min-width:120px;">${Utils.escape(c.name)}</span>
                    <span class="text-muted">${Utils.escape(c.detail)}</span>
                </div>
            `).join('');
            const maxTrade = st.max_trade_amount;
            const extra = (st.ready && maxTrade)
                ? `<div class="text-muted" style="margin-top:6px;">💡 当前风控单笔上限 ${Utils.fmtNum(maxTrade)} USDC，模拟金额请填不大于该值（金额会被策略按比例缩放，此处填的是<strong>源交易</strong>金额，非你的下单金额）</div>`
                : '';
            const banner = st.ready
                ? '<div class="text-muted" style="margin-bottom:8px;">✅ 前置条件已满足，可直接注入模拟交易验证全链路</div>'
                : '<div class="text-muted" style="margin-bottom:8px;">⚠️ 前置条件未满足，请先补齐下方标红的项</div>';
            el.innerHTML = banner + rows + extra;
        } catch (e) {
            el.innerHTML = '<span class="text-muted">自检状态加载失败（后端未启动？）</span>';
        }
    },

    async injectSimTrade() {
        const btn = document.getElementById('btn-sim-inject');
        const size = parseFloat(document.getElementById('sim-size')?.value || '200');
        const price = parseFloat(document.getElementById('sim-price')?.value || '0.55');
        const out = document.getElementById('dash-sim-result');
        if (!out) return;

        if (!(size > 0)) { Toast.warning('模拟金额必须大于 0'); return; }
        if (!(price > 0 && price <= 1)) { Toast.warning('价格需在 0-1 之间'); return; }

        if (btn) { btn.disabled = true; btn.textContent = '注入中...'; }
        out.innerHTML = '<span class="text-muted">注入中...</span>';
        try {
            await API.simulateTrade({ size: size / price, price, side: 'BUY' });
            out.innerHTML = `<span style="color:#4ade80;">✅ 已注入模拟交易：${Utils.fmtNum(size)} USDC @ ${Utils.fmtNum(price, 4)}，请到「交易台」查看待审核或执行结果</span>`;
            Toast.success('模拟交易已注入');
            // 刷新统计与信号，让结果立刻可见
            this.loadStats();
            this.loadSignals();
            this.loadSelfCheck();
        } catch (e) {
            out.innerHTML = `<span style="color:#f87171;">❌ ${Utils.escape(e.message || '注入失败')}</span>`;
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = '⚡ 注入一笔模拟交易'; }
        }
    },

    async loadStats() {
        const grid = document.getElementById('dash-stats-grid');
        if (!grid) return;
        try {
            const stats = await API.getStats();
            AppState.stats = stats;
            const pnlClass = stats.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
            const pnlSign = stats.total_pnl >= 0 ? '+' : '';

            // 实时延迟指标（最近一笔交易从发生到推送的延迟）
            const latency = WS._lastTradeLatency;
            let latencyHtml;
            if (latency == null) {
                latencyHtml = '<div class="value">-</div>';
            } else if (latency < 3000) {
                latencyHtml = `<div class="value" style="color:#4ade80;">${(latency / 1000).toFixed(1)}s</div>`;
            } else if (latency < 10000) {
                latencyHtml = `<div class="value" style="color:#fbbf24;">${(latency / 1000).toFixed(1)}s</div>`;
            } else {
                latencyHtml = `<div class="value" style="color:#f87171;">${(latency / 1000).toFixed(1)}s</div>`;
            }

            // Market Channel WS 状态
            const mws = WS.marketWsStatus;
            const wsDotClass = mws.connected ? 'connected' : (mws.enabled ? 'connecting' : 'disconnected');
            const wsLabel = mws.enabled
                ? (mws.connected ? `已连接 · ${mws.subscribed_markets.length}市场` : '连接中...')
                : '未启用';

            grid.innerHTML = `
                <div class="stat-card">
                    <span class="icon">👛</span>
                    <div class="label">监测钱包</div>
                    <div class="value">${stats.watched_wallets ?? 0}</div>
                </div>
                <div class="stat-card">
                    <span class="icon">📈</span>
                    <div class="label">今日交易</div>
                    <div class="value">${stats.today_trades ?? 0}</div>
                </div>
                <div class="stat-card">
                    <span class="icon">⚡</span>
                    <div class="label">今日信号</div>
                    <div class="value">${stats.today_signals ?? 0}</div>
                </div>
                <div class="stat-card">
                    <span class="icon">🎯</span>
                    <div class="label">今日执行</div>
                    <div class="value">${stats.today_executions ?? 0}</div>
                </div>
                <div class="stat-card ${pnlClass}">
                    <span class="icon">💰</span>
                    <div class="label">总盈亏</div>
                    <div class="value">${pnlSign}$${Utils.fmtNum(stats.total_pnl)}</div>
                </div>
                <div class="stat-card">
                    <span class="icon">⏱️</span>
                    <div class="label">实时延迟</div>
                    ${latencyHtml}
                </div>
                <div class="stat-card">
                    <span class="icon">🔌</span>
                    <div class="label">实时价格 WS</div>
                    <div class="value" style="font-size:0.9em;">
                        <span class="status-dot ${wsDotClass}" style="display:inline-block;"></span>
                        ${wsLabel}
                    </div>
                </div>
            `;
        } catch (e) {
            grid.innerHTML = '<div class="empty-state"><div class="empty-icon">📊</div>统计数据加载失败</div>';
        }
    },

    async loadTrades() {
        const tbody = document.getElementById('dash-trades-tbody');
        if (!tbody) return;
        try {
            // 优先用缓存数据，否则请求
            let trades = AppState.recentTrades.slice(0, 10);
            if (trades.length === 0) {
                trades = await API.getTrades('', 10);
                trades.forEach(t => AppState.addTrade(t));
                trades = trades.slice(0, 10);
            }
            this._renderTrades(tbody, trades);
        } catch (e) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center">暂无交易数据</td></tr>';
        }
    },

    _renderTrades(tbody, trades) {
        if (!trades || trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center">暂无交易</td></tr>';
            return;
        }
        tbody.innerHTML = trades.map(t => `
            <tr>
                <td class="text-muted">${Utils.fmtTime(t.timestamp)}</td>
                <td title="${Utils.escape(t.wallet)}">${Utils.walletLabel(t.wallet)}</td>
                <td class="truncate" title="${Utils.escape(t.market_title || t.market_slug || '')}">${Utils.escape(t.market_title || t.market_slug || Utils.shortAddr(t.condition_id, 8))}</td>
                <td><span class="badge badge-${(t.side || '').toLowerCase()}">${t.side}</span></td>
                <td class="text-mono">${Utils.fmtNum(t.price, 4)}</td>
                <td class="text-mono">$${Utils.fmtNum(t.size * t.price)}</td>
            </tr>
        `).join('');
    },

    async loadSignals() {
        const tbody = document.getElementById('dash-signals-tbody');
        if (!tbody) return;
        const signals = AppState.recentSignals.slice(0, 10);
        if (signals.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center">暂无信号</td></tr>';
            return;
        }
        tbody.innerHTML = signals.map(s => `
            <tr>
                <td class="text-muted">${Utils.fmtTime(s.created_at)}</td>
                <td class="truncate" title="${Utils.escape(s.market?.title || '')}">${Utils.escape(s.market?.title || s.market?.slug || '-')}</td>
                <td><span class="badge badge-${(s.side || '').toLowerCase()}">${s.side}</span></td>
                <td class="text-mono">${Utils.fmtNum(s.price, 4)}</td>
                <td class="text-mono">${Utils.fmtNum(s.size)}</td>
                <td class="text-muted" title="${(s.strategy_chain || []).join(' → ')}">${(s.strategy_chain || []).length}步</td>
            </tr>
        `).join('');
    },

    // ==================== WS 实时更新 ====================

    onTradeUpdate(data) {
        const tbody = document.getElementById('dash-trades-tbody');
        if (!tbody) return;
        // 在表格顶部插入新行并闪烁
        const row = document.createElement('tr');
        row.className = 'flash';
        row.innerHTML = `
            <td class="text-muted">${Utils.fmtTime(data.timestamp)}</td>
            <td title="${Utils.escape(data.wallet)}">${Utils.walletLabel(data.wallet)}</td>
            <td class="truncate" title="${Utils.escape(data.market_title || data.market_slug || '')}">${Utils.escape(data.market_title || data.market_slug || Utils.shortAddr(data.condition_id, 8))}</td>
            <td><span class="badge badge-${(data.side || '').toLowerCase()}">${data.side}</span></td>
            <td class="text-mono">${Utils.fmtNum(data.price, 4)}</td>
            <td class="text-mono">$${Utils.fmtNum(data.size * data.price)}</td>
        `;
        tbody.insertBefore(row, tbody.firstChild);
        // 保持最多 10 行
        while (tbody.children.length > 10) tbody.removeChild(tbody.lastChild);
    },

    onSignalUpdate(data) {
        const tbody = document.getElementById('dash-signals-tbody');
        if (!tbody) return;
        const row = document.createElement('tr');
        row.className = 'flash-green';
        row.innerHTML = `
            <td class="text-muted">${Utils.fmtTime(data.created_at)}</td>
            <td class="truncate" title="${Utils.escape(data.market?.title || '')}">${Utils.escape(data.market?.title || data.market?.slug || '-')}</td>
            <td><span class="badge badge-${(data.side || '').toLowerCase()}">${data.side}</span></td>
            <td class="text-mono">${Utils.fmtNum(data.price, 4)}</td>
            <td class="text-mono">${Utils.fmtNum(data.size)}</td>
            <td class="text-muted">${(data.strategy_chain || []).length}步</td>
        `;
        tbody.insertBefore(row, tbody.firstChild);
        while (tbody.children.length > 10) tbody.removeChild(tbody.lastChild);
    },

    onExecutionUpdate(data) {
        // 仪表盘不直接显示执行，但可更新统计
    },

    onStatsUpdate(data) {
        AppState.stats = data;
        this.loadStats();
    },

    onWalletStatus(data) {
        // 钱包状态变化时刷新统计
        this.loadStats();
    },

    onWsStatus(data) {
        // WS Market Channel 状态变化时刷新统计卡片
        this.loadStats();
    },
};
