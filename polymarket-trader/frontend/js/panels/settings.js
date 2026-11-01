/**
 * 设置面板
 * 代理配置、API 凭证输入（密码框不回显）、风险参数、执行模式切换（paper/live 带确认）、轮询间隔、通知设置
 */

const SettingsPanel = {
    _config: null,
    _notifConfig: null,

    // 通知事件类型定义（与后端 EventType 对应）
    NOTIFY_EVENTS: [
        { key: 'trade_detected',  label: '大额交易' },
        { key: 'signal_generated', label: '跟单信号' },
        { key: 'execution_filled', label: '成交' },
        { key: 'execution_failed', label: '失败' },
        { key: 'risk_blocked',     label: '风控拦截' },
        { key: 'daily_summary',    label: '每日汇总' },
    ],

    async render() {
        const el = document.getElementById('panel-settings');
        el.innerHTML = `
            <div class="panel-header"><h2>🔧 设置</h2></div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">

                <div style="grid-column:1 / -1">${LiveControls.html()}</div>
                <!-- 监控配置 -->
                <div class="card">
                    <h3>⏱ 监控配置</h3>
                    <div class="form-group">
                        <label>轮询间隔（秒）</label>
                        <div style="display:flex;gap:8px;align-items:center;">
                            <input id="monitor-interval" type="number" min="1" max="3600" value="2" style="width:80px;">
                            <button class="btn btn-sm" onclick="SettingsPanel.saveInterval()">保存</button>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>交易查询条数</label>
                        <input id="trade-limit" type="number" min="10" max="500" value="50" style="width:80px;">
                    </div>

                    <!-- 订单金额监测范围 -->
                    <div class="text-muted" style="font-size:12px;margin:14px 0 8px;">
                        💰 <strong style="color:var(--text-primary);">订单金额监测范围</strong>：只监测成交金额（价格 × 数量）落在此区间内的交易，
                        区间外的交易直接忽略。留空或填 0 = 不限制。
                    </div>
                    <div class="form-group">
                        <label>最小金额（USDC）</label>
                        <div style="display:flex;gap:8px;align-items:center;">
                            <input id="monitor-min-amount" type="number" min="0" step="10" placeholder="0 = 不限制" style="width:110px;"
                                   oninput="SettingsPanel.updateAmountRangePreview()">
                            <span class="text-muted">≤</span>
                            <label>最大金额（USDC）</label>
                            <input id="monitor-max-amount" type="number" min="0" step="100" placeholder="0 = 不限制" style="width:110px;"
                                   oninput="SettingsPanel.updateAmountRangePreview()">
                        </div>
                    </div>
                    <div id="amount-range-preview" class="text-muted" style="font-size:12px;margin-bottom:8px;"></div>
                    <button class="btn btn-sm" onclick="SettingsPanel.saveAmountRange()">保存金额范围</button>
                </div>

                <!-- 代理配置 -->
                <div class="card">
                    <h3>🌐 代理配置</h3>
                    <div class="form-group">
                        <label>HTTP 代理</label>
                        <input id="proxy-http" placeholder="http://127.0.0.1:7890">
                    </div>
                    <div class="form-group">
                        <label>HTTPS 代理</label>
                        <input id="proxy-https" placeholder="http://127.0.0.1:7890">
                    </div>
                    <button class="btn btn-sm" onclick="SettingsPanel.saveProxy()">保存代理</button>
                </div>

                <!-- 风险参数 -->
                <div class="card">
                    <h3>🛡 风险参数</h3>
                    <div class="form-group">
                        <label>单笔最大金额（USD）</label>
                        <input id="risk-max-amount" type="number" min="0" step="10" placeholder="500">
                    </div>
                    <div class="form-group">
                        <label>单市场最大仓位（USD）</label>
                        <input id="risk-max-position" type="number" min="0" step="100" placeholder="5000">
                    </div>
                    <div class="form-group">
                        <label>日最大交易数</label>
                        <input id="risk-max-daily" type="number" min="0" step="1" placeholder="100">
                    </div>
                    <button class="btn btn-sm" onclick="SettingsPanel.saveRisk()">保存风险参数</button>
                </div>

                <!-- API 凭证 -->
                <div class="card" style="grid-column:1 / -1;">
                    <h3>实盘账户接入</h3><p class="text-muted">多个钱包独立保存凭证；OKX 逐笔签名、Magic HMAC 后台执行、OAuth 中继 URL 与端口可配置。</p>
                    <button class="btn" onclick="AccountsUI.open()">管理账户 / 切换钱包 / 配置中继</button>
                </div>
                <div class="card" style="display:none;grid-column:1 / -1;">
                    <h3 id="live-credentials-title">🔑 Polymarket API 凭证</h3>
                    <div class="text-muted mb-16" style="font-size:12px;">
                        ⚠️ 凭证仅用于 Live 模式实盘下单。请妥善保管，切勿泄露。保存后不会回显。
                    </div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                        <div class="form-group">
                            <label>API Key</label>
                            <input id="cred-api-key" type="password" placeholder="••••••••">
                        </div>
                        <div class="form-group">
                            <label>API Secret</label>
                            <input id="cred-api-secret" type="password" placeholder="••••••••">
                        </div>
                        <div class="form-group">
                            <label>API Passphrase</label>
                            <input id="cred-api-passphrase" type="password" placeholder="••••••••">
                        </div>
                        <div class="form-group">
                            <label>钱包地址</label>
                            <input id="cred-wallet-address" placeholder="0x...">
                        </div>
                        <div class="form-group" style="grid-column:1 / -1;">
                            <label>签名类型</label>
                            <select id="cred-signature-type"><option value="0">0 · EOA 普通钱包</option><option value="1">1 · POLY_PROXY 代理钱包</option><option value="2">2 · GNOSIS_SAFE 钱包</option></select>
                        </div>
                        <div class="form-group"><label>Funder（资金钱包，代理钱包必填）</label><input id="cred-funder" placeholder="0x..."></div>
                        <div class="form-group" style="grid-column:1 / -1;">
                            <label>Private Key（签名钱包私钥）</label>
                            <input id="cred-private-key" type="password" placeholder="••••••••">
                        </div>
                    </div>
                    <button class="btn btn-sm" onclick="SettingsPanel.saveCredentials()">保存凭证并检测连接</button> <button class="btn btn-secondary btn-sm" onclick="LiveControls.check()">重新检测</button> <button class="btn btn-danger btn-sm" data-live-start disabled onclick="LiveControls.start()">启动实盘</button><p data-live-badge class="text-muted"></p><div data-live-details class="live-checks"></div>
                </div>

                <!-- 通知设置 -->
                <div class="card" style="grid-column:1 / -1;">
                    <h3>🔔 通知设置</h3>
                    <div class="text-muted mb-16" style="font-size:12px;">
                        配置 Telegram / 邮件推送，让用户不用一直盯屏幕。Bot Token 与邮箱密码保存后不会回显（留空表示保持原值）。
                    </div>

                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">

                        <!-- Telegram -->
                        <div class="form-group" style="margin-bottom:0;">
                            <label style="display:flex;align-items:center;gap:8px;">
                                <label class="toggle-switch" style="width:42px;">
                                    <input type="checkbox" id="notif-tg-enabled">
                                    <span class="toggle-slider"></span>
                                </label>
                                <span style="margin:0;font-size:13px;font-weight:600;color:var(--text-primary);">Telegram</span>
                            </label>
                            <div class="form-group" style="margin-top:10px;">
                                <label>Bot Token</label>
                                <input id="notif-tg-token" type="password" placeholder="留空 = 保持原值">
                            </div>
                            <div class="form-group">
                                <label>Chat ID（多个用逗号分隔）</label>
                                <input id="notif-tg-chat-ids" placeholder="例如: 123456789, -1001234567890">
                            </div>
                            <button class="btn btn-sm" onclick="SettingsPanel.testNotification('telegram')">发送测试</button>
                        </div>

                        <!-- 邮件 -->
                        <div class="form-group" style="margin-bottom:0;">
                            <label style="display:flex;align-items:center;gap:8px;">
                                <label class="toggle-switch" style="width:42px;">
                                    <input type="checkbox" id="notif-email-enabled">
                                    <span class="toggle-slider"></span>
                                </label>
                                <span style="margin:0;font-size:13px;font-weight:600;color:var(--text-primary);">邮件</span>
                            </label>
                            <div style="display:grid;grid-template-columns:1fr 100px;gap:8px;margin-top:10px;">
                                <div class="form-group">
                                    <label>SMTP 主机</label>
                                    <input id="notif-email-host" placeholder="smtp.qq.com">
                                </div>
                                <div class="form-group">
                                    <label>端口</label>
                                    <input id="notif-email-port" type="number" min="1" max="65535" value="587">
                                </div>
                            </div>
                            <div class="form-group">
                                <label>用户名</label>
                                <input id="notif-email-user" placeholder="you@example.com">
                            </div>
                            <div class="form-group">
                                <label>密码 / 授权码</label>
                                <input id="notif-email-password" type="password" placeholder="留空 = 保持原值">
                            </div>
                            <div class="form-group">
                                <label>发件人</label>
                                <input id="notif-email-from" placeholder="留空 = 使用用户名">
                            </div>
                            <div class="form-group">
                                <label>收件人（多个用逗号分隔）</label>
                                <input id="notif-email-to" placeholder="a@example.com, b@example.com">
                            </div>
                            <label style="display:flex;align-items:center;gap:6px;font-size:12px;">
                                <input type="checkbox" id="notif-email-ssl">
                                使用 SSL（端口 465）；否则用 STARTTLS（端口 587）
                            </label>
                            <button class="btn btn-sm" style="margin-top:10px;" onclick="SettingsPanel.testNotification('email')">发送测试</button>
                        </div>
                    </div>

                    <!-- 过滤规则 -->
                    <div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--border);">
                        <div class="form-group">
                            <label>通知事件类型</label>
                            <div style="display:flex;flex-wrap:wrap;gap:12px;" id="notif-event-checks">
                                ${this.NOTIFY_EVENTS.map(e => `
                                    <label style="display:flex;align-items:center;gap:5px;font-size:12px;margin-bottom:0;">
                                        <input type="checkbox" value="${e.key}" class="notif-event-check"> ${e.label}
                                    </label>
                                `).join('')}
                            </div>
                            <div class="text-muted" style="font-size:11px;margin-top:4px;">
                                全部取消勾选 = 通知所有事件
                            </div>
                        </div>

                        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
                            <div class="form-group">
                                <label>最小通知金额（USDC）</label>
                                <input id="notif-min-amount" type="number" min="0" step="10" placeholder="0">
                                <div class="text-muted" style="font-size:11px;">低于此金额的交易不通知，0 = 不过滤</div>
                            </div>
                            <div class="form-group">
                                <label>冷却秒数</label>
                                <input id="notif-cooldown" type="number" min="0" step="5" placeholder="0">
                                <div class="text-muted" style="font-size:11px;">同一事件的重复通知间隔，0 = 不限制</div>
                            </div>
                            <div class="form-group">
                                <label>每日汇总时间（UTC）</label>
                                <div style="display:flex;gap:6px;align-items:center;">
                                    <input id="notif-summary-hour" type="number" min="0" max="23" placeholder="8" style="width:70px;">
                                    <span class="text-muted">:</span>
                                    <input id="notif-summary-minute" type="number" min="0" max="59" placeholder="0" style="width:70px;">
                                </div>
                                <div class="text-muted" style="font-size:11px;">每天该时刻发送一次汇总</div>
                            </div>
                        </div>

                        <button class="btn btn-sm btn-success" onclick="SettingsPanel.saveNotificationConfig()">保存通知设置</button>
                    </div>
                </div>

                <!-- 最近通知记录 -->
                <div class="card" style="grid-column:1 / -1;">
                    <h3>📨 最近通知记录</h3>
                    <div id="notification-log" class="text-muted" style="font-size:12px;">加载中...</div>
                </div>

                <!-- 当前配置预览 -->
                <div class="card" style="grid-column:1 / -1;">
                    <h3>📄 当前配置（脱敏）</h3>
                    <pre id="config-display" class="text-mono text-muted" style="font-size:12px;overflow-x:auto;max-height:300px;">加载中...</pre>
                </div>
            </div>
        `;
        LiveControls.draw();
        if(this._focusLive) {this._focusLive=false;document.getElementById('live-credentials-title')?.scrollIntoView({behavior:'smooth',block:'start'});}
        await this.refresh();
    },

    async refresh() {
        try {
            this._config = await API.getConfig();
            AppState.config = this._config;

            // 显示配置
            const display = document.getElementById('config-display');
            if (display) display.textContent = JSON.stringify(this._config, null, 2);

            // 填充表单
            if (this._config.monitor) {
                const el = document.getElementById('monitor-interval');
                if (el) el.value = this._config.monitor.interval || 15;
                // 订单金额监测范围
                const minEl = document.getElementById('monitor-min-amount');
                const maxEl = document.getElementById('monitor-max-amount');
                if (minEl) minEl.value = this._config.monitor.min_amount || '';
                if (maxEl) maxEl.value = this._config.monitor.max_amount || '';
                this.updateAmountRangePreview();
            }
            if (this._config.executor) {
                const el = document.getElementById('exec-mode');
                if (el) el.value = this._config.executor.mode || 'paper';
                AppState.executorMode = this._config.executor.mode || 'paper';
            }
            if (this._config.proxy) {
                const http = document.getElementById('proxy-http');
                const https = document.getElementById('proxy-https');
                if (http) http.value = this._config.proxy.http || '';
                if (https) https.value = this._config.proxy.https || '';
            }
            if (this._config.polymarket) {
                document.getElementById('cred-signature-type').value = this._config.polymarket.signature_type ?? 0;
                document.getElementById('cred-funder').value = this._config.polymarket.funder || '';
                document.getElementById('cred-wallet-address').value = this._config.polymarket.wallet_address || '';
            }
            if (this._config.risk) {
                const amt = document.getElementById('risk-max-amount');
                const pos = document.getElementById('risk-max-position');
                const daily = document.getElementById('risk-max-daily');
                if (amt) amt.value = this._config.risk.max_trade_amount ?? '';
                if (pos) pos.value = this._config.risk.max_position_per_market ?? '';
                if (daily) daily.value = this._config.risk.max_daily_trades ?? '';
            }
        } catch (e) {
            const display = document.getElementById('config-display');
            if (display) display.textContent = '配置加载失败';
        }

        await this.refreshNotificationConfig();
        await this.refreshNotificationLogs();
    },

    // ==================== 通知设置 ====================

    /** 拉取通知配置并填充表单 */
    async refreshNotificationConfig() {
        try {
            this._notifConfig = await API.getNotificationConfig();
        } catch (e) {
            Toast.error('通知配置加载失败');
            return;
        }
        const c = this._notifConfig || {};

        const setVal = (id, v) => {
            const el = document.getElementById(id);
            if (el) el.value = v ?? '';
        };
        const setChecked = (id, v) => {
            const el = document.getElementById(id);
            if (el) el.checked = !!v;
        };

        // Telegram
        setChecked('notif-tg-enabled', c.telegram_enabled);
        const tokenEl = document.getElementById('notif-tg-token');
        if (tokenEl) {
            tokenEl.placeholder = c.telegram_token_set
                ? `${c.telegram_token}（已保存，留空 = 保持原值）`
                : '请输入 Bot Token';
        }
        setVal('notif-tg-chat-ids', (c.telegram_chat_ids || []).join(', '));

        // 邮件
        setChecked('notif-email-enabled', c.email_enabled);
        setVal('notif-email-host', c.email_host);
        setVal('notif-email-port', c.email_port || 587);
        setVal('notif-email-user', c.email_user);
        setVal('notif-email-password', '');
        const pwdEl = document.getElementById('notif-email-password');
        if (pwdEl) {
            pwdEl.placeholder = c.email_password_set ? '已保存，留空 = 保持原值' : '请输入密码 / 授权码';
        }
        setVal('notif-email-from', c.email_from);
        setVal('notif-email-to', (c.email_to || []).join(', '));
        setChecked('notif-email-ssl', c.email_use_ssl);

        // 过滤规则
        setVal('notif-min-amount', c.min_notify_amount ?? 0);
        setVal('notif-cooldown', c.cooldown_seconds ?? 0);
        setVal('notif-summary-hour', c.daily_summary_hour ?? 8);
        setVal('notif-summary-minute', c.daily_summary_minute ?? 0);

        // 事件类型复选框
        const selected = new Set(c.notify_events || []);
        document.querySelectorAll('.notif-event-check').forEach(cb => {
            cb.checked = selected.has(cb.value);
        });
    },

    /** 收集通知配置表单值 */
    collectNotificationConfig() {
        const splitIds = (id) => (document.getElementById(id)?.value || '')
            .split(',')
            .map(s => s.trim())
            .filter(Boolean);

        const events = Array.from(document.querySelectorAll('.notif-event-check'))
            .filter(cb => cb.checked)
            .map(cb => cb.value);

        return {
            telegram_enabled: document.getElementById('notif-tg-enabled')?.checked || false,
            telegram_token: (document.getElementById('notif-tg-token')?.value || '').trim(),
            telegram_chat_ids: splitIds('notif-tg-chat-ids'),
            email_enabled: document.getElementById('notif-email-enabled')?.checked || false,
            email_host: (document.getElementById('notif-email-host')?.value || '').trim(),
            email_port: parseInt(document.getElementById('notif-email-port')?.value) || 587,
            email_user: (document.getElementById('notif-email-user')?.value || '').trim(),
            email_password: document.getElementById('notif-email-password')?.value || '',
            email_from: (document.getElementById('notif-email-from')?.value || '').trim(),
            email_to: splitIds('notif-email-to'),
            email_use_ssl: document.getElementById('notif-email-ssl')?.checked || false,
            min_notify_amount: parseFloat(document.getElementById('notif-min-amount')?.value) || 0,
            notify_events: events,
            cooldown_seconds: parseFloat(document.getElementById('notif-cooldown')?.value) || 0,
            daily_summary_hour: parseInt(document.getElementById('notif-summary-hour')?.value) || 0,
            daily_summary_minute: parseInt(document.getElementById('notif-summary-minute')?.value) || 0,
        };
    },

    /** 保存通知配置 */
    async saveNotificationConfig() {
        const data = this.collectNotificationConfig();

        if (data.telegram_enabled && !data.telegram_chat_ids.length) {
            Toast.warning('启用 Telegram 时请至少填写一个 Chat ID');
            return;
        }
        if (data.email_enabled && (!data.email_host || !data.email_to.length)) {
            Toast.warning('启用邮件时必须填写 SMTP 主机与收件人');
            return;
        }

        try {
            const resp = await API.updateNotificationConfig(data);
            Toast.success('通知设置已保存');
            await this.refreshNotificationConfig();
            await this.refreshNotificationLogs();
            return resp;
        } catch (e) { /* Toast 已显示 */ }
    },

    /** 发送测试通知（先保存配置再测试） */
    async testNotification(channel) {
        const btnLabel = channel === 'email' ? '邮件' : 'Telegram';
        try {
            // 测试前先保存当前表单，保证用最新配置发送
            await this.saveNotificationConfig();

            const resp = await API.testNotification(channel);
            if (resp && resp.ok) {
                Toast.success(`${btnLabel} 测试发送成功`);
            } else {
                Toast.error(`${btnLabel} 测试失败: ${resp?.message || '未知错误'}`);
            }
        } catch (e) { /* Toast 已显示 */ }
    },

    /** 拉取最近通知记录 */
    async refreshNotificationLogs() {
        const box = document.getElementById('notification-log');
        if (!box) return;
        try {
            const logs = await API.getNotificationLogs(20);
            if (!logs || !logs.length) {
                box.innerHTML = '<span class="text-muted">暂无通知记录</span>';
                return;
            }
            const statusIcon = { success: '✅', failed: '❌' };
            const rows = logs.map(l => `
                <div style="display:flex;gap:10px;align-items:center;padding:5px 0;border-bottom:1px solid var(--border);">
                    <span style="width:150px;color:var(--text-muted);font-size:11px;">${(l.created_at || '').replace('T', ' ').slice(0, 19)}</span>
                    <span style="width:110px;font-size:11px;">${l.event_type || '-'}</span>
                    <span style="width:70px;font-size:11px;">${l.channel || '-'}</span>
                    <span style="width:50px;font-size:11px;">${statusIcon[l.status] || '•'}</span>
                    <span style="flex:1;font-size:11px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${l.error || ''}</span>
                </div>
            `).join('');
            box.innerHTML = rows;
        } catch (e) {
            box.innerHTML = '<span class="text-muted">通知记录加载失败</span>';
        }
    },

    async setMode() {
        const mode = document.getElementById('exec-mode')?.value;
        if (mode === 'live') {
            // 危险操作：二次确认
            Modal.confirm(
                '🚨 <strong>警告：您即将切换到 Live（实盘）模式</strong><br><br>' +
                '此操作后，系统将使用 <strong>真实资金</strong> 在 Polymarket 上下单。<br>' +
                '请确认：<br>' +
                '✓ 已配置正确的 API 凭证<br>' +
                '✓ 已设置合理的风险参数<br>' +
                '✓ 充分了解跟单风险<br><br>' +
                '<strong>资金损失风险自负。</strong>',
                async () => {
                    try {
                        await API.setExecutorMode('live');
                        AppState.executorMode = 'live';
                        Toast.success('已切换到 Live 模式');
                        await this.refresh();
                    } catch (e) { /* Toast 已显示 */ }
                },
                '我已了解风险，切换到 Live',
                'btn-danger'
            );
        } else {
            try {
                await API.setExecutorMode('paper');
                AppState.executorMode = 'paper';
                Toast.success('已切换到 Paper 模式');
                await this.refresh();
            } catch (e) { /* Toast 已显示 */ }
        }
    },

    async saveInterval() {
        const interval = parseInt(document.getElementById('monitor-interval').value);
        if (!interval || interval < 1) { Toast.warning('轮询间隔不能小于 1 秒'); return; }
        try {
            await API.updateConfig({ monitor_interval: interval });
            Toast.success(`轮询间隔已设为 ${interval}s`);
        } catch (e) { /* Toast 已显示 */ }
    },

    /** 实时预览当前金额范围的语义（不保存） */
    updateAmountRangePreview() {
        const minV = parseFloat(document.getElementById('monitor-min-amount')?.value) || 0;
        const maxV = parseFloat(document.getElementById('monitor-max-amount')?.value) || 0;
        const el = document.getElementById('amount-range-preview');
        if (!el) return;
        if (!minV && !maxV) {
            el.textContent = '当前：不限制金额，监测所有交易。';
            return;
        }
        if (minV && maxV && minV > maxV) {
            el.innerHTML = '<span style="color:var(--danger);">⚠️ 下限大于上限，保存后不会监测到任何交易。</span>';
            return;
        }
        const lo = minV ? `≥ $${minV}` : '不限下限';
        const hi = maxV ? `≤ $${maxV}` : '不限上限';
        el.textContent = `当前：只监测 ${lo} 且 ${hi} 的交易。`;
    },

    async saveAmountRange() {
        const minV = parseFloat(document.getElementById('monitor-min-amount').value) || 0;
        const maxV = parseFloat(document.getElementById('monitor-max-amount').value) || 0;
        if (minV && maxV && minV > maxV) {
            Toast.warning('最小金额不能大于最大金额');
            return;
        }
        try {
            await API.updateConfig({ monitor_min_amount: minV, monitor_max_amount: maxV });
            const desc = (!minV && !maxV)
                ? '已取消金额限制（监测全部交易）'
                : `已设为 ${minV ? `≥ $${minV}` : '不限'} ~ ${maxV ? `≤ $${maxV}` : '不限'}`;
            Toast.success(`订单金额监测范围：${desc}`);
            this.updateAmountRangePreview();
            await this.refresh();
        } catch (e) { /* Toast 已显示 */ }
    },

    async saveProxy() {
        const proxy = {
            http: document.getElementById('proxy-http').value.trim(),
            https: document.getElementById('proxy-https').value.trim(),
        };
        try {
            await API.updateConfig({ proxy });
            Toast.success('代理配置已保存');
            await this.refresh();
        } catch (e) { /* Toast 已显示 */ }
    },

    async saveRisk() {
        const risk = {
            max_trade_amount: parseFloat(document.getElementById('risk-max-amount').value) || 0,
            max_position_per_market: parseFloat(document.getElementById('risk-max-position').value) || 0,
            max_daily_trades: parseInt(document.getElementById('risk-max-daily').value) || 0,
        };
        try {
            await API.updateConfig({ risk });
            Toast.success('风险参数已保存');
            await this.refresh();
        } catch (e) { /* Toast 已显示 */ }
    },

    async checkLive() {
        const el = document.getElementById('live-preflight-result');
        if (el) el.textContent = '正在检查认证、余额与授权...';
        try {
            const r = await API.getLivePreflight();
            if (el) el.textContent = `认证通过 · 余额 ${r.balance_usdc} USDC · 授权${r.allowance_ready ? '已就绪' : '未就绪'} · ${r.ready ? '可申请启用实盘' : '尚不可启用实盘'}`;
        } catch(e) { if (el) el.textContent = e.message; }
    },

    async stopLive() {
        try { await API.setExecutorMode('paper'); AppState.executorMode = 'paper'; Toast.success('已停止后续实盘下单；已提交订单仍需到交易所核对'); await this.refresh(); } catch(_) {}
    },

    async saveCredentials() {
        const apiKey = document.getElementById('cred-api-key').value.trim();
        const apiSecret = document.getElementById('cred-api-secret').value.trim();
        const passphrase = document.getElementById('cred-api-passphrase').value.trim();
        const walletAddress = document.getElementById('cred-wallet-address').value.trim();
        const privateKey = document.getElementById('cred-private-key').value.trim();


        Modal.confirm(
            '确定应用当前账户凭证吗？<br>使用 Windows 当前用户加密保存，不回显；留空保留原值。',
            async () => {
                try {
                    const creds = {};
                    if (apiKey) creds.api_key = apiKey;
                    if (apiSecret) creds.api_secret = apiSecret;
                    if (passphrase) creds.api_passphrase = passphrase;
                    creds.wallet_address = walletAddress;
                    if (privateKey) creds.private_key = privateKey;
                    creds.signature_type = parseInt(document.getElementById('cred-signature-type').value);
                    const funder = document.getElementById('cred-funder').value.trim();
                    creds.funder = funder;
                    await API.setExecutorCredentials(creds);
                    Toast.success('凭证已保存，请检测连接后再启动实盘');
                    await LiveControls.refresh();
                    // 清空密码框
                    ['cred-api-key', 'cred-api-secret', 'cred-api-passphrase', 'cred-private-key'].forEach(id => {
                        document.getElementById(id).value = '';
                    });
                    await LiveControls.check();
                } catch (e) { /* Toast 已显示 */ }
            },
            '保存',
            'btn-success'
        );
    },
};
