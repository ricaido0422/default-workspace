/**
 * 策略工坊面板
 * 左侧可用策略列表（10种）、中间管道编排区（拖拽/排序/开关/参数表单）、
 * 右侧已保存管道列表、管道可视化、保存/加载配置
 */

const StrategiesPanel = {
    _available: [],
    _pipelines: [],
    _currentPipeline: null,   // 当前编辑的管道
    _draggedStrategy: null,   // 拖拽中的策略类型

    async render() {
        const el = document.getElementById('panel-strategies');
        el.innerHTML = `
            <div class="panel-header">
                <h2>⚙️ 策略工坊</h2>
                <div class="header-actions">
                    <button class="btn btn-sm" onclick="StrategiesPanel.savePipeline()" id="strat-save-btn">💾 保存管道</button>
                </div>
            </div>
            <div class="three-col">
                <!-- 左侧：可用策略 -->
                <div class="card" style="margin-bottom:0;">
                    <h3>📦 可用策略</h3>
                    <div id="available-strategies"></div>
                </div>

                <!-- 中间：管道编排区 -->
                <div class="card" style="margin-bottom:0;">
                    <div class="card-header-row">
                        <h3>🔧 管道编排</h3>
                        <div class="card-actions">
                            <input id="pipeline-name-input" placeholder="管道名称" style="width:140px;">
                        </div>
                    </div>
                    <div id="pipeline-drop-zone" class="pipeline-drop-zone">
                        将左侧策略拖拽到此处开始编排
                    </div>
                    <div id="pipeline-visual" class="pipeline-visual mt-16" style="display:none;"></div>
                    <div id="pipeline-steps" class="mt-16"></div>
                </div>

                <!-- 右侧：已保存管道 -->
                <div class="card" style="margin-bottom:0;">
                    <h3>📋 已保存管道</h3>
                    <div style="display:flex;gap:6px;margin-bottom:12px;">
                        <button class="btn btn-sm" style="flex:1;" onclick="StrategiesPanel.newPipeline()">➕ 新建</button>
                    </div>
                    <div id="pipelines-list"></div>
                </div>
            </div>
        `;

        // 初始化拖拽放置区
        this._initDropZone();

        await this.refresh();
    },

    async refresh() {
        try {
            const [available, pipelines] = await Promise.all([
                API.getAvailableStrategies(),
                API.getPipelines(),
            ]);
            this._available = available;
            this._pipelines = pipelines;
            AppState.availableStrategies = available;
            AppState.pipelines = pipelines;

            this._renderAvailable();
            this._renderPipelinesList();
        } catch (e) {
            document.getElementById('available-strategies').innerHTML = '<div class="text-muted">加载失败</div>';
        }
    },

    // ==================== 左侧：可用策略 ====================

    _renderAvailable() {
        const el = document.getElementById('available-strategies');
        if (!el) return;
        if (this._available.length === 0) {
            el.innerHTML = '<div class="text-muted text-center mt-16">暂无策略</div>';
            return;
        }
        el.innerHTML = this._available.map(s => `
            <div class="strategy-source" draggable="true" data-type="${s.type}"
                 ondragstart="StrategiesPanel.onDragStart(event, '${s.type}')"
                 ondragend="StrategiesPanel.onDragEnd(event)"
                 title="${Utils.escape(s.description)}">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <strong>${Utils.escape(s.name)}</strong>
                    <span class="badge badge-${(s.category || '').toLowerCase()}">${s.category}</span>
                </div>
                <div class="text-muted" style="font-size:11px;margin-top:2px;">${Utils.escape(s.type)}</div>
            </div>
        `).join('');
    },

    // ==================== 拖拽 ====================

    onDragStart(event, type) {
        this._draggedStrategy = type;
        event.dataTransfer.effectAllowed = 'copy';
        event.dataTransfer.setData('text/plain', type);
        event.target.classList.add('dragging');
    },

    onDragEnd(event) {
        event.target.classList.remove('dragging');
        this._draggedStrategy = null;
        const dz = document.getElementById('pipeline-drop-zone');
        if (dz) dz.classList.remove('drag-active');
    },

    _initDropZone() {
        const dz = document.getElementById('pipeline-drop-zone');
        if (!dz) return;

        dz.ondragover = (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
            dz.classList.add('drag-active');
        };
        dz.ondragleave = () => dz.classList.remove('drag-active');
        dz.ondrop = (e) => {
            e.preventDefault();
            dz.classList.remove('drag-active');
            const type = e.dataTransfer.getData('text/plain');
            if (type) this.addStrategyToPipeline(type);
        };

        // 也支持拖到 visual 区域
        const visual = document.getElementById('pipeline-visual');
        if (visual) {
            visual.ondragover = (e) => { e.preventDefault(); };
            visual.ondrop = (e) => {
                e.preventDefault();
                const type = e.dataTransfer.getData('text/plain');
                if (type) this.addStrategyToPipeline(type);
            };
        }
    },

    // ==================== 管道编排 ====================

    newPipeline() {
        this._currentPipeline = {
            id: null,
            name: '',
            strategies: [],
            enabled: true,
            target_wallets: [],
        };
        document.getElementById('pipeline-name-input').value = '';
        this._renderPipeline();
        Toast.info('已创建空管道，拖拽策略开始编排');
    },

    addStrategyToPipeline(type) {
        if (!this._currentPipeline) this.newPipeline();
        const info = this._available.find(s => s.type === type);
        if (!info) return;

        // 构建默认参数
        const params = {};
        if (info.params_schema) {
            for (const [key, schema] of Object.entries(info.params_schema)) {
                params[key] = schema.default;
            }
        }

        const strategy = {
            id: Utils.uid('st'),
            type: info.type,
            category: info.category,
            name: info.name,
            description: info.description,
            enabled: true,
            params,
            display_order: this._currentPipeline.strategies.length,
        };
        this._currentPipeline.strategies.push(strategy);
        this._renderPipeline();
        Toast.success(`已添加策略: ${info.name}`);
    },

    removeStrategy(id) {
        if (!this._currentPipeline) return;
        this._currentPipeline.strategies = this._currentPipeline.strategies.filter(s => s.id !== id);
        this._renderPipeline();
    },

    toggleStrategy(id) {
        if (!this._currentPipeline) return;
        const s = this._currentPipeline.strategies.find(x => x.id === id);
        if (s) { s.enabled = !s.enabled; this._renderPipeline(); }
    },

    moveStrategy(id, dir) {
        if (!this._currentPipeline) return;
        const arr = this._currentPipeline.strategies;
        const idx = arr.findIndex(s => s.id === id);
        if (idx < 0) return;
        const newIdx = idx + dir;
        if (newIdx < 0 || newIdx >= arr.length) return;
        [arr[idx], arr[newIdx]] = [arr[newIdx], arr[idx]];
        arr.forEach((s, i) => s.display_order = i);
        this._renderPipeline();
    },

    updateParam(strategyId, paramKey, value) {
        if (!this._currentPipeline) return;
        const s = this._currentPipeline.strategies.find(x => x.id === strategyId);
        if (s) {
            // 类型转换
            const info = this._available.find(x => x.type === s.type);
            const schema = info?.params_schema?.[paramKey];
            if (schema?.type === 'float') value = parseFloat(value);
            else if (schema?.type === 'int') value = parseInt(value);
            s.params[paramKey] = value;
        }
    },

    _renderPipeline() {
        const dz = document.getElementById('pipeline-drop-zone');
        const visual = document.getElementById('pipeline-visual');
        const steps = document.getElementById('pipeline-steps');
        if (!dz || !visual || !steps) return;

        if (!this._currentPipeline || this._currentPipeline.strategies.length === 0) {
            dz.style.display = 'flex';
            visual.style.display = 'none';
            steps.innerHTML = '';
            return;
        }

        dz.style.display = 'none';
        visual.style.display = 'flex';

        // 管道可视化
        const strs = this._currentPipeline.strategies;
        visual.innerHTML = strs.map((s, i) => {
            const arrow = i < strs.length - 1 ? '<span class="pipeline-arrow">→</span>' : '';
            return `<div class="pipeline-step ${s.enabled ? '' : 'disabled'}">${Utils.escape(s.name)}</div>${arrow}`;
        }).join('');

        // 参数表单
        steps.innerHTML = strs.map((s, i) => {
            const info = this._available.find(x => x.type === s.type);
            const paramHtml = this._renderParamForm(s, info);
            return `
                <div class="drag-item" id="strat-item-${s.id}">
                    <span class="drag-handle" onclick="StrategiesPanel.moveStrategy('${s.id}', -1)">▲</span>
                    <span class="drag-handle" onclick="StrategiesPanel.moveStrategy('${s.id}', 1)">▼</span>
                    <span class="badge badge-${(s.category || '').toLowerCase()}">${s.category}</span>
                    <strong>${Utils.escape(s.name)}</strong>
                    <label class="toggle-switch" style="margin-left:auto;">
                        <input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="StrategiesPanel.toggleStrategy('${s.id}')">
                        <span class="toggle-slider"></span>
                    </label>
                    <button class="btn btn-danger btn-sm btn-icon" onclick="StrategiesPanel.removeStrategy('${s.id}')">✕</button>
                </div>
                ${paramHtml}
            `;
        }).join('');
    },

    _renderParamForm(strategy, info) {
        if (!info?.params_schema || Object.keys(info.params_schema).length === 0) {
            return ''; // 无参数
        }
        const params = info.params_schema;
        const fields = Object.entries(params).map(([key, schema]) => {
            const val = strategy.params[key] ?? schema.default;
            const label = schema.label || key;
            let input;
            if (schema.type === 'bool') {
                input = `<input type="checkbox" ${val ? 'checked' : ''} onchange="StrategiesPanel.updateParam('${strategy.id}','${key}',this.checked)">`;
            } else if (schema.type === 'int' || schema.type === 'float') {
                const min = schema.min != null ? `min="${schema.min}"` : '';
                const max = schema.max != null ? `max="${schema.max}"` : '';
                const step = schema.type === 'float' ? 'step="0.01"' : 'step="1"';
                input = `<input type="number" value="${val}" ${min} ${max} ${step} onchange="StrategiesPanel.updateParam('${strategy.id}','${key}',this.value)" style="width:100px;">`;
            } else if (schema.options) {
                const opts = schema.options.map(o => `<option value="${o}" ${val === o ? 'selected' : ''}>${o}</option>`).join('');
                input = `<select onchange="StrategiesPanel.updateParam('${strategy.id}','${key}',this.value)" style="width:auto;">${opts}</select>`;
            } else {
                input = `<input type="text" value="${Utils.escape(String(val))}" onchange="StrategiesPanel.updateParam('${strategy.id}','${key}',this.value)" style="width:120px;">`;
            }
            return `<div style="display:flex;align-items:center;gap:6px;margin-left:52px;margin-bottom:4px;"><span class="text-muted" style="font-size:12px;width:100px;">${label}:</span>${input}</div>`;
        }).join('');
        return fields;
    },

    // ==================== 右侧：已保存管道 ====================

    _renderPipelinesList() {
        const el = document.getElementById('pipelines-list');
        if (!el) return;
        if (this._pipelines.length === 0) {
            el.innerHTML = '<div class="text-muted text-center mt-16">暂无管道<br>点击「新建」开始</div>';
            return;
        }
        el.innerHTML = this._pipelines.map(p => `
            <div class="drag-item" style="cursor:pointer;" onclick="StrategiesPanel.loadPipeline('${p.id}')">
                <strong>${Utils.escape(p.name)}</strong>
                <div style="margin-left:auto;display:flex;align-items:center;gap:6px;">
                    <span class="badge badge-${p.enabled ? 'enabled' : 'disabled'}">${p.enabled ? '启用' : '停用'}</span>
                    <span class="text-muted" style="font-size:11px;">${p.strategies?.length || 0}步</span>
                </div>
            </div>
            <div style="display:flex;gap:4px;margin-bottom:8px;margin-left:8px;">
                <button class="btn btn-sm ${p.enabled ? 'btn-warning' : 'btn-success'}" onclick="event.stopPropagation();StrategiesPanel.togglePipeline('${p.id}')" style="flex:1;">${p.enabled ? '停用' : '启用'}</button>
                <button class="btn btn-danger btn-sm" onclick="event.stopPropagation();StrategiesPanel.deletePipeline('${p.id}')" style="flex:1;">删除</button>
            </div>
        `).join('');
    },

    async loadPipeline(id) {
        const p = this._pipelines.find(x => x.id === id);
        if (!p) return;
        this._currentPipeline = JSON.parse(JSON.stringify(p)); // 深拷贝
        document.getElementById('pipeline-name-input').value = p.name;
        this._renderPipeline();
        Toast.info(`已加载管道: ${p.name}`);
    },

    async savePipeline() {
        if (!this._currentPipeline) { Toast.warning('没有正在编辑的管道'); return; }
        const name = document.getElementById('pipeline-name-input').value.trim();
        if (!name) { Toast.warning('请输入管道名称'); return; }
        if (this._currentPipeline.strategies.length === 0) { Toast.warning('管道为空，请添加策略'); return; }

        this._currentPipeline.name = name;
        this._currentPipeline.strategies.forEach((s, i) => s.display_order = i);

        try {
            if (this._currentPipeline.id) {
                await API.updatePipeline(this._currentPipeline.id, {
                    strategies: this._currentPipeline.strategies,
                    enabled: this._currentPipeline.enabled,
                    target_wallets: this._currentPipeline.target_wallets,
                });
                Toast.success(`管道「${name}」已更新`);
            } else {
                const result = await API.createPipeline(name);
                this._currentPipeline.id = result.id;
                await API.updatePipeline(result.id, {
                    strategies: this._currentPipeline.strategies,
                    enabled: true,
                    target_wallets: [],
                });
                Toast.success(`管道「${name}」已创建`);
            }
            await this.refresh();
        } catch (e) { /* Toast 已显示 */ }
    },

    async togglePipeline(id) {
        const p = this._pipelines.find(x => x.id === id);
        if (!p) return;
        try {
            await API.updatePipeline(id, { enabled: !p.enabled });
            Toast.info(`管道已${p.enabled ? '停用' : '启用'}`);
            await this.refresh();
        } catch (e) { /* Toast 已显示 */ }
    },

    async deletePipeline(id) {
        const p = this._pipelines.find(x => x.id === id);
        Modal.confirm(
            `确定删除管道「${Utils.escape(p?.name || id)}」吗？`,
            async () => {
                try {
                    await API.deletePipeline(id);
                    Toast.success('管道已删除');
                    if (this._currentPipeline?.id === id) {
                        this._currentPipeline = null;
                        this._renderPipeline();
                    }
                    await this.refresh();
                } catch (e) { /* Toast 已显示 */ }
            },
            '删除',
            'btn-danger'
        );
    },
};
