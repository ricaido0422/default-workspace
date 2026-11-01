/**
 * 主应用模块
 * 全局状态、Toast 通知、模态框/确认对话框、面板路由、WS 事件分发
 */

// ============================================
// Toast 通知系统
// ============================================
const Toast = {
    _container: null,
    _id: 0,

    _getContainer() {
        if (!this._container) this._container = document.getElementById('toast-container');
        return this._container;
    },

    show(message, type = 'info', duration = 3500) {
        const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
        const id = `toast-${++this._id}`;
        const el = document.createElement('div');
        el.className = `toast toast-${type}`;
        el.id = id;
        el.innerHTML = `<span class="toast-icon">${icons[type] || icons.info}</span><span>${message}</span>`;
        this._getContainer().appendChild(el);

        if (duration > 0) {
            setTimeout(() => this.remove(id), duration);
        }
        return id;
    },

    remove(id) {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.add('removing');
        setTimeout(() => el.remove(), 300);
    },

    success(msg, dur) { return this.show(msg, 'success', dur); },
    error(msg, dur)   { return this.show(msg, 'error', dur || 5000); },
    warning(msg, dur) { return this.show(msg, 'warning', dur); },
    info(msg, dur)    { return this.show(msg, 'info', dur); },
};

// ============================================
// 模态框 / 确认对话框
// ============================================
const Modal = {
    _container: null,

    _getContainer() {
        if (!this._container) this._container = document.getElementById('modal-container');
        return this._container;
    },

    /**
     * 显示模态框
     * @param {string} title - 标题
     * @param {string|HTMLElement} body - 内容（HTML 字符串或元素）
     * @param {array} buttons - [{text, class, onClick}] 默认 [关闭]
     */
    show(title, body, buttons) {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';

        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.innerHTML = `<h3>${title}</h3><div class="modal-body"></div><div class="modal-footer"></div>`;

        const bodyEl = modal.querySelector('.modal-body');
        if (typeof body === 'string') bodyEl.innerHTML = body;
        else bodyEl.appendChild(body);

        const footer = modal.querySelector('.modal-footer');
        const btns = buttons || [{ text: '关闭', class: 'btn-secondary', onClick: null }];
        btns.forEach(b => {
            const btn = document.createElement('button');
            btn.className = `btn ${b.class || ''}`;
            btn.textContent = b.text;
            btn.onclick = () => {
                if (b.onClick) b.onClick(modal, overlay);
                else this.close(overlay);
            };
            footer.appendChild(btn);
        });

        overlay.appendChild(modal);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) this.close(overlay);
        });
        this._getContainer().appendChild(overlay);
        return { modal, overlay };
    },

    close(overlay) {
        if (overlay) overlay.remove();
    },

    /**
     * 确认对话框（危险操作二次确认）
     * @param {string} message - 提示信息
     * @param {function} onConfirm - 确认回调
     * @param {string} confirmText - 确认按钮文字
     * @param {string} confirmClass - 确认按钮样式
     */
    confirm(message, onConfirm, confirmText = '确认', confirmClass = 'btn-danger') {
        const { overlay } = this.show('⚠️ 确认操作', `<p>${message}</p>`, [
            { text: '取消', class: 'btn-secondary' },
            { text: confirmText, class: confirmClass, onClick: (modal, ov) => { this.close(ov); onConfirm(); } },
        ]);
    },
};

// ============================================
// 全局状态
// ============================================
const AppState = {
    stats: null,
    wallets: [],
    pipelines: [],
    availableStrategies: [],
    config: null,
    executorMode: 'paper',

    // 实时数据缓存（面板间共享）
    recentTrades: [],      // 最近交易（最多 200 条）
    recentSignals: [],     // 最近信号（最多 100 条）
    recentExecutions: [],  // 最近执行（最多 100 条）

    _maxTrades: 200,
    _maxSignals: 100,
    _maxExecs: 100,

    addTrade(trade) {
        this.recentTrades.unshift(trade);
        if (this.recentTrades.length > this._maxTrades) this.recentTrades.pop();
    },
    addSignal(signal) {
        this.recentSignals.unshift(signal);
        if (this.recentSignals.length > this._maxSignals) this.recentSignals.pop();
    },
    addExecution(exec) {
        this.recentExecutions.unshift(exec);
        if (this.recentExecutions.length > this._maxExecs) this.recentExecutions.pop();
    },
};

