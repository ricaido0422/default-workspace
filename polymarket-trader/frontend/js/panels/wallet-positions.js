/**
 * 目标钱包真实持仓面板（Polymarket 链上持仓）
 *
 * 定位说明（与「虚拟持仓」面板互补，两个面板回答不同问题）：
 *   虚拟持仓面板（💼 /portfolio/positions）：我们自己模拟账户的持仓与盈亏
 *   本面板（🧾 /wallets/positions）：被跟单钱包在 Polymarket 上的真实链上持仓
 *
 * 面板能力：
 *   1) 顶部指标卡：持仓数、持仓总市值、累计盈亏、可赎回
 *   2) 明细表：市场、钱包、结果方向、数量、均价、现价、市值、盈亏、盈亏率、到期
 *   3) 筛选：钱包下拉（留空=全部监控钱包）、状态（OPEN/REDEEMABLE/CLOSED）
 *   4) 手动刷新 + 自动刷新开关（默认 30s）
 *
 * 数据源：GET /api/wallets/positions（Data API /v2/positions）
 */

const WalletPositionsPanel = {
    _timer: null,
    _auto: true,
    _intervalMs: 30000,
    _lastData: null,
    _wallets: [],

    async render() {
        const el = document.getElementById('panel-wallet-positions');
        el.innerHTML = `
            <div class="panel-header">
                <h2>🧾 目标钱包持仓</h2>
                <div class="header-actions">
                    <label class="switch-label" style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-secondary);">
                        <input type="checkbox" id="wp-auto" ${this._auto ? 'checked' : ''}
                               onchange="WalletPositionsPanel.toggleAuto(this.checked)">
                        自动刷新 (${this._intervalMs / 1000}s)
                    </label>
                    <button class="btn btn-secondary btn-sm" onclick="WalletPositionsPanel.refresh()">🔄 刷新</button>
                </div>
            </div>

            <div class="callout info" style="padding:10px 14px;border-radius:8px;font-size:12px;margin-bottom:14px;">
                💡 这里是<strong>被跟单钱包在 Polymarket 上的真实链上持仓</strong>（不是我们的模拟盘）。
                想知道「这个巨鲸现在手里买了什么」，看这个面板；
                我们自己的模拟账户盈亏，请看「💼 虚拟持仓」面板。
            </div>

            <div class="card" style="margin-bottom:14px;">
                <div class="filters-bar">
                    <span class="filter-label">筛选：</span>
                    <select id="wp-wallet" onchange="WalletPositionsPanel.onFilter()" style="min-width:220px;">
                        <option value="">全部监控钱包</option>
                    </select>
                    <select id="wp-status" onchange="WalletPositionsPanel.onFilter()" style="min-width:140px;">
                        <option value="OPEN">未平仓 (OPEN)</option>
                        <option value="REDEEMABLE">可赎回 (REDEEMABLE)</option>
                        <option value="CLOSED">已平仓 (CLOSED)</option>
                    </select>
                    <span class="filter-label" id="wp-count-label">--</span>
                </div>
            </div>

            <div class="stats-grid" id="wp-stats-grid"></div>

            <div class="card" style="margin-top:16px;">
                <h3>📋 持仓明细</h3>
                <div class="table-wrapper" style="max-height:520px;">
                    <table>
                        <thead><tr>
                            <th>市场</th><th>钱包</th><th>方向</th><th>数量</th>
                            <th>均价</th><th>现价</th><th>市值</th>
                            <th>盈亏</th><th>盈亏率</th><th>状态</th><th>到期</th>
                        </tr></thead>
                        <tbody id="wp-tbody">
                            <tr><td colspan="11" class="empty-row">加载中…</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        `;
        await this._loadWalletFilter();
        await this.refresh();
        this._startAuto();
    },

    /** 填充钱包筛选下拉（取监控钱包列表） */
    async _loadWalletFilter() {
        try {
            this._wallets = await API.getWallets();
            const sel = document.getElementById('wp-wallet');
            if (!sel) return;
            this._wallets.forEach(w => {
                const o = document.createElement('option');
                o.value = w.address;
                o.textContent = (w.alias ? `${w.alias} · ` : '') + Utils.shortAddr(w.address, 8, 6);
                sel.appendChild(o);
            });
        } catch (e) {
            console.warn('加载钱包列表失败:', e);
        }
    },

    async refresh() {
        const wallet = document.getElementById('wp-wallet')?.value || '';
        const status = document.getElementById('wp-status')?.value || 'OPEN';
        try {
            const data = await API.getWalletPositions(wallet, status);
            this._lastData = data;
            this._renderStats(data);
            this._renderTable(data);
        } catch (e) {
            const tbody = document.getElementById('wp-tbody');
            if (tbody) {
                tbody.innerHTML = `<tr><td colspan="11" class="empty-row">加载失败：${Utils.escape(e.message || String(e))}</td></tr>`;
            }
            const label = document.getElementById('wp-count-label');
            if (label) label.textContent = '加载失败';
        }
    },

    _renderStats(d) {
        const box = document.getElementById('wp-stats-grid');
        if (!box) return;
        const totalPnl = d.total_pnl || 0;
        const cards = [
            { label: '持仓数', value: d.count ?? 0, color: 'var(--text-primary)' },
            { label: '持仓总市值', value: '$' + Utils.fmtNum(d.total_value || 0), color: 'var(--text-primary)' },
            { label: '累计盈亏', value: (totalPnl >= 0 ? '+$' : '-$') + Utils.fmtNum(Math.abs(totalPnl)), color: totalPnl >= 0 ? 'var(--success)' : 'var(--danger)' },
            { label: '可赎回', value: (d.positions || []).filter(p => p.redeemable).length, color: 'var(--accent)' },
        ];
        box.innerHTML = cards.map(c => `
            <div class="stat-card">
                <div class="label">${c.label}</div>
                <div class="value" style="color:${c.color}">${c.value}</div>
            </div>`).join('');
    },

    _renderTable(d) {
        const tbody = document.getElementById('wp-tbody');
        if (!tbody) return;
        const list = d.positions || [];
        const label = document.getElementById('wp-count-label');
        if (label) label.textContent = `${d.count ?? list.length} 条持仓`;

        if (list.length === 0) {
            const reason = (d.errors && d.errors.length)
                ? `拉取失败：${Utils.escape(d.errors[0])}`
                : '该钱包当前没有符合条件的持仓（需要联网访问 Polymarket）';
            tbody.innerHTML = `<tr><td colspan="11" class="empty-row">${reason}</td></tr>`;
            return;
        }

        tbody.innerHTML = list.map(p => {
            const pnl = p.cash_pnl || 0;
            const pct = p.percent_pnl || 0;
            const title = Utils.escape(p.market_title || p.market_slug || p.condition_id || '');
            return `
            <tr>
                <td class="truncate" title="${title}">${title}</td>
                <td class="text-mono" title="${Utils.escape(p.wallet)}">${Utils.walletLabel(p.wallet)}</td>
                <td><span class="badge ${(p.outcome || '').toLowerCase() === 'yes' ? 'badge-buy' : 'badge-sell'}">${Utils.escape(p.outcome || '--')}</span></td>
                <td class="text-mono">${Utils.fmtNum(p.size)}</td>
                <td class="text-mono">${Utils.fmtNum(p.avg_price, 4)}</td>
                <td class="text-mono">${Utils.fmtNum(p.cur_price, 4)}</td>
                <td class="text-mono">$${Utils.fmtNum(p.current_value)}</td>
                <td class="text-mono" style="color:${pnl >= 0 ? 'var(--success)' : 'var(--danger)'}">${pnl >= 0 ? '+' : ''}$${Utils.fmtNum(Math.abs(pnl))}</td>
                <td class="text-mono" style="color:${pct >= 0 ? 'var(--success)' : 'var(--danger)'}">${pct >= 0 ? '+' : ''}${Utils.fmtNum(pct, 1)}%</td>
                <td>${p.redeemable ? '<span class="badge badge-buy">可赎回</span>' : '<span class="text-muted">持有中</span>'}</td>
                <td class="text-muted">${Utils.escape((p.end_date || '').slice(0, 10))}</td>
            </tr>`;
        }).join('');
    },

    onFilter() {
        this.refresh();
    },

    toggleAuto(on) {
        this._auto = on;
        if (on) this._startAuto(); else this._stopAuto();
    },

    _startAuto() {
        this._stopAuto();
        if (!this._auto) return;
        this._timer = setInterval(() => {
            if (document.getElementById('wp-tbody')) this.refresh();
        }, this._intervalMs);
    },

    _stopAuto() {
        if (this._timer) { clearInterval(this._timer); this._timer = null; }
    },

    /** 面板切换离开时清理轮询，避免后台空转 */
    onLeave() {
        this._stopAuto();
    },
};
