/**
 * 钱包管理面板
 * 添加钱包表单、钱包列表（状态/交易数/启用/删除）、排行榜导入（勾选导入）
 */

const WalletsPanel = {
    _wallets: [],
    _leaderboard: [],
    _selectedLb: new Set(),

    async render() {
        const el = document.getElementById('panel-wallets');
        el.innerHTML = `
            <div class="panel-header"><h2>👛 钱包监控</h2></div>

            <!-- 添加钱包 -->
            <div class="card">
                <h3>➕ 添加监控钱包</h3>
                <div class="form-row">
                    <div class="form-group" style="flex:2;">
                        <label>钱包地址</label>
                        <input id="wallet-address" placeholder="0x..." onkeydown="if(event.key==='Enter')WalletsPanel.add()">
                    </div>
                    <div class="form-group" style="flex:1;">
                        <label>别名（可选）</label>
                        <input id="wallet-alias" placeholder="例如：巨鲸A" onkeydown="if(event.key==='Enter')WalletsPanel.add()">
                    </div>
                    <div class="form-group" style="flex:0 0 auto;">
                        <label>&nbsp;</label>
                        <button class="btn" onclick="WalletsPanel.add()">添加</button>
                    </div>
                </div>
            </div>

            <!-- 排行榜导入 -->
            <div class="card">
                <div class="card-header-row">
                    <h3>🏆 排行榜导入</h3>
                    <div class="card-actions">
                        <select id="lb-window" style="width:auto;">
                            <option value="day">日榜</option>
                            <option value="week" selected>周榜</option>
                            <option value="month">月榜</option>
                            <option value="all">总榜</option>
                        </select>
                        <button class="btn btn-secondary btn-sm" onclick="WalletsPanel.loadLeaderboard()">加载</button>
                        <button class="btn btn-success btn-sm" onclick="WalletsPanel.importSelected()" id="lb-import-btn" disabled>导入选中</button>
                    </div>
                </div>
                <div id="leaderboard-area">
                    <div class="empty-state"><div class="empty-icon">🏆</div>点击「加载」获取排行榜</div>
                </div>
            </div>

            <!-- 监控列表 -->
            <div class="card">
                <h3>📋 监控钱包列表</h3>
                <div class="table-wrapper">
                    <table>
                        <thead><tr>
                            <th>备注名 / 用户名</th><th>擅长板块（手动标记）</th><th>地址</th><th>状态</th>
                            <th>交易数</th><th>最后检查</th><th>操作</th>
                        </tr></thead>
                        <tbody id="wallets-tbody"></tbody>
                    </table>
                </div>
            </div>
        `;
        await this.refresh();
    },

    async refresh() {
        const tbody = document.getElementById('wallets-tbody');
        if (!tbody) return;
        try {
            this._wallets = await API.getWallets();
            AppState.wallets = this._wallets;
            if (this._wallets.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="text-muted text-center">暂无监控钱包</td></tr>';
                return;
            }
            tbody.innerHTML = this._wallets.map(w => `
                <tr id="wallet-row-${w.address}">
                    <td><strong>${Utils.escape(w.alias || w.username || '-')}</strong><div class="text-muted">${Utils.escape(w.username || '尚未获取用户名')}</div></td>
                    <td title="${Utils.escape(w.notes || '')}">${Utils.escape((w.specialties || []).join(' / ') || '待标记')}</td>
                    <td class="text-mono" title="${Utils.escape(w.address)}">${Utils.shortAddr(w.address, 8, 6)}</td>
                    <td>
                        <label class="toggle-switch">
                            <input type="checkbox" ${w.enabled ? 'checked' : ''} onchange="WalletsPanel.toggle('${w.address}')">
                            <span class="toggle-slider"></span>
                        </label>
                    </td>
                    <td>${w.trade_count ?? 0}</td>
                    <td class="text-muted">${Utils.relTime(w.last_checked)}</td>
                    <td>
                        <button class="btn btn-secondary btn-sm" onclick="WalletsPanel.edit('${w.address}')">编辑资料</button>
                        <button class="btn btn-secondary btn-sm" onclick="WalletsPanel.fetchProfile('${w.address}')">获取用户名</button>
                        <button class="btn btn-danger btn-sm" onclick="WalletsPanel.remove('${w.address}')">🗑 删除</button>
                    </td>
                </tr>
            `).join('');
        } catch (e) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-muted text-center">加载失败</td></tr>';
        }
    },

    async add() {
        const address = document.getElementById('wallet-address').value.trim();
        const alias = document.getElementById('wallet-alias').value.trim();
        if (!address) { Toast.warning('请输入钱包地址'); return; }
        if (!address.startsWith('0x')) { Toast.warning('地址应以 0x 开头'); return; }
        try {
            await API.addWallet(address, alias);
            Toast.success(`已添加钱包 ${Utils.shortAddr(address)}`);
            document.getElementById('wallet-address').value = '';
            document.getElementById('wallet-alias').value = '';
            await this.refresh();
        } catch (e) { /* Toast 已由 API 显示 */ }
    },

    async fetchProfile(address) {
        try { await API.refreshWalletProfile(address); Toast.success('已获取公开用户名'); await this.refresh(); } catch (_) {}
    },

    edit(address) {
        const w = this._wallets.find(x => x.address === address);
        if (!w) return;
        Modal.show('钱包资料', `
            <p class="text-muted">板块标签为你的人工判断，不代表系统已验证该钱包盈利能力。</p>
            <div class="form-group"><label>备注名</label><input id="wp-alias" value="${Utils.escape(w.alias || '')}"></div>
            <div class="form-group"><label>用户名</label><input id="wp-username" value="${Utils.escape(w.username || '')}"></div>
            <div class="form-group"><label>擅长板块（逗号分隔）</label><input id="wp-specialties" placeholder="体育, 政治, 加密货币" value="${Utils.escape((w.specialties || []).join(', '))}"></div>
            <div class="form-group"><label>观察备注</label><textarea id="wp-notes">${Utils.escape(w.notes || '')}</textarea></div>`, [
            {text:'取消', class:'btn-secondary'},
            {text:'保存', class:'btn-primary', onClick:async (modal, overlay) => {
                try {
                    await API.updateWalletProfile(address, {
                        alias: modal.querySelector('#wp-alias').value.trim(),
                        username: modal.querySelector('#wp-username').value.trim(),
                        specialties: modal.querySelector('#wp-specialties').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean),
                        notes: modal.querySelector('#wp-notes').value.trim(),
                    });
                    Modal.close(overlay); Toast.success('钱包资料已保存'); await this.refresh();
                } catch (_) {}
            }}]);
    },

    async toggle(address) {
        try {
            await API.toggleWallet(address);
            Toast.info('钱包状态已切换');
            await this.refresh();
        } catch (e) { await this.refresh(); }
    },

    async remove(address) {
        Modal.confirm(
            `确定删除钱包 <code class="text-mono">${Utils.shortAddr(address, 8, 6)}</code> 吗？<br>该操作不可撤销。`,
            async () => {
                try {
                    await API.deleteWallet(address);
                    Toast.success('钱包已删除');
                    await this.refresh();
                } catch (e) { /* Toast 已显示 */ }
            },
            '删除',
            'btn-danger'
        );
    },

    async loadLeaderboard() {
        const window = document.getElementById('lb-window').value;
        const area = document.getElementById('leaderboard-area');
        area.innerHTML = '<div class="loading"><div class="spinner"></div>加载排行榜中...</div>';
        try {
            this._leaderboard = await API.getLeaderboard(window, 20);
            this._selectedLb.clear();
            if (this._leaderboard.length === 0) {
                area.innerHTML = '<div class="empty-state">暂无排行榜数据</div>';
                return;
            }
            area.innerHTML = `
                <div class="table-wrapper" style="max-height:400px;">
                    <table>
                        <thead><tr>
                            <th style="width:40px;"><input type="checkbox" id="lb-select-all" onchange="WalletsPanel.toggleAll(this.checked)"></th>
                            <th>排名</th><th>用户名 / 钱包</th><th>PnL</th><th>交易量</th><th>市场数</th>
                        </tr></thead>
                        <tbody>
                            ${this._leaderboard.map((e, i) => `
                                <tr>
                                    <td><input type="checkbox" class="lb-checkbox" data-idx="${i}" onchange="WalletsPanel.onLbSelect()"></td>
                                    <td>#${e.rank}</td>
                                    <td class="text-mono" title="${Utils.escape(e.wallet)}">${Utils.escape(e.username || "未公开")}<br>${Utils.shortAddr(e.wallet, 8, 6)}</td>
                                    <td class="${e.pnl >= 0 ? 'text-success' : 'text-danger'}">${Utils.fmtUSD(e.pnl)}</td>
                                    <td class="text-mono">$${Utils.fmtNum(e.volume)}</td>
                                    <td>${e.markets_traded ?? '-'}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
            document.getElementById('lb-import-btn').disabled = true;
        } catch (e) {
            area.innerHTML = '<div class="empty-state"><div class="empty-icon">⚠️</div>排行榜加载失败</div>';
        }
    },

    toggleAll(checked) {
        document.querySelectorAll('.lb-checkbox').forEach(cb => {
            cb.checked = checked;
            const idx = parseInt(cb.dataset.idx);
            if (checked) this._selectedLb.add(idx); else this._selectedLb.delete(idx);
        });
        document.getElementById('lb-import-btn').disabled = this._selectedLb.size === 0;
    },

    onLbSelect() {
        this._selectedLb.clear();
        document.querySelectorAll('.lb-checkbox:checked').forEach(cb => this._selectedLb.add(parseInt(cb.dataset.idx)));
        document.getElementById('lb-import-btn').disabled = this._selectedLb.size === 0;
    },

    async importSelected() {
        if (this._selectedLb.size === 0) return;
        const wallets = [];
        const aliases = [];
        const usernames = [];
        this._selectedLb.forEach(idx => {
            const e = this._leaderboard[idx];
            wallets.push(e.wallet);
            aliases.push(e.username || `Rank${e.rank}`);
            usernames.push(e.username || "");
        });
        try {
            const result = await API.importLeaderboard(wallets, aliases, usernames);
            const count = result?.imported ?? wallets.length;
            Toast.success(`已导入 ${count} 个钱包`);
            this._selectedLb.clear();
            document.getElementById('lb-import-btn').disabled = true;
            document.querySelectorAll('.lb-checkbox').forEach(cb => cb.checked = false);
            document.getElementById('lb-select-all').checked = false;
            await this.refresh();
        } catch (e) { /* Toast 已显示 */ }
    },

    onWalletStatus(data) {
        // 更新单个钱包行
        const row = document.getElementById(`wallet-row-${data.address}`);
        if (row) {
            row.classList.add('flash');
            setTimeout(() => row.classList.remove('flash'), 1500);
        }
        this.refresh();
    },
};
