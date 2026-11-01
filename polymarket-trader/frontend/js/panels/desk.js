/** 交易台：手动买卖、半自动审核、自动执行和成交质量。 */
const DeskPanel = {
    _timer: null, _feedBusy:false, _refreshBusy:false, _feed: [], _positions: [], _queue: [], _ticketKey: null,
    esc(v) { return Utils.escape(String(v ?? '')); },
    num(v, n=2) { return Number(v || 0).toFixed(n); },
    async render() {
        this.onLeave();
        document.getElementById('panel-desk').innerHTML = `
            <div class="panel-header"><h2>🛒 交易台</h2><button class="btn btn-secondary" onclick="DeskPanel.refresh()">刷新</button></div>
            <div class="card"><strong id="desk-mode">加载状态…</strong><p class="text-muted">自动跟单仅执行已配置钱包与策略生成的信号；平台最近成交仅供观察和选单。交易可能亏损。审批会按最新盘口复核，旧信号不会追单。</p></div>
            <div class="card"><h3>账户模式与实时同步</h3><p id="desk-sync" class="text-muted">连接状态加载中…</p><label>模拟初始资金 USDC（仅首次成交前可设） <input id="desk-capital" type="number" min="1" value="10000"></label><button class="btn btn-secondary" onclick="DeskPanel.capital()">保存虚拟资金</button><p class="text-muted">模拟盘与实盘使用相同平台行情。模拟成交按盘口测算，仅使用虚拟资金；平台确认最终结果后自动结算。实盘凭证在设置页配置。</p></div><div class="card"><h3>执行规则</h3><div class="form-row">
                <div class="form-group"><label>执行方式</label><select id="desk-workflow"><option value="observe">仅观察</option><option value="review">半自动：逐笔审核</option><option value="auto">自动：规则通过即执行</option></select></div>
                ${this.field('max_age_seconds','信号有效秒数',60,5,3600,1)}
                ${this.field('max_price_deviation','不利价格偏离上限（0.02=2分）',.02,0,.5,.001)}
                ${this.field('max_spread','最大买卖价差',.05,.001,1,.001)}
                ${this.field('min_depth_usdc','限价内最小深度 USDC',20,0,1000000,1)}
                ${this.field('max_event_exposure','单事件合计成本上限 USDC',200,1,1000000,1)}
                ${this.field('max_daily_loss','每日亏损停买 USDC',50,1,1000000,1)}
                ${this.field('max_drawdown','已实现收益回撤停买 USDC',100,1,1000000,1)}
                ${this.field('fee_bps','手续费估算 bps（20=0.2%）',20,0,1000,1)}
                ${this.field('stop_loss_pct','止损比例（0.1=10%）',.1,.001,1,.01)}
                ${this.field('take_profit_pct','止盈比例（0.2=20%）',.2,.001,10,.01)}
                <div class="form-group"><label><input type="checkbox" id="desk-exits_enabled">启用持仓止盈止损</label></div><div class="form-group"><label><input type="checkbox" id="desk-follow_specialties_only">仅买入钱包已标记擅长板块</label></div>
            </div><button class="btn" onclick="DeskPanel.savePolicy()">保存执行规则</button><button class="btn btn-secondary" onclick="DeskPanel.observe()">暂停自动，切为仅观察</button>
            <p class="text-muted">止盈止损每轮间隔5秒检查本交易台持仓（网络耗时另计），使用当前买一价触发；深度不足时可能无法成交。每日按UTC，手续费为估算，不含未实现亏损。单笔金额仍受设置页全局上限约束。</p></div>
            <div class="card"><h3>买入 / 卖出委托</h3><div class="form-row">
                <div class="form-group" style="flex:2"><label>Token ID（不同结果有不同 Token）</label><input id="desk-token" oninput="DeskPanel._ticketKey=null"></div>
                <div class="form-group"><label>市场 condition ID（可留空自动识别）</label><input id="desk-condition" oninput="DeskPanel._ticketKey=null"></div>
                <div class="form-group"><label>市场名称</label><input id="desk-title" oninput="DeskPanel._ticketKey=null"></div>
                <div class="form-group"><label>板块标签</label><input id="desk-sector" value="未分类" oninput="DeskPanel._ticketKey=null"></div>
                <div class="form-group"><label>方向</label><select id="desk-side" onchange="DeskPanel._ticketKey=null"><option value="BUY">BUY 买入</option><option value="SELL">SELL 卖出 / 减仓</option></select></div>
                <div class="form-group"><label>限价（买入上限 / 卖出下限）</label><input id="desk-price" type="number" min="0.001" max="0.999" step="0.001" value="0.5" oninput="DeskPanel._ticketKey=null"></div>
                <div class="form-group"><label>份额（不是USDC金额）</label><input id="desk-size" type="number" min="0.01" step="0.01" value="10" oninput="DeskPanel._ticketKey=null"></div>
            </div><button class="btn btn-secondary" onclick="DeskPanel.loadBook()">读取当前买一卖一</button><button class="btn btn-secondary" onclick="DeskPanel.preview()">预览此委托</button><button id="desk-create" class="btn" onclick="DeskPanel.create()">创建待审核买卖委托</button><p id="desk-preview" class="text-muted"></p></div>
            <div class="card"><h3>待审核与近期决策</h3><p class="text-muted">创建委托不会立即下单。手动委托均需审批；自动模式仅对新跟单信号与止盈止损信号生效。模式切换后，旧队列不能跨模式执行。</p><div class="table-wrapper"><table><thead><tr><th>时间 / 模式</th><th>来源 / 市场</th><th>方向</th><th>限价 × 份额</th><th>状态 / 原因</th><th>操作</th></tr></thead><tbody id="desk-queue"></tbody></table></div></div>
            <div id="desk-summary" class="stats-grid"></div>
            <div class="card"><h3>本交易台持仓与减仓</h3><p class="text-muted">仅显示从本交易台开始记录的持仓。点击卖出将填入委托表，不会立即成交。</p><div class="table-wrapper"><table><thead><tr><th>市场 / Token</th><th>板块 / 事件</th><th>份额</th><th>均价 / 成本</th><th>操作</th></tr></thead><tbody id="desk-positions"></tbody></table></div><button class="btn btn-secondary" onclick="DeskPanel.accountPositions()">读取当前账户持仓快照</button><pre id="desk-account" style="max-height:220px;overflow:auto;white-space:pre-wrap"></pre></div>
            <div class="card"><h3>源钱包 × 板块表现（观测样本）</h3><p id="desk-source-scope" class="text-muted"></p><div class="table-wrapper"><table><thead><tr><th>钱包 / 板块</th><th>样本成交</th><th>配对卖出</th><th>样本已实现毛盈亏</th><th>平均持有 / 样本回撤</th><th>缺失成本的卖出</th></tr></thead><tbody id="desk-matrix"></tbody></table></div></div>
            <div class="card"><h3>本工具按钱包 / 板块归因</h3><div class="table-wrapper"><table><thead><tr><th>来源 / 板块</th><th>配对批次</th><th>盈利批次比</th><th>毛盈亏 / 估算净盈亏</th><th>同份额源价格基准</th><th>平均持有</th></tr></thead><tbody id="desk-attribution"></tbody></table></div></div>
            <div class="card"><h3>成交质量与买卖记录</h3><p id="desk-scope" class="text-muted"></p><div class="table-wrapper"><table><thead><tr><th>时间 / 市场</th><th>方向 / 份额</th><th>源价 / 成交价</th><th>不利滑点（价格）</th><th>手续费估算</th><th>配对毛盈亏</th></tr></thead><tbody id="desk-journal"></tbody></table></div></div>
            <div class="card"><h3>平台最近公开成交</h3><button class="btn btn-secondary btn-sm" onclick="DeskPanel.loadFeed()">刷新平台成交</button><p id="desk-feed-scope" class="text-muted">最近最多100笔公开成交，实时推送，REST 补拉；断线期间不保证无遗漏。</p><div class="table-wrapper" style="max-height:400px"><table><thead><tr><th>时间 / 市场</th><th>钱包</th><th>方向 / 价格 / 金额</th><th>委托入口</th></tr></thead><tbody id="desk-feed"></tbody></table></div></div>`;
        this.organize();
        await this.refresh(true);
        this.loadFeed(); this.loadMatrix();
        this._timer = setInterval(() => { this.refresh(); this.loadFeed(); }, 5000);
    },
    field(key,label,value,min,max,step) { return `<div class="form-group"><label>${label}</label><input type="number" id="desk-${key}" value="${value}" min="${min}" max="${max}" step="${step}"></div>`; },
    organize() {
        const root=document.getElementById('panel-desk');
        const groups=[['trade','市场与下单'],['queue','订单审核'],['positions','持仓与结算'],['risk','风控设置'],['analysis','收益归因']];
        const strip=document.createElement('div');strip.className='workspace-tabs';strip.setAttribute('role','tablist');strip.setAttribute('aria-label','交易台功能');
        for(const [key,label] of groups) strip.insertAdjacentHTML('beforeend',`<button type="button" role="tab" id="desk-tab-${key}" aria-controls="desk-view-${key}" onclick="DeskPanel.setView('${key}')">${label}${key==='queue'?'<span data-queue-count hidden></span>':''}</button>`);
        strip.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const tabs=[...strip.querySelectorAll('[role=tab]')];let index=tabs.indexOf(event.target);index=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[index].click();tabs[index].focus();});
        root.insertBefore(strip,root.children[1]);
        for(const [key] of groups) {const pane=document.createElement('section');pane.id='desk-view-'+key;pane.dataset.deskView=key;pane.setAttribute('role','tabpanel');pane.setAttribute('aria-labelledby','desk-tab-'+key);root.appendChild(pane);}
        const move=(id,view)=>{const card=root.querySelector('#'+id)?.closest('.card');if(card)root.querySelector('#desk-view-'+view).appendChild(card);};
        move('desk-token','trade');move('desk-feed','trade');move('desk-queue','queue');move('desk-positions','positions');move('desk-journal','positions');move('desk-sync','risk');move('desk-workflow','risk');move('desk-matrix','analysis');move('desk-attribution','analysis');
        const summary=root.querySelector('#desk-summary');root.insertBefore(summary,strip);
        const mode=root.querySelector('#desk-mode')?.closest('.card');if(mode){mode.classList.add('desk-mode-note');root.insertBefore(mode,strip);}
        const trade=root.querySelector('#desk-view-trade');trade.classList.add('desk-trade-layout');
        const ticket=root.querySelector('#desk-token').closest('.card');ticket.classList.add('desk-ticket');
        const feed=root.querySelector('#desk-feed').closest('.card');feed.classList.add('desk-market-feed');trade.insertBefore(feed,ticket);
        feed.querySelector('h3').appendChild(feed.querySelector('button'));
        const extra=document.createElement('details');extra.className='secondary-fields';extra.innerHTML='<summary>标的资料与板块（可选）</summary>';
        for(const id of ['desk-condition','desk-title','desk-sector']) extra.appendChild(root.querySelector('#'+id).closest('.form-group'));
        ticket.querySelector('.form-row').appendChild(extra);
        this.setView(this.view||'trade');
    },
    setView(view) {
        if(!['trade','queue','positions','risk','analysis'].includes(view)) view='trade';this.view=view;
        document.querySelectorAll('#panel-desk [data-desk-view]').forEach(el=>el.hidden=el.dataset.deskView!==view);
        document.querySelectorAll('#panel-desk [role=tab]').forEach(el=>el.setAttribute('aria-selected',String(el.id==='desk-tab-'+view)));
        const heading=document.querySelector('#panel-desk .panel-header h2');if(heading) heading.textContent=({trade:'市场与买卖',queue:'订单审核',positions:'持仓与结算',risk:'风控设置',analysis:'收益归因'})[view];
        if(App.currentPanel==='desk') {App.currentView=view;App.activateNav('desk',view);}
    },
    onLeave() { if(this._timer) clearInterval(this._timer); this._timer=null; },
    async refresh(fillPolicy=false) {
        if(this._refreshBusy) return; this._refreshBusy=true;
        try {
            const r=await API.request('GET','/desk/state',null,true); this._queue=r.queue; this._positions=r.summary.positions; App.updateQueueBadge(r.queue);
            this._mode=r.summary.mode; this.syncStatus(r.sync);
            const mode=document.getElementById('desk-mode'); if(!mode) return;
            mode.textContent=`当前账户：${r.summary.mode === 'LIVE' ? 'LIVE 真实资金' : 'PAPER 模拟资金'} · 执行方式：${({observe:'仅观察',review:'半自动审核',auto:'自动执行'})[r.policy.workflow]}${r.exit_worker_error ? ' · '+r.exit_worker_error : ''}`;
            if(fillPolicy) for(const [key,value] of Object.entries(r.policy)) { const el=document.getElementById('desk-'+key); if(el) { if(el.type==='checkbox') el.checked=value; else el.value=value; } }
            const labels={expired:'已过期',waiting:'待审批',observed:'仅观察',executing:'执行中/待核对',filled:'已成交',partial:'部分成交',blocked:'规则拦截',rejected:'已拒绝',pending:'结果待核对',failed:'执行失败',cancelled:'已取消'};
            document.getElementById('desk-queue').innerHTML=r.queue.slice(0,100).map((row,i)=>{
                const s=row.signal;
                return `<tr><td>${this.esc(Utils.fmtTime(row.created_at))}<br>${row.mode}</td><td>${Utils.walletLabel(s.source_trade.wallet)}<br>${this.esc(s.market.title||s.market.token_id)}</td><td>${s.side}</td><td>${this.num(s.price,4)} × ${this.num(s.size)}</td><td>${labels[row.expired ? 'expired' : row.status]||this.esc(row.status)}<br>${this.esc(row.reason)}</td><td>${row.status==='waiting' && !row.expired?`<button class="btn btn-sm" onclick="DeskPanel.approve(${i})">审核执行</button><button class="btn btn-secondary btn-sm" onclick="DeskPanel.reject(${i})">拒绝</button>`:''}<button class="btn btn-secondary btn-sm" onclick="DeskPanel.rebuild(${i})">复制为新委托</button></td></tr>`;
            }).join('')||'<tr><td colspan="6">暂无信号</td></tr>';
            const s=r.summary;
            document.getElementById('desk-summary').innerHTML=[['可用资金 USDC',s.cash_balance],['现金＋本工具持仓',s.equity],['持仓浮动盈亏',s.floating_pnl],['已实现净盈亏（估算）',s.net_pnl_estimate]].map(([k,v])=>`<div class="stat-card"><div class="label">${k}</div><div class="value">${v==null?'暂不可用':this.num(v)}</div></div>`).join('');
            document.getElementById('desk-positions').innerHTML=s.positions.map((p,i)=>`<tr><td>${this.esc(p.title)}<br><small>${this.esc(p.token_id)}</small></td><td>${this.esc(p.sector)}<br>${this.esc(p.event_key)}</td><td>${this.num(p.quantity,4)}</td><td>${this.num(p.avg_entry,4)} / $${this.num(p.cost)}<br>现价 ${p.mark_price==null?"未知":this.num(p.mark_price,4)} · 浮盈 ${p.floating_pnl==null?"未知":this.num(p.floating_pnl)}</td><td><button class="btn btn-danger btn-sm" onclick="DeskPanel.closePosition(${i})">卖出 / 平仓</button></td></tr>`).join('')||'<tr><td colspan="5">暂无本交易台持仓</td></tr>';
            document.getElementById('desk-attribution').innerHTML=s.wallet_sectors.map(p=>`<tr><td>${Utils.walletLabel(p.wallet)} / ${this.esc(p.sector)}</td><td>${p.closed_lots}</td><td>${this.num(p.win_rate*100)}%</td><td>${this.num(p.gross_pnl)} / ${this.num(p.net_pnl_estimate)}</td><td>${this.num(p.benchmark_pnl)}</td><td>${this.num(p.avg_hold_seconds/60)} 分钟</td></tr>`).join('')||'<tr><td colspan="6">尚无配对平仓样本</td></tr>';
            document.getElementById('desk-scope').textContent=s.scope+` 缺失成本的卖出：${s.unmatched_sells} 笔。`;
            document.getElementById('desk-journal').innerHTML=s.journal.map(p=>`<tr><td>${this.esc(Utils.fmtTime(p.timestamp))}<br>${this.esc(p.title)}</td><td>${p.kind==='settlement'?'最终结算':p.side} / ${this.num(p.size)}</td><td>${this.num(p.source_price,4)} / ${this.num(p.price,4)}</td><td>${this.num(p.adverse_slippage,4)}</td><td>${this.num(p.fee_estimate,4)}</td><td>${this.num(p.realized_gross_pnl)}</td></tr>`).join('')||'<tr><td colspan="6">暂无成交</td></tr>';
        } catch(e) { const el=document.getElementById('desk-mode'); if(el) el.textContent='交易台状态获取失败：'+e.message; } finally {this._refreshBusy=false;}
    },
    syncStatus(d) {
        const el=document.getElementById('desk-sync'); if(!el || !d) return;
        el.textContent=`实时成交：${d.rtds_connected?'已连接':'未连接'} · 最近成交 ${d.last_trade_at?Utils.fmtTime(d.last_trade_at):'等待'} · REST补拉 ${d.last_rest_at?Utils.fmtTime(d.last_rest_at):'等待'} · 最终结果同步 ${d.last_resolution_at?Utils.fmtTime(d.last_resolution_at):'等待'} · 本次新增 ${d.received} · 丢弃跟单事件 ${d.dropped_follow_events} ${d.ws_error||''} ${d.rest_error||''} ${d.settlement_error||''}`;
    },
    async capital() {try {await API.request('POST','/desk/paper-capital',{amount:Number(document.getElementById('desk-capital').value)}); Toast.success('虚拟资金已设置'); await this.refresh();} catch(_){}},
    switchMode(mode) {
        const run=async()=>{try {await API.setExecutorMode(mode); await this.refresh(true); Toast.success('账户模式已切换');}catch(_){}};
        if(mode==='live') Modal.confirm('切换后审批和自动执行将使用真实资金。请先在设置页配置凭证，并检查执行方式与止盈止损规则。',run,'切换实盘'); else run();
    },
    async savePolicy() {
        const body={};
        for(const key of ['workflow','max_age_seconds','max_price_deviation','max_spread','min_depth_usdc','max_event_exposure','max_daily_loss','max_drawdown','fee_bps','stop_loss_pct','take_profit_pct','exits_enabled','follow_specialties_only']) {
            const el=document.getElementById('desk-'+key); body[key]=el.type==='checkbox'?el.checked:(key==='workflow'?el.value:Number(el.value));
        }
        const save=async()=>{try { await API.request('PUT','/desk/policy',body); Toast.success('执行规则已保存'); await this.refresh(); } catch(_){} };
        if(body.workflow==='auto') Modal.confirm('启用自动规则后，新跟单信号和已启用的止盈止损信号通过检查就会下单。当前账户若为 LIVE，将使用真实资金。',save,'保存自动规则'); else await save();
    },
    async observe() {
        try { const state=await API.request('GET','/desk/state'); await API.request('PUT','/desk/policy',{...state.policy,workflow:'observe'}); await this.refresh(true); Toast.info('已暂停自动与半自动跟单，手动委托仍可单独审批'); } catch(_){}
    },
    body() {
        if(!this._ticketKey) this._ticketKey=crypto.randomUUID();
        const val=id=>document.getElementById('desk-'+id).value.trim();
        return {token_id:val('token'),condition_id:val('condition'),title:val('title'),sector:val('sector'),side:val('side'),price:Number(val('price')),size:Number(val('size')),client_order_id:this._ticketKey};
    },
    async loadBook() {
        try {
            const token=document.getElementById('desk-token').value.trim();
            const r=await API.request('GET',`/desk/book/${encodeURIComponent(token)}`);
            document.getElementById('desk-preview').textContent=`买一 ${r.best_bid ?? '无'} · 卖一 ${r.best_ask ?? '无'} · 最小份额 ${r.min_order_size ?? '未知'} · tick ${r.tick_size ?? '未知'}`;
            return r;
        } catch(_) { return null; }
    },
    async preview() { try { const r=await API.request('POST','/desk/preview',this.body()); document.getElementById('desk-preview').textContent=`${r.mode} · 预估成交均价 ${this.num(r.price,4)} · 金额 ${this.num(r.cash_estimate)} USDC · 估算费用 ${this.num(r.fee_estimate,4)} · 深度 ${this.num(r.depth_usdc)}。审批时将重新检查，预览不锁价。`; } catch(_){} },
    async create() {
        const button=document.getElementById('desk-create'); button.disabled=true;
        try { await API.request('POST','/desk/orders',this.body()); Toast.success('已创建审核委托，请核对后审批'); await this.refresh(); this.setView('queue'); } catch(_){} finally { button.disabled=false; }
    },
    approve(index) {
        const row=this._queue[index]; if(!row) return; const s=row.signal;
        Modal.confirm(`${row.mode==='LIVE'?'真实资金':'模拟资金'} · ${s.side==='BUY'?'买入':'卖出'}<br>${this.esc(s.market.title||s.market.token_id)}<br>限价 ${this.num(s.price,4)} × ${this.num(s.size)} 份。审批时将复核最新盘口，超时信号会被拦截。`,async()=>{
            try {const r=await API.request('POST',`/desk/queue/${encodeURIComponent(row.signal_id)}/approve`); Toast.info(r.error||`执行状态：${r.status}`); await this.refresh();}catch(_){}
        },row.mode==='LIVE'?'确认真实买卖':'确认模拟买卖');
    },
    rebuild(index) {const s=this._queue[index].signal; this.fillTicket({token:s.market.token_id,condition:s.market.condition_id,title:s.market.title,sector:s.market.sector,side:s.side,price:s.price,size:s.size});},
    async reject(index) { const row=this._queue[index]; try {await API.request('POST',`/desk/queue/${encodeURIComponent(row.signal_id)}/reject`); await this.refresh();}catch(_){} },
    fillTicket(data) {
        this.setView('trade');
        for(const [key,value] of Object.entries(data)) {const el=document.getElementById('desk-'+key); if(el) el.value=value;}
        this._ticketKey=null;
        document.getElementById('desk-token').scrollIntoView({behavior:'smooth',block:'center'});
        document.getElementById('desk-preview').textContent='已填入委托，请检查方向、限价和份额；尚未创建或提交订单。';
    },
    pick(index,side) {const t=this._feed[index]; this.fillTicket({token:t.token_id,condition:t.condition_id,title:t.market_title,side,price:t.price,size:t.size,sector:'未分类'});},
    async closePosition(index) {
        const p=this._positions[index];
        this.fillTicket({token:p.token_id,condition:p.condition_id,title:p.title,sector:p.sector,side:'SELL',size:p.quantity,price:p.avg_entry});
        const book=await this.loadBook();
        if(book?.best_bid) {
            const config=await API.getConfig();
            document.getElementById('desk-price').value=book.best_bid;
            document.getElementById('desk-size').value=Math.floor(Math.min(p.quantity,config.risk.max_trade_amount/book.best_bid)*100)/100;
            Toast.info('已按当前买一填写卖出下限，并按单笔金额上限分批减仓；请预览后创建审核委托');
        }
    },
    async accountPositions() {try {const r=await API.request('GET','/desk/account-positions'); document.getElementById('desk-account').textContent=JSON.stringify(r,null,2);}catch(_){}},
    async loadFeed() {
        if(this._feedBusy) return; this._feedBusy=true;
        try {const r=await API.request('GET','/desk/platform-trades',null,true); if(!document.getElementById('desk-feed')) return; this._feed=r.trades;
            document.getElementById('desk-feed-scope').textContent='真实成交 · 实时推送＋REST补拉 · 最近100笔，断线期间可能有遗漏';
            document.getElementById('desk-feed-scope').title=r.scope;
            document.getElementById('desk-feed').innerHTML=r.trades.map((t,i)=>`<tr><td>${this.esc(Utils.fmtTime(t.timestamp))}<br>${this.esc(t.market_title)}</td><td title="${this.esc(t.wallet)}">${Utils.walletLabel(t.wallet)}</td><td>${this.esc(t.side)} / ${this.num(t.price,4)} / $${this.num(t.price*t.size)}</td><td><button class="btn btn-sm" onclick="DeskPanel.pick(${i},'BUY')">买入</button><button class="btn btn-secondary btn-sm" onclick="DeskPanel.pick(${i},'SELL')">卖出</button></td></tr>`).join('');
        }catch(e) {const el=document.getElementById('desk-feed-scope'); if(el) el.textContent='平台成交暂不可用：'+e.message;} finally {this._feedBusy=false;}
    },
    async loadMatrix() {
        try {const r=await API.request('GET','/desk/source-matrix',null,true); const el=document.getElementById('desk-matrix'); if(!el) return;
            document.getElementById('desk-source-scope').textContent=r.scope;
            el.innerHTML=r.rows.map(p=>`<tr><td>${Utils.walletLabel(p.wallet)} / ${this.esc(p.sector)}</td><td>${p.samples}</td><td>${p.matched_closes}</td><td>${this.num(p.realized_sample_pnl)}</td><td>${this.num((p.avg_hold_seconds||0)/60)} 分钟 / ${this.num(p.max_drawdown)}</td><td>${p.missing_basis_sells}</td></tr>`).join('')||'<tr><td colspan="6">暂无监测样本</td></tr>';
        }catch(_){}
    },
};
