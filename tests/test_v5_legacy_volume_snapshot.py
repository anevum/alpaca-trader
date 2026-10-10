"""Synthetic-only verification of the V5 private legacy volume snapshot.

NEVER reads mounted Railway data, Alpaca APIs or Cloudflare buckets.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest

from scripts.v5_legacy_volume_snapshot import (
    SnapshotError, _snapshot, _verify,
)


def fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "research").mkdir()
    (source / "research" / "negative-study.json").write_text(
        '{"profitable":false,"result":"no_edge"}', encoding="utf-8"
    )
    (source / "archive.bin").write_bytes(b"broker-private-test-fixture\x00")
    db = source / "rhen-core.db"
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    conn.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, decision TEXT NOT NULL)")
    conn.execute("INSERT INTO evidence (decision) VALUES ('REJECTED')")
    conn.commit()
    return source, conn


def test_wal_snapshot_contains_committed_uncheckpointed_rows(tmp_path):
    source, conn = fixture(tmp_path)
    try:
        assert (source / "rhen-core.db-wal").exists()
        target = tmp_path / "copy"
        manifest = _snapshot(source, target)
        assert _verify(target)["totals"]["files"] == 3
        # Assert the snapshot itself omitted transient WAL files BEFORE opening it;
        # sqlite3.connect() in read-write mode can recreate sidecars on inspection.
        assert not (target / "payload" / "rhen-core.db-wal").exists()
        assert not (target / "payload" / "rhen-core.db-shm").exists()
        db = target / "payload" / "rhen-core.db"
        with sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True) as restored:
            assert restored.execute("SELECT decision FROM evidence").fetchall() == [
                ("REJECTED",)
            ]
        assert manifest["snapshot_kind"] == "per-database-consistent;not-globally-atomic"
        assert manifest["research_completeness_proven"] is False
        assert manifest["ready_for_volume_deletion"] is False
        assert (target / "READY").read_text() == "LOCAL_SNAPSHOT_VERIFIED_ONLY\n"
        assert set(manifest["sqlite_sidecars_omitted_in_favor_of_online_backup"]) == {
            "rhen-core.db-wal", "rhen-core.db-shm"
        }
    finally:
        conn.close()


def test_file_corruption_detected_and_cannot_pass_verify(tmp_path):
    source, conn = fixture(tmp_path)
    try:
        target = tmp_path / "copy"
        _snapshot(source, target)
        (target / "payload" / "archive.bin").write_bytes(b"corrupt")
        with pytest.raises(SnapshotError, match="checksum"):
            _verify(target)
    finally:
        conn.close()


def test_file_population_extra_and_missing_detected(tmp_path):
    source, conn = fixture(tmp_path)
    try:
        target = tmp_path / "copy"
        _snapshot(source, target)
        (target / "payload" / "surprise.txt").write_text("unexpected")
        with pytest.raises(SnapshotError, match="population"):
            _verify(target)
        (target / "payload" / "surprise.txt").unlink()
        (target / "payload" / "archive.bin").unlink()
        with pytest.raises(SnapshotError, match="missing"):
            _verify(target)
    finally:
        conn.close()


def test_manifest_corruption_is_rejected(tmp_path):
    source, conn = fixture(tmp_path)
    try:
        target = tmp_path / "copy"
        _snapshot(source, target)
        (target / "manifest.json").write_text("{}")
        with pytest.raises(SnapshotError, match="manifest checksum"):
            _verify(target)
    finally:
        conn.close()


def test_ready_marker_required_and_not_sufficient(tmp_path):
    source, conn = fixture(tmp_path)
    try:
        target = tmp_path / "copy"
        _snapshot(source, target)
        (target / "READY").unlink()
        with pytest.raises(SnapshotError, match="no complete"):
            _verify(target)
        _verify(target, require_ready=False)
    finally:
        conn.close()


def test_symlink_and_special_source_files_are_rejected(tmp_path):
    source, conn = fixture(tmp_path)
    try:
        (source / "external-link").symlink_to(tmp_path / "outside", target_is_directory=False)
        with pytest.raises(SnapshotError, match="symlink"):
            _snapshot(source, tmp_path / "copy")
        assert not (tmp_path / "copy").exists()
    finally:
        conn.close()


def test_refuses_existing_output_and_output_under_source(tmp_path):
    source, conn = fixture(tmp_path)
    try:
        with pytest.raises(SnapshotError, match="separate"):
            _snapshot(source, source / "new-copy")
        out = tmp_path / "existing"
        out.mkdir()
        with pytest.raises(SnapshotError, match="overwrite"):
            _snapshot(source, out)
    finally:
        conn.close()


def test_sidecar_without_sqlite_database_is_preserved_not_dropped(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "orphan.db-wal").write_bytes(b"forensic-orphan")
    (src / "study.md").write_text("unproven result")
    manifest = _snapshot(src, tmp_path / "copy")
    assert sorted(x["path"] for x in manifest["files"]) == [
        "orphan.db-wal", "study.md"
    ]
    assert (tmp_path / "copy" / "payload" / "orphan.db-wal").read_bytes() == b"forensic-orphan"


def test_snapshot_outputs_strict_private_file_permissions(tmp_path):
    source, conn = fixture(tmp_path)
    try:
        target = tmp_path / "copy"
        _snapshot(source, target)
        if os.name == "posix":
            for path in [target, target / "payload",
                         target / "manifest.json", target / "payload" / "archive.bin",
                         target / "payload" / "rhen-core.db"]:
                assert path.stat().st_mode & 0o077 == 0
        assert b"broker-private-test-fixture" not in (target / "manifest.json").read_bytes()
        assert b"REJECTED" not in (target / "manifest.json").read_bytes()
    finally:
        conn.close()


def test_failed_snapshot_is_never_declared_ready(tmp_path, monkeypatch):
    source, conn = fixture(tmp_path)
    try:
        import scripts.v5_legacy_volume_snapshot as snap
        def broken(*_args):
            raise SnapshotError("simulated read failure")
        monkeypatch.setattr(snap, "_file_copy", broken)
        target = tmp_path / "copy"
        with pytest.raises(SnapshotError, match="simulated"):
            _snapshot(source, target)
        assert target.exists()
        assert not (target / "READY").exists()
    finally:
        conn.close()