// ============================================
// 工具函数
// ============================================
const Utils = {
    walletLabel(address) {
        const w = (AppState.wallets || []).find(x => x.address.toLowerCase() === (address || '').toLowerCase());
        return this.escape(w?.alias || w?.username || this.shortAddr(address, 8, 6));
    },
    /** 截断地址显示 */
    shortAddr(addr, head = 6, tail = 4) {
        if (!addr) return '-';
        return addr.length > head + tail + 2 ? `${addr.slice(0, head)}...${addr.slice(-tail)}` : addr;
    },

    /** 格式化时间 */
    fmtTime(ts) {
        if (!ts) return '-';
        const d = new Date(ts);
        return d.toLocaleTimeString('zh-CN', { hour12: false });
    },

    /** 格式化日期时间 */
    fmtDateTime(ts) {
        if (!ts) return '-';
        const d = new Date(ts);
        return d.toLocaleString('zh-CN', { hour12: false });
    },

    /** 相对时间 */
    relTime(ts) {
        if (!ts) return '-';
        const diff = Date.now() - new Date(ts).getTime();
        if (diff < 60000) return `${Math.floor(diff / 1000)}秒前`;
        if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
        return `${Math.floor(diff / 86400000)}天前`;
    },

    /** 格式化数字 */
    fmtNum(n, decimals = 2) {
        if (n == null || isNaN(n)) return '-';
        return Number(n).toFixed(decimals);
    },

    /** 格式化货币 */
    fmtUSD(n) {
        if (n == null || isNaN(n)) return '-';
        const sign = n >= 0 ? '+' : '';
        return `${sign}$${Number(n).toFixed(2)}`;
    },

    /** HTML 转义 */
    escape(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    /** 防抖 */
    debounce(fn, delay = 300) {
        let timer;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), delay);
        };
    },

    /** 生成唯一 ID */
    uid(prefix = 'id') {
        return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    },
};

