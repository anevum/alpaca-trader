from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import httpx
import pytest

from app.btc_direct_strategy import BtcDirectParameters, BtcDirectSwingStrategy
from app.btc_discovery_contract import (COSTS, DELAYS, LIVE_ID, STAGES, VERSION, GATES, catalog,
    chrono_contract, candidate_strategy, fingerprint, gate, resolve_strategy, valid_assignment)
from app.graen.btc_discovery import (NAMESPACE, BtcDiscoveryJob, approved_assignment, projection,
    read_state, record_paper, paper_progress)
from app.rhen_core.store import RhenCoreStore
from app.rhen_core.supervisor import PROCESSES, _child_env
from app.velum_core import ContinuousReplayEngine
from app.velum_btc import normalized_corpus, corpus_coverage
from app.config import Settings

UTC = timezone.utc


def test_data_gap_diagnostics_preserve_fail_closed_chronology():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    lower = start - timedelta(days=35)
    rows = [{"t": (lower + timedelta(hours=i)).isoformat(), "o": "100", "h": "101", "l": "99", "c": "100"} for i in range(841)]
    rows.pop(20)
    quality = corpus_coverage(rows, start, end)
    assert quality == {"expected_bars": 841, "received_bars": 840, "missing_hours": 1,
        "first_missing_at": (lower + timedelta(hours=20)).isoformat(), "last_missing_at": (lower + timedelta(hours=20)).isoformat()}
    with pytest.raises(ValueError, match="incomplete_hourly_corpus:missing=1"):
        normalized_corpus(rows, start, end)


def test_baseline_reader_heartbeat_has_no_assignment_authority(tmp_path):
    store = RhenCoreStore(tmp_path / "heartbeat.db")
    ack = record_paper(store, {"heartbeat": True, "candidate_id": LIVE_ID, "trading_mode": "paper", "run_id": "baseline"})
    assert ack["ok"] and ack["live_authority"] is False
    assert projection(store)["paper_reader"]["authenticated"] is True
    assert approved_assignment(read_state(store)) is None
    with pytest.raises(ValueError):
        record_paper(store, {"heartbeat": True, "candidate_id": LIVE_ID, "trading_mode": "live"})


def evidence(expectancy=.01):
    return {'dataset_fingerprint': 'test-corpus', 'scenarios': {
        f'{cost}:delay_{delay}': {'costs': params, 'delay_bars': delay, 'metrics': {
            'trade_count': 25, 'independent_days': 15, 'net_expectancy': expectancy,
            'profit_factor': 1.5, 'max_drawdown': .08, 'net_return': .1}, 'trades': []}
        for cost, params in COSTS.items() for delay in DELAYS}}


def approved_state():
    candidate = deepcopy(next(iter(catalog().values())))
    results = {stage: evidence() for stage in STAGES[:3]}
    results['VELUM_REPLAY'] = {'verified': True, 'candidate_fingerprint': candidate['fingerprint'],
        'receipts': {stage: {'dataset_fingerprint': results[stage]['dataset_fingerprint'],
                            'result_fingerprint': fingerprint(results[stage])} for stage in STAGES[:3]}}
    contract = chrono_contract(datetime(2026, 10, 1, tzinfo=UTC))
    candidate.update(stage='FORWARD_PAPER', history=list(STAGES[:4]), results=results,
                     approved_at='2026-10-01T00:00:00+00:00', rejection_reasons=[])
    candidate['approval_fingerprint'] = fingerprint({'candidate': candidate['fingerprint'], 'results': results,
        'contract': contract, 'gates': GATES, 'version': VERSION})
    return {'version': VERSION, 'contract': contract, 'stage': 'FORWARD_PAPER', 'status': 'FORWARD_PAPER',
            'paper_candidate_id': candidate['candidate_id'], 'candidates': {candidate['candidate_id']: candidate}}


def test_deterministic_finite_catalog_and_parameterized_default_parity():
    assert catalog() == catalog()
    assert len(catalog()) == 8
    default = BtcDirectSwingStrategy()
    assert default.strategy_version_id == LIVE_ID
    assert default.parameters.payload() == BtcDirectParameters().payload()
    assert default.hard_stop_pct == Decimal('.025')
    for row in catalog().values():
        strategy = candidate_strategy(row)
        assert strategy.parameters.payload() == row['parameters']
        assert strategy.strategy_version_id == row['candidate_id']
        assert strategy.hard_stop_pct <= default.hard_stop_pct
        assert strategy.max_hold_minutes <= default.max_hold_minutes


