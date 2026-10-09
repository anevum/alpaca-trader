#!/usr/bin/env python3
"""RHEN Core read-only session evidence recovery (operator-controlled OFFLINE).

Run against a private, consistent copy of /data/rhen-core.db. This script cannot
reconstruct candidate records previously sampled out of decision cycles or
removed during analytics shedding / retention. Its output explicitly blocks
claims of complete historical alpha evidence. No network, broker, or live APIs.

Usage:
  python rhen_canonical_recovery_export.py --db /private/rhen-core-snapshot.db \
    --start 2026-10-07 --end 2026-10-09 \
    --output /private/rhen-core-recovered.json
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote
from zoneinfo import ZoneInfo

SCHEMA = 'rhen-core-canonical-recovery-v1'
NY = ZoneInfo('America/New_York')
UTC = timezone.utc
MAX_ROWS = 250000
MAX_SESSIONS = 7
SAFE_FEATURES = frozenset({
    'market', 'momentum_pct', 'vwap_edge_pct', 'relative_volume_ratio',
    'trend_persistence', 'quality_score', 'current_close', 'bar_time',
    'evidence_reference_only', 'warmup_bar_count', 'required_bar_count',
    'confirmation_passes', 'regime_passes', 'opportunity_score',
    'estimated_net_edge_pct', 'expected_gross_move_pct', 'return_5m',
    'return_15m', 'return_60m', 'range_60m_pct', 'market_quality',
})


def _as_date(s: str) -> date:
    try:
        d = date.fromisoformat(s)
    except (TypeError, ValueError) as exc:
        raise ValueError('session must be YYYY-MM-DD') from exc
    if d.isoformat() != s:
        raise ValueError('session must be YYYY-MM-DD')
    return d


def _utc_bounds(d: date) -> tuple[str, str]:
    a = datetime.combine(d, time.min, tzinfo=NY).astimezone(UTC)
    b = datetime.combine(d + timedelta(days=1), time.min, tzinfo=NY).astimezone(UTC)
    return a.isoformat(), b.isoformat()


def _dec(v: object) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return x if x.is_finite() else None


def _decode_json(s: str | None) -> dict:
    if not s:
        return {}
    try:
        obj = json.loads(s)
    except (TypeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _read_bounded(conn: sqlite3.Connection, query: str, args: tuple, *, limit: int = MAX_ROWS) -> list[sqlite3.Row]:
    if not 1 <= limit <= MAX_ROWS:
        raise ValueError('unbounded query prohibited')
    got = conn.execute(query + ' LIMIT ?', (*args, limit + 1)).fetchall()
    if len(got) > limit:
        raise ValueError(f'source exceeds strict {limit}-row cap; export must paginate without sampling')
    return got


def _fetch_session(conn: sqlite3.Connection, session: date) -> dict:
    start, end = _utc_bounds(session)
    cs = _read_bounded(
        conn,
        "SELECT candidate_key, observed_at, cycle_key, run_id, strategy_version_id, "
        "symbol, market_lane, action, qualified, final_decision, reason, "
        "reference_price, stop_price, target_price, feature_json "
        "FROM candidates WHERE observed_at >= ? AND observed_at < ? "
        "ORDER BY observed_at, candidate_key",
        (start, end),
    )
    cycles_raw = _read_bounded(
        conn,
        "SELECT event_key, occurred_at, run_id, strategy_version_id, payload_json "
        "FROM events WHERE event_type='decision_cycle' AND occurred_at >= ? "
        "AND occurred_at < ? ORDER BY occurred_at, event_key",
        (start, end),
    )
    # Outcomes can be computed after the source session, including next session.
    # This is deliberately a bounded 72h follow-up scan; do not claim outcome
    # existence is complete if the original records were shed or pruned.
    followup = datetime.fromisoformat(end) + timedelta(hours=72)
    os_ = _read_bounded(
        conn,
        "SELECT event_key, occurred_at, payload_json FROM events "
        "WHERE event_type='candidate_forward_outcome' AND occurred_at >= ? "
        "AND occurred_at < ? ORDER BY occurred_at, event_key",
        (start, followup.isoformat()),
    )
    reports = _read_bounded(
        conn,
        "SELECT event_key, occurred_at, payload_json FROM events "
        "WHERE event_type='research_daily_report' AND occurred_at >= ? "
        "AND occurred_at < ? ORDER BY occurred_at DESC, event_key DESC",
        (start, followup.isoformat()),
        limit=1000,
    )

    candidates = []
    by_cycle = Counter()
    rejected_by_cycle = Counter()
    accepted_ids = set()
    for row in cs:
        k = str(row['candidate_key'] or '')
        if not k or k in accepted_ids:
            raise ValueError('candidate key missing or duplicated in source')
        accepted_ids.add(k)
        cycle = str(row['cycle_key'] or '')
        by_cycle[cycle] += 1
        if str(row['final_decision'] or '').lower() == 'rejected':
            rejected_by_cycle[cycle] += 1
        features = _decode_json(row['feature_json'])
        candidates.append({
            'candidate_id': k, 'observed_at': row['observed_at'],
            'cycle_key': cycle or None, 'run_id': row['run_id'],
            'strategy_version_id': row['strategy_version_id'],
            'symbol': row['symbol'], 'market_lane': row['market_lane'],
            'action': row['action'], 'qualified': bool(row['qualified']),
            'final_decision': row['final_decision'], 'reason': row['reason'],
            'decision_reference_price': row['reference_price'],
            'stop_price': row['stop_price'], 'target_price': row['target_price'],
            'features': {k:features[k] for k in sorted(features) if k in SAFE_FEATURES},
            'forward_outcomes': {},
        })

    by_id = {row['candidate_id']:row for row in candidates}
    outcome_seen: set[tuple[str, int]] = set()
    outcome_events_scanned = 0
    outcome_duplicate_count = 0
    orphan_outcome_count = 0
    for row in os_:
        payload = _decode_json(row['payload_json'])
        cid = str(payload.get('candidate_id') or payload.get('candidate_key') or '')
        if not cid or cid not in by_id:
            orphan_outcome_count += 1
            continue
        try:
            horizon = int(payload.get('horizon_minutes'))
        except (ValueError, TypeError):
            continue
        if horizon <= 0 or horizon > 390:
            continue
        outcome_events_scanned += 1
        key = (cid, horizon)
        if key in outcome_seen:
            outcome_duplicate_count += 1
        outcome_seen.add(key)
        by_id[cid]['forward_outcomes'][str(horizon)] = {
            'status': payload.get('status'),
            'forward_return': payload.get('forward_return'),
            'max_favorable_return': payload.get('max_favorable_return'),
            'max_adverse_return': payload.get('max_adverse_return'),
            'computed_at': payload.get('computed_at') or row['occurred_at'],
            'methodology_version': payload.get('methodology_version'),
        }

    cycles = []
    cycle_ids = set()
    reported_total = 0
    recovered_total = 0
    missing_minimum = 0
    invalid_cycles = 0
    for row in cycles_raw:
        payload = _decode_json(row['payload_json'])
        cid = str(payload.get('cycle_key') or '')
        if not cid or cid in cycle_ids:
            invalid_cycles += 1
            continue
        cycle_ids.add(cid)
        expected = payload.get('candidate_count')
        reported_rejected = payload.get('rejected_count')
        observed = by_cycle.get(cid, 0)
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            expected = None
        if expected is None or expected < 0:
            invalid_cycles += 1
        else:
            reported_total += expected
            recovered_total += min(observed, expected)
            missing_minimum += max(0, expected-observed)
            if expected < observed:
                invalid_cycles += 1
        rejected_observed = rejected_by_cycle.get(cid, 0)
        try:
            rejected_expected = int(reported_rejected)
            if rejected_expected < 0:
                rejected_expected = None
        except (TypeError, ValueError):
            rejected_expected = None
        cycles.append({
            'cycle_key':cid,'observed_at':row['occurred_at'],
            'run_id':row['run_id'],'strategy_version_id':row['strategy_version_id'],
            'expected_candidates':expected,'candidates_recovered':observed,
            'minimum_missing_candidates': max(0,expected-observed) if expected is not None else None,
            'expected_rejected':rejected_expected,
            'rejected_recovered':rejected_observed,
            'data_status':payload.get('data_status'),
            'rejected_population_complete': (
                expected is not None and observed==expected and
                rejected_expected is not None and rejected_observed==rejected_expected
            ),
            'source':'compact_rhen_core_decision_cycle',
        })

    for cycle in by_cycle:
        if cycle and cycle not in cycle_ids:
            invalid_cycles += 1

    forward_complete_15m = 0
    forward_invalid_15m = 0
    for c in candidates:
        outcome = c['forward_outcomes'].get('15') or {}
        if outcome.get('status') != 'complete':
            continue
        forward_return = _dec(outcome.get('forward_return'))
        raw_time = outcome.get('computed_at')
        try:
            calculated = datetime.fromisoformat(str(raw_time).replace('Z','+00:00'))
            observed = datetime.fromisoformat(str(c['observed_at']).replace('Z','+00:00'))
            valid_time = (
                calculated.tzinfo is not None
                and observed.tzinfo is not None
                and calculated >= observed
            )
        except (TypeError, ValueError):
            valid_time = False
        if forward_return is None or not valid_time:
            forward_invalid_15m += 1
        else:
            forward_complete_15m += 1

    summary = None
    for row in reports:
        r = _decode_json(row['payload_json'])
        if r.get('session') == session.isoformat():
            # All account, order IDs, personal fields and raw broker trades
            # intentionally omitted from the research source-provenance summary.
            summary = {
                'report_version':r.get('report_version'),
                'source_fingerprint':r.get('source_fingerprint'),
                'metrics':r.get('metrics'),
                'data_quality_warnings':r.get('data_quality_warnings'),
                'strategy_version_id':r.get('strategy_version_id'),
                'runtime_git_commit':r.get('runtime_git_commit'),
            }
            break

    blockers = []
    if not cycles:
        blockers.append('MISSING_DECISION_CYCLE_EVENTS')
    if not candidates:
        blockers.append('MISSING_SURVIVING_CANDIDATES')
    if missing_minimum:
        blockers.append('CANDIDATE_REJECTION_SAMPLING_OR_SHEDDING')
    if invalid_cycles:
        blockers.append('CYCLE_COUNTS_INVALID_OR_UNLINKED')
    if forward_complete_15m < len(candidates):
        blockers.append('INCOMPLETE_15MIN_FORWARD_OUTCOME_COVERAGE')
    if outcome_duplicate_count:
        blockers.append('DUPLICATED_OUTCOME_EVENTS')
    if summary is None:
        blockers.append('CANONICAL_DAILY_REPORT_NOT_FOUND')
    else:
        warnings = summary.get('data_quality_warnings')
        if isinstance(warnings,list) and warnings:
            blockers.append('DAILY_REPORT_HAS_DATA_QUALITY_WARNINGS')

    return {
        'session':session.isoformat(),
        'source_bounds_utc':{'start':start,'end_exclusive':end},
        'readiness':{
            'state':'AWAITING_COMPLETE_CANONICAL_EVIDENCE',
            'minimum_missing_candidates_from_observed_cycles':missing_minimum,
            'reported_candidates_from_observed_cycles':reported_total,
            'recovered_candidates_linked_to_observed_cycles':recovered_total,
            'unlinked_or_invalid_cycles':invalid_cycles,
            'recovered_candidates':len(candidates),
            'observed_cycle_count':len(cycles),
            'complete_15m_forward_outcomes':forward_complete_15m,
            'invalid_15m_forward_outcomes':forward_invalid_15m,
            'outcome_events_linked':outcome_events_scanned,
            'outcome_events_orphaned_or_other_sessions':orphan_outcome_count,
            'blockers':sorted(set(blockers + [
                'NO_EXTERNAL_PROOF_ALL_SCANNER_CYCLES_DELIVERED',
                'NO_VERIFIED_ASOF_FULL_UNIVERSE',
                'NO_FULL_BROKER_ACTIVITY_API_HISTORY',
            ])),
            'validated_alpha':False,
            'eligible_for_live_promotion':False,
        },
        'cycles':cycles,
        'candidates':candidates,
        'daily_report_aggregate':summary,
    }


def recover(db: str | Path, start: str, end: str) -> dict:
    start_day = _as_date(start)
    end_day = _as_date(end)
    span = (end_day-start_day).days
    if span < 0 or span >= MAX_SESSIONS:
        raise ValueError(f'bounded read supports 1-{MAX_SESSIONS} sessions')
    path = Path(db).resolve(strict=True)
    if not path.is_file():
        raise ValueError('a regular, pre-created SQLite snapshot is required')
    # mode=ro and query_only must never mutate RHEN Core. Read directly from a
    # consistent, operator-owned private copy for best isolation; no cache.
    uri = 'file:' + quote(str(path),safe='/') + '?mode=ro'
    with closing(sqlite3.connect(uri,uri=True,timeout=15)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        rows = [_fetch_session(conn,start_day+timedelta(days=n)) for n in range(span+1)]
        conn.rollback()
    material = {'schema_version':SCHEMA, 'sessions':rows}
    source_sha256=hashlib.sha256(json.dumps(material,sort_keys=True,separators=(',',':'),allow_nan=False,default=str).encode()).hexdigest()
    return {
        'schema_version':SCHEMA,
        'research_only':True,
        'broker_calls_made':False,
        'production_database_writes':False,
        'live_strategy_modified':False,
        'automatic_promotion_authorized':False,
        'complete_historical_candidate_population_proven':False,
        'source_fingerprint':source_sha256,
        'source_kind':'private_read_only_rhen_core_snapshot',
        'period':{'start_session':start,'end_session':end},
        'total':{
            'cycles':sum(x['readiness']['observed_cycle_count'] for x in rows),
            'surviving_candidates':sum(x['readiness']['recovered_candidates'] for x in rows),
            'reported_candidates_in_observed_cycles':sum(x['readiness']['reported_candidates_from_observed_cycles'] for x in rows),
            'minimum_missing_candidates':sum(x['readiness']['minimum_missing_candidates_from_observed_cycles'] for x in rows),
            'complete_15min_outcomes':sum(x['readiness']['complete_15m_forward_outcomes'] for x in rows),
        },
        'sessions':rows,
        'limitations':[
            'RHEN Core currently samples only first three rejected candidates per decision cycle.',
            'Analytics shedding, overwrite, and retention can erase records before this export.',
            'A Core snapshot alone cannot prove total emitted scanner cycles without independent counts.',
            'Missing historical candidates MUST NOT be synthetically reconstituted from bar data.',
            'Broker-only execution confirmations do not constitute a full candidate decision population.',
            'The original pre-compaction Foundation shadow feed may contain a different population; independently audit delivery and source authenticity.',
        ],
        'decision':'AWAITING_COMPLETE_CANONICAL_EVIDENCE',
    }


def write_private_json(file: str | Path, data: dict) -> None:
    path = Path(file)
    path.parent.mkdir(parents=True,exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os,'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump(data,f,sort_keys=True,indent=2,allow_nan=False,default=str)
        f.write('\n')


def main() -> None:
    parser=argparse.ArgumentParser(description='RHEN Core private read-only candidate recovery; never a complete-corpus claim')
    parser.add_argument('--db',required=True,help='Consistent private copy of RHEN Core sqlite database')
    parser.add_argument('--start',required=True)
    parser.add_argument('--end',required=True)
    parser.add_argument('--output',required=True,help='Private destination; refuses to overwrite')
    args=parser.parse_args()
    document=recover(args.db,args.start,args.end)
    write_private_json(args.output,document)
    print(json.dumps({'state':document['decision'],'totals':document['total'],'output':str(Path(args.output).resolve()),'source_fingerprint':document['source_fingerprint']},sort_keys=True))


if __name__=='__main__':
    main()