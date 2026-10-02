from datetime import datetime, timezone

from foundation.report_read import (
    _attach_outcomes,
    _candidate_is_crypto,
    _forward_outcomes,
    _valid_date,
)


def test_valid_date():
    assert _valid_date("2026-10-01")
    assert not _valid_date("2026-99-01")
    assert not _valid_date(None)


def test_crypto_candidate_detection():
    assert _candidate_is_crypto({"market_lane": "crypto"})
    assert _candidate_is_crypto({"strategy_version_id": "CRYPTO-2026-09-29-001"})
    assert not _candidate_is_crypto({"market_lane": "us_equity"})


def test_attach_crypto_outcomes_by_candidate_key():
    candidate = {
        "candidate_key": "run:cycle:BTC/USD",
        "symbol": "BTC/USD",
        "observed_at": datetime(2026, 10, 1, tzinfo=timezone.utc).isoformat(),
    }
    rows = _attach_outcomes(
        [candidate],
        {
            "run:cycle:BTC/USD": {
                "10": {
                    "status": "complete",
                    "forward_return": "0.01",
                }
            }
        },
    )
    assert rows[0]["forward_outcomes"]["10"]["status"] == "complete"


def test_attach_equity_outcomes_as_list():
    candidate = {"candidate_key": "run:cycle:SPY", "symbol": "SPY"}
    rows = _attach_outcomes(
        [candidate],
        {
            "run:cycle:SPY": {
                "5": {"status": "complete"},
                "30": {"status": "complete"},
            }
        },
        equity_shape=True,
    )
    assert [row["horizon_minutes"] for row in rows[0]["outcomes"]] == [5, 30]


class _ForwardOutcomeCursor:
    def __init__(self) -> None:
        self.query = ""
        self.args = ()
        self.execute_calls = 0

    def execute(self, query, args) -> None:
        self.execute_calls += 1
        self.query = query
        self.args = args

    def fetchall(self):
        return []


def test_forward_outcomes_filters_by_requested_candidate_identities():
    cur = _ForwardOutcomeCursor()
    grouped, complete = _forward_outcomes(
        cur,
        candidate_identities={"candidate-b", "candidate-a"},
    )

    assert grouped == {}
    assert complete == []
    assert cur.execute_calls == 1
    assert "candidate_id" in cur.query
    assert "candidate_key" in cur.query
    assert cur.args[0] == "candidate_forward_outcome"
    assert cur.args[1] == ["candidate-a", "candidate-b"]
    assert cur.args[-1] == 50000


def test_forward_outcomes_skips_database_for_empty_identity_set():
    cur = _ForwardOutcomeCursor()
    grouped, complete = _forward_outcomes(cur, candidate_identities=set())

    assert grouped == {}
    assert complete == []
    assert cur.execute_calls == 0
