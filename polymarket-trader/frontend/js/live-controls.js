/** 统一实盘连接与模式入口。状态检测只读，启动时后端重新检查。 */
const LiveControls = {
    state: null, busy: false,
    html() { return `<section class="card live-control"><div class="copy-actions"><h3>模拟 / 实盘操作</h3><strong data-live-badge>读取状态…</strong></div><p class="text-muted">模拟盘：平台真实行情与结算、虚拟资金。实盘：对接 Polymarket CLOB，使用真实资金。</p><div class="copy-actions"><button class="btn btn-secondary" onclick="LiveControls.configure()">配置实盘</button><button class="btn btn-secondary" data-live-check onclick="LiveControls.check()">检测实盘连接</button><button class="btn btn-danger" data-live-start disabled onclick="LiveControls.start()">启动实盘</button><button class="btn btn-secondary" onclick="LiveControls.paper()">切回模拟盘 / 停止实盘</button></div><div data-live-details class="live-checks"></div><p data-live-note class="text-muted"></p></section>`; },
    open() {document.getElementById('account-drawer').showModal();this.refresh();},
    init() { document.getElementById('account-controls').innerHTML=this.html(); this.refresh(); this.timer=setInterval(()=>this.refresh(),10000); },
    async refresh() {
        try {this.state=await API.request('GET','/executor/status',null,true); this.draw();}
        catch(_) {document.body.dataset.liveState='unknown';document.querySelectorAll('[data-shell-connection]').forEach(el=>el.textContent='连接状态未知');document.querySelectorAll('[data-live-badge]').forEach(el=>{el.textContent='状态不可用';el.style.color='var(--warning)';});document.querySelectorAll('[data-live-start]').forEach(el=>el.disabled=true);}
    },
    draw() {
        const s=this.state;if(!s) return;
        document.querySelectorAll('[data-shell-mode]').forEach(el=>el.textContent=s.mode==='LIVE'?'实盘 · 真实资金':'模拟盘 · 虚拟资金');
        document.querySelectorAll('[data-shell-connection]').forEach(el=>el.textContent=({unconfigured:'未配置实盘',unchecked:'待检测',stale:'需重新检测',ready:'检查通过',blocked:'连接异常',pending_orders:'订单待核对'})[s.state]||'待检测');
        document.body.classList.toggle('live-mode',s.mode==='LIVE');
        document.querySelectorAll('[data-shell-paper]').forEach(el=>el.hidden=s.mode!=='LIVE');
        document.body.dataset.liveState=s.state;
        const labels={unconfigured:'未配置完整',unchecked:'尚未检测',stale:'检测结果已过期',ready:'前置检查通过',blocked:'检查未通过',pending_orders:'有待核对订单，暂停新实盘'};
        document.querySelectorAll('[data-live-badge]').forEach(el=>{el.textContent=`${s.mode==='LIVE'?'LIVE 实盘已启动':'PAPER 模拟盘'} · ${this.busy?'检测 / 处理进行中':labels[s.state]||s.state}`;el.style.color=s.mode==='LIVE'?'var(--danger)':s.ready?'var(--success)':'var(--warning)';});
        document.querySelectorAll('[data-live-check]').forEach(el=>el.disabled=this.busy);
        document.querySelectorAll('[data-live-start]').forEach(el=>{el.disabled=this.busy||!s.ready||s.mode==='LIVE';el.textContent=s.mode==='LIVE'?'实盘已启动':'启动实盘';});
        const flags=s.configuration?.fields||{};
        const fields={api_key:'API Key',api_secret:'Secret',api_passphrase:'Passphrase',private_key:'签名私钥'};
        const config=s.configuration?.capabilities ? `${s.configuration.name} · ${s.configuration.capabilities.description}` : Object.entries(fields).map(([k,v])=>`${v} ${flags[k]?'已填写':'缺失'}`).join(' · ');
        const checks=(s.checks||[]).map(c=>`<div><strong class="${c.ok?'live-pass':'live-fail'}">${c.ok?'通过':'未通过'}</strong> ${Utils.escape(c.label)}：${Utils.escape(c.message)}</div>`).join('');
        document.querySelectorAll('[data-live-details]').forEach(el=>el.innerHTML=`<div>${config} · 签名类型 ${s.configuration?.signature_type??'—'} · Funder ${s.configuration?.funder_configured?'已填写':'未填写'}</div>${checks}${s.pending_orders?`<div class="live-fail">${s.pending_orders} 笔真实订单结果待核对，请到执行记录处理。</div>`:''}`);
        document.querySelectorAll('[data-live-note]').forEach(el=>el.textContent=`${s.checked_at?'最近检测 '+Utils.fmtTime(s.checked_at)+' · ':'尚未检测 · '}${s.mode==='LIVE'?'后台每30秒更新检查 · ':''}只读检查不下单。检查通过表示前置条件就绪，单笔成交仍取决于实时余额、授权和流动性。`);
    },
    configure() {document.getElementById('account-drawer')?.close();AccountsUI.open();},
    async check() {
        if(this.busy) return;this.busy=true;this.draw();
        try {this.state=await API.request('POST','/executor/check');Toast.info(this.state.ready?'实盘前置检查通过，可点击启动实盘':'检测完成，请按未通过项处理');}
        catch(_) {await this.refresh();} finally {this.busy=false;this.draw();}
    },
    start() {
        if(this.busy||!this.state?.ready) return;
        document.getElementById('account-drawer')?.close();
        Modal.confirm('启动后将使用真实资金。现有自动规则会按全局执行方式处理新信号；半自动仍逐笔审核。启动前会再次检查连接、认证、余额、授权和待核对订单。',async()=>{
            this.busy=true;this.draw();
            try {const r=await API.request('POST','/executor/mode',{mode:'live'});this.state=r.status;AppState.executorMode='live';Toast.success('实盘已启动');await this.updatePanel();}
            catch(_) {await this.refresh();}finally{this.busy=false;this.draw();}
        },'确认启动实盘');
    },
    async paper() {
        // 停止按钮不因连接检测进行中而被禁用。
        try {const r=await API.request('POST','/executor/mode',{mode:'paper'});this.state=r.status;AppState.executorMode='paper';this.draw();Toast.info('已切回模拟盘，尚未提交的实盘单停止发送；已提交订单仍需核对');await this.updatePanel();}catch(_){}
    },
    async updatePanel() {
        if(App.currentPanel==='desk') await DeskPanel.refresh();
        else if(App.currentPanel==='copy-hub') await CopyHubPanel.loadState();
        else if(App.currentPanel==='settings') await SettingsPanel.refresh();
    },
};