// ============================================
// 主应用
// ============================================
const App = {
    currentPanel: 'dashboard',
    _panels: {
        dashboard: () => DashboardPanel,
        desk: () => DeskPanel,
        'copy-hub': () => CopyHubPanel,
        wallets:   () => WalletsPanel,
        trades:    () => TradesPanel,
        strategies:() => StrategiesPanel,
        smart:     () => SmartPanel,
        execution: () => ExecutionPanel,
        backtest:  () => BacktestPanel,
        analytics: () => AnalyticsPanel,
        optimizer: () => OptimizerPanel,
        portfolio: () => PortfolioPanel,
        positions: () => PositionsPanel,
        'wallet-positions': () => WalletPositionsPanel,
        settings:  () => SettingsPanel,
    },

    async init() {
        // 导航切换
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', () => this.switchPanel(item.dataset.panel,item.dataset.view));
        });

        // 连接 WebSocket
        WS.connect();
        LiveControls.init();

        // 注册 WS 消息处理
        WS.on('trade_update',     (d) => this.onTradeUpdate(d));
        WS.on('signal_generated', (d) => this.onSignalGenerated(d));
        WS.on('execution_update', (d) => this.onExecutionUpdate(d));
        WS.on('stats_update',     (d) => this.onStatsUpdate(d));
        WS.on('wallet_status',    (d) => this.onWalletStatus(d));
        WS.on('ws_status',        (d) => this.onWsStatus(d));
        WS.on('subscribed',       (d) => console.log('WS 已订阅:', d));
        WS.on('error',            (d) => Toast.error(`WS 错误: ${d?.message || '未知'}`));
        WS.on('pong',             () => {}); // 心跳响应

        // 订阅所有频道
        WS.subscribe(['trade_update', 'signal_generated', 'execution_update', 'stats_update', 'wallet_status', 'ws_status', 'platform_trade', 'sync_status', 'settlement_update']);
        WS.on('platform_trade', () => { if(document.getElementById('desk-feed') && !DeskPanel._pushTimer) DeskPanel._pushTimer=setTimeout(()=>{DeskPanel._pushTimer=null;DeskPanel.loadFeed();},500); });
        WS.on('sync_status', d => DeskPanel.syncStatus(d));
        WS.on('settlement_update', () => DeskPanel.refresh());

        // 加载初始面板
        try { AppState.wallets = await API.getWallets(); } catch (_) {}
        const [initialPanel,initialView]=location.hash.slice(1).split('/');
        this.switchPanel(this._panels[initialPanel]?initialPanel:'copy-hub',initialView);
        window.addEventListener('hashchange',()=>{const [panel,view]=location.hash.slice(1).split('/');if(this._panels[panel]) this.switchPanel(panel,view);});

        console.log('🚀 Polymarket Trader 已启动');
    },

    switchPanel(name,view) {
        if (!this._panels[name]) return;
        view=view||(name==='copy-hub'?'discover':name==='desk'?'trade':'');
        if(name==='copy-hub') CopyHubPanel.tab=view==='rules'?'rules':'discover';
        if(name==='desk') DeskPanel.view=view;
        this.currentView=view;
        // 离开旧面板前，给面板一个清理定时器的机会（如交易流统计轮询）
        const prev = this._panels[this.currentPanel]?.();
        if (prev && prev.onLeave) prev.onLeave();
        document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        this.activateNav(name,view);
        document.getElementById(`panel-${name}`)?.classList.add('active');
        this.currentPanel = name;
        const scroller=document.querySelector('.workspace-panels');if(scroller) scroller.scrollTop=0;

        const panel = this._panels[name]();
        if (panel && panel.render) panel.render();
    },

    activateNav(name,view='') {
        document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.panel===name&&(!el.dataset.view||el.dataset.view===view)));
        const titles={'copy-hub':view==='rules'?'我的跟单':'发现交易员',desk:({trade:'市场与买卖',queue:'订单审核',positions:'持仓与结算',risk:'交易风控',analysis:'收益归因'})[view]||'交易台',wallets:'观察钱包',settings:'设置与连接'};
        const active=document.querySelector('.nav-item.active');
        document.getElementById('workspace-title').textContent=titles[name]||active?.textContent.trim()||'工作台';
        if(active?.closest('.advanced-nav')) active.closest('.advanced-nav').open=true;
        history.replaceState(null,'','#'+name+(view?'/'+view:''));
    },
    updateQueueBadge(rows) {
        const count=rows.filter(r=>r.status==='waiting'&&!r.expired).length;
        document.querySelectorAll('[data-queue-count]').forEach(el=>{el.textContent=count;el.hidden=!count;});
    },

    // ==================== WS 事件处理 ====================

    onTradeUpdate(data) {
        AppState.addTrade(data);
        if (this.currentPanel === 'trades')    TradesPanel.onTradeUpdate(data);
        if (this.currentPanel === 'dashboard') DashboardPanel.onTradeUpdate(data);
    },

    onSignalGenerated(data) {
        AppState.addSignal(data);
        if (this.currentPanel === 'dashboard') DashboardPanel.onSignalUpdate(data);
        if (this.currentPanel === 'execution') ExecutionPanel.onSignalUpdate(data);
    },

    onExecutionUpdate(data) {
        AppState.addExecution(data);
        if (this.currentPanel === 'execution') ExecutionPanel.onExecutionUpdate(data);
        if (this.currentPanel === 'dashboard') DashboardPanel.onExecutionUpdate(data);
    },

    onStatsUpdate(data) {
        AppState.stats = data;
        if (this.currentPanel === 'dashboard') DashboardPanel.onStatsUpdate(data);
    },

    onWalletStatus(data) {
        if (this.currentPanel === 'wallets')   WalletsPanel.onWalletStatus(data);
        if (this.currentPanel === 'dashboard') DashboardPanel.onWalletStatus(data);
    },

    onWsStatus(data) {
        if (this.currentPanel === 'dashboard') DashboardPanel.onWsStatus(data);
    },
};

// 启动
document.addEventListener('DOMContentLoaded', () => App.init());