@pytest.mark.parametrize('assignment', [None, {}, {'candidate_id': 'UNKNOWN'}, {'stage': 'LIVE'}, approved_state()])
def test_live_resolution_ignores_all_research_state(assignment):
    strategy = resolve_strategy('live', assignment)
    assert strategy.strategy_version_id == LIVE_ID
    assert strategy.parameters == BtcDirectParameters()


@pytest.mark.parametrize('mutation', [lambda x: x.update(candidate_id='unknown'),
    lambda x: x['parameters'].update(hard_stop_pct='0.1'), lambda x: x.update(fingerprint='bad'),
    lambda x: x.update(stage='HOLDOUT'), lambda x: x.update(stage='LIVE'), lambda x: x.update(live_authority=True)])
def test_unknown_or_invalid_paper_candidates_fail_closed(mutation):
    row = approved_assignment(approved_state())
    mutation(row)
    with pytest.raises((ValueError, TypeError)):
        resolve_strategy('paper', row)


def test_restart_restores_identical_approved_paper_candidate(tmp_path):
    path = tmp_path / 'core.db'
    initial = RhenCoreStore(path)
    initial.set_kv(NAMESPACE, 'pipeline', approved_state())
    first = approved_assignment(read_state(initial))
    restored = approved_assignment(read_state(RhenCoreStore(path)))
    assert first == restored
    assert resolve_strategy('paper', restored).strategy_version_id == first['candidate_id']
    assert resolve_strategy('live', restored).strategy_version_id == LIVE_ID


@pytest.mark.parametrize('stage', STAGES[:4])
def test_stages_cannot_be_skipped(tmp_path, stage):
    state = approved_state()
    row = next(iter(state['candidates'].values()))
    row['history'].remove(stage)
    with pytest.raises(ValueError, match='lifecycle'):
        approved_assignment(state)


def test_receipt_and_evidence_tampering_close_approval():
    state = approved_state()
    row = next(iter(state['candidates'].values()))
    row['results']['HOLDOUT']['scenarios']['HIGH:delay_2']['metrics']['net_expectancy'] = -.01
    with pytest.raises(ValueError):
        approved_assignment(state)
    state = approved_state()
    row = next(iter(state['candidates'].values()))
    row['results']['VELUM_REPLAY']['receipts']['HOLDOUT']['result_fingerprint'] = 'bad'
    with pytest.raises(ValueError, match='receipt'):
        approved_assignment(state)


def test_gates_require_all_nonzero_cost_and_delay_scenarios():
    assert not gate(evidence())
    result = evidence()
    result['scenarios']['HIGH:delay_2']['costs'] = {'fee_bps': 0, 'spread_bps': 0, 'slippage_bps': 0}
    assert 'HIGH:delay_2:invalid_friction_contract' in gate(result)
    assert len(gate({'scenarios': {}})) == 9
    for field, value in [('trade_count', 1), ('independent_days', 1), ('net_expectancy', 0),
                          ('profit_factor', 1), ('max_drawdown', .16)]:
        result = evidence()
        result['scenarios']['BASE:delay_1']['metrics'][field] = value
        assert any(field in x for x in gate(result))
    assert any('stability' in x for x in gate(evidence(.003), evidence(.01)))


def corpus(start, hours=5):
    lower = start - timedelta(days=35)
    return [{'t': (lower + timedelta(hours=i)).isoformat(), 'o': '100', 'h': '100.1', 'l': '99.9', 'c': '100'}
            for i in range(840 + hours)]


