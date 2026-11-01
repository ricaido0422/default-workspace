/* ================= 交易员排行榜 ================= */
const LB_COLS = ['#', '交易员', '样本', '胜率', '盈亏比', '累计盈亏', '最大回撤', '体检结论'];

function pct(n){ return typeof n==='number' ? (n*100).toFixed(1)+'%' : '—'; }
function pf(n){
  if(n===Infinity) return '∞';
  if(typeof n!=='number' || !isFinite(n)) return '—';
  return n.toFixed(2);
}
function money(n){
  if(typeof n!=='number' || !isFinite(n)) return '—';
  const c = n>=0 ? 'pos' : 'neg';
  return `<span class="${c}">${n>=0?'+':'-'}$${Math.abs(n).toFixed(0)}</span>`;
}

function renderLeaderboard(d){
  const box = $('lbBody');
  if(!d.ok){
    box.innerHTML = `<tr><td colspan="8" class="empty">${escapeHtml(d.error||'获取失败')}</td></tr>`;
    return;
  }
  if(!d.rows?.length){
    box.innerHTML = '<tr><td colspan="8" class="empty">名单为空，请在下方添加地址</td></tr>';
    return;
  }
  box.innerHTML = d.rows.map(r=>{
    if(r.error){
      return `<tr><td>${r.rank}</td><td class="mono">${escapeHtml(r.address.slice(0,10)+'…')}</td>
        <td colspan="6" class="neg">获取失败：${escapeHtml(r.error)}</td></tr>`;
    }
    if(!r.total){
      return `<tr><td>${r.rank}</td><td class="mono">${escapeHtml((r.name||r.address).slice(0,14))}</td>
        <td colspan="6">无已平仓记录，无法评估（纯持仓型或新号）</td></tr>`;
    }
    const qual = r.exhausted
      ? (r.qualified
          ? '<span class="t-follow">合格</span>'
          : '<span class="t-skip">未达标</span>')
      : '<span class="t-error">数据不完整</span>';
    const flags = (r.flags||[]).map(f=>`<div class="flag flag-${f.level}">${escapeHtml(f.text)}</div>`).join('');
    // 点击「设为目标」直接把这个交易员填进跟单对象
    const setBtn = `<button class="mini" data-set-target="${escapeHtml(r.address)}">设为跟单对象</button>`;
    return `<tr>
      <td>${r.rank}</td>
      <td>
        <div class="mono">${escapeHtml(r.name || r.address.slice(0,10)+'…')}</div>
        ${setBtn}
      </td>
      <td>${r.total}</td>
      <td>${r.exhausted ? pct(r.winRate) : '<span class="t-error" title="数据被截断，此值虚高不可用">' + pct(r.winRate) + '?</span>'}</td>
      <td>${r.exhausted ? pf(r.profitFactor) : '<span class="t-error">?</span>'}</td>
      <td>${money(r.netPnl)}</td>
      <td>$${(r.maxDrawdownUsd||0).toFixed(0)}</td>
      <td>${qual}${flags}</td>
    </tr>`;
  }).join('');

  // 绑定「设为目标」按钮
  box.querySelectorAll('[data-set-target]').forEach(b=>{
    b.onclick = async ()=>{
      const addr = b.dataset.setTarget;
      try{
        await api('/api/config','POST',{ targetWallet: addr });
        showAlert('已把 '+addr.slice(0,10)+'… 设为跟单对象。请「停止→启动」生效。','ok');
        lastConfigSig='';
        refresh();
      }catch(e){ showAlert('设置失败：'+e.message,'err'); }
    };
  });
}

async function loadLeaderboard(){
  const box = $('lbBody');
  box.innerHTML = '<tr><td colspan="8" class="empty">正在逐个翻页拉取已平仓记录，较慢，请稍候…</td></tr>';
  $('lbProgress').textContent = '扫描中…';
  try{
    const d = await api('/api/leaderboard?limit=8');
    renderLeaderboard(d);
    $('lbProgress').textContent = d.refreshedAt
      ? `已扫 ${d.scanned}/${d.watchlistTotal} 位 · ${new Date(d.refreshedAt).toLocaleTimeString('zh-CN')}`
      : '';
  }catch(e){
    box.innerHTML = `<tr><td colspan="8" class="empty neg">${escapeHtml(e.message)}</td></tr>`;
    $('lbProgress').textContent = '';
  }
}

