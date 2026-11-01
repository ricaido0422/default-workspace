/**
 * 策略回测面板
 * 回测配置区（钱包 / 管道 / 参数）、运行按钮、核心指标卡片、
 * 盈亏曲线图（Canvas 手绘折线）、逐笔明细表、过滤原因归因分析
 */

const BacktestPanel = {
    _wallets: [],
    _pipelines: [],
    _strategies: [],
    _result: null,
    _compareResults: [],
    _running: false,
    _sortCol: 'timestamp',
    _sortDir: 'asc',
    _filterMode: 'all',   // all | passed | filtered
    _maxRows: 500,

    async render() {
        const el = document.getElementById('panel-backtest');
        el.innerHTML = `
            <div class="panel-header">
                <h2>🧪 策略回测</h2>
                <div class="header-actions">
                    <button class="btn btn-secondary btn-sm" onclick="BacktestPanel.refresh()">🔄 刷新</button>
                </div>
            </div>

            <!-- 配置区 -->
            <div class="card">
                <h3>⚙️ 回测配置</h3>
                <div class="form-row">
                    <div class="form-group">
                        <label>钱包</label>
                        <select id="bt-wallet"></select>
                    </div>
                    <div class="form-group">
                        <label>策略管道</label>
                        <select id="bt-pipeline"></select>
                    </div>
                    <div class="form-group">
                        <label>初始资金 (USDC)</label>
                        <input type="number" id="bt-capital" value="1000" min="1" step="100">
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>起始时间</label>
                        <input type="datetime-local" id="bt-start">
                    </div>
                    <div class="form-group">
                        <label>结束时间</label>
                        <input type="datetime-local" id="bt-end">
                    </div>
                    <div class="form-group">
                        <label>滑点 (tick 数)</label>
                        <input type="number" id="bt-slippage" value="1" min="0" step="1">
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>最小金额 (USDC)</label>
                        <input type="number" id="bt-min-amount" value="0" min="0" step="10">
                    </div>
                    <div class="form-group">
                        <label>单市场最大仓位 (USDC)</label>
                        <input type="number" id="bt-max-position" value="500" min="1" step="50">
                    </div>
                    <div class="form-group" style="display:flex;align-items:flex-end;gap:8px;">
                        <button class="btn" id="bt-run-btn" style="flex:1;" onclick="BacktestPanel.run()">▶️ 运行回测</button>
                    </div>
                </div>
                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                    <button class="btn btn-secondary btn-sm" onclick="BacktestPanel.compareAll()">🔁 对比全部钱包</button>
                    <span class="text-muted" style="font-size:11px;">
                        未选择已保存管道时，将用「镜像跟单」作为基线策略
                    </span>
                </div>
            </div>

            <!-- 结果区 -->
            <div id="bt-results" style="display:none;">
                <!-- 核心指标卡片 -->
                <div class="stats-grid" id="bt-stats-grid"></div>

                <div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;">
                    <!-- 左：明细表 -->
                    <div class="card">
                        <h3>📋 逐笔明细</h3>
                        <div style="display:flex;gap:6px;margin-bottom:10px;align-items:center;flex-wrap:wrap;">
                            <button class="btn btn-sm btn-secondary" id="bt-filter-all" onclick="BacktestPanel.setFilterMode('all')">全部</button>
                            <button class="btn btn-sm btn-secondary" id="bt-filter-passed" onclick="BacktestPanel.setFilterMode('passed')">仅通过</button>
                            <button class="btn btn-sm btn-secondary" id="bt-filter-filtered" onclick="BacktestPanel.setFilterMode('filtered')">仅过滤</button>
                            <span class="text-muted" style="font-size:11px;margin-left:auto;" id="bt-row-count"></span>
                        </div>
                        <div class="table-wrapper" style="max-height:520px;">
                            <table>
                                <thead><tr>
                                    <th data-col="timestamp" onclick="BacktestPanel.sort('timestamp')">时间</th>
                                    <th>市场</th>
                                    <th>方向</th>
                                    <th data-col="passed" onclick="BacktestPanel.sort('passed')">通过</th>
                                    <th>过滤原因</th>
                                    <th>成交价</th>
                                    <th>数量</th>
                                    <th>盈亏</th>
                                </tr></thead>
                                <tbody id="bt-tbody"></tbody>
                            </table>
                        </div>
                    </div>

                    <!-- 右：盈亏曲线 + 归因 -->
                    <div style="display:flex;flex-direction:column;gap:16px;">
                        <div class="card">
                            <h3>📈 盈亏曲线</h3>
                            <canvas id="bt-equity-chart" width="320" height="200" style="width:100%;border-radius:6px;"></canvas>
                            <div class="text-muted" style="font-size:11px;margin-top:6px;" id="bt-chart-hint"></div>
                        </div>
                        <div class="card">
                            <h3>🔍 过滤原因归因</h3>
                            <div id="bt-filter-stats"></div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 多钱包对比区 -->
            <div id="bt-compare" style="display:none;">
                <div class="card">
                    <h3>🔁 多钱包对比</h3>
                    <div class="table-wrapper">
                        <table>
                            <thead><tr>
                                <th>钱包</th><th>总交易</th><th>通过率</th><th>成交</th>
                                <th>胜率</th><th>总盈亏</th><th>盈亏比</th><th>最大回撤</th>
                                <th>基准盈亏</th><th>超额收益</th>
                            </tr></thead>
                            <tbody id="bt-compare-tbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;

        await this.refresh();
        this.setFilterMode(this._filterMode);
    },

    async refresh() {
        try {
            const [wallets, pipelines] = await Promise.all([
                API.getWallets(),
                API.getPipelines(),
            ]);
            this._wallets = wallets || [];
            this._pipelines = pipelines || [];
            AppState.wallets = this._wallets;
            AppState.pipelines = this._pipelines;
        } catch (e) {
            this._wallets = AppState.wallets || [];
            this._pipelines = AppState.pipelines || [];
        }
        this._renderSelectors();
    },

    _renderSelectors() {
        const walletSel = document.getElementById('bt-wallet');
        const pipeSel = document.getElementById('bt-pipeline');
        if (!walletSel || !pipeSel) return;

        const prevWallet = walletSel.value;
        walletSel.innerHTML = this._wallets.length
            ? this._wallets.map(w => {
                const label = w.alias ? `${w.alias} (${Utils.shortAddr(w.address, 6, 4)})` : Utils.shortAddr(w.address, 8, 6);
                return `<option value="${Utils.escape(w.address)}">${Utils.escape(label)}</option>`;
            }).join('')
            : '<option value="">（暂无监控钱包）</option>';

        const prevPipe = pipeSel.value;
        const opts = ['<option value="">（默认：镜像跟单基线）</option>'];
        this._pipelines.forEach(p => {
            const steps = p.strategies?.length || 0;
            opts.push(`<option value="${Utils.escape(p.id)}">${Utils.escape(p.name)}（${steps} 步）</option>`);
        });
        pipeSel.innerHTML = opts.join('');

        if (prevWallet) walletSel.value = prevWallet;
        if (prevPipe) pipeSel.value = prevPipe;
    },

    // ==================== 运行回测 ====================

    _collectParams() {
        const startRaw = document.getElementById('bt-start')?.value;
        const endRaw = document.getElementById('bt-end')?.value;
        const num = (id, def) => {
            const v = parseFloat(document.getElementById(id)?.value);
            return isNaN(v) ? def : v;
        };
        return {
            start_time: startRaw ? new Date(startRaw).toISOString() : null,
            end_time: endRaw ? new Date(endRaw).toISOString() : null,
            initial_capital: num('bt-capital', 1000),
            slippage_ticks: Math.max(0, Math.round(num('bt-slippage', 1))),
            min_amount: num('bt-min-amount', 0),
            max_position_per_market: num('bt-max-position', 500),
            tick_size: 0.01,
        };
    },

    async run() {
        if (this._running) return;
        const wallet = document.getElementById('bt-wallet')?.value;
        if (!wallet) {
            Toast.warning('请先选择要回测的钱包');
            return;
        }
        const pipelineId = document.getElementById('bt-pipeline')?.value || null;
        const params = this._collectParams();

        this._setLoading(true);
        try {
            const result = await API.runBacktest(wallet, pipelineId, params);
            this._result = result;
            this._renderResult(result);
            Toast.success(`回测完成：${result.metrics.total_trades} 笔历史交易`);
        } catch (e) {
            // Toast 已由 API 层显示
        } finally {
            this._setLoading(false);
        }
    },

    async compareAll() {
        if (this._running) return;
        if (this._wallets.length === 0) {
            Toast.warning('没有可对比的监控钱包');
            return;
        }
        const pipelineId = document.getElementById('bt-pipeline')?.value || null;
        const params = this._collectParams();
        const addresses = this._wallets.map(w => w.address);

        this._setLoading(true, '对比中...');
        try {
            const data = await API.compareBacktest(addresses, pipelineId, params);
            this._compareResults = data.results || [];
            this._renderCompare(data);
            Toast.success(`对比完成：${data.count} 个钱包`);
        } catch (e) {
            // Toast 已由 API 层显示
        } finally {
            this._setLoading(false);
        }
    },

    _setLoading(loading, label = '回测中...') {
        this._running = loading;
        const btn = document.getElementById('bt-run-btn');
        if (btn) {
            btn.disabled = loading;
            btn.innerHTML = loading
                ? `<div class="spinner"></div>${label}`
                : '▶️ 运行回测';
        }
        const results = document.getElementById('bt-results');
        if (loading && results) {
            results.style.display = 'block';
            const grid = document.getElementById('bt-stats-grid');
            if (grid && !grid.innerHTML.trim()) {
                grid.innerHTML = '<div class="loading"><div class="spinner"></div>回测计算中...</div>';
            }
        }
    },

    // ==================== 结果渲染 ====================

    _renderResult(result) {
        const container = document.getElementById('bt-results');
        if (!container) return;
        container.style.display = 'block';

        this._renderMetrics(result);
        this._renderTable();
        this._renderChart();
        this._renderFilterStats();
    },

    _renderMetrics(result) {
        const grid = document.getElementById('bt-stats-grid');
        if (!grid) return;
        const m = result.metrics || {};
        const pnlClass = (m.total_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';
        const excessClass = (m.excess_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';

        grid.innerHTML = `
            <div class="stat-card"><span class="icon">📊</span><div class="label">历史交易</div><div class="value">${m.total_trades || 0}</div><div class="sub">通过 ${m.total_signals || 0} / 过滤 ${m.filtered_trades || 0}</div></div>
            <div class="stat-card"><span class="icon">🎯</span><div class="label">通过率</div><div class="value">${Utils.fmtNum((m.pass_rate || 0) * 100, 1)}%</div><div class="sub">成交 ${m.executed_trades || 0} 笔</div></div>
            <div class="stat-card"><span class="icon">🏆</span><div class="label">胜率</div><div class="value">${Utils.fmtNum((m.win_rate || 0) * 100, 1)}%</div><div class="sub">基准 ${Utils.fmtNum((m.benchmark_win_rate || 0) * 100, 1)}%</div></div>
            <div class="stat-card ${pnlClass}"><span class="icon">💰</span><div class="label">总盈亏</div><div class="value">${Utils.fmtUSD(m.total_pnl || 0)}</div><div class="sub">均价 ${Utils.fmtUSD(m.avg_pnl || 0)}</div></div>
            <div class="stat-card"><span class="icon">⚖️</span><div class="label">盈亏比</div><div class="value">${Utils.fmtNum(m.profit_factor || 0, 2)}</div><div class="sub">最大盈 ${Utils.fmtUSD(m.max_win || 0)} / 最大亏 ${Utils.fmtUSD(m.max_loss || 0)}</div></div>
            <div class="stat-card"><span class="icon">📉</span><div class="label">最大回撤</div><div class="value">${Utils.fmtUSD(m.max_drawdown || 0)}</div><div class="sub">期末 ${Utils.fmtUSD(m.final_equity || 0)}</div></div>
            <div class="stat-card ${excessClass}"><span class="icon">🚀</span><div class="label">超额收益</div><div class="value">${Utils.fmtUSD(m.excess_pnl || 0)}</div><div class="sub">vs 基准 ${Utils.fmtUSD(m.benchmark_pnl || 0)}（${Utils.fmtNum(m.excess_return_pct || 0, 2)}%）</div></div>
        `;
    },

    _visibleTrades() {
        if (!this._result) return [];
        let list = (this._result.trades || []).slice();
        if (this._filterMode === 'passed') {
            list = list.filter(t => t.passed);
        } else if (this._filterMode === 'filtered') {
            list = list.filter(t => !t.passed);
        }
        return list;
    },

    _renderTable() {
        const tbody = document.getElementById('bt-tbody');
        const counter = document.getElementById('bt-row-count');
        if (!tbody) return;

        const list = this._visibleTrades();
        if (counter) {
            counter.textContent = `显示 ${Math.min(list.length, this._maxRows)} / ${list.length} 笔`;
        }

        if (list.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-muted text-center">暂无明细</td></tr>';
            return;
        }

        const col = this._sortCol, dir = this._sortDir === 'asc' ? 1 : -1;
        list.sort((a, b) => {
            const va = a[col] ?? '';
            const vb = b[col] ?? '';
            if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
            if (typeof va === 'boolean' && typeof vb === 'boolean') return ((va ? 1 : 0) - (vb ? 1 : 0)) * dir;
            return String(va).localeCompare(String(vb)) * dir;
        });

        tbody.innerHTML = list.slice(0, this._maxRows).map(t => {
            const src = t.source_trade || {};
            const pnl = t.pnl;
            const pnlClass = pnl == null ? 'text-muted' : (pnl >= 0 ? 'text-success' : 'text-danger');
            const passedBadge = t.passed
                ? '<span class="badge badge-filled">通过</span>'
                : '<span class="badge badge-failed">过滤</span>';
            const sideBadge = t.side
                ? `<span class="badge badge-${(t.side || '').toLowerCase()}">${t.side}</span>`
                : '<span class="text-muted">-</span>';
            const reason = t.passed
                ? (t.reason || '')
                : (t.filter_strategy_name ? `[${t.filter_strategy_name}] ${t.reason}` : (t.reason || ''));
            return `
                <tr>
                    <td class="text-muted text-mono">${Utils.fmtDateTime(t.timestamp || src.timestamp)}</td>
                    <td class="truncate" title="${Utils.escape(src.market_title || src.market_slug || '')}">${Utils.escape(src.market_title || src.market_slug || '-')}</td>
                    <td>${sideBadge}</td>
                    <td>${passedBadge}</td>
                    <td class="truncate text-muted" title="${Utils.escape(reason)}">${Utils.escape(reason || '-')}</td>
                    <td class="text-mono">${t.fill_price != null ? Utils.fmtNum(t.fill_price, 4) : '-'}</td>
                    <td class="text-mono">${t.fill_size != null ? Utils.fmtNum(t.fill_size, 2) : '-'}</td>
                    <td class="${pnlClass} text-mono">${pnl != null ? Utils.fmtUSD(pnl) : '-'}</td>
                </tr>
            `;
        }).join('');
    },

    _renderChart() {
        const canvas = document.getElementById('bt-equity-chart');
        if (!canvas || !this._result) return;
        const ctx = canvas.getContext('2d');
        const w = canvas.width, h = canvas.height;

        // 背景
        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, w, h);

        const curve = this._result.equity_curve || [];
        const hint = document.getElementById('bt-chart-hint');
        if (hint) {
            const src = this._result.data_source || 'db';
            hint.textContent = `累计盈亏曲线（${curve.length} 个成交点，数据来源：${src === 'api' ? 'Polymarket API' : '本地数据库'}）`;
        }

        if (curve.length === 0) {
            ctx.fillStyle = '#6e7681';
            ctx.font = '13px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('暂无成交数据', w / 2, h / 2);
            return;
        }

        // 起点 0
        const points = [0, ...curve];
        const maxV = Math.max(...points, 0);
        const minV = Math.min(...points, 0);
        const range = (maxV - minV) || 1;

        const pad = 24;
        const chartW = w - pad * 2;
        const chartH = h - pad * 2;
        const zeroY = pad + chartH * (maxV / range);

        // 网格线
        ctx.strokeStyle = '#21262d';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const y = pad + (chartH / 4) * i;
            ctx.beginPath();
            ctx.moveTo(pad, y);
            ctx.lineTo(w - pad, y);
            ctx.stroke();
        }

        // 零线
        ctx.strokeStyle = '#30363d';
        ctx.beginPath();
        ctx.moveTo(pad, zeroY);
        ctx.lineTo(w - pad, zeroY);
        ctx.stroke();

        // 折线
        const stepX = points.length > 1 ? chartW / (points.length - 1) : chartW;
        const toX = (i) => pad + i * stepX;
        const toY = (v) => pad + chartH * ((maxV - v) / range);

        // 渐变填充
        const grad = ctx.createLinearGradient(0, pad, 0, h - pad);
        const last = points[points.length - 1];
        const positive = last >= 0;
        grad.addColorStop(0, positive ? 'rgba(63,185,80,0.28)' : 'rgba(248,81,73,0.28)');
        grad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.beginPath();
        ctx.moveTo(toX(0), toY(points[0]));
        for (let i = 1; i < points.length; i++) ctx.lineTo(toX(i), toY(points[i]));
        ctx.lineTo(toX(points.length - 1), zeroY);
        ctx.lineTo(toX(0), zeroY);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();

        ctx.beginPath();
        ctx.moveTo(toX(0), toY(points[0]));
        for (let i = 1; i < points.length; i++) ctx.lineTo(toX(i), toY(points[i]));
        ctx.strokeStyle = positive ? '#3fb950' : '#f85149';
        ctx.lineWidth = 2;
        ctx.stroke();

        // 末端点
        ctx.fillStyle = positive ? '#3fb950' : '#f85149';
        ctx.beginPath();
        ctx.arc(toX(points.length - 1), toY(points[points.length - 1]), 3, 0, Math.PI * 2);
        ctx.fill();

        // 坐标标签
        ctx.fillStyle = '#8b949e';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'left';
        ctx.fillText(`$${maxV.toFixed(1)}`, 2, pad + 4);
        ctx.fillText(`$${minV.toFixed(1)}`, 2, h - pad + 4);
        ctx.textAlign = 'right';
        ctx.fillText(`$${last.toFixed(2)}`, w - 4, h - pad + 4);
    },

    _renderFilterStats() {
        const el = document.getElementById('bt-filter-stats');
        if (!el || !this._result) return;
        const stats = this._result.filter_stats || [];
        if (stats.length === 0) {
            el.innerHTML = '<div class="text-muted text-center mt-16">没有被过滤的交易</div>';
            return;
        }
        const maxCount = Math.max(...stats.map(s => s.count), 1);
        el.innerHTML = stats.map(s => {
            const width = Math.max(4, (s.count / maxCount) * 100);
            return `
                <div style="margin-bottom:10px;">
                    <div style="display:flex;justify-content:space-between;font-size:12px;">
                        <span><strong>${Utils.escape(s.strategy_name || '未知')}</strong>
                            <span class="text-muted" style="font-size:11px;">${Utils.escape(s.reason || '')}</span>
                        </span>
                        <span class="text-mono">${s.count} 次 · ${Utils.fmtNum(s.pct || 0, 1)}%</span>
                    </div>
                    <div style="height:6px;background:var(--bg-tertiary);border-radius:3px;margin-top:4px;overflow:hidden;">
                        <div style="height:100%;width:${width}%;background:var(--warning);border-radius:3px;"></div>
                    </div>
                </div>
            `;
        }).join('');
    },

    _renderCompare(data) {
        const wrap = document.getElementById('bt-compare');
        const tbody = document.getElementById('bt-compare-tbody');
        if (!wrap || !tbody) return;
        wrap.style.display = 'block';

        const results = data.results || [];
        if (results.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" class="text-muted text-center">暂无对比数据</td></tr>';
            return;
        }

        tbody.innerHTML = results.map((r, i) => {
            const m = r.metrics || {};
            const pnlCls = (m.total_pnl || 0) >= 0 ? 'text-success' : 'text-danger';
            const exCls = (m.excess_pnl || 0) >= 0 ? 'text-success' : 'text-danger';
            return `
                <tr>
                    <td class="text-mono">${i + 1}. ${Utils.escape(Utils.shortAddr(r.wallet, 8, 6))}</td>
                    <td class="text-mono">${m.total_trades || 0}</td>
                    <td class="text-mono">${Utils.fmtNum((m.pass_rate || 0) * 100, 1)}%</td>
                    <td class="text-mono">${m.executed_trades || 0}</td>
                    <td class="text-mono">${Utils.fmtNum((m.win_rate || 0) * 100, 1)}%</td>
                    <td class="${pnlCls} text-mono">${Utils.fmtUSD(m.total_pnl || 0)}</td>
                    <td class="text-mono">${Utils.fmtNum(m.profit_factor || 0, 2)}</td>
                    <td class="text-mono">${Utils.fmtUSD(m.max_drawdown || 0)}</td>
                    <td class="text-mono">${Utils.fmtUSD(m.benchmark_pnl || 0)}</td>
                    <td class="${exCls} text-mono">${Utils.fmtUSD(m.excess_pnl || 0)}</td>
                </tr>
            `;
        }).join('');
    },

    // ==================== 交互 ====================

    setFilterMode(mode) {
        this._filterMode = mode;
        // 激活项使用默认 .btn（主色），未激活项使用 .btn-secondary
        ['all', 'passed', 'filtered'].forEach(m => {
            const btn = document.getElementById(`bt-filter-${m}`);
            if (!btn) return;
            if (m === mode) btn.classList.remove('btn-secondary');
            else btn.classList.add('btn-secondary');
        });
        this._renderTable();
    },

    sort(col) {
        if (this._sortCol === col) this._sortDir = this._sortDir === 'asc' ? 'desc' : 'asc';
        else { this._sortCol = col; this._sortDir = 'asc'; }
        this._updateSortIndicators();
        this._renderTable();
    },

    _updateSortIndicators() {
        document.querySelectorAll('#panel-backtest th[data-col]').forEach(th => {
            th.classList.remove('sort-asc', 'sort-desc');
            if (th.dataset.col === this._sortCol) {
                th.classList.add(this._sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
            }
        });
    },
};
