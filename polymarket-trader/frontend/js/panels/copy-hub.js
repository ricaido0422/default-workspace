/** 发现钱包与快捷跟单。真实数据只来自现有平台连接和本地台账。 */
const CopyHubPanel = {
    rows: [], rules: [], selected: null, mode: 'PAPER', tab: 'discover', loading: false,
    esc(v) { return Utils.escape(String(v ?? '')); },
    money(v) { return v == null ? '—' : '$' + Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}); },
    async render() {
        document.getElementById('panel-copy-hub').innerHTML = `
        <div class="panel-header"><div><h2 id="copy-page-title">发现交易员</h2><p class="text-muted">先观察板块与成交，再设置跟单金额和退出规则。</p></div><div class="copy-actions"><span id="copy-mode" class="copy-mode">加载账户…</span><button class="btn" onclick="CopyHubPanel.openEditor(true)">＋ 新建跟单</button></div></div>
        <div id="copy-stats" class="stats-grid"></div>
        <div class="copy-layout"><section class="card copy-list"><div class="copy-actions"><button id="copy-tab-discover" class="btn" onclick="CopyHubPanel.setTab('discover')">发现交易员</button><button id="copy-tab-rules" class="btn btn-secondary" onclick="CopyHubPanel.setTab('rules')">我的跟单</button><button class="btn btn-secondary" onclick="CopyHubPanel.refresh()">刷新</button></div>
        <div id="copy-discover-controls" class="copy-filters"><input id="copy-search" aria-label="搜索交易员" placeholder="搜索用户名、备注或地址" oninput="CopyHubPanel.drawList()"><select id="copy-window" aria-label="榜单时间范围" onchange="CopyHubPanel.loadDiscovery()"><option value="week">近7天</option><option value="month">近30天</option><option value="day">近24小时</option><option value="all">全部时间</option></select><select id="copy-sector" aria-label="人工板块标签" onchange="CopyHubPanel.drawList()"><option value="">全部人工标签</option><option>体育</option><option>政治</option><option>加密货币</option><option>经济</option><option>未分类</option></select><label><input id="copy-watched" type="checkbox" onchange="CopyHubPanel.drawList()"> 仅已观察</label></div>
        <p id="copy-list-status" class="text-muted copy-note">正在读取官方榜单…</p><div class="table-wrapper"><table><thead id="copy-head"></thead><tbody id="copy-body"></tbody></table></div>
        <p class="text-muted copy-note">官方榜单盈亏不是本工具跟单收益。板块来自人工标签；无完整历史成本时不展示推断胜率。高级策略仍独立运行，避免同钱包重复跟单。</p></section>
        <dialog id="copy-editor-drawer" class="copy-editor"><div class="drawer-heading"><div><span class="workspace-kicker">COPY CONFIGURATION</span><h3 id="copy-editor-title">配置跟单</h3></div><button type="button" class="icon-close" aria-label="关闭跟单配置" onclick="CopyHubPanel.closeEditor()">×</button></div><p id="copy-selected" class="text-muted copy-note">选择左侧交易员，或直接填写钱包地址。</p>
        <form id="copy-form" onsubmit="return false"><label>钱包地址<input id="copy-wallet" required pattern="0x[0-9a-fA-F]{40}" placeholder="0x…" oninput="CopyHubPanel.newAddress()"></label>
        <label>规则名称<input id="copy-name" required maxlength="80" placeholder="如：体育钱包 · 小额观察"></label>
        <label>金额方式<select id="copy-sizing" onchange="CopyHubPanel.updateSizing()"><option value="fixed">固定 USDC 金额</option><option value="ratio">按源交易份额比例</option></select></label>
        <div class="copy-pair copy-sizing-fields"><label>固定金额 USDC<input id="copy-amount" type="number" required min="1" max="10000" step="0.01" value="10"></label><label>比例（0.1＝10%）<input id="copy-ratio" type="number" required min="0.001" max="10" step="0.001" value="0.1"></label></div>
        <div class="copy-pair"><label>单笔金额上限 USDC<input id="copy-max_amount" type="number" required min="1" max="10000" step="0.01" value="25"></label><label>源成交最小 USDC<input id="copy-min_source_amount" type="number" required min="0" max="10000000" step="0.01" value="0"></label></div>
        <details class="secondary-fields"><summary>买入价格过滤</summary><div class="copy-pair"><label>买入价格下限<input id="copy-min_price" type="number" required min="0.001" max="0.999" step="0.001" value="0.01"></label><label>买入价格上限<input id="copy-max_price" type="number" required min="0.001" max="0.999" step="0.001" value="0.99"></label></div></details>
        <label><input id="copy-follow_sells" type="checkbox" checked> 跟随卖出（受本规则已买入份额限制）</label><label><input id="copy-enabled" type="checkbox" checked> 启用规则</label>
        <p id="copy-bind" class="copy-mode copy-note"></p><button id="copy-save" type="button" class="btn" onclick="CopyHubPanel.save()">保存跟单规则</button> <button type="button" class="btn btn-secondary" onclick="CopyHubPanel.clearEditor()">新建</button>
        </form><p class="text-muted copy-note">同账户模式共享资金。止盈止损、价差与深度上限在交易台统一设置。半自动进入审核队列；自动模式保存后仅处理新成交。暂停不会撤销已提交订单。</p><button class="btn btn-secondary" onclick="App.switchPanel('desk','risk')">止盈止损与风控</button></dialog></div>`;
        LiveControls.draw();
        this.clearEditor(); this.setTab(this.tab);
        await this.refresh();
    },
    async refresh() {
        await this.loadState();
        await this.loadDiscovery();
    },
    async loadState() {
        try {
            const [r,s] = await Promise.all([API.request('GET','/desk/copy-rules'), API.request('GET','/desk/state')]);
            this.rules=r.rules; this.mode=r.mode; App.updateQueueBadge(s.queue);
            if(!document.getElementById('copy-mode')) return;
            document.getElementById('copy-mode').textContent=r.mode==='LIVE'?'LIVE · 真实资金':'PAPER · 模拟资金';
            document.getElementById('copy-stats').innerHTML=[['当前可用资金',this.money(s.summary.cash_balance)],['本工具持仓市值',this.money(s.summary.market_value)],['持仓浮动盈亏',this.money(s.summary.floating_pnl)],['执行方式',({observe:'仅观察',review:'半自动审核',auto:'自动执行'})[r.workflow]]].map(([label,value])=>`<div class="stat-card"><div class="label">${label}</div><div class="value">${value}</div></div>`).join('');
            this.drawBind(); this.drawList();
        } catch(e) { const el=document.getElementById('copy-list-status'); if(el) el.textContent='账户状态读取失败：'+e.message; }
    },
    async loadDiscovery() {
        const serial=(this._discoverySerial||0)+1; this._discoverySerial=serial; this.loading=true;
        const el=document.getElementById('copy-list-status'); if(!el) {this.loading=false;return;}
        const period=document.getElementById('copy-window').value;
        el.textContent='正在读取官方榜单…';
        try {
            const r=await API.request('GET','/desk/discovery?window='+period,null,true);
            if(serial!==this._discoverySerial || !document.getElementById('copy-list-status')) return;
            this.rows=r.rows;
            el.textContent=r.leaderboard_available?'官方榜单已更新 · 本地标签用于辅助筛选':'官方榜单暂不可用，保留已观察钱包；可刷新重试';
            this.drawList();
        } catch(e) {if(serial===this._discoverySerial) {el.textContent='榜单读取失败：'+e.message; this.drawList();}} finally {this.loading=false;}
    },
    setTab(tab) {
        this.tab=tab;
        const title=document.getElementById('copy-page-title');if(title) title.textContent=tab==='rules'?'我的跟单':'发现交易员';
        if(App.currentPanel==='copy-hub') {App.currentView=tab;App.activateNav('copy-hub',tab);}
        document.getElementById('copy-discover-controls').hidden=tab!=='discover';
        for(const name of ['discover','rules']) document.getElementById('copy-tab-'+name).className='btn'+(name===tab?'':' btn-secondary');
        this.drawList();
    },
    drawList() {
        const body=document.getElementById('copy-body'); if(!body) return;
        const head=document.getElementById('copy-head');
        if(this.tab==='rules') {
            head.innerHTML='<tr><th>规则 / 钱包</th><th>模式 / 状态</th><th>金额 / 上限</th><th>操作</th></tr>';
            body.innerHTML=this.rules.map((r,i)=>`<tr><td>${this.esc(r.name)}<br><small>${this.esc(r.wallet)}</small></td><td>${r.mode} · ${r.enabled?(r.mode===this.mode?'已启用':'等待切换账户'):'已暂停'}</td><td>${r.sizing==='fixed'?this.money(r.amount):this.esc(r.ratio*100)+'%'}<br>上限 ${this.money(r.max_amount)}</td><td><button class="btn btn-secondary btn-sm" onclick="CopyHubPanel.edit(${i})">编辑</button> <button class="btn btn-secondary btn-sm" onclick="CopyHubPanel.toggle(${i})">${r.enabled?'暂停':'恢复'}</button></td></tr>`).join('')||'<tr><td colspan="4">尚无快捷规则，先选择一个交易员。</td></tr>';
            return;
        }
        head.innerHTML='<tr><th>交易员 / 人工板块</th><th>官方区间盈亏 / 成交量</th><th>本地观测成交</th><th>操作</th></tr>';
        const q=document.getElementById('copy-search').value.trim().toLowerCase(), tag=document.getElementById('copy-sector').value, watched=document.getElementById('copy-watched').checked;
        body.innerHTML=this.rows.map((r,i)=>({r,i})).filter(({r})=>(!q||[r.wallet,r.alias,r.username].join(' ').toLowerCase().includes(q))&&(!tag||(r.specialties||[]).includes(tag))&&(!watched||r.watched)).map(({r,i})=>`<tr><td><strong>${this.esc(r.alias||r.username||Utils.shortAddr(r.wallet))}</strong><br><small>${this.esc(Utils.shortAddr(r.wallet))} · ${this.esc((r.specialties||[]).join(' / ')||'未标记板块')}</small></td><td>${this.money(r.pnl)}<br><small>成交量 ${this.money(r.volume)}</small></td><td>${r.watched?r.trade_count+' 笔':'尚未观察'}</td><td><button class="btn btn-sm" onclick="CopyHubPanel.pick(${i})">配置跟单</button></td></tr>`).join('')||'<tr><td colspan="4">无匹配钱包；可调整筛选或直接输入地址。</td></tr>';
    },
    drawBind() {const el=document.getElementById('copy-bind'); if(el) el.textContent=`规则绑定：${this.selected?.mode||this.mode} · 保持当前全局执行方式`;},
    clearEditor() {
        this.selected=null;
        const form=document.getElementById('copy-form'); if(!form) return;
        form.reset(); this.updateSizing(); document.getElementById('copy-wallet').readOnly=false;
        document.getElementById('copy-editor-title').textContent='快捷配置跟单';
        document.getElementById('copy-selected').textContent='选择左侧交易员，或直接填写钱包地址。'; this.drawBind();
    },
    newAddress() {this.selected=null;this.drawBind();},
    pick(index) {
        const row=this.rows[index]; if(!row) return;
        const rule=this.rules.find(r=>r.wallet.toLowerCase()===row.wallet.toLowerCase()&&r.mode===this.mode);
        if(rule) {this.edit(this.rules.indexOf(rule)); return;}
        this.clearEditor(); document.getElementById('copy-wallet').value=row.wallet;
        document.getElementById('copy-name').value=(row.alias||row.username||Utils.shortAddr(row.wallet))+' · 跟单';
        this.openEditor();
        document.getElementById('copy-selected').textContent=(row.alias||row.username||row.wallet)+' · '+((row.specialties||[]).join(' / ')||'未标记板块');
    },
    edit(index) {
        const r=this.rules[index];this.selected={...r};
        for(const [k,v] of Object.entries(r)) {const el=document.getElementById('copy-'+k); if(el) {if(el.type==='checkbox') el.checked=v;else el.value=v;}}
        this.openEditor(); this.updateSizing();
        document.getElementById('copy-wallet').readOnly=true;
        document.getElementById('copy-editor-title').textContent='编辑跟单规则';
        document.getElementById('copy-selected').textContent=r.name+' · 修改后旧审核单会失效';this.drawBind();
    },
    openEditor(reset=false) {if(reset) this.clearEditor();const el=document.getElementById('copy-editor-drawer');if(el&&!el.open) el.showModal();},
    closeEditor() {document.getElementById('copy-editor-drawer')?.close();},
    onLeave() {this.closeEditor();},
    updateSizing() {const fixed=document.getElementById('copy-sizing').value==='fixed';document.getElementById('copy-amount').closest('label').hidden=!fixed;document.getElementById('copy-ratio').closest('label').hidden=fixed;},
    async save() {
        if(!document.getElementById('copy-form').reportValidity()) return;
        const r={id:this.selected?.id||'copy_'+crypto.randomUUID().replaceAll('-',''),mode:this.selected?.mode||this.mode,account_id:this.selected?.account_id||'legacy'};
        for(const k of ['wallet','name','sizing','amount','ratio','max_amount','min_source_amount','min_price','max_price','follow_sells','enabled']) {
            const el=document.getElementById('copy-'+k);r[k]=el.type==='checkbox'?el.checked:(el.type==='number'?Number(el.value):el.value.trim());
        }
        const run=async()=>{const button=document.getElementById('copy-save');button.disabled=true;try {await API.request('PUT','/desk/copy-rules/'+r.id,r);this.selected=r;this.closeEditor();Toast.success('规则已保存，新成交按当前执行方式处理');await this.loadState();this.setTab('rules');}catch(_){}finally{button.disabled=false;}};
        if(r.mode==='LIVE'&&r.enabled) {this.closeEditor();Modal.confirm('启用此实盘规则后，新成交会进入当前执行流程；全局为自动时会使用真实资金。确认前请检查金额与风控。',run,'保存实盘跟单');}else await run();
    },
    toggle(index) {
        const r={...this.rules[index],enabled:!this.rules[index].enabled};
        const run=async()=>{try {await API.request('PUT','/desk/copy-rules/'+r.id,r);await this.loadState();Toast.success(r.enabled?'规则已恢复':'规则已暂停，旧审核单已失效');}catch(_){}};
        if(r.mode==='LIVE'&&r.enabled) Modal.confirm('恢复后按全局执行方式处理新的真实资金跟单。',run,'恢复实盘规则');else run();
    },
};