def test_chronological_splits_are_half_open_and_warmup_is_not_scored():
    contract = chrono_contract(datetime(2026, 10, 1, tzinfo=UTC))
    assert contract['development'][1] == contract['validation'][0]
    assert contract['validation'][1] == contract['holdout'][0]
    start = datetime(2026, 3, 1, tzinfo=UTC)
    end = start + timedelta(hours=5)
    rows = corpus(start)
    rows.append({'t': end.isoformat(), 'o': '1', 'h': '999999', 'l': '.1', 'c': '1'})
    normalized = normalized_corpus(rows, start, end)
    assert len(normalized) == 845
    candidate = next(iter(catalog().values()))
    engine = ContinuousReplayEngine(Settings(), candidate_strategy(candidate))
    seen = []
    def evaluate(self, *, bars, now, **kwargs):
        from app.strategy import Signal
        assert all(datetime.fromisoformat(x['t']) < now for x in bars)
        seen.append(now)
        return Signal(action='hold', symbol='BTC/USD', reason='test')
    original = BtcDirectSwingStrategy.evaluate
    BtcDirectSwingStrategy.evaluate = evaluate
    try:
        result = engine.run_btc_direct(rows, start=start, end=end, candidate=candidate)
    finally:
        BtcDirectSwingStrategy.evaluate = original
    assert len(seen) == 5 and min(seen) == start and max(seen) < end
    assert all(x['metrics']['trade_count'] == 0 for x in result['scenarios'].values())


def test_replay_charges_fees_and_executes_delays_at_future_open():
    start = datetime(2026, 3, 1, tzinfo=UTC)
    rows = corpus(start)
    # A changing open exposes the one/two-hour delayed-entry cost.
    for i, row in enumerate(rows[840:]):
        row.update(o=str(100+i), h=str(100.1+i), l=str(99.9+i), c=str(100+i))
    candidate = next(iter(catalog().values()))
    engine = ContinuousReplayEngine(Settings(), candidate_strategy(candidate))
    result = engine.run_btc_direct(rows, start=start, end=start+timedelta(hours=5), candidate=candidate,
                                 prepared_signals={start.isoformat(): True})
    base = result['scenarios']['BASE:delay_0']['trades'][0]
    high = result['scenarios']['HIGH:delay_0']['trades'][0]
    assert high['net_return'] < base['net_return']
    entry = Decimal(base['entry_price']); exit_ = Decimal(base['exit_price']); fee = Decimal('.0035')
    expected = exit_ * (1-fee) / (entry*(1+fee)) - 1
    assert base['net_return'] == pytest.approx(float(expected))
    delayed = result['scenarios']['BASE:delay_2']['trades'][0]
    assert datetime.fromisoformat(delayed['entry_at']) == start+timedelta(hours=2)
    assert float(delayed['entry_price']) > float(base['entry_price'])
    assert all(datetime.fromisoformat(t['exit_at']) <= start+timedelta(hours=5)
               for s in result['scenarios'].values() for t in s['trades'])


def test_duplicate_or_missing_hourly_data_is_rejected():
    start = datetime(2026, 3, 1, tzinfo=UTC)
    rows = corpus(start)
    with pytest.raises(ValueError, match='duplicate'):
        normalized_corpus(rows + [rows[0]], start, start+timedelta(hours=5))
    with pytest.raises(ValueError, match='incomplete'):
        normalized_corpus(rows[:-1], start, start+timedelta(hours=5))


def test_paper_observations_cannot_assign_candidate_or_grant_live(tmp_path):
    store = RhenCoreStore(tmp_path/'core.db')
    state = approved_state()
    store.set_kv(NAMESPACE, 'pipeline', state)
    row = approved_assignment(state)
    for mode in ('live', 'unknown'):
        with pytest.raises(ValueError):
            record_paper(store, {'candidate_id': row['candidate_id'], 'trading_mode': mode})
    with pytest.raises(ValueError):
        record_paper(store, {'candidate_id': 'UNKNOWN', 'trading_mode': 'paper'})
    with pytest.raises(ValueError, match='flat'):
        record_paper(store, {'candidate_id': row['candidate_id'], 'trading_mode': 'paper', 'activate': True, 'flat': False})
    ack = record_paper(store, {'candidate_id': row['candidate_id'], 'trading_mode': 'paper', 'activate': True,
                              'flat': True, 'run_id': 'paper-1', 'orders': []})
    assert ack['live_authority'] is False
    assert read_state(RhenCoreStore(tmp_path/'core.db'))['paper_runtime'] == ack['paper_runtime']
    assert approved_assignment(read_state(store))['candidate_id'] == row['candidate_id']


