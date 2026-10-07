"""Trim only excess rows using an indexed oldest-first walk.

The names are internal constants, never input from HTTP or provider messages.
Call inside the writer's existing transaction to keep hard retention bounds.
"""


def trim_oldest(db, table, timestamp, capacity):
    excess = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] - capacity
    if excess <= 0:
        return 0
    return db.execute(f"DELETE FROM {table} WHERE id IN "
        f"(SELECT id FROM {table} ORDER BY {timestamp},id LIMIT ?)", (excess,)).rowcount
