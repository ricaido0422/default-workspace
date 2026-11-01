/**
 * 虚拟持仓面板（模拟资金账户 · 实盘价盯市结算）
 *
 * 定位说明：
 *   本系统模拟盘接入的是 Polymarket 真实数据（真实钱包交易信号 + 真实盘口成交价），
 *   投入的是虚拟资金（不下真实单、不动真实 USDC），
 *   盈亏严格按实盘价格结算：
 *     - 已实现盈亏：平仓时按真实成交价与加权均价配对计算
 *     - 浮动盈亏  ：持有中按 CLOB 实时真实价（多头看 bid / 空头看 ask）盯市
 *
 * 面板能力：
 *   1) 4 张指标卡：未平仓数、已实现盈亏、浮动盈亏、总盈亏
 *   2) 持仓明细表：市场、方向、数量、均价、现价、市值、已实现/浮动/总盈亏、价格来源
 *   3) 自动刷新开关：默认 15 秒拉取一次真实价，保证浮动盈亏跟随实盘
 *   4) 重置按钮：清空虚拟持仓（不影响历史执行记录）
 *
 * 数据源：GET /api/portfolio/positions、POST /api/portfolio/positions/reset
 */

const PositionsPanel = {
    _timer: null,
    _auto: true,
    _intervalMs: 15000,
    _loading: false,
    _lastData: null,

    async render() {
        const el = document.getElementById('panel-positions');
        el.innerHTML = `
            <div class="panel-header">
                <h2>💼 虚拟持仓</h2>
                <div class="header-actions">
                    <label class="switch-label" style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-secondary);">
                        <input type="checkbox" id="ps-auto" ${this._auto ? 'checked' : ''}
                               onchange="PositionsPanel.toggleAuto(this.checked)">
                        自动刷新 (${this._intervalMs / 1000}s)
                    </label>
                    <button class="btn btn-secondary btn-sm" onclick="PositionsPanel.refresh()">🔄 刷新</button>
                    <button class="btn btn-danger btn-sm" onclick="PositionsPanel.confirmReset()">🗑️ 重置模拟账户</button>
                </div>
            </div>

            <div class="callout info" style="padding:10px 14px;border-radius:8px;font-size:12px;margin-bottom:14px;">
                💡 实盘数据 + 虚拟资金 + 实盘价结算：信号来自真实钱包交易，成交价取真实盘口价，
                盈亏按 Polymarket 真实市价盯市（多头按买价 bid、空头按卖价 ask）。
            </div>

            <div class="stats-grid" id="ps-stats-grid"></div>

            <div class="card" style="margin-top:16px;">
                <h3>📋 持仓明细</h3>
                <div class="table-wrapper" style="max-height:520px;">
                    <table>
                        <thead><tr>
                            <th>市场</th><th>来源钱包</th><th>方向</th><th>数量</th>
                            <th>均价</th><th>现价</th><th>市值</th>
                            <th>已实现</th><th>浮动</th><th>总盈亏</th><th>计价</th>
                        </tr></thead>
                        <tbody id="ps-tbody">
                            <tr><td colspan="11" class="empty-row">加载中…</td></tr>
                        </tbody>
                    </table>
                </div>
                <div id="ps-updated" style="font-size:11px;color:var(--text-secondary);margin-top:8px;"></div>
            </div>
        `;
        this._startTimer();
        await this.refresh();
    },

    destroy() {
        this._stopTimer();
    },

    // ==================== 自动刷新 ====================

    toggleAuto(on) {
        this._auto = on;
        if (on) this._startTimer();
        else this._stopTimer();
        if (on) this.refresh();
    },

    _startTimer() {
        this._stopTimer();
        if (!this._auto) return;
        this._timer = setInterval(() => {
            // 只在面板可见时轮询，避免后台无用请求
            const el = document.getElementById('panel-positions');
            if (!el || !el.classList.contains('active')) return;
            this.refresh(true);
        }, this._intervalMs);
    },

    _stopTimer() {
        if (this._timer) {
            clearInterval(this._timer);
            this._timer = null;
        }
    },

    // ==================== 数据 ====================

    async refresh(silent = false) {
        if (this._loading) return;
        this._loading = true;
        try {
            const data = await API.getVirtualPositions();
            this._lastData = data;
            this._render(data);
            if (!silent) {
                // 静默轮询不弹提示，避免干扰
            }
        } catch (e) {
            const tb = document.getElementById('ps-tbody');
            if (tb) {
                tb.innerHTML = `<tr><td colspan="11" class="empty-row">加载失败: ${this._esc(e.message || e)}</td></tr>`;
            }
            if (!silent) Toast.error('加载虚拟持仓失败: ' + (e.message || e));
        } finally {
            this._loading = false;
        }
    },

    // ==================== 渲染 ====================

    _render(data) {
        const d = data || {};
        const rows = Array.isArray(d.positions) ? d.positions : [];

        this._renderStats(d, rows);
        this._renderTable(rows);

        const upd = document.getElementById('ps-updated');
        if (upd) {
            upd.textContent = rows.length
                ? `最后更新：${new Date().toLocaleTimeString('zh-CN')} · 共 ${rows.length} 条持仓记录`
                : `最后更新：${new Date().toLocaleTimeString('zh-CN')}`;
        }
    },

    _renderStats(d, rows) {
        const realized = Number(d.total_realized_pnl || 0);
        const floating = Number(d.total_floating_pnl || 0);
        const total = Number(d.total_pnl || 0);
        const cards = [
            { label: '未平仓持仓', value: d.open_positions ?? 0, color: 'var(--text-primary)' },
            { label: '已实现盈亏', value: this._money(realized), color: this._pnlColor(realized) },
            { label: '浮动盈亏',   value: this._money(floating), color: this._pnlColor(floating) },
            { label: '总盈亏',     value: this._money(total),    color: this._pnlColor(total) },
        ];
        const box = document.getElementById('ps-stats-grid');
        if (!box) return;
        box.innerHTML = cards.map(c => `
            <div class="stat-card">
                <div class="label">${c.label}</div>
                <div class="value" style="color:${c.color}">${c.value}</div>
            </div>
        `).join('');
    },

    _renderTable(rows) {
        const tb = document.getElementById('ps-tbody');
        if (!tb) return;
        if (!rows.length) {
            tb.innerHTML = '<tr><td colspan="11" class="empty-row">暂无虚拟持仓。产生跟单成交后会自动出现在这里。</td></tr>';
            return;
        }
        // 未平仓优先，其次按总盈亏绝对值降序
        const sorted = [...rows].sort((a, b) => {
            const ao = a.side === 'FLAT' ? 1 : 0;
            const bo = b.side === 'FLAT' ? 1 : 0;
            if (ao !== bo) return ao - bo;
            return Math.abs(Number(b.total_pnl || 0)) - Math.abs(Number(a.total_pnl || 0));
        });
        tb.innerHTML = sorted.map(r => {
            const sideText = { LONG: '多头', SHORT: '空头', FLAT: '已平' }[r.side] || r.side || '—';
            const sideColor = r.side === 'LONG' ? '#4ade80' : (r.side === 'SHORT' ? '#f87171' : 'var(--text-secondary)');
            const srcText = this._sourceText(r.price_source);
            return `<tr>
                <td title="${this._esc(r.market_slug || r.token_id)}">${this._esc(r.market_title || this._short(r.token_id))}</td>
                <td title="${this._esc(r.wallet)}">${this._shortAddr(r.wallet)}</td>
                <td><span style="color:${sideColor};font-weight:600;">${sideText}</span></td>
                <td>${Number(r.net_qty || 0).toFixed(2)}</td>
                <td>${Number(r.avg_entry || 0).toFixed(4)}</td>
                <td>${r.mark_price != null ? Number(r.mark_price).toFixed(4) : '—'}</td>
                <td>$${Number(r.position_value || 0).toFixed(2)}</td>
                <td>${this._pnlHtml(r.realized_pnl)}</td>
                <td>${this._pnlHtml(r.floating_pnl)}</td>
                <td>${this._pnlHtml(r.total_pnl)}</td>
                <td><span style="font-size:11px;color:var(--text-secondary);" title="盯市价格来源">${srcText}</span></td>
            </tr>`;
        }).join('');
    },

    // ==================== 操作 ====================

    confirmReset() {
        // Modal.confirm 是回调式（非 Promise），确认后才执行清空
        Modal.confirm(
            '将清空全部虚拟持仓（浮动盈亏归零）。<br>历史执行记录不受影响。确定继续吗？',
            async () => {
                try {
                    const res = await API.resetVirtualPositions();
                    Toast.success(`已清空 ${res.removed ?? 0} 条虚拟持仓`);
                    await this.refresh();
                } catch (e) {
                    Toast.error('重置失败: ' + (e.message || e));
                }
            },
            '清空持仓'
        );
    },

    // ==================== 工具 ====================

    _sourceText(src) {
        return {
            bid: '买价 bid',
            ask: '卖价 ask',
            midpoint: '中间价',
            unavailable: '不可用',
            flat: '已平仓',
        }[src] || (src || '—');
    },

    _pnlColor(v) {
        const n = Number(v || 0);
        return n > 0 ? '#4ade80' : (n < 0 ? '#f87171' : 'var(--text-secondary)');
    },

    _money(v) {
        const n = Number(v || 0);
        return `${n > 0 ? '+' : ''}$${n.toFixed(2)}`;
    },

    _pnlHtml(v) {
        const n = Number(v || 0);
        return `<span style="color:${this._pnlColor(n)}">${n > 0 ? '+' : ''}${n.toFixed(2)}</span>`;
    },

    _shortAddr(a) {
        if (!a) return '—';
        return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
    },

    _short(s) {
        const v = String(s ?? '');
        return v.length > 14 ? `${v.slice(0, 14)}…` : (v || '—');
    },

    _esc(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
        ));
    },
};
