/**
 * 跟单执行面板
 * 跟单订单表格、执行日志流、盈亏统计图（Canvas 柱状图）
 */

const ExecutionPanel = {
    _records: [],
    _logs: [],
    _maxLogs: 200,
    _maxRecords: 500,
    _sortCol: 'timestamp',
    _sortDir: 'desc',

    async render() {
        const el = document.getElementById('panel-execution');
        el.innerHTML = `
            <div class="panel-header">
                <h2>🎯 跟单执行</h2>
                <div class="header-actions">
                    <button class="btn btn-secondary btn-sm" onclick="ExecutionPanel.refresh()">🔄 刷新</button>
                </div>
            </div>

            <!-- 盈亏统计 -->
            <div class="stats-grid" id="exec-stats-grid">
                <div class="loading"><div class="spinner"></div>加载中...</div>
            </div>

            <div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;">
                <!-- 左：订单表格 -->
                <div class="card">
                    <h3>📋 跟单订单</h3>
                    <div class="table-wrapper" style="max-height:500px;">
                        <table>
                            <thead><tr>
                                <th data-col="timestamp" onclick="ExecutionPanel.sort('timestamp')">时间</th>
                                <th>信号来源</th>
                                <th>市场</th>
                                <th>方向</th>
                                <th>价格</th>
                                <th>数量</th>
                                <th>状态</th>
                                <th>成交价</th>
                                <th>盈亏</th>
                                <th>模式</th><th>订单与说明</th>
                            </tr></thead>
                            <tbody id="exec-tbody"></tbody>
                        </table>
                    </div>
                </div>

                <!-- 右：日志流 + 图表 -->
                <div style="display:flex;flex-direction:column;gap:16px;">
                    <div class="card">
                        <h3>📊 盈亏图</h3>
                        <canvas id="pnl-chart" width="320" height="180" style="width:100%;border-radius:6px;"></canvas>
                    </div>
                    <div class="card">
                        <h3>📝 执行日志</h3>
                        <div class="log-stream" id="exec-log-stream"></div>
                    </div>
                </div>
            </div>
        `;
        this._updateSortIndicators();
        await this.refresh();
    },

    cancel(id) {
        Modal.confirm('向交易所请求撤销尚未成交的订单。已成交部分不能撤回，撤单结果需要重新核对。', async()=>{
            try { await API.request('POST', `/executions/${encodeURIComponent(id)}/cancel`); await this.refresh(); Toast.info('已查询撤单后的交易所状态'); } catch(_){}
        }, '请求撤单');
    },
    async reconcile(id) {
        try { await API.reconcileExecution(id); await this.refresh(); Toast.info('已查询交易所状态'); } catch(_) {}
    },

    async refresh() {
        await this.loadRecords();
        this._renderStats();
        this._renderChart();
    },

    async loadRecords() {
        const tbody = document.getElementById('exec-tbody');
        if (!tbody) return;
        try {
            this._records = await API.getExecutions(200);
            // 合并缓存
            AppState.recentExecutions.forEach(e => {
                if (!this._records.find(x => x.signal_id === e.signal_id)) this._records.unshift(e);
            });
        } catch (e) {
            this._records = AppState.recentExecutions.slice();
        }
        this._renderTable();
    },

    _renderTable() {
        const tbody = document.getElementById('exec-tbody');
        if (!tbody) return;

        let records = this._records.slice();
        records.sort((a, b) => {
            const va = a[this._sortCol] || '';
            const vb = b[this._sortCol] || '';
            if (typeof va === 'string') return this._sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
            return this._sortDir === 'asc' ? va - vb : vb - va;
        });

        if (records.length === 0) {
            tbody.innerHTML = '<tr><td colspan="11" class="text-muted text-center">暂无执行记录</td></tr>';
            return;
        }

        tbody.innerHTML = records.slice(0, 200).map(r => {
            const pnlClass = (r.pnl || 0) >= 0 ? 'text-success' : 'text-danger';
            const statusCls = (r.status || '').toLowerCase();
            const modeCls = (r.mode || '').toLowerCase();
            return `
                <tr id="exec-row-${Utils.escape(r.signal_id)}">
                    <td class="text-muted">${Utils.fmtTime(r.timestamp)}</td>
                    <td class="text-mono" title="${Utils.escape(r.signal_id)}">${Utils.shortAddr(r.signal_id, 8, 4)}</td>
                    <td class="truncate">${Utils.escape(r.market_title || r.market_slug || '-')}</td>
                    <td><span class="badge badge-${(r.side || '').toLowerCase()}">${r.side || '-'}</span></td>
                    <td class="text-mono">${Utils.fmtNum(r.price, 4)}</td>
                    <td class="text-mono">${Utils.fmtNum(r.size)}</td>
                    <td><span class="badge badge-${statusCls}">${r.status}</span></td>
                    <td class="text-mono">${Utils.fmtNum(r.fill_price, 4)}</td>
                    <td class="${pnlClass} text-mono">${Utils.fmtUSD(r.pnl)}</td>
                    <td><span class="badge badge-${modeCls}">${r.mode}</span></td>
                    <td><div class="text-mono">${Utils.escape(r.order_id || '-')}</div><div>${Utils.escape(r.error || '')}</div>${r.mode === 'LIVE' && r.status === 'PENDING' ? `<button class="btn btn-secondary btn-sm" onclick="ExecutionPanel.reconcile('${Utils.escape(r.signal_id)}')">核对订单</button><button class="btn btn-danger btn-sm" onclick="ExecutionPanel.cancel('${Utils.escape(r.signal_id)}')">请求撤单</button>` : ''}</td>
                </tr>
            `;
        }).join('');
    },

    _renderStats() {
        const grid = document.getElementById('exec-stats-grid');
        if (!grid) return;
        const records = this._records;
        const filled = records.filter(r => r.status === 'FILLED');
        const failed = records.filter(r => r.status === 'FAILED');
        const pending = records.filter(r => r.status === 'PENDING' || r.status === 'SUBMITTED');
        const totalPnl = filled.reduce((sum, r) => sum + (r.pnl || 0), 0);
        const pnlClass = totalPnl >= 0 ? 'pnl-positive' : 'pnl-negative';

        grid.innerHTML = `
            <div class="stat-card"><span class="icon">📋</span><div class="label">总订单</div><div class="value">${records.length}</div></div>
            <div class="stat-card"><span class="icon">✅</span><div class="label">已成交</div><div class="value">${filled.length}</div></div>
            <div class="stat-card"><span class="icon">⏳</span><div class="label">待执行</div><div class="value">${pending.length}</div></div>
            <div class="stat-card"><span class="icon">❌</span><div class="label">失败</div><div class="value">${failed.length}</div></div>
            <div class="stat-card ${pnlClass}"><span class="icon">💰</span><div class="label">累计盈亏</div><div class="value">${Utils.fmtUSD(totalPnl)}</div></div>
        `;
    },

    _renderChart() {
        const canvas = document.getElementById('pnl-chart');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const w = canvas.width, h = canvas.height;

        // 清空
        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, w, h);

        // 取最近 30 条已成交记录的累计 PnL
        const filled = this._records.filter(r => r.status === 'FILLED').slice(0, 30).reverse();
        if (filled.length === 0) {
            ctx.fillStyle = '#6e7681';
            ctx.font = '13px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('暂无盈亏数据', w / 2, h / 2);
            return;
        }

        // 计算累计 PnL
        let cumPnl = 0;
        const points = filled.map(r => { cumPnl += (r.pnl || 0); return cumPnl; });
        const maxPnl = Math.max(...points, 0);
        const minPnl = Math.min(...points, 0);
        const range = maxPnl - minPnl || 1;

        const pad = 20;
        const chartW = w - pad * 2;
        const chartH = h - pad * 2;
        const zeroY = pad + chartH * (maxPnl / range);

        // 零线
        ctx.strokeStyle = '#30363d';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(pad, zeroY);
        ctx.lineTo(w - pad, zeroY);
        ctx.stroke();

        // 柱状图
        const barW = chartW / filled.length;
        points.forEach((pnl, i) => {
            const x = pad + i * barW;
            const y = pad + chartH * ((maxPnl - pnl) / range);
            const barH = zeroY - y;
            ctx.fillStyle = pnl >= 0 ? '#3fb950' : '#f85149';
            ctx.fillRect(x + 1, Math.min(y, zeroY), barW - 2, Math.abs(barH));
        });

        // 标签
        ctx.fillStyle = '#8b949e';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'left';
        ctx.fillText(`$${maxPnl.toFixed(1)}`, 2, pad + 4);
        ctx.fillText(`$${minPnl.toFixed(1)}`, 2, h - pad + 4);
    },

    sort(col) {
        if (this._sortCol === col) this._sortDir = this._sortDir === 'asc' ? 'desc' : 'asc';
        else { this._sortCol = col; this._sortDir = 'desc'; }
        this._updateSortIndicators();
        this._renderTable();
    },

    _updateSortIndicators() {
        document.querySelectorAll('#panel-execution th[data-col]').forEach(th => {
            th.classList.remove('sort-asc', 'sort-desc');
            if (th.dataset.col === this._sortCol) th.classList.add(this._sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
        });
    },

    _addLog(level, message) {
        const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
        this._logs.unshift({ time, level, message });
        if (this._logs.length > this._maxLogs) this._logs.pop();
        this._renderLogs();
    },

    _renderLogs() {
        const el = document.getElementById('exec-log-stream');
        if (!el) return;
        el.innerHTML = this._logs.slice(0, 50).map(l => `
            <div class="log-line">
                <span class="log-time">${l.time}</span>
                <span class="log-level-${l.level}">[${l.level.toUpperCase()}]</span>
                ${Utils.escape(l.message)}
            </div>
        `).join('') || '<div class="text-muted">暂无日志</div>';
    },

    // ==================== WS 实时更新 ====================

    onExecutionUpdate(data) {
        // 去重更新或插入
        const idx = this._records.findIndex(r => r.signal_id === data.signal_id);
        if (idx >= 0) {
            this._records[idx] = { ...this._records[idx], ...data };
        } else {
            this._records.unshift(data);
            if (this._records.length > this._maxRecords) this._records.pop();
        }

        // 更新表格
        this._renderTable();
        this._renderStats();
        this._renderChart();

        // 添加日志
        const level = data.status === 'FILLED' ? 'success' : data.status === 'FAILED' ? 'error' : 'info';
        this._addLog(level, `${data.status} | ${data.mode || ''} | PnL: ${Utils.fmtUSD(data.pnl)} | ${Utils.shortAddr(data.signal_id, 8, 4)}`);

        // 闪烁行
        const row = document.getElementById(`exec-row-${data.signal_id}`);
        if (row) {
            row.classList.add(data.status === 'FILLED' ? 'flash-green' : data.status === 'FAILED' ? 'flash-red' : 'flash');
            setTimeout(() => row.classList.remove('flash', 'flash-green', 'flash-red'), 1500);
        }
    },

    onSignalUpdate(data) {
        this._addLog('info', `信号生成 | ${data.side} | ${Utils.escape(data.market?.title || '-')} | 价格 ${Utils.fmtNum(data.price, 4)}`);
    },
};
