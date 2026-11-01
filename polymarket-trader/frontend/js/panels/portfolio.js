/**
 * 组合跟单面板（只读）
 * 汇总多个被监测钱包的整体跟单表现，侧重「盈亏是否集中在单一钱包」：
 *   1) 时间范围切换（7 天 / 30 天 / 全部）
 *   2) 上部 4 张指标卡：组合总盈亏、参与钱包数、总信号数、平均胜率
 *   3) 中部按钱包表格：地址短显、信号、成交、胜率、盈亏、占比条
 *   4) 下部集中度提示：Top1 钱包盈亏占比 > 60% 时警示分散风险
 *
 * 数据源：GET /api/analytics/summary?days=N、GET /api/analytics/wallets?days=N
 * 本面板不发起任何写操作。
 */

const PortfolioPanel = {
    _days: 7,
    _loading: false,

    async render() {
        const el = document.getElementById('panel-portfolio');
        el.innerHTML = `
            <div class="panel-header">
                <h2>🧺 组合跟单</h2>
                <div class="header-actions">
                    <div class="seg-group" id="pf-range-group">
                        <button class="seg-btn ${this._days === 7 ? 'active' : ''}"  onclick="PortfolioPanel.setRange(7)">7 天</button>
                        <button class="seg-btn ${this._days === 30 ? 'active' : ''}" onclick="PortfolioPanel.setRange(30)">30 天</button>
                        <button class="seg-btn ${this._days === 0 ? 'active' : ''}"  onclick="PortfolioPanel.setRange(0)">全部</button>
                    </div>
                    <button class="btn btn-secondary btn-sm" onclick="PortfolioPanel.refresh()">🔄 刷新</button>
                </div>
            </div>

            <!-- 空数据引导 -->
            <div class="card" id="pf-empty" style="display:none;">
                <div class="empty-state" style="padding:48px 24px;text-align:center;">
                    <div style="font-size:42px;margin-bottom:12px;">🧺</div>
                    <h3>暂无组合跟单数据</h3>
                    <p style="color:var(--text-secondary);margin:8px 0 20px;">
                        组合跟单需要系统产生跟单信号与执行记录后才会统计。<br>
                        先去添加监测钱包，或到绩效分析查看更细的归因。
                    </p>
                    <div style="display:flex;gap:10px;justify-content:center;">
                        <button class="btn btn-primary btn-sm" onclick="App.switchPanel('wallets')">👛 添加钱包</button>
                        <button class="btn btn-primary btn-sm" onclick="App.switchPanel('analytics')">📈 绩效分析</button>
                    </div>
                </div>
            </div>

            <div id="pf-body" style="display:none;">
                <!-- 上部：4 张指标卡 -->
                <div class="stats-grid" id="pf-stats-grid"></div>

                <!-- 中部：按钱包明细 -->
                <div class="card" style="margin-top:16px;">
                    <h3>👛 按钱包明细</h3>
                    <div class="table-wrapper" style="max-height:460px;">
                        <table>
                            <thead><tr>
                                <th>钱包</th><th>信号数</th><th>成交数</th>
                                <th>胜率</th><th>盈亏</th><th>占比</th>
                            </tr></thead>
                            <tbody id="pf-wallet-tbody"></tbody>
                        </table>
                    </div>
                </div>

                <!-- 下部：集中度提示 -->
                <div class="card" style="margin-top:16px;" id="pf-concentration-card">
                    <h3>⚠️ 盈亏集中度</h3>
                    <div id="pf-concentration"></div>
                </div>
            </div>
        `;

        await this.refresh();
    },

    async setRange(days) {
        this._days = days;
        document.querySelectorAll('#pf-range-group .seg-btn').forEach(b => {
            b.classList.toggle('active', Number(b.getAttribute('onclick').match(/\d+/)[0]) === days);
        });
        await this.refresh();
    },

    async refresh() {
        if (this._loading) return;
        this._loading = true;
        try {
            const d = this._days;
            const [summary, wallets] = await Promise.all([
                API.getAnalyticsSummary(d),
                API.getAnalyticsWallets(d),
            ]);

            const s = summary || {};
            const rows = (wallets || []).filter(r => r && r.wallet);
            const hasData = s.has_data
                || Number(s.total_signals || 0) > 0
                || Number(s.total_executions || 0) > 0
                || rows.length > 0;

            const emptyEl = document.getElementById('pf-empty');
            const bodyEl = document.getElementById('pf-body');
            if (emptyEl) emptyEl.style.display = hasData ? 'none' : 'block';
            if (bodyEl) bodyEl.style.display = hasData ? 'block' : 'none';

            if (hasData) {
                this._renderStats(s, rows);
                this._renderWallets(rows);
                this._renderConcentration(rows);
            }
        } catch (e) {
            Toast.error('加载组合跟单数据失败: ' + (e.message || e));
        } finally {
            this._loading = false;
        }
    },

    // ==================== 渲染 ====================

    _renderStats(s, rows) {
        const pnl = Number(s.total_pnl || 0);
        const pnlColor = pnl > 0 ? '#4ade80' : (pnl < 0 ? '#f87171' : 'var(--text-primary)');
        // 平均胜率：优先用总览口径，缺失时按钱包明细加权/简单平均
        let avgWin = s.win_rate;
        if (avgWin == null && rows.length) {
            avgWin = rows.reduce((a, r) => a + Number(r.win_rate || 0), 0) / rows.length;
        }
        const cards = [
            { label: '组合总盈亏', value: `${pnl > 0 ? '+' : ''}$${pnl.toFixed(2)}`, color: pnlColor },
            { label: '参与钱包数', value: rows.length || s.total_wallets || 0 },
            { label: '总信号数',   value: s.total_signals ?? 0 },
            { label: '平均胜率',   value: `${(Number(avgWin || 0) * 100).toFixed(1)}%` },
        ];
        document.getElementById('pf-stats-grid').innerHTML = cards.map(c => `
            <div class="stat-card">
                <div class="label">${c.label}</div>
                <div class="value" style="${c.color ? `color:${c.color}` : ''}">${c.value}</div>
            </div>
        `).join('');
    },

    _renderWallets(rows) {
        const tb = document.getElementById('pf-wallet-tbody');
        if (!rows.length) {
            tb.innerHTML = '<tr><td colspan="6" class="empty-row">暂无数据</td></tr>';
            return;
        }
        // 占比：|盈亏| 在全部钱包 |盈亏| 之和中的份额
        const absSum = rows.reduce((a, r) => a + Math.abs(Number(r.total_pnl || 0)), 0);
        const maxAbs = Math.max(...rows.map(r => Math.abs(Number(r.total_pnl || 0))), 1e-9);
        // 按盈亏绝对值降序，集中度更直观
        const sorted = [...rows].sort((a, b) =>
            Math.abs(Number(b.total_pnl || 0)) - Math.abs(Number(a.total_pnl || 0)));
        tb.innerHTML = sorted.map(r => {
            const pnl = Number(r.total_pnl || 0);
            const share = absSum > 0 ? Math.abs(pnl) / absSum * 100 : 0;
            const w = Math.abs(pnl) / maxAbs * 100;
            const color = pnl >= 0 ? '#4ade80' : '#f87171';
            return `<tr>
                <td title="${this._esc(r.wallet)}">${r.alias ? this._esc(r.alias) : this._shortAddr(r.wallet)}</td>
                <td>${r.signal_count ?? 0}</td>
                <td>${r.execution_count ?? 0}</td>
                <td>${(Number(r.win_rate || 0) * 100).toFixed(1)}%</td>
                <td>${this._pnl(pnl)}</td>
                <td>
                    <div class="bar-track" title="盈亏占比 ${share.toFixed(1)}%">
                        <div class="bar-fill" style="width:${w.toFixed(1)}%;background:${color}"></div>
                    </div>
                    <span style="font-size:11px;color:var(--text-secondary);">${share.toFixed(1)}%</span>
                </td>
            </tr>`;
        }).join('');
    },

    /**
     * 集中度判定规则：
     *   Top1 盈亏占比 = max(|单钱包盈亏|) / Σ|单钱包盈亏| × 100%
     *   > 60% → warning 色调，提示组合盈亏过度依赖单一钱包，需分散风险
     *   40% ~ 60% → info 提示关注
     *   ≤ 40% → success 色调，认为已较分散
     */
    _renderConcentration(rows) {
        const box = document.getElementById('pf-concentration');
        if (!box) return;
        if (!rows.length) {
            box.innerHTML = '<div class="text-muted" style="font-size:12px;">暂无数据，无法评估集中度。</div>';
            return;
        }
        const absSum = rows.reduce((a, r) => a + Math.abs(Number(r.total_pnl || 0)), 0);
        if (absSum <= 0) {
            box.innerHTML = '<div class="text-muted" style="font-size:12px;">统计区间内盈亏为 0，暂无集中度风险。</div>';
            return;
        }
        const top = rows.reduce((a, b) =>
            Math.abs(Number(b.total_pnl || 0)) > Math.abs(Number(a.total_pnl || 0)) ? b : a);
        const share = Math.abs(Number(top.total_pnl || 0)) / absSum * 100;

        let tone = 'success', text = '盈亏来源较为分散';
        if (share > 60) {
            tone = 'warning';
            text = '组合盈亏过度依赖单一钱包，建议分散跟单对象';
        } else if (share > 40) {
            tone = 'info';
            text = '盈亏集中度偏高，建议关注该钱包的后续表现';
        }
        box.innerHTML = `
            <div class="callout ${tone}" style="padding:12px 14px;border-radius:8px;font-size:13px;">
                <div style="margin-bottom:6px;">
                    Top1 钱包 <b>${top.alias ? this._esc(top.alias) : this._shortAddr(top.wallet)}</b>
                    盈亏占比 <b>${share.toFixed(1)}%</b>
                </div>
                <div style="color:var(--text-secondary);">${text}</div>
            </div>`;
    },

    _shortAddr(a) {
        if (!a) return '—';
        return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
    },

    _pnl(v) {
        const n = Number(v || 0);
        const c = n > 0 ? '#4ade80' : (n < 0 ? '#f87171' : 'var(--text-secondary)');
        return `<span style="color:${c}">${n > 0 ? '+' : ''}${n.toFixed(2)}</span>`;
    },

    _esc(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
        ));
    },
};
