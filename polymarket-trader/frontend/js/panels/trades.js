/**
 * 实时交易流面板
 * 目标钱包交易实时表格、WS 实时追加、筛选器（钱包/市场/方向）、排序
 */

const TradesPanel = {
    _trades: [],
    _maxRows: 500,
    _sortCol: 'timestamp',
    _sortDir: 'desc',
    _filters: { wallet: '', market: '', side: '' },
    _stats: null,          // 买卖方向统计（/api/trades/stats）
    _statsTimer: null,     // 统计轮询定时器

    async render() {
        const el = document.getElementById('panel-trades');
        el.innerHTML = `
            <div class="panel-header">
                <h2>📈 实时交易流</h2>
                <div class="header-actions">
                    <button class="btn btn-secondary btn-sm" onclick="TradesPanel.refresh()">🔄 刷新</button>
                    <button class="btn btn-secondary btn-sm" onclick="TradesPanel.clear()">🗑 清空</button>
                </div>
            </div>

            <!-- 买卖方向统计卡 -->
            <div class="stats-grid" id="trades-stats" style="margin-bottom:14px;"></div>

            <!-- 筛选器 -->
            <div class="card">
                <div class="filters-bar">
                    <span class="filter-label">筛选：</span>
                    <input id="trade-filter-wallet" placeholder="钱包地址 0x..." style="min-width:200px;" oninput="TradesPanel.onFilter()">
                    <input id="trade-filter-market" placeholder="市场关键词" style="min-width:180px;" oninput="TradesPanel.onFilter()">
                    <select id="trade-filter-side" onchange="TradesPanel.onFilter()">
                        <option value="">全部方向</option>
                        <option value="BUY">买入</option>
                        <option value="SELL">卖出</option>
                    </select>
                    <span class="filter-label" id="trade-count-label">0 条</span>
                </div>
            </div>

            <!-- 交易表格 -->
            <div class="card">
                <div class="table-wrapper" id="trades-scroll" style="max-height:calc(100vh - 380px);">
                    <table>
                        <thead><tr>
                            <th data-col="timestamp" onclick="TradesPanel.sort('timestamp')">时间</th>
                            <th data-col="wallet" onclick="TradesPanel.sort('wallet')">钱包</th>
                            <th data-col="market_title" onclick="TradesPanel.sort('market_title')">市场</th>
                            <th data-col="side" onclick="TradesPanel.sort('side')">方向</th>
                            <th data-col="price" onclick="TradesPanel.sort('price')">价格</th>
                            <th data-col="size" onclick="TradesPanel.sort('size')">数量</th>
                            <th data-col="amount" onclick="TradesPanel.sort('amount')">金额</th>
                        </tr></thead>
                        <tbody id="trades-tbody"></tbody>
                    </table>
                </div>
            </div>
        `;
        // 应用排序标记
        this._updateSortIndicators();
        await Promise.all([this.refresh(), this.refreshStats()]);
        this._startStatsPolling();
    },

    /** 拉取买卖方向统计 */
    async refreshStats() {
        try {
            this._stats = await API.getTradesStats(this._filters.wallet);
        } catch (e) {
            this._stats = null;
        }
        this._renderStats();
    },

    _renderStats() {
        const box = document.getElementById('trades-stats');
        if (!box) return;
        const s = this._stats;
        if (!s || !s.total) {
            box.innerHTML = `
                <div class="stat-card">
                    <div class="label">买入笔数</div>
                    <div class="value text-muted">--</div>
                </div>
                <div class="stat-card">
                    <div class="label">卖出笔数</div>
                    <div class="value text-muted">--</div>
                </div>
                <div class="stat-card">
                    <div class="label">净流向（买−卖）</div>
                    <div class="value text-muted">--</div>
                </div>`;
            return;
        }
        const buyPct = s.total ? (s.buy_count / s.total * 100) : 0;
        const sellPct = s.total ? (s.sell_count / s.total * 100) : 0;
        box.innerHTML = `
            <div class="stat-card" onclick="TradesPanel.filterSide('BUY')" style="cursor:pointer" title="点击只看买入">
                <div class="label">🟢 买入笔数</div>
                <div class="value" style="color:var(--success)">${s.buy_count} <span style="font-size:0.55em;color:var(--text-muted)">(${buyPct.toFixed(0)}%)</span></div>
                <div class="sub">均额 $${Utils.fmtUSD(s.avg_buy_amount)} · 合计 $${Utils.fmtUSD(s.buy_amount)}</div>
            </div>
            <div class="stat-card" onclick="TradesPanel.filterSide('SELL')" style="cursor:pointer" title="点击只看卖出">
                <div class="label">🔴 卖出笔数</div>
                <div class="value" style="color:var(--danger)">${s.sell_count} <span style="font-size:0.55em;color:var(--text-muted)">(${sellPct.toFixed(0)}%)</span></div>
                <div class="sub">均额 $${Utils.fmtUSD(s.avg_sell_amount)} · 合计 $${Utils.fmtUSD(s.sell_amount)}</div>
            </div>
            <div class="stat-card">
                <div class="label">净流向（买−卖）</div>
                <div class="value" style="color:${(s.buy_amount - s.sell_amount) >= 0 ? 'var(--success)' : 'var(--danger)'}">${(s.buy_amount - s.sell_amount) >= 0 ? '+' : ''}$${Utils.fmtUSD(s.buy_amount - s.sell_amount)}</div>
                <div class="sub">共 ${s.total} 笔 · 最大单笔 $${Utils.fmtUSD(s.max_amount)}</div>
            </div>`;
    },

    /** 面板激活时开始轮询统计；离开时停止 */
    _startStatsPolling() {
        this._stopStatsPolling();
        this._statsTimer = setInterval(() => {
            if (document.getElementById('trades-stats')) this.refreshStats();
        }, 10000);
    },

    _stopStatsPolling() {
        if (this._statsTimer) { clearInterval(this._statsTimer); this._statsTimer = null; }
    },

    /** 面板切换离开时清理轮询，避免后台空转 */
    onLeave() {
        this._stopStatsPolling();
    },

    async refresh() {
        try {
            const trades = await API.getTrades('', 200);
            this._trades = trades;
            // 合并缓存中可能更新的数据
            AppState.recentTrades.forEach(t => {
                if (!this._trades.find(x => x.tx_hash === t.tx_hash && x.token_id === t.token_id)) {
                    this._trades.unshift(t);
                }
            });
            this._render();
        } catch (e) {
            // 使用缓存数据
            this._trades = AppState.recentTrades.slice();
            this._render();
        }
    },

    _render() {
        const tbody = document.getElementById('trades-tbody');
        if (!tbody) return;

        let filtered = this._trades.filter(t => {
            if (this._filters.wallet && !(t.wallet || '').toLowerCase().includes(this._filters.wallet.toLowerCase())) return false;
            if (this._filters.market) {
                const m = (t.market_title || t.market_slug || '').toLowerCase();
                if (!m.includes(this._filters.market.toLowerCase())) return false;
            }
            if (this._filters.side && t.side !== this._filters.side) return false;
            return true;
        });

        // 排序
        filtered.sort((a, b) => {
            let va, vb;
            if (this._sortCol === 'amount') { va = a.price * a.size; vb = b.price * b.size; }
            else { va = a[this._sortCol] || ''; vb = b[this._sortCol] || ''; }
            if (typeof va === 'string') return this._sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
            return this._sortDir === 'asc' ? va - vb : vb - va;
        });

        // 虚拟滚动：最多渲染 200 行
        const display = filtered.slice(0, 200);

        const countLabel = document.getElementById('trade-count-label');
        if (countLabel) countLabel.textContent = `${filtered.length} 条${filtered.length > 200 ? '（显示前 200）' : ''}`;

        if (display.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-muted text-center">暂无交易数据</td></tr>';
            return;
        }

        tbody.innerHTML = display.map(t => `
            <tr>
                <td class="text-muted">${Utils.fmtTime(t.timestamp)}</td>
                <td class="text-mono" title="${Utils.escape(t.wallet)}">${Utils.walletLabel(t.wallet)}</td>
                <td class="truncate" title="${Utils.escape(t.market_title || t.market_slug || '')}">${Utils.escape(t.market_title || t.market_slug || Utils.shortAddr(t.condition_id, 8))}</td>
                <td><span class="badge badge-${(t.side || '').toLowerCase()}">${t.side}</span></td>
                <td class="text-mono">${Utils.fmtUSD(t.price, 4)}</td>
                <td class="text-mono">${Utils.fmtUSD(t.size)}</td>
                <td class="text-mono">$${Utils.fmtUSD(t.price * t.size)}</td>
            </tr>
        `).join('');
    },

    onFilter() {
        this._filters.wallet = document.getElementById('trade-filter-wallet')?.value.trim() || '';
        this._filters.market = document.getElementById('trade-filter-market')?.value.trim() || '';
        this._filters.side = document.getElementById('trade-filter-side')?.value || '';
        this._render();
        this.refreshStats();
    },

    sort(col) {
        if (this._sortCol === col) {
            this._sortDir = this._sortDir === 'asc' ? 'desc' : 'asc';
        } else {
            this._sortCol = col;
            this._sortDir = 'desc';
        }
        this._updateSortIndicators();
        this._render();
    },

    _updateSortIndicators() {
        document.querySelectorAll('#panel-trades th[data-col]').forEach(th => {
            th.classList.remove('sort-asc', 'sort-desc');
            if (th.dataset.col === this._sortCol) {
                th.classList.add(this._sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
            }
        });
    },

    clear() {
        this._trades = [];
        this._render();
        Toast.info('交易流已清空（仅本地）');
    },

    /** 方向快捷筛选：点统计卡即只看买入/卖出 */
    filterSide(side) {
        const sel = document.getElementById('trade-filter-side');
        if (!sel) return;
        // 再次点击同一方向则取消筛选
        sel.value = (this._filters.side === side) ? '' : side;
        this.onFilter();
    },

    // ==================== WS 实时更新 ====================

    onTradeUpdate(data) {
        // 去重
        if (this._trades.find(t => t.tx_hash === data.tx_hash && t.token_id === data.token_id)) return;
        this._trades.unshift(data);
        if (this._trades.length > this._maxRows) this._trades.pop();

        // 如果当前筛选匹配，插入新行并闪烁
        const tbody = document.getElementById('trades-tbody');
        if (!tbody) return;

        // 检查筛选
        if (this._filters.wallet && !(data.wallet || '').toLowerCase().includes(this._filters.wallet.toLowerCase())) return;
        if (this._filters.market) {
            const m = (data.market_title || data.market_slug || '').toLowerCase();
            if (!m.includes(this._filters.market.toLowerCase())) return;
        }
        if (this._filters.side && data.side !== this._filters.side) return;

        // 如果是按时间倒序（默认），插入到顶部
        if (this._sortCol === 'timestamp' && this._sortDir === 'desc') {
            const row = document.createElement('tr');
            row.className = 'flash';
            row.innerHTML = `
                <td class="text-muted">${Utils.fmtTime(data.timestamp)}</td>
                <td class="text-mono" title="${Utils.escape(data.wallet)}">${Utils.walletLabel(data.wallet)}</td>
                <td class="truncate" title="${Utils.escape(data.market_title || data.market_slug || '')}">${Utils.escape(data.market_title || data.market_slug || Utils.shortAddr(data.condition_id, 8))}</td>
                <td><span class="badge badge-${(data.side || '').toLowerCase()}">${data.side}</span></td>
                <td class="text-mono">${Utils.fmtUSD(data.price, 4)}</td>
                <td class="text-mono">${Utils.fmtUSD(data.size)}</td>
                <td class="text-mono">$${Utils.fmtUSD(data.price * data.size)}</td>
            `;
            tbody.insertBefore(row, tbody.firstChild);
            // 保持最多 200 行
            while (tbody.children.length > 200) tbody.removeChild(tbody.lastChild);
        } else {
            this._render(); // 其他排序方式重新渲染
        }

        // 更新计数
        const countLabel = document.getElementById('trade-count-label');
        if (countLabel) {
            const n = parseInt(countLabel.textContent) || 0;
            countLabel.textContent = `${n + 1} 条`;
        }
        // 买卖统计随新交易增量更新（轻量：只在本地数组上重算）
        if (this._stats && this._stats.total) {
            const isBuy = (data.side || '').toUpperCase().endsWith('BUY');
            const amt = (data.price || 0) * (data.size || 0);
            this._stats.total += 1;
            if (isBuy) {
                this._stats.buy_count += 1;
                this._stats.buy_amount = Math.round((this._stats.buy_amount + amt) * 100) / 100;
                this._stats.avg_buy_amount = Math.round(this._stats.buy_amount / this._stats.buy_count * 100) / 100;
            } else {
                this._stats.sell_count += 1;
                this._stats.sell_amount = Math.round((this._stats.sell_amount + amt) * 100) / 100;
                this._stats.avg_sell_amount = Math.round(this._stats.sell_amount / this._stats.sell_count * 100) / 100;
            }
            if (amt > this._stats.max_amount) this._stats.max_amount = amt;
            this._renderStats();
        }
    },
};