def test_command_projection_has_real_state_not_fabricated_activity(tmp_path):
    store = RhenCoreStore(tmp_path/'core.db')
    assert projection(store)['running'] is False
    assert projection(store)['state'] == 'NOT_STARTED'
    state = approved_state()
    store.set_kv(NAMESPACE, 'pipeline', state)
    projected = store.strategy_pipeline_research()
    assert projected['candidate']['candidate_id'] == state['paper_candidate_id']
    assert projected['candidate']['supersedes_strategy_version_id'] is None
    assert projected['btc_discovery']['live_broker_writes_allowed'] is False
    assert projected['validation']['status'] == 'VERIFIED'
    assert projected['release_gate']['automatic_promotion'] is False
    assert 'trades' not in projected['candidate']['results']['HOLDOUT']['scenarios']['HIGH:delay_2']


def test_unified_runtime_has_one_scheduled_btc_research_authority(monkeypatch):
    for role in ('graen', 'graen-research', 'crypto-research', 'velum'):
        spec = next(x for x in PROCESSES if x.name == role)
        env = _child_env(spec)
        if role in ('graen', 'graen-research'):
            assert env['GRAEN_RESEARCH_AUTORUN'] == 'false'
        if role == 'crypto-research':
            assert env['GRAEN_LEGACY_BTC_RUNTIME_DISABLED'] == 'true'
        if role == 'velum':
            assert env['VELUM_AUTORUN'] == 'false'
    from app.orchestration_scheduler import SchedulerRuntime
    monkeypatch.setenv('RHEN_UNIFIED_ROLE', 'iren')
    monkeypatch.setenv('RHEN_CANONICAL_SCHEDULER_ENABLED', 'false')
    runtime = SchedulerRuntime()
    active = [w for w in runtime.workflows if runtime.workflow_enabled(w)]
    assert [w['implementation_target'] for w in active] == ['graen_btc_discovery']
    assert runtime.enabled


def test_candidate_in_live_engine_blocks_before_broker_reads():
    from app.crypto_execution import CryptoExecutionEngine
    from app.state import RuntimeState
    class Broker:
        def __getattr__(self, key):
            raise AssertionError('live candidate touched broker: '+key)
    settings = Settings(TRADING_MODE='live', CRYPTO_EXECUTION_MODE='btc_direct_live_signal', CRYPTO_LANE_ENABLED=True)
    engine = CryptoExecutionEngine(settings, Broker(), None, candidate_strategy(next(iter(catalog().values()))), RuntimeState(), None)
    result = asyncio.run(engine.run_once())
    assert result['action'] == 'blocked'
    assert 'live_lane' in result['reason']


def test_live_settings_reject_research_identity():
    with pytest.raises(ValueError, match='paper_only'):
        Settings(TRADING_MODE='live', CRYPTO_STRATEGY_VERSION_ID=next(iter(catalog())))


