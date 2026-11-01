/** 账户管理；插件签名只由明确点击触发。私钥从不进入插件通道。 */
const AccountsUI = {
    data:null, provider:null, connectedId:null, busy:false,
    init() {
        const d=document.createElement('dialog');d.id='accounts-dialog';d.className='account-drawer';
        d.innerHTML=`<div class="copy-actions"><h2>实盘账户与钱包</h2><button class="btn btn-secondary" data-close>关闭</button></div>
        <p class="text-muted">先停止实盘，再切换或配置钱包。凭证使用本机 Windows 用户加密保存。</p>
        <div class="copy-actions"><select data-select style="min-width:220px"></select><button class="btn" data-switch>切换钱包</button><button class="btn btn-secondary" data-new>新增账户</button></div>
        <p data-capability></p><div data-editor></div>
        <div class="copy-actions"><button class="btn" data-save>加密保存配置</button><button class="btn btn-secondary" data-connect>连接 OKX 并授权</button><button class="btn btn-secondary" data-derive>从已保存私钥生成 HMAC 凭证</button><button class="btn btn-secondary" data-snapshot>同步余额 / 持仓</button></div>
        <p data-result class="text-muted"></p><div data-jobs></div><div data-events class="text-muted"></div>`;
        document.body.append(d);this.dialog=d;
        d.querySelector('[data-close]').onclick=()=>d.close();
        d.querySelector('[data-select]').onchange=()=>this.edit(d.querySelector('[data-select]').value);
        d.querySelector('[data-new]').onclick=()=>this.edit(null);
        d.querySelector('[data-switch]').onclick=()=>this.switch();
        d.querySelector('[data-save]').onclick=()=>this.save();
        d.querySelector('[data-connect]').onclick=()=>this.connect();
        d.querySelector('[data-derive]').onclick=async()=>{try{await API.request('POST',`/accounts/${this.data.active_id}/derive-credentials`);await this.load();Toast.success('HMAC 凭证已生成并加密保存');await LiveControls.refresh();}catch(_){}};
        d.querySelector('[data-snapshot]').onclick=()=>this.snapshot();
        this.timer=setInterval(()=>this.tick(),10000);
    },
    async open(){if(!this.dialog)this.init();await this.load();if(!this.dialog.open)this.dialog.showModal();},
    async load(){this.data=await API.request('GET','/accounts');const select=this.dialog.querySelector('[data-select]');select.innerHTML=this.data.accounts.map(p=>`<option value="${p.id}">${Utils.escape(p.name)}${p.id===this.data.active_id?' · 当前':''}</option>`).join('');select.value=this.data.active_id;this.edit(this.data.active_id);await this.jobs();},
    edit(id){
        this.editId=id;const p=this.data.accounts.find(p=>p.id===id)||{kind:'magic_hmac',name:'',address:'',signature_type:0,funder:'',relay_url:''};this.editKind=p.kind;
        const e=this.dialog.querySelector('[data-editor]');
        e.innerHTML=`<div class="form-group"><label>账户备注名</label><input data-name maxlength="80" value="${Utils.escape(p.name)}" placeholder="例如：Magic 主账户 / OKX 交易钱包"></div>
        <div class="form-group"><label>接入方式</label><select data-kind><option value="magic_hmac">Magic / EVM 私钥 + HMAC · 后台执行</option><option value="okx">OKX 插件 · 每笔手动签名</option><option value="magic_oauth">OAuth 可配置中继桥 · 需要兼容服务</option></select></div>
        <div class="form-group"><label>签名钱包 EVM 地址</label><input data-address value="${Utils.escape(p.address)}" placeholder="0x…"></div>
        <div data-specific></div>`;
        e.querySelector('[data-kind]').value=p.kind;e.querySelector('[data-kind]').onchange=()=>{this.editKind=e.querySelector('[data-kind]').value;this.specific(p);};this.specific(p);
    },
    specific(p){
        const kind=this.editKind,flags=p.credentials||{};const secret=(key,label)=>`<div class="form-group"><label>${label} ${flags[key]?'· 已加密保存，留空保留':''}</label><input type="password" autocomplete="new-password" data-secret="${key}" placeholder="${flags[key]?'留空保留':'未配置'}"></div>`;
        this.dialog.querySelector('[data-specific]').innerHTML=kind==='okx'?'<p class="text-muted">不填写私钥。保存实际 EOA 地址后点击“连接 OKX 并授权”。浏览器与插件需持续在线，逐笔点击签名。</p>':kind==='magic_oauth'?`<p class="text-muted">这是兼容桥配置，需实现 bridge/1 协议；不是任意 Google token 即可使用的官方下单接口。</p><div class="form-group"><label>中继 URL（可含端口）</label><input data-relay value="${Utils.escape(p.relay_url||'')}" placeholder="http://127.0.0.1:9000"></div>${secret('oauth_token','OAuth 会话 token')}`:
        `<div class="form-group"><label>签名类型</label><select data-signature><option value="0">0 · EOA</option><option value="1">1 · Proxy</option><option value="2">2 · Safe</option></select></div><div class="form-group"><label>Funder 资金钱包（代理账户必填）</label><input data-funder value="${Utils.escape(p.funder||'')}"></div>${secret('private_key','导出的签名私钥')}${secret('api_key','API Key')}${secret('api_secret','API Secret')}${secret('api_passphrase','API Passphrase')}`;
        const signature=this.dialog.querySelector('[data-signature]');if(signature)signature.value=p.signature_type||0;
        this.dialog.querySelector('[data-connect]').hidden=kind!=='okx';
        this.dialog.querySelector('[data-derive]').hidden=kind!=='magic_hmac';
        this.dialog.querySelector('[data-capability]').textContent=({okx:'插件逐笔签名 · 仅半自动 · 需保持浏览器在线',magic_hmac:'EVM 私钥签名 + CLOB HMAC · 可后台执行',magic_oauth:'OAuth 兼容中继桥 · 需通过协议与账户校验'})[kind];
    },
    async save(){
        const d=this.dialog,get=s=>d.querySelector(s)?.value?.trim()||'';
        const profile={id:this.editId||crypto.randomUUID(),name:get('[data-name]'),kind:this.editKind,address:get('[data-address]'),chain_id:137,signature_type:Number(get('[data-signature]')||0),funder:get('[data-funder]'),relay_url:get('[data-relay]')};
        const secrets={};d.querySelectorAll('[data-secret]').forEach(el=>{if(el.value)secrets[el.dataset.secret]=el.value;});
        try{await API.request('PUT','/accounts',{profile,secrets});d.querySelectorAll('[data-secret]').forEach(el=>el.value='');await this.load();Toast.success('账户配置已加密保存');await LiveControls.refresh();}catch(_){}
    },
    async switch(){try{await API.request('POST',`/accounts/${this.dialog.querySelector('[data-select]').value}/select`);this.connectedId=null;await this.load();await LiveControls.refresh();await LiveControls.updatePanel();Toast.success('钱包已切换，需重新检测连接');}catch(_){}},
    async connect(){
        const p=this.data.accounts.find(p=>p.id===this.data.active_id);
        if(p.kind!=='okx'||this.editId!==p.id){Toast.info('请先保存并切换到该 OKX 账户');return;}
        const provider=window.okxwallet;
        if(!provider?.request){Toast.info('此浏览器未检测到 OKX 注入钱包，请在装有 OKX 插件的浏览器打开本机地址');return;}
        try{
            const addresses=await provider.request({method:'eth_requestAccounts'});
            await provider.request({method:'wallet_switchEthereumChain',params:[{chainId:'0x89'}]});
            if(addresses[0]?.toLowerCase()!==p.address.toLowerCase())throw new Error('插件地址与保存地址不同，请切换插件账户');
            this.provider=provider;
            if(!this.listeners){const changed=()=>{this.connectedId=null;LiveControls.refresh();Toast.info('插件账户或网络已改变，请重新连接');};provider.on?.('accountsChanged',changed);provider.on?.('chainChanged',changed);this.listeners=true;}
            const {job_id}=await API.request('POST',`/accounts/${p.id}/browser/challenge`);
            await this.jobs();await this.sign(job_id);this.connectedId=p.id;
        }catch(e){Toast.info(e.message||'钱包连接未完成');}
    },
    async sign(id){
        if(this.busy)return;this.busy=true;
        try{
            const jobs=await API.request('GET','/accounts/browser/jobs'),job=jobs.find(j=>j.id===id);if(!job)throw new Error('任务已过期或已处理');
            const p=this.data.accounts.find(p=>p.id===this.data.active_id),provider=this.provider||window.okxwallet;if(!provider?.request)throw new Error('请连接 OKX 插件');
            const chain=await provider.request({method:'eth_chainId'}),addresses=await provider.request({method:'eth_accounts'});
            if(chain!=='0x89'||addresses[0]?.toLowerCase()!==p.address.toLowerCase())throw new Error('请将插件切回正确钱包及 Polygon 网络');
            const signature=await provider.request({method:'eth_signTypedData_v4',params:[p.address,JSON.stringify(job.typed_data)]});
            await API.request('POST',`/accounts/browser/jobs/${id}/signature`,{signature});
            Toast.success(job.kind==='auth'?'插件授权已完成':'签名已提交，请等待真实成交回执');await this.jobs();await LiveControls.refresh();
        }catch(e){Toast.info(e.message||'签名未完成，请检查任务状态');throw e;}finally{this.busy=false;}
    },
    async jobs(){if(!this.dialog?.open)return;try{const jobs=await API.request('GET','/accounts/browser/jobs');const host=this.dialog.querySelector('[data-jobs]');host.innerHTML=jobs.map(j=>`<div class="card"><strong>${j.kind==='auth'?'连接授权':j.kind==='cancel'?'撤单授权':'订单签名'}</strong><p>${Utils.escape(j.intent_id)} · 到期 ${Utils.fmtTime(j.expires_at)}</p><button class="btn" data-sign="${Utils.escape(j.id)}">打开 OKX 确认签名</button> <button class="btn btn-secondary" data-reject="${Utils.escape(j.id)}">拒绝</button></div>`).join('');host.querySelectorAll('[data-sign]').forEach(b=>b.onclick=()=>this.sign(b.dataset.sign).catch(()=>{}));host.querySelectorAll('[data-reject]').forEach(b=>b.onclick=async()=>{await API.request('POST',`/accounts/browser/jobs/${b.dataset.reject}/reject`);await this.jobs();});}catch(_){}},
    async snapshot(){try{const r=await API.request('GET',`/accounts/${this.data.active_id}/snapshot`);this.dialog.querySelector('[data-result]').textContent=`账户 ${r.account_id} · 余额 ${r.balance_usdc.toFixed(4)} USDC · ${r.positions.length} 项平台持仓 · ${Utils.fmtTime(r.observed_at)}`;}catch(_){}},
    async tick(){
        if(this.connectedId&&this.provider){try{const a=await this.provider.request({method:'eth_accounts'}),c=await this.provider.request({method:'eth_chainId'}),p=this.data.accounts.find(p=>p.id===this.connectedId);if(c!=='0x89'||a[0]?.toLowerCase()!==p.address.toLowerCase())this.connectedId=null;else await API.request('POST',`/accounts/${this.connectedId}/browser/heartbeat`,null,true);}catch(_){this.connectedId=null;}}
        await this.jobs();
        if(this.dialog?.open){try{const events=await API.request('GET','/accounts/events');this.dialog.querySelector('[data-events]').innerHTML='<h3>最近执行与同步状态</h3>'+events.slice(0,8).map(e=>`<p>${Utils.escape(e.state)} · ${Utils.escape(e.intent_id)}<br>${Utils.escape(e.error||'状态已确认')} · ${Utils.fmtTime(e.created_at)}</p>`).join('');}catch(_){}}
    },
};
