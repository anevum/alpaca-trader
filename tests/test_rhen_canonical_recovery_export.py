"""Synthetic/temporary SQLite fixtures; none of the tests access live RHEN."""
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from scripts.rhen_canonical_recovery_export import recover, write_private_json, _utc_bounds
from datetime import date


def make_db(tmp_path, *, full=False, corrupted=False):
    path=tmp_path / 'synthetic_rhen_core.db'
    with sqlite3.connect(path) as c:
        c.executescript('''
            create table candidates(
            candidate_key text primary key, observed_at text,
            cycle_key text, run_id text, strategy_version_id text,
            symbol text, market_lane text, action text, qualified int,
            final_decision text, reason text, reference_price text,
            stop_price text, target_price text, feature_json text
            );
            create table events(
            event_key text primary key, event_type text,
            occurred_at text, run_id text, strategy_version_id text,
            payload_json text
            );
        ''')
        def event(k,t,at,p):
            c.execute('insert into events values(?,?,?,?,?,?)',
                      (k,t,at,'test-run','LIVE-TEST',json.dumps(p)))
        event('scan-1','decision_cycle','2026-10-07T14:00:00+00:00',{
            'cycle_key':'scan-1','candidate_count':6 if not full else 4,
            'qualified_count':1,'rejected_count':5 if not full else 3,
            'data_status':'ok'})
        for i in range(4):
            c.execute('insert into candidates values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                'candidate-%s'%i,'2026-10-07T14:00:00+00:00',
                'scan-1','test-run','LIVE-TEST','AAPL' if i==0 else 'MSFT',
                'us_equity','buy' if i==0 else 'hold',
                1 if i==0 else 0,'selected' if i==0 else 'rejected',
                'condition', '101','100.5','101.5',
                json.dumps({'quality_score':81,'bar_time':'2026-10-07T13:59:00Z',
                'market_quality':{'spread_pct':'0.0002'},
                'secret_that_must_not_export':'private','note':'ignored'})))
            event(f'out-{i}','candidate_forward_outcome','2026-10-07T15:00:00+00:00',{
                'candidate_id':f'candidate-{i}', 'status':'complete',
                'horizon_minutes':15,'forward_return':'0.0004',
                'computed_at': '2026-10-07T15:00:00+00:00'})
        event('report','research_daily_report','2026-10-07T21:00:00+00:00',{
            'session':'2026-10-07','metrics':{'trade_count':2,'realized_pnl':'-0.02'},
            'account':{'account_id':'PRIVATE'},'source_fingerprint':'A'*64,
            'data_quality_warnings':['shed analytics']})
        if corrupted:
            event('scan-bad','decision_cycle','2026-10-07T15:00:00+00:00',{
                'cycle_key':'scan-1','candidate_count':100,
                'rejected_count':98})
    return path


def test_partial_recovery_exposes_two_missing_rejections(tmp_path):
    db=make_db(tmp_path)
    before=db.read_bytes()
    out=recover(db,'2026-10-07','2026-10-07')
    r=out['sessions'][0]['readiness']
    assert r['observed_cycle_count']==1
    assert r['recovered_candidates']==4
    assert r['reported_candidates_from_observed_cycles']==6
    assert r['minimum_missing_candidates_from_observed_cycles']==2
    assert r['complete_15m_forward_outcomes']==4
    assert 'CANDIDATE_REJECTION_SAMPLING_OR_SHEDDING' in r['blockers']
    assert out['complete_historical_candidate_population_proven'] is False
    assert out['automatic_promotion_authorized'] is False
    assert len(out['source_fingerprint'])==64
    text=json.dumps(out)
    assert 'PRIVATE' not in text
    assert 'secret_that_must_not_export' not in text
    assert db.read_bytes()==before


def test_even_four_of_four_does_not_prove_upstream_cycle_coverage(tmp_path):
    db=make_db(tmp_path,full=True)
    out=recover(db,'2026-10-07','2026-10-07')
    assert out['sessions'][0]['readiness']['minimum_missing_candidates_from_observed_cycles']==0
    assert out['decision']=='AWAITING_COMPLETE_CANONICAL_EVIDENCE'
    assert 'NO_EXTERNAL_PROOF_ALL_SCANNER_CYCLES_DELIVERED' in out['sessions'][0]['readiness']['blockers']


def test_bad_cycle_identity_is_blocked(tmp_path):
    db=make_db(tmp_path,corrupted=True)
    out=recover(db,'2026-10-07','2026-10-07')
    assert out['sessions'][0]['readiness']['unlinked_or_invalid_cycles']>0
    assert 'CYCLE_COUNTS_INVALID_OR_UNLINKED' in out['sessions'][0]['readiness']['blockers']


def test_nonexistent_session_not_zero_trades_claim(tmp_path):
    db=make_db(tmp_path)
    out=recover(db,'2026-10-08','2026-10-08')
    r=out['sessions'][0]['readiness']
    assert 'MISSING_DECISION_CYCLE_EVENTS' in r['blockers']
    assert 'MISSING_SURVIVING_CANDIDATES' in r['blockers']


def test_private_export_permissions_and_no_replace(tmp_path):
    out=tmp_path/'research_private.json'
    write_private_json(out,{'state':'BLOCKED'})
    assert out.stat().st_mode&0o777==0o600
    with pytest.raises(FileExistsError):
        write_private_json(out,{'state':'FALSIFIED_COMPLETE'})
    assert json.loads(out.read_text())=={'state':'BLOCKED'}


def test_session_bounds_use_dst_correctly():
    a,b=_utc_bounds(date(2026,10,9))
    assert a=='2026-10-09T04:00:00+00:00'
    assert b=='2026-10-10T04:00:00+00:00'
    a,b=_utc_bounds(date(2026,11,1))
    assert a=='2026-11-01T04:00:00+00:00'
    assert b=='2026-11-02T05:00:00+00:00'


def test_invalid_span_fails(tmp_path):
    db=make_db(tmp_path)
    with pytest.raises(ValueError,match='bounded'):
        recover(db,'2026-10-07','2026-10-15')
    with pytest.raises(ValueError,match='bounded'):
        recover(db,'2026-10-09','2026-10-07')