def test_default_v3_decisions_identical_to_frozen_original():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parent/'fixtures'/'btc_direct_v3.py'
    spec = importlib.util.spec_from_file_location('original_btc_v3', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = module.BtcDirectSwingStrategy()
    default = BtcDirectSwingStrategy()
    explicit_default = BtcDirectSwingStrategy(BtcDirectParameters())
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for kind in ('flat', 'bull', 'bear', 'breakout', 'below_breakout', 'oscillating', 'invalid', 'unfinished'):
        rows = []
        for i in range(840):
            close = Decimal('50000') + Decimal(i*40 if kind in ('bull', 'breakout','below_breakout','unfinished') else -i*20 if kind=='bear' else (i%20)*100 if kind=='oscillating' else 0)
            rows.append({'t':(start+timedelta(hours=i)).isoformat(), 'o':str(close-15),
                         'h':str(close+20), 'l':str(close-25), 'c':str(close)})
        if kind == 'below_breakout':
            rows[-1]['c'] = rows[-2]['c']
        if kind == 'invalid':
            rows[-1]['l'] = '0'
        if kind == 'unfinished':
            rows.append({'t':(start+timedelta(hours=840)).isoformat(), 'o':'1','h':'999999','l':'1','c':'999999'})
        now = start+timedelta(hours=840, minutes=30)
        for prefix in (rows[:20], rows[:720], rows[:721], rows):
            for symbol in ('BTC/USD','ETH/USD'):
                for position in (True,False):
                    kwargs = dict(bars=prefix, confirmation_bars={}, symbol=symbol,has_position=position,order_notional=Decimal('5'),now=now)
                    reference = original.evaluate(**kwargs)
                    assert vars(default.evaluate(**kwargs)) == vars(reference)
                    assert vars(explicit_default.evaluate(**kwargs)) == vars(reference)


def test_canary_verifies_full_lifecycle_proof_and_no_local_database(monkeypatch):
    from app.btc_paper_selection import BtcPaperSelection
    from app.crypto_execution import CryptoExecutionEngine
    from app.state import RuntimeState
    monkeypatch.setenv('BTC_DISCOVERY_PAPER_SELECTION','true')
    settings = Settings(TRADING_MODE='paper',CRYPTO_EXECUTION_MODE='btc_direct_paper', TRADING_INGEST_TOKEN='test',TRADING_RUN_ID='paper-test')
    assignment = approved_assignment(approved_state())
    assignment['approval_results']['HOLDOUT']['scenarios']['HIGH:delay_2']['metrics']['trade_count']=1
    def respond(request):
        assert request.method == 'GET'
        return httpx.Response(200,json={'ok':True,'assignment':assignment,'live_authority':False})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(respond),**kw))
    engine = CryptoExecutionEngine(settings,None,None,BtcDirectSwingStrategy(),RuntimeState(),None)
    asyncio.run(engine.paper_selection.sync(engine,[],[],[]))
    assert not engine.paper_selection.entries_allowed
    assert engine.strategy.strategy_version_id == LIVE_ID
    assert engine.paper_selection.error.startswith('paper_assignment_unavailable:')


def test_candidate_change_waits_for_flat_and_restores_active_after_restart(monkeypatch):
    from app.crypto_execution import CryptoExecutionEngine
    from app.state import RuntimeState
    monkeypatch.setenv('BTC_DISCOVERY_PAPER_SELECTION','true')
    settings = Settings(TRADING_MODE='paper',CRYPTO_EXECUTION_MODE='btc_direct_paper',TRADING_INGEST_TOKEN='test',TRADING_RUN_ID='paper-test')
    state=approved_state(); active=approved_assignment(state)
    # Make another genuinely approved deterministic candidate with a separate proof.
    second = deepcopy(list(catalog().values())[1])
    template = next(iter(state['candidates'].values()))
    row = {**deepcopy(template), **second}
    row['results']['VELUM_REPLAY']['candidate_fingerprint'] = row['fingerprint']
    row['approval_fingerprint']=fingerprint({'candidate':row['fingerprint'],'results':row['results'],
        'contract':state['contract'],'gates':GATES,'version':VERSION})
    state['candidates'][row['candidate_id']]=row
    state['paper_candidate_id']=row['candidate_id']
    state['paper_runtime']={'candidate_id':active['candidate_id'],'run_id':'paper-test','activated_at':'2026-10-01T00:00:00+00:00'}
    assignment=approved_assignment(state)
    def respond(request):
        if request.method=='GET':
            return httpx.Response(200,json={'ok':True,'assignment':assignment,'active_assignment':active,'live_authority':False})
        return httpx.Response(200,json={'ok':True,'candidate_id':row['candidate_id'],'live_authority':False,
                                      'paper_runtime':{'activated_at':'2026-10-02T00:00:00+00:00'}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(respond),**kw))
    engine=CryptoExecutionEngine(settings,None,None,BtcDirectSwingStrategy(),RuntimeState(),None)
    positions=[{'symbol':'BTC/USD','asset_class':'crypto','qty':'1'}]
    asyncio.run(engine.paper_selection.sync(engine,positions,[],[]))
    assert engine.strategy.strategy_version_id==active['candidate_id']
    assert engine.paper_selection.snapshot()['status']=='PENDING_STRATEGY_CHANGE'
    assert engine.paper_selection.pending_candidate_id==row['candidate_id']
    asyncio.run(engine.paper_selection.sync(engine,[],[],[]))
    assert engine.strategy.strategy_version_id==row['candidate_id']
    assert engine.paper_selection.pending_candidate_id is None


