/**
 * 策略调优面板
 * 对选中策略管道中 FILTER 类策略的浮点参数做网格扫描：
 *   1) 从 /api/backtest/strategies 的 param schema 派生扫描区间（不硬编码参数名）
 *   2) 组合数 = 各参数档数笛卡尔积，上限 60 组，超出拒绝启动
 *   3) 串行调用 /api/backtest/run（body 带 pipeline_config，参数被当前组合覆盖）
 *   4) 按评分公式排序，支持一键把最优参数写回选中管道
 *
 * 评分公式（score，越大越好）：
 *   score = total_pnl * (1 + win_rate) * (profit_factor / (1 + profit_factor))
 *         * (1 - min(max_drawdown / max(initial_capital, 1), 1))
 *   说明：
 *     - total_pnl      绝对盈亏量级
 *     - (1 + win_rate) 胜率加成，win_rate 归一化到 0~1
 *     - pf/(1+pf)      盈亏比饱和项，避免盈亏比爆炸放大噪声
 *     - 最后一项       回撤惩罚，按初始资金归一化并裁剪到 0~1
 *     - total_signals === 0 时 score = 0（无信号视为无效组合）
 */

const OptimizerPanel = {
    _wallets: [],
    _pipelines: [],
    _catalog: [],        // /api/backtest/strategies 策略目录
    _space: [],          // 扫描参数条目 [{key, label, strategyName, sid, min, max, step}]
    _results: [],        // [{combo, metrics, score}]
    _running: false,
    _maxCombos: 60,      // 组合数硬上限
    _done: 0,
    _failed: 0,
    _best: null,

    async render() {
        const el = document.getElementById('panel-optimizer');
        el.innerHTML = `
            <div class="panel-header">
                <h2>🧬 策略调优</h2>
                <div class="header-actions">
                    <button class="btn btn-secondary btn-sm" onclick="OptimizerPanel.refresh()">🔄 刷新</button>
                </div>
            </div>

            <!-- 引导：缺钱包或缺管道 -->
            <div class="card" id="opt-empty" style="display:none;">
                <div class="empty-state" style="padding:48px 24px;text-align:center;">
                    <div style="font-size:42px;margin-bottom:12px;">🧭</div>
                    <h3>还无法开始调优</h3>
                    <p style="color:var(--text-secondary);margin:8px 0 20px;" id="opt-empty-hint"></p>
                    <div style="display:flex;gap:10px;justify-content:center;">
                        <button class="btn btn-primary btn-sm" onclick="App.switchPanel('wallets')">👛 添加钱包</button>
                        <button class="btn btn-primary btn-sm" onclick="App.switchPanel('strategies')">⚙️ 配置策略</button>
                    </div>
                </div>
            </div>

            <div id="opt-body" style="display:none;">
                <!-- 配置区 -->
                <div class="card">
                    <h3>⚙️ 调优配置</h3>
                    <div class="form-row">
                        <div class="form-group">
                            <label>钱包</label>
                            <select id="opt-wallet"></select>
                        </div>
                        <div class="form-group">
                            <label>策略管道</label>
                            <select id="opt-pipeline" onchange="OptimizerPanel.onPipelineChange()"></select>
                        </div>
                        <div class="form-group">
                            <label>初始资金 (USDC)</label>
                            <input type="number" id="opt-capital" value="1000" min="1" step="100">
                        </div>
                    </div>
                </div>

                <!-- 扫描空间 -->
                <div class="card" id="opt-space-card" style="display:none;">
                    <div class="card-header-row">
                        <h3>🔬 扫描空间</h3>
                        <span class="text-muted" style="font-size:11px;" id="opt-combo-count"></span>
                    </div>
                    <p class="text-muted" style="font-size:12px;margin:0 0 12px;">
                        区间由后端参数 schema 派生：min = schema.min，max = min(schema.max, 默认值 × 10)，step = (max − min) / 4，共 5 档。
                    </p>
                    <div id="opt-space"></div>
                    <div style="margin-top:14px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                        <button class="btn btn-primary" id="opt-run-btn" onclick="OptimizerPanel.start()">▶️ 开始扫描</button>
                        <button class="btn btn-secondary" onclick="OptimizerPanel.resetAllParams()">↩️ 全部恢复默认</button>
                        <span class="text-muted" style="font-size:11px;" id="opt-space-hint"></span>
                    </div>
                </div>

                <!-- 无 FILTER 参数提示 -->
                <div class="card" id="opt-nospace" style="display:none;">
                    <div class="empty-state" style="padding:32px 24px;text-align:center;">
                        <div style="font-size:36px;margin-bottom:10px;">🤷</div>
                        <h3>该管道没有可扫描的数值参数</h3>
                        <p style="color:var(--text-secondary);margin:8px 0 0;">
                            仅对管道内 FILTER 类策略的浮点参数生成扫描空间，请换一个管道或先在策略工坊补充过滤条件。
                        </p>
                    </div>
                </div>

                <!-- 进度 -->
                <div class="card" id="opt-progress-card" style="display:none;">
                    <div class="card-header-row">
                        <h3>⏳ 扫描进度</h3>
                        <span class="text-muted" style="font-size:11px;" id="opt-progress-text"></span>
                    </div>
                    <div class="opt-progress">
                        <div class="opt-progress-fill" id="opt-progress-fill" style="width:0%"></div>
                    </div>
                    <div style="margin-top:10px;font-size:12px;color:var(--text-secondary);" id="opt-best-line"></div>
                </div>

                <!-- 结果表 -->
                <div class="card" id="opt-result-card" style="display:none;">
                    <div class="card-header-row">
                        <h3>🏆 扫描结果</h3>
                        <span class="text-muted" style="font-size:11px;" id="opt-result-summary"></span>
                    </div>
                    <div class="table-wrapper" style="max-height:520px;">
                        <table>
                            <thead><tr>
                                <th>排名</th><th>参数组合</th><th>信号数</th><th>成交数</th>
                                <th>胜率</th><th>总盈亏</th><th>盈亏比</th><th>最大回撤</th>
                                <th>超额收益</th><th>评分</th><th>操作</th>
                            </tr></thead>
                            <tbody id="opt-result-tbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;

        await this.refresh();
    },

    // ==================== 数据加载 ====================

    async refresh() {
        try {
            const [wallets, pipelines, catalog] = await Promise.all([
                API.getWallets(),
                API.getPipelines(),
                API.getBacktestStrategies(),
            ]);
            this._wallets = wallets || [];
            this._pipelines = pipelines || [];
            this._catalog = catalog || [];
        } catch (e) {
            this._wallets = AppState.wallets || [];
            this._pipelines = AppState.pipelines || [];
            this._catalog = [];
        }
        this._renderSelectors();
    },

    _renderSelectors() {
        const wSel = document.getElementById('opt-wallet');
        const pSel = document.getElementById('opt-pipeline');
        if (!wSel || !pSel) return;

        const emptyEl = document.getElementById('opt-empty');
        const bodyEl = document.getElementById('opt-body');
        const hint = document.getElementById('opt-empty-hint');

        if (!this._wallets.length || !this._pipelines.length) {
            if (emptyEl && bodyEl) {
                bodyEl.style.display = 'none';
                emptyEl.style.display = 'block';
                const missing = [];
                if (!this._wallets.length) missing.push('监控钱包');
                if (!this._pipelines.length) missing.push('策略管道');
                if (hint) hint.textContent = `缺少${missing.join('和')}，无法运行回测扫描。`;
            }
            return;
        }
        if (emptyEl && bodyEl) {
            emptyEl.style.display = 'none';
            bodyEl.style.display = 'block';
        }

        const prevW = wSel.value;
        wSel.innerHTML = this._wallets.map(w => {
            const label = w.alias ? `${w.alias} (${this._shortAddr(w.address)})` : this._shortAddr(w.address);
            return `<option value="${this._esc(w.address)}">${this._esc(label)}</option>`;
        }).join('');
        if (prevW) wSel.value = prevW;

        const prevP = pSel.value;
        pSel.innerHTML = this._pipelines.map(p =>
            `<option value="${this._esc(p.id)}">${this._esc(p.name)}（${p.strategies?.length || 0} 步）</option>`
        ).join('');
        if (prevP) pSel.value = prevP;

        this.onPipelineChange();
    },

    /** 管道切换：从 param schema 派生扫描空间 */
    onPipelineChange() {
        const pSel = document.getElementById('opt-pipeline');
        const pipeline = this._pipelines.find(p => p.id === pSel?.value);
        const spaceEl = document.getElementById('opt-space');
        const cardEl = document.getElementById('opt-space-card');
        const noneEl = document.getElementById('opt-nospace');
        if (!spaceEl || !cardEl || !noneEl) return;

        // 切换管道视为一次新的扫描上下文，清空旧结果
        this._results = [];
        this._best = null;
        const resCard = document.getElementById('opt-result-card');
        if (resCard) resCard.style.display = 'none';

        const byType = {};
        this._catalog.forEach(s => { byType[s.type] = s; });

        const space = [];
        (pipeline?.strategies || []).forEach(st => {
            if (String(st.category || '').toUpperCase() !== 'FILTER') return;
            const schema = (byType[st.type]?.params) || st.params || {};
            Object.entries(schema).forEach(([key, spec]) => {
                const t = String(spec.type || '').toLowerCase();
                if (t !== 'float' && t !== 'number' && t !== 'double') return; // 仅浮点/数值参数
                const def = Number(spec.default ?? 0);
                const sMin = spec.min != null ? Number(spec.min) : 0;
                let sMax = spec.max != null ? Number(spec.max) : def * 10;
                // max = min(schema.max, 默认值 × 10)，同时保证区间有效
                sMax = Math.min(sMax, def * 10);
                if (!(sMax > sMin)) sMax = sMin + Math.max(Math.abs(def), 1);
                space.push({
                    key,
                    sid: st.id,
                    strategyName: st.name || st.type,
                    label: spec.label || key,
                    min: sMin,
                    max: sMax,
                    step: (sMax - sMin) / 4,
                });
            });
        });

        this._space = space;

        if (!space.length) {
            cardEl.style.display = 'none';
            noneEl.style.display = 'block';
            return;
        }
        cardEl.style.display = 'block';
        noneEl.style.display = 'none';

        spaceEl.innerHTML = space.map((p, i) => `
            <div class="param-row">
                <div class="param-label">
                    <span class="param-name">${this._esc(p.label)}</span>
                    <span class="param-meta">${this._esc(p.strategyName)} · <code>${this._esc(p.key)}</code></span>
                </div>
                <div class="param-inputs">
                    <input type="number" id="opt-p-${i}-min"  value="${p.min}" step="any" title="最小值">
                    <span class="param-sep">~</span>
                    <input type="number" id="opt-p-${i}-max"  value="${p.max}" step="any" title="最大值">
                    <span class="param-sep">步长</span>
                    <input type="number" id="opt-p-${i}-step" value="${this._round(p.step)}" step="any" title="步长">
                    <button class="btn btn-sm btn-secondary" onclick="OptimizerPanel.resetParam(${i})">一键默认</button>
                </div>
            </div>
        `).join('');

        this.updateComboCount();
    },

    /** 恢复单个参数的默认区间 */
    resetParam(i) {
        const p = this._space[i];
        if (!p) return;
        document.getElementById(`opt-p-${i}-min`).value = this._round(p.min);
        document.getElementById(`opt-p-${i}-max`).value = this._round(p.max);
        document.getElementById(`opt-p-${i}-step`).value = this._round(p.step);
        this.updateComboCount();
    },

    /** 全部恢复默认区间 */
    resetAllParams() {
        this._space.forEach((_, i) => this.resetParam(i));
        Toast.info('已恢复全部参数的默认扫描区间');
    },

    // ==================== 组合生成 ====================

    /** 读取当前输入框中的区间 */
    _readRanges() {
        return this._space.map((p, i) => {
            const num = (id, fb) => {
                const v = parseFloat(document.getElementById(id)?.value);
                return isNaN(v) ? fb : v;
            };
            const min = num(`opt-p-${i}-min`, p.min);
            const max = num(`opt-p-${i}-max`, p.max);
            let step = num(`opt-p-${i}-step`, p.step);
            if (!(max > min)) return { ...p, min, max, step: 0, tiers: [min] };
            if (!(step > 0)) step = (max - min) / 4;
            return { ...p, min, max, step, tiers: this._tiers(min, max, step) };
        });
    },

    /** 由区间生成档位（至少 3 档） */
    _tiers(min, max, step) {
        const out = [];
        for (let v = min; v <= max + 1e-9; v += step) out.push(this._round(v));
        if (out.length < 3) return [this._round(min), this._round((min + max) / 2), this._round(max)];
        if (out[out.length - 1] !== this._round(max)) out.push(this._round(max));
        return out;
    },

    /** 笛卡尔积，带 60 组上限 */
    _buildCombos(ranges) {
        let combos = [{}];
        ranges.forEach(p => {
            const next = [];
            combos.forEach(c => p.tiers.forEach(v => next.push({ ...c, [p.key]: v })));
            combos = next;
        });
        return combos;
    },

    updateComboCount() {
        const ranges = this._readRanges();
        const combos = this._buildCombos(ranges);
        const countEl = document.getElementById('opt-combo-count');
        const hintEl = document.getElementById('opt-space-hint');
        const runBtn = document.getElementById('opt-run-btn');
        const over = combos.length > this._maxCombos;
        if (countEl) {
            countEl.innerHTML = `共 <b>${combos.length}</b> 组组合（上限 ${this._maxCombos}）`
                + (over ? ' <span style="color:#f87171;">已超限，请收窄区间或增大步长</span>' : '');
        }
        if (hintEl) hintEl.textContent = over ? '组合数超过上限，无法启动扫描' : '';
        if (runBtn) runBtn.disabled = over;
        return combos.length;
    },

    // ==================== 扫描执行 ====================

    async start() {
        if (this._running) return;
        const wallet = document.getElementById('opt-wallet')?.value;
        const pipeline = this._pipelines.find(p => p.id === document.getElementById('opt-pipeline')?.value);
        if (!wallet || !pipeline) {
            Toast.warning('请先选择钱包和策略管道');
            return;
        }
        const capital = Math.max(1, parseFloat(document.getElementById('opt-capital')?.value) || 1000);

        const ranges = this._readRanges();
        const combos = this._buildCombos(ranges);
        if (!combos.length) { Toast.error('扫描空间为空'); return; }
        if (combos.length > this._maxCombos) {
            Toast.error(`组合数 ${combos.length} 超过上限 ${this._maxCombos} 组，请收窄区间或增大步长`);
            return;
        }

        this._running = true;
        this._results = [];
        this._best = null;
        this._done = 0;
        this._failed = 0;
        this._setRunning(true);

        for (let i = 0; i < combos.length; i++) {
            if (!this._running) break; // 用户中止
            const combo = combos[i];
            // 深拷贝选中管道策略链，按当前组合覆盖对应参数
            const config = { strategies: JSON.parse(JSON.stringify(pipeline.strategies || [])) };
            ranges.forEach(p => {
                config.strategies.forEach(st => {
                    if (st.id !== p.sid) return;
                    if (!st.params || typeof st.params !== 'object') st.params = {};
                    st.params[p.key] = combo[p.key];
                });
            });

            const params = {
                initial_capital: capital,
                slippage_ticks: 1,
                tick_size: 0.01,
            };

            try {
                // 串行执行：一组完成后再发起下一组
                const result = await API.runBacktest(wallet, null, params, config);
                const metrics = result?.metrics || {};
                const entry = {
                    combo,
                    params: ranges.map(p => ({ ...p, value: combo[p.key] })),
                    metrics,
                    score: this._score(metrics),
                };
                this._results.push(entry);
                if (!this._best || entry.score > this._best.score) this._best = entry;
            } catch (e) {
                // 单组失败只计数，不中断整体扫描（Toast 已由 API 层给出）
                this._failed++;
            }
            this._done++;
            this._setProgress(this._done, combos.length, this._failed);
        }

        this._running = false;
        this._setRunning(false);
        this._renderResults();
        if (this._done > 0) {
            Toast.success(`扫描完成：有效 ${this._results.length} 组，失败 ${this._failed} 组`);
        } else if (this._failed > 0) {
            Toast.error(`扫描结束：全部 ${this._failed} 组均失败`);
        }
    },

    /**
     * 评分公式
     * score = total_pnl * (1 + win_rate) * (profit_factor/(1+profit_factor))
     *       * (1 - min(max_drawdown/max(initial_capital,1),1))
     * total_signals === 0 时 score = 0
     */
    _score(m) {
        const signals = Number(m.total_signals || 0);
        if (signals === 0) return 0;
        const pnl = Number(m.total_pnl || 0);
        let wr = Number(m.win_rate || 0);
        if (wr > 1) wr = wr / 100;                 // 兼容 0~100 口径
        const pf = Math.max(0, Number(m.profit_factor || 0));
        const cap = Math.max(1, Number(m.initial_capital || 1));
        const dd = Math.min(Math.max(Number(m.max_drawdown || 0) / cap, 0), 1);
        return pnl * (1 + wr) * (pf / (1 + pf)) * (1 - dd);
    },

    _setRunning(on) {
        const btn = document.getElementById('opt-run-btn');
        if (btn) {
            btn.disabled = on;
            btn.innerHTML = on ? '⏳ 扫描中…' : '▶️ 开始扫描';
        }
        document.querySelectorAll('#opt-result-tbody button').forEach(b => { b.disabled = on; });
        ['opt-wallet', 'opt-pipeline', 'opt-capital'].forEach(id => {
            const e = document.getElementById(id);
            if (e) e.disabled = on;
        });
        document.querySelectorAll('#opt-space input, #opt-space button').forEach(e => { e.disabled = on; });
    },

    _setProgress(done, total, failed) {
        const card = document.getElementById('opt-progress-card');
        if (card) card.style.display = 'block';
        const fill = document.getElementById('opt-progress-fill');
        const text = document.getElementById('opt-progress-text');
        const pct = total ? (done / total * 100) : 0;
        if (fill) fill.style.width = `${pct.toFixed(1)}%`;
        if (text) text.textContent = `${done} / ${total} 组${failed ? ` · 失败 ${failed}` : ''}`;
        const bestLine = document.getElementById('opt-best-line');
        if (bestLine) {
            bestLine.innerHTML = this._best
                ? `当前最优：评分 <b>${this._fmt(this._best.score)}</b> · 盈亏 <b>${this._pnl(this._best.metrics.total_pnl)}</b>`
                : '等待第一个有效组合…';
        }
    },

    // ==================== 结果渲染 ====================

    _renderResults() {
        const card = document.getElementById('opt-result-card');
        const tb = document.getElementById('opt-result-tbody');
        const sum = document.getElementById('opt-result-summary');
        if (!card || !tb) return;

        if (!this._results.length) {
            card.style.display = 'block';
            tb.innerHTML = '<tr><td colspan="11" class="empty-row">没有有效结果（全部组合失败或无信号）</td></tr>';
            if (sum) sum.textContent = `失败 ${this._failed} 组`;
            return;
        }

        card.style.display = 'block';
        const sorted = [...this._results].sort((a, b) => b.score - a.score);
        if (sum) sum.textContent = `有效 ${this._results.length} 组 · 失败 ${this._failed} 组 · 按评分降序`;

        tb.innerHTML = sorted.map((r, idx) => {
            const m = r.metrics;
            const tags = r.params.map(p =>
                `<span class="tag" title="${this._esc(p.label)}（${this._esc(p.strategyName)}）">${this._esc(p.label)}=${this._fmt(p.value)}</span>`
            ).join(' ');
            return `<tr>
                <td><span class="rank-badge ${idx === 0 ? 'rank-1' : ''}">${idx + 1}</span></td>
                <td>${tags}</td>
                <td>${Number(m.total_signals || 0)}</td>
                <td>${Number(m.executed_trades || 0)}</td>
                <td>${this._pct(m.win_rate)}</td>
                <td>${this._pnl(m.total_pnl)}</td>
                <td>${Number(m.profit_factor || 0).toFixed(2)}</td>
                <td>${this._fmt(m.max_drawdown)}</td>
                <td>${this._pnl(m.excess_return_pnl)}</td>
                <td><b>${this._fmt(r.score)}</b></td>
                <td><button class="btn btn-sm btn-primary" onclick="OptimizerPanel.apply(${idx})">应用此参数</button></td>
            </tr>`;
        }).join('');
    },

    /** 把指定结果写回选中管道 */
    async apply(idx) {
        if (this._running) return;
        const pipeline = this._pipelines.find(p => p.id === document.getElementById('opt-pipeline')?.value);
        const sorted = [...this._results].sort((a, b) => b.score - a.score);
        const row = sorted[idx];
        if (!pipeline || !row) return;

        const strategies = JSON.parse(JSON.stringify(pipeline.strategies || []));
        row.params.forEach(p => {
            strategies.forEach(st => {
                if (st.id !== p.sid) return;
                if (!st.params || typeof st.params !== 'object') st.params = {};
                st.params[p.key] = p.value;
            });
        });

        try {
            await API.updatePipeline(pipeline.id, { strategies });
            const idxInPipe = this._pipelines.findIndex(p => p.id === pipeline.id);
            if (idxInPipe >= 0) this._pipelines[idxInPipe] = { ...pipeline, strategies };
            Toast.success(`已应用参数到管道「${pipeline.name}」`);
        } catch (e) {
            // Toast 已由 API 层显示
        }
    },

    // ==================== 工具 ====================

    _shortAddr(a) {
        if (!a) return '—';
        return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
    },

    _round(v) {
        const n = Number(v);
        if (!isFinite(n)) return 0;
        return Math.abs(n) >= 1000 ? Math.round(n) : Math.round(n * 1000) / 1000;
    },

    _fmt(v) {
        const n = Number(v || 0);
        const s = Math.abs(n) >= 1000 ? n.toFixed(0) : n.toFixed(2);
        return n > 0 ? `+${s}` : s;
    },

    _pct(v) {
        const n = Number(v || 0);
        return `${(n > 1 ? n : n * 100).toFixed(1)}%`;
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
