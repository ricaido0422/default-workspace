/* 控制台前端：轮询 /api/state 刷新界面，改参数后 POST /api/config */
const $ = (id) => document.getElementById(id);
const FIELDS = [
  'targetWallet','sizingMode','fixedOrderSizeUsd','percentScale',
  'minTradeUsd','maxTradeUsd','maxPerOrderUsd','maxPerMarketUsd',
  'maxTotalInvestedUsd','minMarketVolumeUsd','stopLossTotalUsd',
  'takeProfitTotalUsd','pollIntervalSeconds','orderType','funderAddress',
  'scopeMode','scopeKeywords',
  'maxDailyLossUsd','autoFlattenOnStop','minSellPrice',
];
let lastConfigSig = '';

function escapeHtml(s){
  return String(s??'').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function usd(n){ return typeof n==='number' && isFinite(n) ? '$'+n.toFixed(2) : String(n??''); }

function showAlert(msg, tone='info'){
  const el=$('alert');
  el.textContent = msg;
  el.className = 'alert ' + tone;
  el.classList.remove('hidden');
}
function hideAlert(){ $('alert').classList.add('hidden'); }

/* ---------- 渲染 ---------- */
function renderState(d){
  const b=d.bot;
  const dot=$('statusDot');
  dot.className='dot '+(b.running?'on':'off');
  $('sRunning').textContent = b.running ? '运行中' : '已停止';
  $('sRunning').style.color = b.running ? 'var(--ok)' : 'var(--dim)';
  $('sSeen').textContent=b.seen;
  $('sFollowed').textContent=b.followed;
  $('sSkipped').textContent=b.skipped;
  $('sInvested').textContent=usd(b.invested);
  $('sRound').textContent=b.round;
  const mode=d.config?.mode ?? '—';
  const badge=$('modeBadge');
  badge.textContent = mode==='live' ? 'LIVE 真实下单' : 'DRY-RUN 只观察';
  badge.className='badge '+(mode==='live'?'live':'dry');

  renderModeSwitch(mode, d);

  $('btnStart').disabled = b.running;
  $('btnStop').disabled = !b.running;

  renderHealth(b, d.config||{});

  if(b.lastError){
    showAlert('轮询出错：'+b.lastError, 'err');
  } else if(!b.running && !$('alert').classList.contains('hidden')
            && $('alert').textContent.startsWith('轮询出错')){
    hideAlert();
  }

  renderEvents(d.events||[]);
  renderLogs(d.logs||[]);

  // 只在配置真的变了时才回填表单，避免打断用户输入
  const sig = JSON.stringify(d.config||{});
  if(sig !== lastConfigSig){
    lastConfigSig = sig;
    fillForm(d.config||{});
  }
}

/* ---------- 模式开关：dry-run ⇄ live，切 live 要手输 CONFIRM ----------
   为什么不做成下拉框：模式是唯一一个"点错就真金白银亏出去"的选项，
   必须比保存参数多一道摩擦。切 LIVE 前把前置条件逐项摊开，缺一项就明确说缺什么。 */
function renderModeSwitch(mode, d){
  const btn=$('btnModeToggle');
  const hint=$('msHint');
  const list=$('msChecklist');
  const wrap=$('modeSwitchWrap');
  if(!btn) return;
  const live = mode==='live';
  wrap.className = 'panel wide modeswitch ' + (live ? 'is-live' : 'is-dry');
  btn.className = 'ms-btn ' + (live ? 'is-live' : 'is-dry');
  btn.textContent = live ? '当前：LIVE　点此切回 DRY-RUN' : '当前：DRY-RUN　点此切换到 LIVE';
  hint.textContent = live
    ? '正在用真钱下单。每一笔都会真实花掉你钱包里的 USDC。'
    : 'dry-run = 只观察、打印、绝不下单。想用真钱交易再切 LIVE。';

  // 切 LIVE 的前置条件。缺什么说什么，不说"请检查配置"这种废话。
  const checks = live ? [] : [
    { ok: Boolean(d.hasPrivateKey), text: d.hasPrivateKey ? '已保存私钥' : '还没填私钥（在下方「钱包」里填，只存在你本机）' },
    { ok: /^0x[0-9a-fA-F]{39,40}$/.test(d.config?.funderAddress || ''), text: '充值地址格式正确（USDC on Base）' },
    { ok: (d.balance ?? null) === null ? null : d.balance.ok,
      text: (d.balance ?? null) === null ? '点一下「测试网络」可顺带查 USDC 余额'
        : d.balance.ok ? `钱包余额 ${usd(d.balance.balance)}`
        : '查不到余额（' + d.balance.error + '）' },
  ];
  if(live){
    list.innerHTML = '<p class="hint">已处于 LIVE。想停下来观察，切回 DRY-RUN 即可，已下的单不受影响。</p>';
  } else {
    list.innerHTML = '<p class="ms-ctitle">切到 LIVE 前需要满足：</p><ul class="ms-clist">' +
      checks.map(c=>{
        const tone = c.ok === null ? 'pend' : c.ok ? 'ok' : 'no';
        const icon = c.ok === null ? '·' : c.ok ? '✓' : '✗';
        return `<li class="ms-${tone}"><b>${icon}</b><span>${escapeHtml(c.text)}</span></li>`;
      }).join('') + '</ul>';
  }
}

$('btnModeToggle').onclick = async () => {
  const d = await api('/api/state').catch(()=>null);
  const mode = d?.config?.mode ?? 'dry-run';
  if(mode === 'live'){
    // 切回 dry-run 不需要确认：这是变安全的动作
    if(!window.confirm('切回 DRY-RUN？\n\n已成交的单不受影响，之后只会观察不再下单。')) return;
    try{
      await api('/api/mode','POST',{ mode: 'dry-run' });
      showAlert('已切回 DRY-RUN，从现在起只观察不下单。','ok');
      lastConfigSig=''; refresh();
    }catch(e){ showAlert('切换失败：'+e.message,'err'); }
    return;
  }
  // 切 LIVE：必须手输 CONFIRM
  const input = window.prompt(
    '切换到 LIVE 会用真钱下单。\n\n' +
    '请确认：① 钱包已充值 USDC（Base 网络）② 已填私钥 ③ 已经用 dry-run 观察过几天\n\n' +
    '确认请输入 CONFIRM（大写）：');
  if(input === null) return;
  if(input.trim() !== 'CONFIRM'){
    showAlert('没有输入 CONFIRM，已取消切换。仍然安全地停在 DRY-RUN。','info');
    return;
  }
  try{
    // 只提交 mode，不把整个表单带上去——别处正在编辑但还没保存的值不该被顺手写进去
    const r = await api('/api/mode','POST',{ mode: 'live' });
    showAlert(r.message || '已切到 LIVE，正在用真钱下单。','ok');
    lastConfigSig=''; refresh();
  }catch(e){
    // 服务端会明确说缺哪一项（私钥/地址/余额），原样展示，不让用户去猜
    const detail = e.data?.errors?.length
      ? '\n' + e.data.errors.map(x=>'· '+x).join('\n')
      : '';
    showAlert('还不能切到 LIVE：' + (e.data?.error || e.message) + detail, 'err');
    refresh();
  }
};

/* ---------- 健康面板：一眼看出"有没有真的在干活" ----------
    之前界面上只能看到"运行中/已停止"，但连续失败 20 次它也显示运行中。
    这里把连续失败、下次轮询倒计时、浮亏进度条、停机原因全部摊开。 */
function renderHealth(b, cfg){
  const panel=$('healthPanel');
  if(!panel) return;

  const rows=[];

  // 1) 网络健康
  if(b.consecutiveFail > 0){
    rows.push(row('err','连续失败',
      `已连续 ${b.consecutiveFail} 次轮询失败，正在退避重试（下次 ${countdown(b.nextRunAt)}）。连续 6 次会自动停机。`));
  } else if(b.running){
    rows.push(row('ok','网络','正常。下次轮询 '+countdown(b.nextRunAt)));
  } else if(b.autoStopped){
    rows.push(row('err','已自动停机',
      `连续失败达到上限。先双击「1-检测网络.bat」确认代理正常，再回来点启动。`));
  } else {
    rows.push(row('dim','网络','未运行，无状态'));
  }

  // 2) 轮询耗时
  if(b.lastPollMs != null){
    rows.push(row(b.lastPollMs > 10000 ? 'warn' : 'ok','上次耗时',
      `${b.lastPollMs} ms` + (b.lastPollMs > 10000 ? '（偏慢，可能网络拥堵或被限流）' : '')));
  }

  // 3) 浮亏进度条 —— 止损/止盈之间画一根位置条
  const risk=b.risk||{};
  const stop=Number(cfg.stopLossTotalUsd ?? 0);
  const take=Number(cfg.takeProfitTotalUsd ?? 0);
  if(risk.halted){
    rows.push(row('err','风险停机', `${risk.haltReason}　→　点下方「解除停机」才能重新启动`));
  }
  if(risk.pnl != null && isFinite(risk.pnl)){
    rows.push(pnlBar(risk.pnl, stop, take, risk.pnlSource));
  } else if(b.running){
    rows.push(row('dim','浮亏','首次检查将在第 5 轮后进行'));
  }

  // 4) 单日熔断
  const dl=risk.dailyLoss;
  const today=new Date().toISOString().slice(0,10);
  const usedToday = dl && dl.date===today ? dl.amount : 0;
  const cap=Number(cfg.maxDailyLossUsd ?? 0);
  if(cap>0){
    rows.push(row(usedToday>0?'warn':'ok','今日已亏',
      `${usd(usedToday)} / 熔断线 ${usd(cap)}`));
  } else {
    rows.push(row('dim','今日熔断','未启用（建议设 5，这是当天亏损的硬上限）'));
  }

  panel.innerHTML = rows.join('');
}

function row(tone,label,text){
  return `<div class="hrow h-${tone}">
    <span class="hlabel">${escapeHtml(label)}</span>
    <span class="htext">${text}</span>
  </div>`;
}

/** 浮亏进度条：止损线在左，止盈线在右，当前位置在中间。 */
function pnlBar(pnl, stop, take, source){
  const lo = stop<0 ? stop : -10;
  const hi = take>0 ? take : 10;
  const clamp = (v)=>Math.max(lo,Math.min(hi,v));
  const pct = ((clamp(pnl)-lo)/(hi-lo))*100;
  const tone = pnl<=stop ? 'err' : pnl>=take ? 'ok' : pnl<0 ? 'warn' : 'ok';
  const srcTip = source ? `（${escapeHtml(source)}）` : '';
  return `<div class="hrow h-${tone}">
    <span class="hlabel">浮盈亏</span>
    <span class="hpnl">
      <span class="hpnl-val">${usd(pnl)}</span>
      <span class="hpnl-bar"><i style="left:${pct.toFixed(1)}%"></i></span>
      <span class="hpnl-scale">${usd(lo)} … ${usd(hi)}</span>
    </span>
    <span class="hsub">${srcTip}</span>
  </div>`;
}

function countdown(iso){
  if(!iso) return '—';
  const s=Math.round((new Date(iso).getTime()-Date.now())/1000);
  if(s<=0) return '即将执行';
  if(s<60) return `${s} 秒后`;
  return `${Math.floor(s/60)} 分 ${s%60} 秒后`;
}

function renderEvents(events){
  const tb=$('evBody');
  if(!events.length){
    tb.innerHTML='<tr><td colspan="5" class="empty">暂无记录，启动后这里会出现跟单/跳过记录</td></tr>';
    return;
  }
  tb.innerHTML = events.map(e=>{
    const kind = e.kind==='follow' ? '<span class="t-follow">跟单</span>'
               : e.kind==='error' ? '<span class="t-error">失败</span>'
               : e.kind==='history' ? '<span class="t-history">历史</span>'
               : '<span class="t-skip">跳过</span>';
    const amt = e.amount!=null ? usd(e.amount) : '—';
    return `<tr>
      <td>${escapeHtml(e.ts)}</td>
      <td>${kind}</td>
      <td>${usd(e.size)}</td>
      <td class="mkt" title="${escapeHtml(e.title)}">${escapeHtml(e.title)}</td>
      <td class="rsn">${escapeHtml(e.reason)}</td>
    </tr>`;
  }).join('');
}

function renderLogs(logs){
  const box=$('logBox');
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  box.innerHTML = logs.map(l=>
    `<div class="l-${l.level}">[${escapeHtml(l.ts)}] ${escapeHtml(l.level.toUpperCase().padEnd(4))} ${escapeHtml(l.msg)}</div>`
  ).join('');
  if(atBottom) box.scrollTop = box.scrollHeight;
}

function fillForm(c){
  for(const k of FIELDS){
    const el=$('f_'+k);
    if(!el || c[k]===undefined) continue;
    // 复选框要读 checked，不是 value
    if (el.type === 'checkbox') { el.checked = Boolean(c[k]); continue; }
    // scopeKeywords 在配置里是数组，回填成人类可读的逗号串
    el.value = Array.isArray(c[k]) ? c[k].join(', ') : c[k];
  }
}

/* ---------- 请求 ---------- */
async function api(path, method='GET', body){
  const opt = { method, headers:{} };
  if(body){ opt.headers['content-type']='application/json'; opt.body=JSON.stringify(body); }
  const r = await fetch(path, opt);
  let j;
  try{ j=await r.json(); }catch{ throw new Error('服务器返回了非 JSON 响应'); }
  if(!r.ok){
    // 把服务端的 errors 数组（"缺哪一项"）一起带上，调用方能原样展示给用户
    const err = new Error(j.errors?.length ? j.errors.join('；') : (j.error || '请求失败'));
    err.data = j;
    throw err;
  }
  return j;
}

async function refresh(){
  try{
    const d = await api('/api/state');
    renderState(d);
  }catch(e){
    showAlert('连不上控制台后端：'+e.message+'（程序可能已关闭，请重新双击 bat）','err');
  }
}

/* ---------- 事件绑定 ---------- */
$('btnStart').onclick = async () => {
  try{
    await api('/api/start','POST');
    showAlert('已启动。第一轮只记录不跟单（防止把你对象过去 100 笔一次性追高）。','ok');
    refresh();
  }catch(e){ showAlert('启动失败：'+e.message,'err'); }
};
$('btnStop').onclick = async () => {
  try{
    await api('/api/stop','POST');
    showAlert('已停止。参数、持仓账目都已落盘，下次启动接着算。','info');
    refresh();
  }catch(e){ showAlert('停止失败：'+e.message,'err'); }
};
$('btnProbe').onclick = async () => {
  showAlert('正在测试连通性并查询 USDC 余额…','info');
  try{
    const r = await api('/api/probe');
    const lines = r.items.map(i=>`${i.ok?'✓':'✗'} ${i.name} ${i.ms}ms ${i.note||''}`).join('　|　');
    // 余额单独查一次，结果会进 snapshot，模式开关才能显示"钱包余额 $X"
    let balLine = '';
    try{
      const b = await api('/api/balance');
      balLine = b.ok ? `　|　USDC 余额 ${usd(b.balance)}` : `　|　余额查询失败（${b.error}）`;
    }catch(e){ balLine = '　|　余额查询失败'; }
    showAlert(lines + balLine, r.ok?'ok':'err');
    lastConfigSig='';
    refresh();
  }catch(e){ showAlert('测试失败：'+e.message,'err'); }
};
$('btnSim').onclick = async () => {
  showAlert('正在用最近 50 条真实成交试跑当前参数…','info');
  try{
    const r = await api('/api/simulate');
    const head = `最近 ${r.total} 条成交：能跟 ${r.pass} 笔，合计 $${r.passAmount.toFixed(2)}`;
    const detail = r.reasons.map(x=>`${x.reason} ×${x.count}`).join('　|　');
    if(r.pass === 0){
      showAlert(head + '。全是下面这些原因拦掉的，说明参数太严：　' + detail, 'err');
    } else {
      showAlert(head + '。被拦掉的：　' + detail, 'ok');
    }
  }catch(e){ showAlert('体检失败：'+e.message,'err'); }
};
$('btnUnhalt').onclick = async () => {
  try{
    const r = await api('/api/unhalt','POST');
    $('unhaltMsg').textContent = `已解除停机：${r.reason||'手动解除'}`;
    showAlert('已解除风险停机。停机期间的持仓账目没有动过。','ok');
    refresh();
  }catch(e){
    $('unhaltMsg').textContent = '';
    showAlert('解除失败：'+e.message,'err');
  }
};

$('btnSave').onclick = async () => {
  const patch={};
  for(const k of FIELDS){
    const el=$('f_'+k);
    if(!el) continue;
    patch[k] = el.type === 'checkbox' ? el.checked : el.value;
  }
  const pk=$('f_privateKey').value.trim();
  if(pk) patch.privateKey=pk;   // 留空=不修改
  try{
    const r=await api('/api/config','POST',patch);
    $('saveMsg').textContent = r.message || '已保存';
    $('saveMsg').style.color='var(--ok)';
    $('f_privateKey').value='';
    lastConfigSig='';            // 强制下次刷新回填
    showAlert('已保存，下一轮轮询自动生效，不用重启。','ok');
    setTimeout(refresh,300);
  }catch(e){
    $('saveMsg').textContent = '保存失败：'+e.message;
    $('saveMsg').style.color='var(--err)';
    showAlert('保存失败：'+e.message,'err');
  }
};

/* 页签切换已移到 tabs.js（那里统一管理主面板和分析中心的全部页签），此处不再处理。 */

/* ---------- 启动 ---------- */
refresh();
setInterval(refresh, 3000);