def test_order_tags_and_candidate_statistics_do_not_mix_history(tmp_path):
    from app.crypto_execution import CryptoExecutionEngine
    from app.crypto_stats import crypto_trade_stats
    from app.btc_discovery_contract import candidate_order_tag, candidate_order_matches
    from app.state import RuntimeState
    candidates=list(catalog().values())
    settings=Settings()
    strategy=candidate_strategy(candidates[0])
    engine=CryptoExecutionEngine(settings,None,None,strategy,RuntimeState(),None)
    ids=[engine._client_order_id('BTC/USD',action) for action in ('buy','sell','hardstop')]
    assert all(len(x)<=48 and x.startswith('anevum-crypto-') for x in ids)
    assert '-hardstop-' in ids[-1]
    assert all(candidate_order_matches({'client_order_id':x},candidates[0]['candidate_id']) for x in ids)
    assert not candidate_order_matches({'client_order_id':ids[0]},candidates[1]['candidate_id'])
    orders=[]
    for tag, price in [(candidate_order_tag(candidates[0]['candidate_id']),105),
                       (candidate_order_tag(candidates[1]['candidate_id']),1),('legacy',1)]:
        for side,at,p in [('buy','2026-10-01T01:00:00Z',100),('sell','2026-10-01T02:00:00Z',price)]:
            orders.append({'id':tag+side,'client_order_id':f'anevum-crypto-btc-{side}-{tag}-test',
                           'status':'filled','filled_at':at,'side':side,'filled_qty':'1',
                           'filled_avg_price':str(p),'symbol':'BTC/USD'})
    stats=crypto_trade_stats(orders,[],strategy_version_id=candidates[0]['candidate_id'],strategy_family=strategy.strategy_family)
    assert stats['closed_trades']==1
    store=RhenCoreStore(tmp_path/'core.db');state=approved_state();store.set_kv(NAMESPACE,'pipeline',state)
    store.set_kv(NAMESPACE,'paper_runtime',{'candidate_id':state['paper_candidate_id'],'run_id':'paper-1','activated_at':'2026-10-01T00:00:00+00:00'})
    record_paper(store,{'candidate_id':state['paper_candidate_id'],'trading_mode':'paper','run_id':'paper-1','orders':orders})
    fills=store.get_kv(NAMESPACE,'paper_fills:'+state['paper_candidate_id'],{})[0]
    assert len(fills)==2
    state=read_state(store);paper_progress(store,state)
    assert state['paper_progress']['metrics']['trade_count']==1
    assert state['stage']=='FORWARD_PAPER'


def test_interrupted_confirmatory_stage_is_burned_not_retried(tmp_path, monkeypatch):
    monkeypatch.setenv('RHEN_CORE_DB_PATH',str(tmp_path/'core.db'))
    job=BtcDiscoveryJob(Settings())
    state=approved_state();state.pop('paper_candidate_id');state['stage']='VALIDATION';state['running']=True
    row=next(iter(state['candidates'].values()));state['current_candidate_id']=row['candidate_id']
    job.save(state)
    result=asyncio.run(job.tick())
    assert result['state']=='REJECTED'
    assert result['candidate']['rejection_reasons']==['interrupted_confirmatory_stage_evidence_burned']


