/**
 * WebSocket 客户端模块
 * 自动重连（指数退避）、心跳保活、消息分发到订阅者
 */

const WS = {
    socket: null,
    channels: new Set(),
    handlers: {},       // { type: [handler, ...] }
    _reconnectDelay: 1000,
    _maxReconnectDelay: 30000,
    _heartbeatTimer: null,
    _heartbeatInterval: 30000,   // 30s 心跳
    _reconnectTimer: null,
    _forceClose: false,
    _msgCount: 0,

    // Polymarket Market Channel WS 状态（由后端 ws_status 事件推送）
    marketWsStatus: {
        connected: false,
        subscribed_markets: [],
        price_cache_size: 0,
        uptime: 0,
        enabled: false,
    },
    // 最近一笔交易的实时延迟（ms）：交易时间戳到推送时间的差值
    _lastTradeLatency: null,

    /** 连接 WebSocket */
    connect() {
        if (this.socket && this.socket.readyState === WebSocket.OPEN) return;
        this._forceClose = false;
        this._updateStatus('connecting', '连接中...');

        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        try {
            this.socket = new WebSocket(`${protocol}//${location.host}/ws`);
        } catch (e) {
            this._scheduleReconnect();
            return;
        }

        this.socket.onopen = () => {
            this._reconnectDelay = 1000; // 重置退避
            this._updateStatus('connected', '已连接');
            this._startHeartbeat();

            // 重新订阅所有频道
            if (this.channels.size > 0) {
                this.send({ type: 'subscribe', channels: [...this.channels] });
            }
        };

        this.socket.onclose = () => {
            this._stopHeartbeat();
            this._updateStatus('disconnected', '未连接');
            if (!this._forceClose) this._scheduleReconnect();
        };

        this.socket.onerror = () => {
            // onclose 会随后触发重连
        };

        this.socket.onmessage = (event) => {
            this._msgCount++;
            let msg;
            try {
                msg = JSON.parse(event.data);
            } catch (e) {
                console.warn('WS 消息解析失败:', event.data);
                return;
            }
            // 内部处理 ws_status：更新 Market Channel WS 状态
            if (msg.type === 'ws_status' && msg.data) {
                this.marketWsStatus = {
                    connected: msg.data.connected || false,
                    subscribed_markets: msg.data.subscribed_markets || [],
                    price_cache_size: msg.data.price_cache_size || 0,
                    uptime: msg.data.uptime || 0,
                    enabled: msg.data.enabled !== false,
                };
                this._updateMarketWsStatus();
            }
            // 内部处理 trade_update：计算实时延迟
            if (msg.type === 'trade_update' && msg.data && msg.data.timestamp) {
                const tradeTs = new Date(msg.data.timestamp).getTime();
                const now = Date.now();
                this._lastTradeLatency = now - tradeTs;
            }
            this._dispatch(msg.type, msg.data);
        };
    },

    /** 主动断开 */
    disconnect() {
        this._forceClose = true;
        this._stopHeartbeat();
        if (this._reconnectTimer) clearTimeout(this._reconnectTimer);
        if (this.socket) {
            this.socket.onclose = null;
            this.socket.close();
        }
        this._updateStatus('disconnected', '未连接');
    },

    /** 发送消息 */
    send(data) {
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify(data));
            return true;
        }
        return false;
    },

    /** 订阅频道 */
    subscribe(channels) {
        channels.forEach(c => this.channels.add(c));
        return this.send({ type: 'subscribe', channels: [...this.channels] });
    },

    /** 取消订阅 */
    unsubscribe(channels) {
        channels.forEach(c => this.channels.delete(c));
        return this.send({ type: 'unsubscribe', channels });
    },

    /** 注册消息处理器 */
    on(type, handler) {
        if (!this.handlers[type]) this.handlers[type] = [];
        this.handlers[type].push(handler);
    },

    /** 注销消息处理器 */
    off(type, handler) {
        if (!this.handlers[type]) return;
        this.handlers[type] = this.handlers[type].filter(h => h !== handler);
    },

    /** 分发消息到处理器 */
    _dispatch(type, data) {
        const handlers = this.handlers[type] || [];
        handlers.forEach(h => {
            try { h(data); }
            catch (e) { console.error(`WS handler [${type}] 异常:`, e); }
        });
    },

    /** 指数退避重连 */
    _scheduleReconnect() {
        if (this._reconnectTimer) clearTimeout(this._reconnectTimer);
        const delay = this._reconnectDelay;
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, this._maxReconnectDelay);
        this._updateStatus('disconnected', `${Math.round(delay / 1000)}s 后重连...`);
        this._reconnectTimer = setTimeout(() => this.connect(), delay);
    },

    /** 心跳 */
    _startHeartbeat() {
        this._stopHeartbeat();
        this._heartbeatTimer = setInterval(() => {
            this.send({ type: 'ping', ts: Date.now() });
        }, this._heartbeatInterval);
    },

    _stopHeartbeat() {
        if (this._heartbeatTimer) {
            clearInterval(this._heartbeatTimer);
            this._heartbeatTimer = null;
        }
    },

    /** 更新状态指示器 */
    _updateStatus(cls, text) {
        const dot = document.getElementById('ws-status');
        const label = document.getElementById('ws-status-text');
        if (dot) dot.className = `status-dot ${cls}`;
        if (label) label.textContent = text;
    },

    /** 更新 Market Channel WS 状态指示器 */
    _updateMarketWsStatus() {
        const dot = document.getElementById('market-ws-status');
        const label = document.getElementById('market-ws-status-text');
        if (!dot || !label) return;
        const s = this.marketWsStatus;
        if (!s.enabled) {
            dot.className = 'status-dot disconnected';
            label.textContent = '实时价格未启用';
        } else if (s.connected) {
            dot.className = 'status-dot connected';
            const count = s.subscribed_markets.length;
            label.textContent = `实时价格 · ${count}市场`;
        } else {
            dot.className = 'status-dot connecting';
            label.textContent = '实时价格连接中...';
        }
    },

    /** 获取统计 */
    getStats() {
        return {
            msgCount: this._msgCount,
            connected: this.socket?.readyState === WebSocket.OPEN,
            marketWs: this.marketWsStatus,
            latency: this._lastTradeLatency,
        };
    },
};