/* ================= 交易员体检 ================= */
async function loadAudit(addrOverride){
  const addr = addrOverride || $('auditAddr').value.trim() || ($('f_targetWallet')?.value || '');
  if(!addr){
    $('auditResult').innerHTML = '<div class="empty">请填地址</div>';
    return;
  }
  $('auditResult').innerHTML = '<div class="empty">正在拉取全量已平仓记录…</div>';
  try{
    const d = await api('/api/audit?address='+encodeURIComponent(addr));
    if(!d.ok){ $('auditResult').innerHTML = `<div class="neg">${escapeHtml(d.error)}</div>`; return; }
    const r = d.raw;
    const cards = [
      ['已平仓笔数', r.total],
      ['胜 / 负', `${r.wins} / ${r.losses}`],
      ['胜率', pct(r.winRate)],
      ['盈亏比 PF', pf(r.profitFactor)],
      ['累计盈亏', money(r.netPnl)],
      ['平均收益率', pct(r.avgReturn)],
      ['最大回撤', money(-Math.abs(r.maxDrawdownUsd))],
      ['单笔最好 / 最差', `${money(r.bestTrade)} / ${money(r.worstTrade)}`],
    ];
    const flagsHtml = (d.flags||[]).map(f=>`<div class="flag flag-${f.level}">${escapeHtml(f.text)}</div>`).join('')
      || '<div class="flag flag-ok">没有触发任何风险提示</div>';

    const days = r.sampleDays ? `　覆盖约 ${r.sampleDays} 天` : '';
    $('auditResult').innerHTML = `
      <div class="auditcards">${cards.map(([k,v])=>`<div class="acard"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('')}</div>
      <div class="flaglist">${flagsHtml}</div>
      <div class="hint">数据源：Polymarket 官方 /closed-positions 全量翻页${days}。这是我们自己的算法，不采信任何平台标称值。</div>`;
  }catch(e){
    $('auditResult').innerHTML = `<div class="neg">${escapeHtml(e.message)}</div>`;
  }
}

/* ================= 应用推荐 ================= */
async function loadRecommend(){
  const capital = Number($('recCapital').value || 30);
  $('recResult').innerHTML = '<div class="empty">正在分析对方的下注节奏…</div>';
  try{
    const d = await api('/api/recommend?capital='+capital);
    if(!d.ok){ $('recResult').innerHTML = `<div class="neg">${escapeHtml(d.error)}</div>`; return; }
    const s = d.suggested;
    const rows = [
      ['每笔金额', '$'+s.fixedOrderSizeUsd],
      ['规模区间下限', '$'+s.minTradeUsd],
      ['规模区间上限', '$'+s.maxTradeUsd],
      ['单笔上限', '$'+s.maxPerOrderUsd],
      ['单市场上限', '$'+s.maxPerMarketUsd],
      ['总仓位上限', '$'+s.maxTotalInvestedUsd],
    ];
    $('recResult').innerHTML = `
      <table class="kv">
        ${rows.map(([k,v])=>`<tr><td>${k}</td><td class="v">${v}</td></tr>`).join('')}
      </table>
      <div class="rationale">${d.rationale.map(t=>`<div>· ${escapeHtml(t)}</div>`).join('')}</div>
      <button id="btnApplyRec" class="primary mini">一键套用这套参数</button>
      <span class="hint" id="applyMsg"></span>`;
    $('btnApplyRec').onclick = async ()=>{
      try{
        const r = await api('/api/apply-recommend','POST',{ capital });
        $('applyMsg').textContent = '已套用并保存。请「停止→启动」。';
        $('applyMsg').style.color='var(--ok)';
        lastConfigSig=''; refresh();
      }catch(e){
        $('applyMsg').textContent = '套用失败：'+e.message;
        $('applyMsg').style.color='var(--err)';
      }
    };
  }catch(e){
    $('recResult').innerHTML = `<div class="neg">${escapeHtml(e.message)}</div>`;
  }
}

/* ================= 板块 / 赛事过滤 ================= */
const SCOPE_PRESETS = [
  ['只看电竞大板块', 'esports'],
  ['只看英雄联盟', 'league-of-legends'],
  ['只看 Dota2', 'dota-2'],
  ['只看体育大板块', 'sports'],
  ['排除电竞', '--esports'],
];

async function loadScopeScan(){
  const note=$('scopeNote'), box=$('scopeResult');
  note.textContent='正在扫描他最近的下注板块…';
  box.innerHTML='<div class="empty">拉取最近成交并逐个查 /events 标签，约需十几秒…</div>';
  try{
    const d = await api('/api/scopes?limit=60');
    if(!d.ok){ box.innerHTML=`<div class="neg">${escapeHtml(d.error)}</div>`; note.textContent=''; return; }
    note.textContent = `扫了最近 ${d.trades} 笔成交`;
    if(!d.scopes.length){
      box.innerHTML='<div class="empty">没扫到任何板块标签（可能对方最近没交易，或 gamma-api 在限流）</div>';
      return;
    }
    const cur = new Set(String($('f_scopeKeywords').value||'').split(/[,，]/).map(s=>s.trim().toLowerCase()).filter(Boolean));
    const mode = String($('f_scopeMode').value||'all');
    box.innerHTML = `
      <div class="presetbar">${SCOPE_PRESETS.map(([label,val])=>
        `<button class="mini" data-preset="${escapeHtml(val)}">${escapeHtml(label)}</button>`).join('')}</div>
      <table class="lb">
        <thead><tr><th>板块</th><th>笔数</th><th>成交额</th><th>占样本</th><th>操作</th></tr></thead>
        <tbody>${d.scopes.map(s=>{
          const pctv = d.trades ? Math.round(s.count/d.trades*100) : 0;
          const on = cur.has(s.slug.toLowerCase());
          const act = mode==='blacklist'
            ? `<button class="mini danger" data-bl="${escapeHtml(s.slug)}">拉黑</button>`
            : `<button class="mini" data-wl="${escapeHtml(s.slug)}">只跟这个</button>`;
          return `<tr>
            <td class="mono">${escapeHtml(s.label || s.slug)}${on?' <span class="t-follow">✓已选</span>':''}</td>
            <td>${s.count}</td>
            <td>$${s.amount.toFixed(0)}</td>
            <td>${pctv}%</td>
            <td>${act}</td>
          </tr>`;
        }).join('')}</tbody>
      </table>`;

    const apply = (list, m)=>{
      $('f_scopeMode').value = m;
      $('f_scopeKeywords').value = list.join(', ');
      saveFromScopeBanner(m, list);
    };
    box.querySelectorAll('[data-wl]').forEach(b=> b.onclick=()=>apply([b.dataset.wl],'whitelist'));
    box.querySelectorAll('[data-bl]').forEach(b=> b.onclick=()=>apply([b.dataset.bl],'blacklist'));
    box.querySelectorAll('[data-preset]').forEach(b=>{
      const v=b.dataset.preset;
      b.onclick=()=>{
        if(v.startsWith('--')) apply([v.slice(2)],'blacklist');
        else apply([v],'whitelist');
      };
    });
  }catch(e){
    box.innerHTML=`<div class="neg">${escapeHtml(e.message)}</div>`; note.textContent='';
  }
}

async function saveFromScopeBanner(mode, list){
  try{
    await api('/api/config','POST',{ scopeMode:mode, scopeKeywords:list.join(',') });
    showAlert(`已设为 ${mode}：${list.join('，')}。请「停止→启动」生效。`,'ok');
    lastConfigSig=''; refresh();
    loadScopeScan();
  }catch(e){ showAlert('保存失败：'+e.message,'err'); }
}

async function loadScopeSim(){
  const note=$('scopeNote'), box=$('scopeResult');
  note.textContent='正在用最近成交试跑板块关键词…';
  try{
    const d = await api('/api/scopes/simulate?limit=40');
    if(!d.ok){ box.innerHTML=`<div class="neg">${escapeHtml(d.error)}</div>`; note.textContent=''; return; }
    note.textContent = `最近 ${d.total} 笔：板块过滤后能跟 ${d.pass} 笔`;
    const reasons = d.reasons.length
      ? `<div class="rationale">${d.reasons.map(x=>`<div>· ${escapeHtml(x.reason)} ×${x.count}</div>`).join('')}</div>`
      : '';
    box.innerHTML = `
      ${reasons}
      <div class="tablescroll"><table class="lb">
        <thead><tr><th>结果</th><th>市场</th><th>所属板块</th><th>金额</th></tr></thead>
        <tbody>${d.rows.map(r=>`
          <tr class="${r.pass?'row-pass':'row-skip'}">
            <td>${r.pass?'<span class="t-follow">跟</span>':'<span class="t-skip">跳</span>'}</td>
            <td class="mkt">${escapeHtml(r.title)}</td>
            <td class="mono sm">${escapeHtml(r.label)}</td>
            <td>$${(r.usd||0).toFixed(0)}</td>
          </tr>`).join('')}</tbody>
      </table></div>`;
  }catch(e){
    box.innerHTML=`<div class="neg">${escapeHtml(e.message)}</div>`; note.textContent='';
  }
}

/* ================= 关注名单 ================= */
async function loadWatchlist(){
  try{
    const d = await api('/api/watchlist');
    const box = $('wlBody');
    if(!d.traders?.length){
      box.innerHTML = '<tr><td colspan="3" class="empty">还没有添加任何地址</td></tr>';
      return;
    }
    box.innerHTML = d.traders.map(t=>`
      <tr>
        <td class="mono">${escapeHtml(t.name || '(未命名)')}</td>
        <td class="mono sm">${escapeHtml(t.address)}</td>
        <td><button class="mini danger" data-del="${escapeHtml(t.address)}">删除</button></td>
      </tr>`).join('');
    box.querySelectorAll('[data-del]').forEach(b=>{
      b.onclick = async ()=>{
        await api('/api/watchlist?address='+encodeURIComponent(b.dataset.del),'DELETE');
        loadWatchlist();
      };
    });
  }catch(e){ /* 静默 */ }
}

async function addToWatchlist(){
  const addr = $('wlAddr').value.trim();
  const name = $('wlName').value.trim();
  if(!addr){ $('wlMsg').textContent='请填地址'; return; }
  try{
    await api('/api/watchlist','POST',{ address: addr, name });
    $('wlAddr').value=''; $('wlName').value='';
    $('wlMsg').textContent='已添加'; $('wlMsg').style.color='var(--ok)';
    loadWatchlist();
  }catch(e){
    $('wlMsg').textContent='添加失败：'+e.message; $('wlMsg').style.color='var(--err)';
  }
}

/* ================= 导出给主脚本 ================= */
window.leaderboard = {
  renderLeaderboard, loadLeaderboard, loadAudit, loadRecommend,
  loadWatchlist, addToWatchlist, LB_COLS,
  loadScopeScan, loadScopeSim,
};
