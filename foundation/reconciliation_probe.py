from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import httpx


def event(
    *,
    run_id: str,
    strategy_version_id: str,
    event_key: str,
    event_type: str,
    payload: dict,
    symbol: str | None = None,
) -> dict:
    return {
        "event_key": event_key,
        "run_id": run_id,
        "strategy_version_id": strategy_version_id,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "correlation_id": f"{run_id}:probe",
        "source": "foundation-reconcile-probe",
        "payload": payload,
    }


async def post(http: httpx.AsyncClient, url: str, headers: dict, body: dict) -> dict:
    response = await http.post(url, headers=headers, json=body)
    response.raise_for_status()
    return response.json()


async def main() -> None:
    ingest_url = os.environ["FOUNDATION_INGEST_URL"].strip()
    token = os.environ["FOUNDATION_INGEST_TOKEN"].strip()
    base = ingest_url.rsplit("/v1/events", 1)[0]
    reconcile_url = base + "/v1/trading-reconcile"
    headers = {
        "content-type": "application/json",
        "x-anevum-ingest-token": token,
    }
    strategy_version_id = "FOUNDATION-RECONCILE-PROBE-001"

    async with httpx.AsyncClient(timeout=10.0) as http:
        # Case 1: a durable entry intent without a broker order must fail closed.
        run_id = f"foundation-resolve-{uuid4()}"
        client_order_id = f"anevum-spy-buy-probe-{uuid4().hex[:12]}"
        intent_id = str(uuid4())
        intent_event = event(
            run_id=run_id,
            strategy_version_id=strategy_version_id,
            event_key=f"{run_id}:intent:{client_order_id}",
            event_type="order_intent",
            symbol="SPY",
            payload={
                "intent": {
                    "intent_id": intent_id,
                    "idempotency_key": client_order_id,
                    "symbol": "SPY",
                    "side": "buy",
                    "intended_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        await post(http, ingest_url, headers, {"events": [intent_event]})

        reconcile_body = {
            "action": "reconcile",
            "reconcile": {
                "run_id": run_id,
                "strategy_version_id": strategy_version_id,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "managed_symbols": ["SPY"],
                "broker_positions": [],
                "open_orders": [],
            },
        }
        blocked = await post(http, reconcile_url, headers, reconcile_body)
        blocked_result = blocked["result"]
        if blocked_result.get("safe_to_enter") is not False:
            raise RuntimeError(f"unresolved intent did not fail closed: {blocked}")
        unresolved = blocked_result.get("unresolved_intents") or []
        if len(unresolved) != 1 or unresolved[0].get("client_order_id") != client_order_id:
            raise RuntimeError(f"wrong unresolved intent result: {blocked}")

        resolved = await post(
            http,
            reconcile_url,
            headers,
            {
                "action": "resolve_intent",
                "intent": {
                    "run_id": run_id,
                    "client_order_id": client_order_id,
                    "state": "broker_not_found",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "correlation_id": f"{run_id}:probe",
                },
            },
        )
        if not resolved.get("ok") or int(resolved.get("updated") or 0) != 1:
            raise RuntimeError(f"intent resolution failed: {resolved}")

        cleared = await post(http, reconcile_url, headers, reconcile_body)
        if cleared["result"].get("safe_to_enter") is not True:
            raise RuntimeError(f"resolved intent did not clear gate: {cleared}")

        # Case 2: a broker order matching the durable intent must reconcile safe.
        run_id_2 = f"foundation-order-{uuid4()}"
        client_order_id_2 = f"anevum-qqq-buy-probe-{uuid4().hex[:12]}"
        broker_order_id = str(uuid4())
        intent_2 = event(
            run_id=run_id_2,
            strategy_version_id=strategy_version_id,
            event_key=f"{run_id_2}:intent:{client_order_id_2}",
            event_type="order_intent",
            symbol="QQQ",
            payload={
                "intent": {
                    "intent_id": str(uuid4()),
                    "idempotency_key": client_order_id_2,
                    "symbol": "QQQ",
                    "side": "buy",
                    "intended_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        order_payload = {
            "id": broker_order_id,
            "client_order_id": client_order_id_2,
            "symbol": "QQQ",
            "side": "buy",
            "type": "market",
            "status": "new",
            "qty": "0.1",
            "filled_qty": "0",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        order_event = event(
            run_id=run_id_2,
            strategy_version_id=strategy_version_id,
            event_key=f"{run_id_2}:order:{broker_order_id}:new:0:probe",
            event_type="broker_order",
            symbol="QQQ",
            payload={"order": order_payload},
        )
        await post(http, ingest_url, headers, {"events": [intent_2, order_event]})

        matched = await post(
            http,
            reconcile_url,
            headers,
            {
                "action": "reconcile",
                "reconcile": {
                    "run_id": run_id_2,
                    "strategy_version_id": strategy_version_id,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "managed_symbols": ["QQQ"],
                    "broker_positions": [],
                    "open_orders": [order_payload],
                },
            },
        )
        if matched["result"].get("safe_to_enter") is not True:
            raise RuntimeError(f"matched broker order did not reconcile safe: {matched}")

    print(
        "FOUNDATION_CANONICAL_RECONCILIATION_PROBE_PASSED",
        {
            "unresolved_intent_fail_closed": True,
            "resolved_intent_clears_gate": True,
            "matched_broker_order_safe": True,
        },
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