def test_canonical_job_advances_in_order_and_persists_approved_paper(tmp_path, monkeypatch):
    monkeypatch.setenv('RHEN_CORE_DB_PATH',str(tmp_path/'core.db'))
    monkeypatch.setenv('TRADING_INGEST_TOKEN','test-only')
    job=BtcDiscoveryJob(Settings())
    class Data:
        async def historical_bars_many(self,*args,**kwargs):
            return {'BTC/USD': []}
    job.data=Data()
    async def complete_corpus(*args, **kwargs):
        return []
    monkeypatch.setattr(job, '_load_corpus', complete_corpus)
    monkeypatch.setattr(ContinuousReplayEngine,'run_btc_direct',lambda *a,**k: evidence())
    def respond(request):
        assert request.url.path=='/v1/graen/btc-direct-replay'
        state=read_state(job.store);row=state['candidates'][state['selected_candidate_id']]
        assert row['history']==list(STAGES[:3])
        result={'verified':True,'owner':'VELUM','candidate_fingerprint':row['fingerprint'],
                'receipts':{stage:{'dataset_fingerprint':row['results'][stage]['dataset_fingerprint'],
                                   'result_fingerprint':fingerprint(row['results'][stage])} for stage in STAGES[:3]}}
        return httpx.Response(200,json=result)
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(respond),**kw))
    for _ in range(8):
        result=asyncio.run(job.tick())
        assert result['current_stage']=='DEVELOPMENT'
        assert result['paper_candidate_id'] is None
    validation=asyncio.run(job.tick())
    assert validation['current_stage']=='HOLDOUT'
    assert validation['paper_candidate_id'] is None
    holdout=asyncio.run(job.tick())
    assert holdout['current_stage']=='VELUM_REPLAY'
    paper=asyncio.run(job.tick())
    assert paper['current_stage']=='FORWARD_PAPER'
    assignment=approved_assignment(read_state(RhenCoreStore(tmp_path/'core.db')))
    assert assignment['candidate_id']==paper['paper_candidate_id']
    assert assignment['lifecycle_history']==list(STAGES[:4])
    assert resolve_strategy('paper',assignment).strategy_version_id==assignment['candidate_id']
    assert resolve_strategy('live',assignment).strategy_version_id==LIVE_ID


def test_paper_success_stops_at_review_and_failure_closes_entries(tmp_path):
    from app.btc_discovery_contract import candidate_order_tag
    store=RhenCoreStore(tmp_path/'core.db');state=approved_state();store.set_kv(NAMESPACE,'pipeline',state)
    identity=state['paper_candidate_id'];now=datetime.now(UTC);activated=now-timedelta(days=40)
    store.set_kv(NAMESPACE,'paper_runtime',{'candidate_id':identity,'run_id':'paper-1','activated_at':activated.isoformat()})
    tag=candidate_order_tag(identity);orders=[]
    for day in range(30):
        for side,offset,price in [('buy',1,100),('sell',2,105)]:
            orders.append({'id':f'{day}-{side}','client_order_id':f'anevum-crypto-btc-{side}-{tag}-{day}',
                'status':'filled','filled_at':(activated+timedelta(days=day,hours=offset)).isoformat(),
                'side':side,'filled_qty':'1','filled_avg_price':str(price),'symbol':'BTC/USD'})
    record_paper(store,{'candidate_id':identity,'trading_mode':'paper','run_id':'paper-1','orders':orders})
    state=read_state(store);paper_progress(store,state);store.set_kv(NAMESPACE,'pipeline',state)
    assert state['stage']=='ELIGIBLE_FOR_REVIEW'
    assert state['paper_progress']['metrics']['trade_count']==30
    assignment=approved_assignment(read_state(store))
    assert assignment['live_authority'] is False
    assert assignment['entries_allowed'] is False
    assert resolve_strategy('live',assignment).strategy_version_id==LIVE_ID


def test_minute_recovery_requires_complete_observed_hour():
    from app.graen.btc_discovery import _aggregate_recovery
    hour = datetime(2026, 1, 1, 12, tzinfo=UTC)
    partial = [
        {'t': (hour + timedelta(minutes=i)).isoformat(), 'o': '100', 'h': '101', 'l': '99', 'c': '100.5'}
        for i in range(59)
    ]
    assert _aggregate_recovery(partial, {hour}) == []
    rows = [
        {'t': (hour + timedelta(minutes=i)).isoformat(),
         'o': str(100 + i/100), 'h': str(101 + i/100),
         'l': str(99 - i/100), 'c': str(100.5 + i/100)}
        for i in range(60)
    ]
    recovered = _aggregate_recovery(rows, {hour})
    assert recovered == [{
        't': hour.isoformat(), 'o': '100.0', 'h': '101.59', 'l': '98.41', 'c': '101.09'
    }]


