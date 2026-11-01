/**
 * API 客户端模块
 * 封装所有 REST API 调用，统一错误处理，自动 Toast 反馈
 */

const API = {
    baseUrl: '/api',
    _silent: false, // 静默模式：不弹 Toast

    /**
     * 底层请求方法
     * @param {string} method - HTTP 方法
     * @param {string} path - API 路径（不含 /api 前缀）
     * @param {object|null} body - 请求体
     * @param {boolean} silent - 是否静默（不弹错误 Toast）
     * @returns {Promise<any>}
     */
    async request(method, path, body, silent = false) {
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        if (body) opts.body = JSON.stringify(body);

        try {
            const resp = await fetch(this.baseUrl + path, opts);

            // 非 2xx 错误
            if (!resp.ok) {
                let errMsg = `API ${resp.status}: ${resp.statusText}`;
                try {
                    const errBody = await resp.json();
                    if (errBody.detail) errMsg = typeof errBody.detail === 'string' ? errBody.detail : JSON.stringify(errBody.detail);
                    else if (errBody.message) errMsg = errBody.message;
                } catch (_) { /* 非 JSON 错误体 */ }

                if (!silent) Toast.error(errMsg);
                throw new Error(errMsg);
            }

            // 204 No Content
            if (resp.status === 204) return null;

            return await resp.json();
        } catch (e) {
            // 网络错误（fetch 本身失败）
            if (e instanceof TypeError) {
                const msg = '网络请求失败，请检查后端服务是否运行';
                if (!silent) Toast.error(msg);
                throw new Error(msg);
            }
            throw e; // 已处理过的错误
        }
    },

    // ==================== 钱包管理 ====================
    updateWalletProfile(address, data) { return this.request('PUT', `/wallets/${encodeURIComponent(address)}/profile`, data); },
    refreshWalletProfile(address) { return this.request('POST', `/wallets/${encodeURIComponent(address)}/profile/refresh`); },
    getLivePreflight() { return this.request('GET', '/executor/preflight'); },
    reconcileExecution(id) { return this.request('POST', `/executions/${encodeURIComponent(id)}/reconcile`); },
    getWallets()              { return this.request('GET', '/wallets'); },
    addWallet(address, alias) { return this.request('POST', '/wallets', { address, alias }); },
    deleteWallet(address)     { return this.request('DELETE', `/wallets/${address}`); },
    toggleWallet(address)     { return this.request('POST', `/wallets/${address}/toggle`); },

    // ==================== 排行榜 ====================
    getLeaderboard(window = 'week', limit = 20) {
        return this.request('GET', `/leaderboard?window=${window}&limit=${limit}`, null, true);
    },
    importLeaderboard(wallets, aliases, usernames) {
        return this.request('POST', '/wallets/import-leaderboard', { wallets, aliases, usernames: usernames || [] });
    },

    // ==================== 策略流水线 ====================
    getPipelines()           { return this.request('GET', '/pipelines'); },
    createPipeline(name)     { return this.request('POST', '/pipelines', { name }); },
    updatePipeline(id, data) { return this.request('PUT', `/pipelines/${id}`, data); },
    deletePipeline(id)       { return this.request('DELETE', `/pipelines/${id}`); },

    // ==================== 策略类型 ====================
    getAvailableStrategies() { return this.request('GET', '/strategies/available'); },

    // ==================== 交易与执行 ====================
    getTrades(wallet = '', limit = 50) {
        const q = wallet ? `?wallet=${wallet}&limit=${limit}` : `?limit=${limit}`;
        return this.request('GET', `/trades${q}`, null, true);
    },
    getTradesStats(wallet = '') {
        const q = wallet ? `?wallet=${wallet}` : '';
        return this.request('GET', `/trades/stats${q}`, null, true);
    },
    getExecutions(limit = 50) { return this.request('GET', `/executions?limit=${limit}`, null, true); },

    // ==================== 仪表盘 ====================
    getStats() { return this.request('GET', '/stats', null, true); },

    // ==================== 配置管理 ====================
    getConfig()    { return this.request('GET', '/config', null, true); },
    updateConfig(data) { return this.request('PUT', '/config', data); },

    // ==================== 执行器 ====================
    setExecutorMode(mode) { return this.request('POST', '/executor/mode', { mode }); },
    setExecutorCredentials(creds) { return this.request('POST', '/executor/credentials', creds); },

    // ==================== 通知推送 ====================
    getNotificationConfig() {
        return this.request('GET', '/notifications/config', null, true);
    },
    updateNotificationConfig(data) {
        return this.request('PUT', '/notifications/config', data);
    },
    testNotification(channel) {
        return this.request('POST', '/notifications/test', { channel });
    },
    getNotificationLogs(limit = 50) {
        return this.request('GET', `/notifications/log?limit=${limit}`, null, true);
    },

    // ==================== 策略回测 ====================
    /** 运行回测：wallet + (pipelineId | pipelineConfig) + params */
    runBacktest(wallet, pipelineId, params, pipelineConfig) {
        const body = { wallet, params: params || {} };
        if (pipelineConfig) body.pipeline_config = pipelineConfig;
        else if (pipelineId) body.pipeline_id = pipelineId;
        return this.request('POST', '/backtest/run', body);
    },
    /** 多钱包对比回测 */
    compareBacktest(wallets, pipelineId, params, pipelineConfig) {
        const body = { wallets, params: params || {} };
        if (pipelineConfig) body.pipeline_config = pipelineConfig;
        else if (pipelineId) body.pipeline_id = pipelineId;
        return this.request('POST', '/backtest/compare', body);
    },
    /** 获取可用于回测的策略类型及参数 schema */
    getBacktestStrategies() { return this.request('GET', '/backtest/strategies', null, true); },

    // ==================== 绩效分析 ====================
    /** 绩效总览：信号数/执行数/成交率/盈亏/胜率/延迟分布 */
    getAnalyticsSummary(days = 7) {
        return this.request('GET', `/analytics/summary?days=${days}`, null, true);
    },
    /** 按钱包分组的绩效 */
    getAnalyticsWallets(days = 7) {
        return this.request('GET', `/analytics/wallets?days=${days}`, null, true);
    },
    /** 按策略分组的绩效（归因） */
    getAnalyticsStrategies(days = 7) {
        return this.request('GET', `/analytics/strategies?days=${days}`, null, true);
    },
    /** 按市场分组的绩效（盈利/亏损 Top10） */
    getAnalyticsMarkets(days = 7) {
        return this.request('GET', `/analytics/markets?days=${days}`, null, true);
    },
    /** 每日绩效时间序列 */
    getAnalyticsDaily(days = 30) {
        return this.request('GET', `/analytics/daily?days=${days}`, null, true);
    },

    // ==================== 链路自检（模拟注入） ====================
    /** 注入一笔模拟交易，走完整跟单链路（仅 paper 模式） */
    simulateTrade(data) {
        return this.request('POST', '/simulate/trade', data);
    },
    /** 链路自检前置条件状态 */
    getSimulateStatus() {
        return this.request('GET', '/simulate/status', null, true);
    },

    // ==================== 组合跟单 ====================
    getPortfolio()        { return this.request('GET', '/portfolio', null, true); },
    updatePortfolio(data) { return this.request('PUT', '/portfolio', data); },
    getPortfolioExposure() { return this.request('GET', '/portfolio/exposure', null, true); },
    getPortfolioWeights()  { return this.request('GET', '/portfolio/weights', null, true); },
    checkPortfolio(wallet, amount) {
        return this.request('POST', `/portfolio/check?wallet=${encodeURIComponent(wallet)}&amount=${amount}`, null, true);
    },
    /** 虚拟持仓盯市快照（模拟资金账户，按实盘价结算） */
    getVirtualPositions(wallet) {
        const q = wallet ? `?wallet=${encodeURIComponent(wallet)}` : '';
        return this.request('GET', `/portfolio/positions${q}`, null, true);
    },
    /** 清空虚拟持仓（重置模拟账户） */
    resetVirtualPositions(wallet) {
        const q = wallet ? `?wallet=${encodeURIComponent(wallet)}` : '';
        return this.request('POST', `/portfolio/positions/reset${q}`, null, true);
    },
    /** 目标钱包真实持仓（Polymarket 链上持仓，非模拟） */
    getWalletPositions(wallet = '', status = 'OPEN') {
        const params = new URLSearchParams();
        if (wallet) params.set('wallet', wallet);
        params.set('status', status);
        return this.request('GET', `/wallets/positions?${params.toString()}`, null, true);
    },

    // ==================== WebSocket 状态 ====================
    getWsStatus()              { return this.request('GET', '/ws/status', null, true); },
    wsSubscribe(tokenIds)      { return this.request('POST', '/ws/subscribe', { token_ids: tokenIds }); },
    getWsPrice(tokenId)        { return this.request('GET', `/ws/price/${tokenId}`, null, true); },
};
