from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db" / "migrations"
# Platform Core validation uses this same deterministic migrator.


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        raise SystemExit(f"no migration files found in {MIGRATIONS}")

    with psycopg.connect(database_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("create schema if not exists anevum")
            cur.execute(
                """
                create table if not exists anevum.schema_migrations (
                    migration_name text primary key,
                    sha256 text not null,
                    applied_at timestamptz not null default now()
                )
                """
            )

            for path in files:
                sql = path.read_text(encoding="utf-8")
                digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()

                cur.execute(
                    "select sha256 from anevum.schema_migrations where migration_name = %s",
                    (path.name,),
                )
                row = cur.fetchone()
                if row:
                    if row[0] != digest:
                        raise RuntimeError(
                            f"migration drift: {path.name} was already applied with a different hash"
                        )
                    print(f"already applied: {path.name}", flush=True)
                    continue

                print(f"applying: {path.name}", flush=True)
                with conn.transaction():
                    cur.execute(sql)
                    cur.execute(
                        """
                        insert into anevum.schema_migrations (migration_name, sha256)
                        values (%s, %s)
                        """,
                        (path.name, digest),
                    )

            cur.execute(
                """
                select schema_name
                from information_schema.schemata
                where schema_name in ('anevum','iren','rhen','graen','velum','nostra')
                order by schema_name
                """
            )
            schemas = [row[0] for row in cur.fetchall()]
            expected = ["anevum", "graen", "iren", "nostra", "rhen", "velum"]
            if schemas != expected:
                raise RuntimeError(f"schema verification failed: {schemas}")

            cur.execute(
                """
                select count(*)
                from anevum.systems
                where system_key in ('IREN','RHEN','GRAEN','VELUM','NOSTRA')
                """
            )
            count = cur.fetchone()[0]
            if count != 5:
                raise RuntimeError(f"system registry verification failed: {count}")

    print("foundation migration verification passed", flush=True)


if __name__ == "__main__":
    main()