def test_incomplete_development_data_waits_without_consuming_candidate(tmp_path, monkeypatch):
    from app.graen.btc_discovery import CorpusUnavailable
    monkeypatch.setenv('RHEN_CORE_DB_PATH', str(tmp_path/'core.db'))
    job = BtcDiscoveryJob(Settings())

    async def unavailable(*args, **kwargs):
        raise CorpusUnavailable('incomplete_hourly_corpus:missing=1')

    monkeypatch.setattr(job, '_load_corpus', unavailable)
    before = list(catalog())
    result = asyncio.run(job.tick())
    state = read_state(job.store)
    assert result['state'] == 'WAITING_FOR_DATA'
    assert result['search_completed'] == 0
    assert set(state['candidates']) == set(before)
    assert all(row['status'] == 'QUEUED' for row in state['candidates'].values())
    assert all(row['history'] == [] for row in state['candidates'].values())
    assert state['last_error'].startswith('CorpusUnavailable:')


def test_exhausted_state_from_data_only_rejections_is_recoverable(tmp_path, monkeypatch):
    from app.graen.btc_discovery import _reset_incomplete_development_rows
    monkeypatch.setenv('RHEN_CORE_DB_PATH', str(tmp_path/'core.db'))
    job = BtcDiscoveryJob(Settings())
    state = {
        'version': VERSION,
        'contract': chrono_contract(datetime(2026, 10, 1, tzinfo=UTC)),
        'stage': 'DEVELOPMENT',
        'status': 'EXHAUSTED',
        'running': False,
        'candidates': {
            key: {**value, 'stage':'DEVELOPMENT', 'status':'REJECTED', 'history':[], 'results':{},
                  'rejection_reasons':['stage_failed:ValueError:incomplete_hourly_corpus:missing=1']}
            for key, value in catalog().items()
        }
    }
    assert _reset_incomplete_development_rows(state)
    assert state['status'] == 'WAITING_FOR_DATA'
    assert all(row['status'] == 'QUEUED' for row in state['candidates'].values())

    assert all(row['rejection_reasons'] == [] for row in state['candidates'].values())
    assert all(row['non_consumptive_failures'] == [
        'stage_failed:ValueError:incomplete_hourly_corpus:missing=1'
    ] for row in state['candidates'].values())


def test_waiting_state_migrates_legacy_pre_evidence_rejections_without_consuming_search(tmp_path, monkeypatch):
    from app.graen.btc_discovery import CorpusUnavailable
    monkeypatch.setenv('RHEN_CORE_DB_PATH', str(tmp_path/'core.db'))
    job = BtcDiscoveryJob(Settings())
    candidates = {}
    values = list(catalog().items())
    for index, (key, value) in enumerate(values):
        if index == len(values) - 1:
            candidates[key] = {**value, 'stage':'DEVELOPMENT', 'status':'QUEUED',
                               'history':[], 'results':{}, 'rejection_reasons':[]}
        else:
            reason = (
                'stage_failed:ValueError'
                if index < 3
                else 'stage_failed:ValueError:incomplete_hourly_corpus:missing=1'
            )
            candidates[key] = {**value, 'stage':'DEVELOPMENT', 'status':'REJECTED',
                               'history':[], 'results':{}, 'rejection_reasons':[reason]}
    state = {
        'version': VERSION,
        'contract': chrono_contract(datetime(2026, 10, 6, tzinfo=UTC)),
        'stage': 'DEVELOPMENT',
        'status': 'WAITING_FOR_DATA',
        'running': False,
        'current_candidate_id': values[-1][0],
        'candidates': candidates,
    }
    job.save(state)

    async def unavailable(*args, **kwargs):
        raise CorpusUnavailable('incomplete_hourly_corpus:missing=1')

    monkeypatch.setattr(job, '_load_corpus', unavailable)
    result = asyncio.run(job.tick())
    persisted = read_state(job.store)

    assert result['state'] == 'WAITING_FOR_DATA'
    assert result['search_completed'] == 0
    assert all(row['status'] == 'QUEUED' for row in persisted['candidates'].values())
    assert all(row['history'] == [] and row['results'] == {} for row in persisted['candidates'].values())
    migrated = [row for row in persisted['candidates'].values() if row.get('non_consumptive_failures')]
    assert len(migrated) == 7
    assert all(row['rejection_reasons'] == [] for row in migrated)
    assert 'current_candidate_id' in persisted  # current retry is recorded after migration
