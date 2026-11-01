/**
 * 绩效分析面板
 * 跟单效果统计：总览指标、按钱包/策略/市场归因、每日盈亏柱状图
 *
 * 数据源：GET /api/analytics/{summary,wallets,strategies,markets,daily}
 */

const AnalyticsPanel = {
    _days: 7,
    _loading: false,

    async render() {
        const el = document.getElementById('panel-analytics');
        el.innerHTML = `
            <div class="panel-header">
                <h2>📈 绩效分析</h2>
                <div class="header-actions">
                    <div class="seg-group" id="an-range-group">
                        <button class="seg-btn ${this._days === 7 ? 'active' : ''}"  onclick="AnalyticsPanel.setRange(7)">7 天</button>
                        <button class="seg-btn ${this._days === 30 ? 'active' : ''}" onclick="AnalyticsPanel.setRange(30)">30 天</button>
                        <button class="seg-btn ${this._days === 0 ? 'active' : ''}"  onclick="AnalyticsPanel.setRange(0)">全部</button>
                    </div>
                    <button class="btn btn-secondary btn-sm" onclick="AnalyticsPanel.refresh()">🔄 刷新</button>
                </div>
            </div>

            <!-- 总览指标 -->
            <div class="stats-grid" id="an-summary-grid">
                <div class="loading"><div class="spinner"></div>加载中...</div>
            </div>

            <!-- 空数据引导 -->
            <div class="card" id="an-empty" style="display:none;">
                <div class="empty-state" style="padding:48px 24px;text-align:center;">
                    <div style="font-size:42px;margin-bottom:12px;">🌱</div>
                    <h3>暂无跟单数据</h3>
                    <p style="color:var(--text-secondary);margin:8px 0 20px;">
                        绩效分析需要系统运行并产生跟单信号后才会显示。<br>
                        先去添加监测钱包、配置策略管道，或者跑一次回测验证策略效果。
                    </p>
                    <div style="display:flex;gap:10px;justify-content:center;">
                        <button class="btn btn-primary btn-sm" onclick="App.switchPanel('wallets')">👛 添加钱包</button>
                        <button class="btn btn-primary btn-sm" onclick="App.switchPanel('strategies')">⚙️ 配置策略</button>
                        <button class="btn btn-primary btn-sm" onclick="App.switchPanel('backtest')">🧪 跑回测</button>
                    </div>
                </div>
            </div>

            <div id="an-body" style="display:none;">
                <!-- 按钱包 + 按策略 -->
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                    <div class="card">
                        <h3>👛 按钱包</h3>
                        <div class="table-wrapper" style="max-height:320px;">
                            <table>
                                <thead><tr>
                                    <th>钱包</th><th>信号</th><th>执行</th><th>成交率</th>
                                    <th>胜率</th><th>盈亏</th><th>占比</th>
                                </tr></thead>
                                <tbody id="an-wallet-tbody"></tbody>
                            </table>
                        </div>
                    </div>
                    <div class="card">
                        <h3>⚙️ 按策略（归因）</h3>
                        <div class="table-wrapper" style="max-height:320px;">
                            <table>
                                <thead><tr>
                                    <th>策略</th><th>位置</th><th>触发</th><th>通过率</th><th>贡献盈亏</th>
                                </tr></thead>
                                <tbody id="an-strategy-tbody"></tbody>
                            </table>
                        </div>
                    </div>
                </div>

                <!-- 每日盈亏柱状图 -->
                <div class="card" style="margin-top:16px;">
                    <h3>📊 每日盈亏</h3>
                    <canvas id="an-daily-chart" width="900" height="220"
                            style="width:100%;height:220px;border-radius:6px;"></canvas>
                    <div style="margin-top:8px;font-size:12px;color:var(--text-secondary);">
                        <span style="color:#4ade80;">■ 盈利日</span> &nbsp;
                        <span style="color:#f87171;margin-left:12px;">■ 亏损日</span> &nbsp;
                        <span style="margin-left:12px;">折线为累计盈亏</span>
                    </div>
                </div>

                <!-- 按市场 -->
                <div class="card" style="margin-top:16px;">
                    <div class="card-header-row">
                        <h3>🌍 按市场</h3>
                        <div class="seg-group">
                            <button class="seg-btn active" id="an-mkt-profit-btn" onclick="AnalyticsPanel.setMarketTab('profit')">盈利 Top10</button>
                            <button class="seg-btn" id="an-mkt-loss-btn" onclick="AnalyticsPanel.setMarketTab('loss')">亏损 Top10</button>
                        </div>
                    </div>
                    <div class="table-wrapper" style="max-height:340px;">
                        <table>
                            <thead><tr>
                                <th>#</th><th>市场</th><th>执行</th><th>胜率</th>
                                <th>总盈亏</th><th>平均</th><th>最佳</th><th>最差</th>
                            </tr></thead>
                            <tbody id="an-market-tbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;

        await this.refresh();
    },

    async setRange(days) {
        this._days = days;
        document.querySelectorAll('#an-range-group .seg-btn').forEach(b => {
            b.classList.toggle('active', Number(b.getAttribute('onclick').match(/\d+/)[0]) === days);
        });
        await this.refresh();
    },

    async refresh() {
        if (this._loading) return;
        this._loading = true;
        try {
            const d = this._days;
            const [summary, wallets, strategies, daily, markets] = await Promise.all([
                API.getAnalyticsSummary(d),
                API.getAnalyticsWallets(d),
                API.getAnalyticsStrategies(d),
                API.getAnalyticsDaily(d === 0 ? 30 : Math.max(d, 30)),
                API.getAnalyticsMarkets(d),
            ]);

            const hasData = summary && (summary.has_data || (summary.total_signals || 0) > 0 || (summary.total_executions || 0) > 0);

            document.getElementById('an-empty').style.display = hasData ? 'none' : 'block';
            document.getElementById('an-body').style.display = hasData ? 'block' : 'none';

            if (hasData) {
                this._renderSummary(summary || {});
                this._renderWallets(wallets || []);
                this._renderStrategies(strategies || []);
                this._renderDaily(daily || []);
                this._renderMarkets(markets || []);
            }
        } catch (e) {
            Toast.error('加载绩效数据失败: ' + (e.message || e));
        } finally {
            this._loading = false;
        }
    },

    // ==================== 渲染 ====================

    _renderSummary(s) {
        const pnl = Number(s.total_pnl || 0);
        const pnlColor = pnl > 0 ? '#4ade80' : (pnl < 0 ? '#f87171' : 'var(--text-primary)');
        const pct = (v) => `${(Number(v || 0) * 100).toFixed(1)}%`;
        const secs = Number(s.avg_hold_seconds || 0);
        const hold = secs >= 3600 ? `${(secs / 3600).toFixed(1)}h` : (secs >= 60 ? `${(secs / 60).toFixed(1)}m` : `${secs.toFixed(0)}s`);

        const cards = [
            { label: '跟单信号', value: s.total_signals ?? 0 },
            { label: '执行记录', value: s.total_executions ?? 0 },
            { label: '成交率',   value: pct(s.fill_rate) },
            { label: '胜率',     value: pct(s.win_rate) },
            { label: '总盈亏',   value: `${pnl > 0 ? '+' : ''}$${pnl.toFixed(2)}`, color: pnlColor },
            { label: '平均盈亏', value: `$${Number(s.avg_pnl || 0).toFixed(2)}` },
            { label: '平均持仓', value: hold },
            { label: '平均延迟', value: `${Number(s.avg_latency_ms || 0).toFixed(0)}ms` },
        ];

        document.getElementById('an-summary-grid').innerHTML = cards.map(c => `
            <div class="stat-card">
                <div class="label">${c.label}</div>
                <div class="value" style="${c.color ? `color:${c.color}` : ''}">${c.value}</div>
            </div>
        `).join('');

        // 执行质量小计
        const rb = Number(s.risk_blocked_count || 0);
        const fl = Number(s.failed_count || 0);
        const cc = Number(s.cancelled_count || 0);
        const latency = (rb || fl || cc) ? `
            <div class="exec-quality" style="margin-top:12px;font-size:12px;color:var(--text-secondary);">
                执行质量：<span style="color:#fbbf24;">风控拦截 ${rb}</span> ·
                <span style="color:#f87171;">失败 ${fl}</span> ·
                <span style="color:var(--text-secondary);">取消 ${cc}</span>
                &nbsp;|&nbsp; 延迟分布 P50 ${Number(s.latency_p50_ms || 0).toFixed(0)}ms /
                P90 ${Number(s.latency_p90_ms || 0).toFixed(0)}ms /
                P95 ${Number(s.latency_p95_ms || 0).toFixed(0)}ms /
                Max ${Number(s.latency_max_ms || 0).toFixed(0)}ms
                &nbsp;|&nbsp; 覆盖 ${s.total_wallets ?? 0} 个钱包 / ${s.total_markets ?? 0} 个市场
            </div>` : '';
        document.getElementById('an-summary-grid').insertAdjacentHTML('beforeend', rb ? latency : '');
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

    _renderWallets(rows) {
        const tb = document.getElementById('an-wallet-tbody');
        if (!rows.length) {
            tb.innerHTML = '<tr><td colspan="7" class="empty-row">暂无数据</td></tr>';
            return;
        }
        const maxAbs = Math.max(...rows.map(r => Math.abs(Number(r.total_pnl || 0))), 1e-9);
        tb.innerHTML = rows.map(r => {
            const pnl = Number(r.total_pnl || 0);
            const w = Math.abs(pnl) / maxAbs * 100;
            const color = pnl >= 0 ? '#4ade80' : '#f87171';
            return `<tr>
                <td title="${r.wallet || ''}">${r.alias ? this._esc(r.alias) : this._shortAddr(r.wallet)}</td>
                <td>${r.signal_count ?? 0}</td>
                <td>${r.execution_count ?? 0}</td>
                <td>${(Number(r.fill_rate || 0) * 100).toFixed(0)}%</td>
                <td>${(Number(r.win_rate || 0) * 100).toFixed(0)}%</td>
                <td>${this._pnl(pnl)}</td>
                <td>
                    <div class="bar-track" title="${Number(r.contribution_pct || 0).toFixed(1)}%">
                        <div class="bar-fill" style="width:${w.toFixed(1)}%;background:${color}"></div>
                    </div>
                </td>
            </tr>`;
        }).join('');
    },

    _renderStrategies(rows) {
        const tb = document.getElementById('an-strategy-tbody');
        if (!rows.length) {
            tb.innerHTML = '<tr><td colspan="5" class="empty-row">暂无数据</td></tr>';
            return;
        }
        tb.innerHTML = rows.map(r => `<tr>
            <td>${this._esc(r.name || r.strategy_id || '—')}${r.pipeline_name ? ` <span class="tag" title="所属管道">${this._esc(r.pipeline_name)}</span>` : ''}</td>
            <td>#${r.position ?? 0}</td>
            <td>${r.triggered_count ?? 0}</td>
            <td>${(Number(r.pass_rate || 0) * 100).toFixed(0)}%</td>
            <td>${this._pnl(r.contributed_pnl)}</td>
        </tr>`).join('');
    },

    _marketRows: [],

    setMarketTab(tab) {
        document.getElementById('an-mkt-profit-btn')?.classList.toggle('active', tab === 'profit');
        document.getElementById('an-mkt-loss-btn')?.classList.toggle('active', tab === 'loss');
        this._renderMarketRows(this._marketRows, tab);
    },

    _renderMarkets(rows) {
        this._marketRows = rows || [];
        const active = document.getElementById('an-mkt-profit-btn')?.classList.contains('active') ? 'profit' : 'loss';
        this._renderMarketRows(this._marketRows, active);
    },

    _renderMarketRows(rows, tab) {
        const tb = document.getElementById('an-market-tbody');
        if (!tb) return;
        const picked = tab === 'profit'
            ? rows.filter(r => Number(r.total_pnl || 0) > 0).sort((a, b) => b.total_pnl - a.total_pnl)
            : rows.filter(r => Number(r.total_pnl || 0) < 0).sort((a, b) => a.total_pnl - b.total_pnl);

        if (!picked.length) {
            tb.innerHTML = `<tr><td colspan="8" class="empty-row">${tab === 'profit' ? '暂无盈利市场' : '暂无亏损市场'}</td></tr>`;
            return;
        }

        tb.innerHTML = picked.slice(0, 10).map((r, i) => `<tr>
            <td>${i + 1}</td>
            <td title="${this._esc(r.title || r.slug || '')}">${this._esc(r.title || r.slug || r.market_key || '—')}</td>
            <td>${r.execution_count ?? 0}</td>
            <td>${(Number(r.win_rate || 0) * 100).toFixed(0)}%</td>
            <td>${this._pnl(r.total_pnl)}</td>
            <td>${this._pnl(r.avg_pnl)}</td>
            <td>${this._pnl(r.best_pnl)}</td>
            <td>${this._pnl(r.worst_pnl)}</td>
        </tr>`).join('');
    },

    _renderDaily(rows) {
        const canvas = document.getElementById('an-daily-chart');
        if (!canvas) return;
        const parent = canvas.parentElement;
        const dpr = window.devicePixelRatio || 1;
        const cssW = parent.clientWidth || 900;
        const cssH = 220;
        canvas.width = cssW * dpr;
        canvas.height = cssH * dpr;
        canvas.style.height = cssH + 'px';

        const ctx = canvas.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, cssW, cssH);

        if (!rows.length) {
            ctx.fillStyle = '#8b949e';
            ctx.font = '13px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('暂无每日数据', cssW / 2, cssH / 2);
            return;
        }

        // ── 坐标与刻度 ──
        const padL = 52, padR = 16, padT = 16, padB = 28;
        const plotW = cssW - padL - padR;
        const plotH = cssH - padT - padB;
        const vals = rows.map(r => Number(r.pnl || 0));
        const cums = rows.map(r => Number(r.cumulative_pnl || 0));
        const maxV = Math.max(...vals, ...cums, 0);
        const minV = Math.min(...vals, ...cums, 0);
        const span = (maxV - minV) || 1;
        const y = (v) => padT + plotH - ((v - minV) / span) * plotH;

        // 网格 + 零轴
        ctx.strokeStyle = 'rgba(139,148,158,0.15)';
        ctx.fillStyle = '#8b949e';
        ctx.font = '10px sans-serif';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const gv = minV + (span * i / 4);
            const gy = y(gv);
            ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(cssW - padR, gy); ctx.stroke();
            ctx.textAlign = 'right';
            ctx.fillText(gv.toFixed(0), padL - 6, gy + 3);
        }
        // 零轴加亮
        if (minV < 0 && maxV > 0) {
            ctx.strokeStyle = 'rgba(139,148,158,0.45)';
            ctx.beginPath(); ctx.moveTo(padL, y(0)); ctx.lineTo(cssW - padR, y(0)); ctx.stroke();
        }

        // ── 柱状图（每日盈亏）──
        const slot = plotW / rows.length;
        const barW = Math.max(2, Math.min(26, slot * 0.6));
        const y0 = y(0);
        rows.forEach((r, i) => {
            const v = Number(r.pnl || 0);
            const cx = padL + slot * i + slot / 2;
            const vy = y(v);
            ctx.fillStyle = v > 0 ? '#4ade80' : (v < 0 ? '#f87171' : 'rgba(139,148,158,0.4)');
            const top = Math.min(vy, y0);
            const h = Math.max(1, Math.abs(y0 - vy));
            ctx.fillRect(cx - barW / 2, top, barW, h);
        });

        // ── 累计盈亏折线 ──
        ctx.strokeStyle = '#58a6ff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        rows.forEach((r, i) => {
            const cx = padL + slot * i + slot / 2;
            const cy = y(Number(r.cumulative_pnl || 0));
            i === 0 ? ctx.moveTo(cx, cy) : ctx.lineTo(cx, cy);
        });
        ctx.stroke();

        // ── 横轴日期（稀疏标注）──
        ctx.fillStyle = '#8b949e';
        ctx.textAlign = 'center';
        const step = Math.ceil(rows.length / 8);
        rows.forEach((r, i) => {
            if (i % step === 0 || i === rows.length - 1) {
                const cx = padL + slot * i + slot / 2;
                const label = String(r.date || '').slice(5);
                ctx.fillText(label, cx, cssH - 8);
            }
        });
    },

    _esc(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
        ));
    },
};
