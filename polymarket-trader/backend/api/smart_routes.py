from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, SecretStr, Field
from backend.core.smart_trading import SmartStrategy, SmartTrading, ModelConfig, TEMPLATES, estimate, now

router=APIRouter(prefix='/api/smart',tags=['smart-strategies'])


def engine(request):
    return request.app.state.desk.smart


@router.get('/state')
async def state(request:Request):
    e=engine(request)
    configs=await e.configs()
    summaries=[]
    for s in configs:
        _,_,closed=await e.owned(s)
        summaries.append({'config':s.model_dump(),'estimate':estimate(s,closed)})
    async with e.store._db.execute('SELECT * FROM smart_decisions ORDER BY id DESC LIMIT 50') as cur:
        decisions=[dict(r) for r in await cur.fetchall()]
    model=ModelConfig(**(await e.store.get_config('smart_model') or {}))
    templates={k:dict(v,estimate=estimate(SmartStrategy(id='smart_'+'0'*16,name=v['name'],kind=k,
        take_profit=v['take_profit'],stop_loss=v['stop_loss']))) for k,v in TEMPLATES.items()}
    return {'templates':templates,'strategies':summaries,'decisions':decisions,
        'mode':e.desk.manager.mode.value,'account_id':e.desk.manager.accounts.active_id,
        'workflow':(await e.desk.policy()).workflow,'worker_error':await e.store.get_config('smart_worker_error'),
        'worker':e.status,'warmup':{sid+':'+token:len(items) for (sid,token),items in e.histories.items()},
        'model':dict(model.model_dump(),has_api_key=bool(e.vault.get('smart_model').get('api_key'))),
        'model_usage':await e.store.get_config('smart_model_usage') or {}}


@router.post('/estimate')
async def preview(body:SmartStrategy):
    return estimate(body)


@router.put('/strategies/{strategy_id}')
async def save(request:Request,strategy_id:str,body:SmartStrategy):
    e=engine(request)
    if body.id!=strategy_id: raise HTTPException(422,'策略标识不匹配')
    if body.enabled and not body.tokens: raise HTTPException(422,'请指定至少一个真实市场 Token ID，禁止无范围扫描下单')
    if body.enabled and body.kind in ('whale','consensus','llm') and not body.wallets:
        raise HTTPException(422,'钱包跟随/共识/大模型策略必须配置来源钱包白名单')
    if body.enabled and body.kind in ('consensus','llm') and len(body.wallets)<body.min_wallets:
        raise HTTPException(422,'钱包数量少于共识门槛')
    if body.enabled and (body.use_llm or body.kind=='llm'):
        m=ModelConfig(**(await e.store.get_config('smart_model') or {}))
        if not m.enabled or not m.base_url or not m.model: raise HTTPException(422,'请先配置并启用模型接口')
    if body.enabled and body.mode!=e.desk.manager.mode.value: raise HTTPException(409,'策略模式与当前交易模式不一致')
    if body.enabled and body.mode=='LIVE':
        if body.account_id!=e.desk.manager.accounts.active_id or not (await e.desk.manager.live_connection.snapshot())['ready']:
            raise HTTPException(409,'实盘账户未选中或前置连接检查未通过')
    async with e.lock,e.desk.lock:
        rows=await e.configs()
        old=next((s for s in rows if s.id==strategy_id),None)
        if not old and len(rows)>=30: raise HTTPException(409,'最多30个策略配置')
        if old and (old.mode,old.account_id,old.kind)!=(body.mode,body.account_id,body.kind):
            raise HTTPException(409,'已有策略不能更换模式、账户或类型，请新建')
        if old and old.tokens!=body.tokens:
            for token in old.tokens:
                qty,_,_=await e.owned(old,token)
                if qty: raise HTTPException(409,'策略仍有持仓，不能更换交易标的')
        await e.store.set_config('smart_strategies',[r.model_dump() for r in rows if r.id!=strategy_id]+[body.model_dump()])
        # 修改即作废旧审核单，暂停不能留下可执行旧信号。
        for q in await e.desk.queue(limit=100000):
            if q['status']=='waiting' and q['signal']['pipeline_id']==strategy_id:
                await e.desk.mark(q['signal_id'],'rejected','智能策略已修改或暂停，旧信号失效')
        e.histories.clear()
    return body.model_dump()


@router.post('/stop')
async def stop(request:Request):
    e=engine(request)
    # 不等待模型网络请求；配置哈希和审批前校验会拒绝迟到建议。
    async with e.desk.lock:
        rows=await e.configs()
        await e.store.set_config('smart_strategies',[s.model_copy(update={'enabled':False}).model_dump() for s in rows])
        for q in await e.desk.queue(limit=100000):
            if q['status']=='waiting' and q['signal']['pipeline_id'].startswith('smart_'):
                await e.desk.mark(q['signal_id'],'rejected','所有智能策略已暂停')
    return {'ok':True,'scope':'停止新策略信号与未提交审核单；已有持仓保留，已提交订单继续核对'}


class ModelRequest(BaseModel):
    config:ModelConfig
    api_key:SecretStr|None=None


@router.put('/model')
async def model(request:Request,body:ModelRequest):
    e=engine(request)
    async with e.lock:
        old=ModelConfig(**(await e.store.get_config('smart_model') or {}))
        values=e.vault.get('smart_model')
        # 更换供应商不能把上一家 API Key 发给新地址。
        if old.base_url!=body.config.base_url: values={}
        if body.api_key and body.api_key.get_secret_value(): values['api_key']=body.api_key.get_secret_value()
        if body.config.enabled and (not body.config.base_url or not body.config.model): raise HTTPException(422,'请填写接口地址与模型名称')
        import asyncio
        await asyncio.to_thread(e.vault.put,'smart_model',values)
        await e.store.set_config('smart_model',body.config.model_dump())
    return dict(body.config.model_dump(),has_api_key=bool(values.get('api_key')))


@router.post('/model/check')
async def check_model(request:Request):
    e=engine(request)
    from backend.models.schemas import TradeEvent
    s=SmartStrategy(id='smart_'+'0'*16,name='连接测试',kind='llm')
    t=TradeEvent(wallet='0x'+'0'*40,token_id='1',condition_id='connection-test',side='BUY',price=.5,size=1,
                 market_title='Connection test only. No trade will be submitted.')
    try:
        await e.model_decision(s,t,[t])
        return {'ok':True,'message':'模型接口与 JSON 契约正常；本次只测试连接，未生成订单'}
    except Exception:
        raise HTTPException(502,'模型连接/输出校验失败：检查地址、模型、密钥、调用预算与 JSON 输出。测试不会下单。')
