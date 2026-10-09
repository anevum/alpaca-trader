"""Isolated paper-only accounting kernel for future RHEN Cloud members.

No Alpaca client, secrets, HTTP handler, shared live database, or broker-write path.
The caller MUST resolve a verified member identity before obtaining an account
scope; a supplied client-side user ID is never an authorization credential.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

MICROSHARES = 1_000_000
SYMBOL = re.compile(r"^[A-Z]{1,7}$")
IDENT = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise ValueError(f"Invalid {label}")
    return value


@dataclass(frozen=True)
class PaperPolicy:
    symbols: tuple[str, ...]
    strategy_version: str
    max_positions: int = 2
    max_total_exposure_bp: int = 3000
    max_position_notional_cents: int = 5000
    max_order_notional_cents: int = 1000

    def __post_init__(self) -> None:
        _identity(self.strategy_version, "strategy version")
        if not self.symbols or len(self.symbols) > 50 or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Expected 1-50 unique allowed symbols")
        if any(not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in self.symbols):
            raise ValueError("Invalid allowed symbol")
        for value, lo, hi, name in (
            (self.max_positions, 1, 10, "max positions"),
            (self.max_total_exposure_bp, 1, 10000, "gross exposure"),
            (self.max_position_notional_cents, 1, 100_000_000, "position notional"),
            (self.max_order_notional_cents, 1, 100_000_000, "order notional"),
        ):
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError("Invalid " + name)


@dataclass(frozen=True)
class PaperSignal:
    signal_id: str
    strategy_version: str
    symbol: str
    side: str
    price_cents: int
    published_at: int
    requested_notional_cents: int = 0

    def __post_init__(self) -> None:
        _identity(self.signal_id, "signal")
        _identity(self.strategy_version, "strategy version")
        if not isinstance(self.symbol, str) or not SYMBOL.fullmatch(self.symbol):
            raise ValueError("Invalid U.S. equity/ETF symbol")
        if self.side not in ("buy", "sell"):
            raise ValueError("Paper engine supports long-only buys and flat exits")
        if type(self.price_cents) is not int or not 1 <= self.price_cents <= 100_000_000:
            raise ValueError("Invalid price in cents")
        if type(self.published_at) is not int or self.published_at <= 0:
            raise ValueError("Invalid signal timestamp")
        if type(self.requested_notional_cents) is not int or self.requested_notional_cents < 0:
            raise ValueError("Invalid requested notional")
        if self.side == "buy" and self.requested_notional_cents == 0:
            raise ValueError("Entry notional must be positive")


@dataclass(frozen=True)
class PaperReceipt:
    member_id: str
    signal_id: str
    status: str
    reason: str
    symbol: str
    side: str
    quantity_microshares: int
    cash_delta_cents: int


class PaperAccountScope:
    """Bound to a server-verified member identity. This is not an auth mechanism."""

    def __init__(self, kernel: "PaperExecutionKernel", member_id: str):
        self._kernel = kernel
        self._member_id = _identity(member_id, "member identity")

    def submit_signal(self, signal: PaperSignal, *, now: int | None = None) -> PaperReceipt:
        return self._kernel._submit(self._member_id, signal, now=now)

    def status(self) -> dict:
        return self._kernel._status(self._member_id)

    def pause_new_entries(self) -> None:
        self._kernel._set_paused(self._member_id, True)

    def resume_new_entries(self) -> None:
        self._kernel._set_paused(self._member_id, False)


class PaperExecutionKernel:
    """Independent SQLite-backed *simulation*, never connected to a broker.

    Account scope is injected by the future authenticated RHEN Cloud gateway.
    SQLite BEGIN IMMEDIATE serializes account decisions even across connections.
    The algorithm treats known fill prices as instantaneous simulated fills,
    without claiming broker, bid/ask, margin, stop-order or execution realism.
    """

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._connection = sqlite3.connect(self._db_path, isolation_level=None, timeout=15)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS paper_accounts (
              member_id TEXT PRIMARY KEY NOT NULL,
              cash_cents INTEGER NOT NULL CHECK(cash_cents>=0),
              initial_cash_cents INTEGER NOT NULL CHECK(initial_cash_cents>0),
              realized_pnl_cents INTEGER NOT NULL DEFAULT 0,
              paused INTEGER NOT NULL DEFAULT 1 CHECK(paused IN(0,1)),
              paper_mode TEXT NOT NULL DEFAULT 'paper' CHECK(paper_mode='paper')
            );
            CREATE TABLE IF NOT EXISTS paper_policies (
              member_id TEXT PRIMARY KEY NOT NULL REFERENCES paper_accounts(member_id) ON DELETE CASCADE,
              policy_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_positions (
              member_id TEXT NOT NULL REFERENCES paper_accounts(member_id) ON DELETE CASCADE,
              symbol TEXT NOT NULL,
              quantity_microshares INTEGER NOT NULL CHECK(quantity_microshares>0),
              cost_basis_cents INTEGER NOT NULL CHECK(cost_basis_cents>0),
              PRIMARY KEY(member_id,symbol)
            );
            CREATE TABLE IF NOT EXISTS paper_receipts (
              member_id TEXT NOT NULL REFERENCES paper_accounts(member_id) ON DELETE CASCADE,
              signal_id TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN('simulated','rejected')),
              reason TEXT NOT NULL,
              symbol TEXT NOT NULL,
              side TEXT NOT NULL CHECK(side IN('buy','sell')),
              quantity_microshares INTEGER NOT NULL,
              cash_delta_cents INTEGER NOT NULL,
              recorded_at INTEGER NOT NULL,
              signal_digest TEXT NOT NULL CHECK(length(signal_digest)=64),
              PRIMARY KEY(member_id,signal_id)
            );
            """
        )

    def close(self) -> None:
        self._connection.close()

    def create_paper_account(self, verified_member_id: str, cash_cents: int, policy: PaperPolicy) -> None:
        member_id = _identity(verified_member_id, "member identity")
        if type(cash_cents) is not int or not 1 <= cash_cents <= 100_000_000_000:
            raise ValueError("Invalid paper seed capital")
        if not isinstance(policy, PaperPolicy):
            raise ValueError("Paper policy required")
        # Serializes account creation: no orphan policy or partial first-time setup.
        db = self._connection
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                "INSERT INTO paper_accounts(member_id,cash_cents,initial_cash_cents) VALUES(?,?,?)",
                (member_id, cash_cents, cash_cents),
            )
            db.execute(
                "INSERT INTO paper_policies(member_id,policy_json) VALUES(?,?)",
                (member_id, json.dumps(policy.__dict__, separators=(",", ":"))),
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    def verified_scope(self, server_verified_member_id: str) -> PaperAccountScope:
        member_id = _identity(server_verified_member_id, "member identity")
        if not self._connection.execute(
            "SELECT 1 FROM paper_accounts WHERE member_id=?", (member_id,)
        ).fetchone():
            raise LookupError("Member paper account does not exist")
        return PaperAccountScope(self, member_id)

    def _set_paused(self, member_id: str, paused: bool) -> None:
        self._connection.execute(
            "UPDATE paper_accounts SET paused=? WHERE member_id=?", (int(paused), member_id)
        )

    def _status(self, member_id: str) -> dict:
        account = self._connection.execute(
            "SELECT cash_cents,initial_cash_cents,realized_pnl_cents,paused,paper_mode "
            "FROM paper_accounts WHERE member_id=?", (member_id,)
        ).fetchone()
        if account is None:
            raise LookupError("Unknown member")
        positions = self._connection.execute(
            "SELECT symbol,quantity_microshares,cost_basis_cents FROM paper_positions "
            "WHERE member_id=? ORDER BY symbol", (member_id,)
        ).fetchall()
        receipts = self._connection.execute(
            "SELECT COUNT(*) FROM paper_receipts WHERE member_id=?", (member_id,)
        ).fetchone()[0]
        return {
            "mode": "paper", "cashCents": account["cash_cents"],
            "initialCashCents": account["initial_cash_cents"],
            "realizedPnLCents": account["realized_pnl_cents"],
            "paused": bool(account["paused"]),
            "positions": [dict(r) for r in positions],
            "receiptCount": receipts,
            "brokerageConnected": False, "executionEnabled": False,
            "liveExecutionEnabled": False,
        }

    def _submit(self, member_id: str, signal: PaperSignal, *, now: int | None = None) -> PaperReceipt:
        if not isinstance(signal, PaperSignal):
            raise TypeError("Verified paper signal required")
        epoch = int(time.time()) if now is None else now
        if type(epoch) is not int or epoch < 1:
            raise ValueError("Invalid reference clock")
        # Replay must be byte-for-byte semantically identical, not merely reuse an ID.
        signal_digest = hashlib.sha256(
            json.dumps(asdict(signal), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        db = self._connection
        db.execute("BEGIN IMMEDIATE")
        try:
            previous = db.execute(
                "SELECT member_id,signal_id,status,reason,symbol,side,"
                "quantity_microshares,cash_delta_cents,signal_digest FROM paper_receipts "
                "WHERE member_id=? AND signal_id=?", (member_id, signal.signal_id)
            ).fetchone()
            if previous:
                prior = dict(previous)
                if prior.pop("signal_digest") != signal_digest:
                    raise ValueError("Signal ID conflicts with a previously recorded signal")
                db.execute("COMMIT")
                return PaperReceipt(**prior)
            account = db.execute(
                "SELECT cash_cents,initial_cash_cents,paused,paper_mode FROM paper_accounts "
                "WHERE member_id=?", (member_id,)
            ).fetchone()
            if not account or account["paper_mode"] != "paper":
                raise LookupError("Not an active paper member")
            raw_policy = db.execute(
                "SELECT policy_json FROM paper_policies WHERE member_id=?", (member_id,)
            ).fetchone()
            if not raw_policy:
                raise RuntimeError("Missing member policy")
            policy = PaperPolicy(**json.loads(raw_policy["policy_json"]))
            positions = db.execute(
                "SELECT symbol,quantity_microshares,cost_basis_cents FROM paper_positions "
                "WHERE member_id=?", (member_id,)
            ).fetchall()
            held = {p["symbol"]: p for p in positions}
            status, reason, quantity, delta = "rejected", "", 0, 0
            if signal.strategy_version != policy.strategy_version:
                reason = "unapproved_strategy_version"
            elif signal.published_at > epoch + 5 or epoch - signal.published_at > 90:
                reason = "stale_or_future_signal"
            elif signal.symbol not in policy.symbols:
                reason = "not_allowed"
            elif signal.side == "buy":
                if account["paused"]:
                    reason = "paused"
                elif signal.symbol not in held and len(held) >= policy.max_positions:
                    reason = "position_slots_exhausted"
                else:
                    current = held.get(signal.symbol)
                    cost_held = current["cost_basis_cents"] if current else 0
                    total_cost = sum(p["cost_basis_cents"] for p in positions)
                    exposure_cap = account["initial_cash_cents"] * policy.max_total_exposure_bp // 10000
                    budget = min(
                        signal.requested_notional_cents,
                        policy.max_order_notional_cents,
                        policy.max_position_notional_cents - cost_held,
                        exposure_cap - total_cost,
                        account["cash_cents"],
                    )
                    if budget <= 0:
                        reason = "risk_budget_exhausted"
                    else:
                        quantity = budget * MICROSHARES // signal.price_cents
                        delta = -((quantity * signal.price_cents + MICROSHARES - 1) // MICROSHARES)
                        if quantity <= 0 or delta >= 0 or -delta > budget:
                            reason, quantity, delta = "fractional_budget_unavailable", 0, 0
                        else:
                            status, reason = "simulated", "simulated_buy"
                            db.execute(
                                "INSERT INTO paper_positions(member_id,symbol,quantity_microshares,cost_basis_cents) "
                                "VALUES(?,?,?,?) ON CONFLICT(member_id,symbol) DO UPDATE SET "
                                "quantity_microshares=paper_positions.quantity_microshares+excluded.quantity_microshares,"
                                "cost_basis_cents=paper_positions.cost_basis_cents+excluded.cost_basis_cents",
                                (member_id, signal.symbol, quantity, -delta),
                            )
                            db.execute(
                                "UPDATE paper_accounts SET cash_cents=cash_cents+? WHERE member_id=?",
                                (delta, member_id),
                            )
            else:
                position = held.get(signal.symbol)
                if not position:
                    reason = "no_long_position"
                else:
                    # Pausing blocks NEW entries but permits risk-reducing exits.
                    quantity = position["quantity_microshares"]
                    delta = quantity * signal.price_cents // MICROSHARES
                    cost = position["cost_basis_cents"]
                    status, reason = "simulated", "simulated_flat_exit"
                    db.execute(
                        "DELETE FROM paper_positions WHERE member_id=? AND symbol=?",
                        (member_id, signal.symbol),
                    )
                    db.execute(
                        "UPDATE paper_accounts SET cash_cents=cash_cents+?, "
                        "realized_pnl_cents=realized_pnl_cents+? WHERE member_id=?",
                        (delta, delta-cost, member_id),
                    )
            receipt = PaperReceipt(
                member_id, signal.signal_id, status, reason, signal.symbol, signal.side, quantity, delta
            )
            db.execute(
                "INSERT INTO paper_receipts "
                "(member_id,signal_id,status,reason,symbol,side,quantity_microshares,cash_delta_cents,recorded_at,signal_digest)"
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (*receipt.__dict__.values(), epoch, signal_digest),
            )
            db.execute("COMMIT")
            return receipt
        except Exception:
            db.execute("ROLLBACK")
            raise
