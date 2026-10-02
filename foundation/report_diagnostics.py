"""Read-only report storage diagnostics; never emits event payloads or credentials."""
from __future__ import annotations

import json
import os
import time

import psycopg


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '30s'")
            started = time.monotonic()
            cur.execute("""
                select event_type, count(*), sum(pg_column_size(payload)),
                       max(pg_column_size(payload))
                from rhen.events group by event_type order by count(*) desc
            """)
            rows = cur.fetchall()
            print("FOUNDATION_REPORT_STORAGE=" + json.dumps({
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "event_types": [
                    {"type": row[0], "count": row[1],
                     "stored_payload_bytes": row[2], "max_stored_payload_bytes": row[3]}
                    for row in rows
                ],
            }), flush=True)
            cur.execute("""
                select indexname, indexdef from pg_indexes
                where schemaname='rhen' and tablename='events'
                order by indexname
            """)
            print("FOUNDATION_REPORT_INDEXES=" + json.dumps(cur.fetchall()), flush=True)
    print("FOUNDATION_REPORT_DIAGNOSTICS_READ_ONLY=PASS", flush=True)


if __name__ == "__main__":
    main()
