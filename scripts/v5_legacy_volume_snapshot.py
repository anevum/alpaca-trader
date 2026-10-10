#!/usr/bin/env python3
"""Private, one-shot RHEN legacy volume evidence snapshot.

Designed for a volume that may still have read-only observers and writers.
SQLite files are copied with sqlite3's online backup API, not by copying an
inconsistent SQLite main file separately from its WAL. Other regular files are
stream-copied with a pre/post source-stat guard. This is *not* a cross-file
atomic snapshot; obtain a native Railway volume backup first.

No network, broker, Cloudflare, or GitHub access. No auto-deletion. Output may
contain confidential broker information and must remain on private storage.

    python -m scripts.v5_legacy_volume_snapshot snapshot \
        --source /data --output /private/legacy-rhen-20261010
    python -m scripts.v5_legacy_volume_snapshot verify \
        --snapshot /private/legacy-rhen-20261010
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
from urllib.parse import quote

SCHEMA = "anevum.legacy-volume-snapshot.v1"
SQLITE_MAGIC = b"SQLite format 3\x00"
MAX_FILES = 100_000
MAX_BYTES = 20 * 1024 * 1024 * 1024
CHUNK = 1024 * 1024


class SnapshotError(RuntimeError):
    pass


def _canonical(data: dict) -> bytes:
    return (json.dumps(data, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(CHUNK), b""):
            digest.update(part)
    return digest.hexdigest()


def _safe_path(value: str) -> PurePosixPath:
    rel = PurePosixPath(value)
    if (not value or value.startswith("/") or "\\" in value or
            rel.is_absolute() or any(p in ("", ".", "..") for p in value.split("/"))):
        raise SnapshotError("unsafe relative path in manifest")
    if value in ("manifest.json", "manifest.sha256", "READY"):
        raise SnapshotError("unsafe metadata path")
    return rel


def _is_sqlite(path: Path) -> bool:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as src:
            return src.read(16) == SQLITE_MAGIC
    except OSError as exc:
        raise SnapshotError("source file unavailable") from exc


def _walk_regular(root: Path) -> tuple[list[str], list[tuple[str, Path]]]:
    dirs: list[str] = []
    files: list[tuple[str, Path]] = []
    for current, folders, names in os.walk(root, topdown=True, followlinks=False):
        folders.sort()
        names.sort()
        current_path = Path(current)
        for folder in folders:
            item = current_path / folder
            if item.is_symlink() or not stat.S_ISDIR(item.lstat().st_mode):
                raise SnapshotError("unsafe or special directory in source")
            dirs.append(item.relative_to(root).as_posix())
        for name in names:
            item = current_path / name
            if not stat.S_ISREG(item.lstat().st_mode):
                raise SnapshotError("symlink or special source file prohibited")
            rel = item.relative_to(root).as_posix()
            _safe_path("payload/" + rel)
            files.append((rel, item))
            if len(files) > MAX_FILES:
                raise SnapshotError("source file count exceeds safety bound")
    return sorted(dirs), sorted(files)


def _sqlite_uri(path: Path, *, immutable: bool = False) -> str:
    return "file:" + quote(str(path), safe="/") + (
        "?mode=ro&immutable=1" if immutable else "?mode=ro"
    )


def _check_sqlite(path: Path, *, immutable: bool) -> None:
    conn = sqlite3.connect(_sqlite_uri(path, immutable=immutable),
                           uri=True, timeout=20)
    try:
        conn.execute("PRAGMA query_only=ON")
        result = conn.execute("PRAGMA integrity_check").fetchall()
        if result != [("ok",)]:
            raise SnapshotError("SQLite integrity verification failed")
    finally:
        conn.close()


def _sqlite_backup(src: Path, target: Path) -> None:
    source = sqlite3.connect(_sqlite_uri(src), uri=True, timeout=30)
    try:
        target.touch(mode=0o600, exist_ok=False)
        destination = sqlite3.connect(str(target), timeout=30)
        try:
            source.backup(destination, pages=256, sleep=0.025)
            destination.commit()
        finally:
            destination.close()
    except sqlite3.Error as exc:
        raise SnapshotError("SQLite online backup failed") from exc
    finally:
        source.close()
    os.chmod(target, 0o600)
    _check_sqlite(target, immutable=True)


def _file_copy(src: Path, target: Path) -> None:
    before = src.stat()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(src, flags)
    try:
        with os.fdopen(fd, "rb") as old, target.open("xb") as new:
            os.chmod(target, 0o600)
            while True:
                data = old.read(CHUNK)
                if not data:
                    break
                new.write(data)
            new.flush()
            os.fsync(new.fileno())
            opened = os.fstat(old.fileno())
    except OSError as exc:
        raise SnapshotError("ordinary source copy failed") from exc
    after = src.stat()
    for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"):
        if getattr(before, name) != getattr(opened, name) or getattr(before, name) != getattr(after, name):
            raise SnapshotError("ordinary source changed during backup")


def _snapshot(source: Path, output: Path) -> dict:
    if source.is_symlink() or not source.is_dir():
        raise SnapshotError("source must be a real directory")
    root = source.resolve(strict=True)
    dest = output.resolve(strict=False)
    if dest == root or root in dest.parents or dest in root.parents:
        raise SnapshotError("snapshot output must be separate from source volume")
    dirs, files = _walk_regular(root)
    if not files:
        raise SnapshotError("refusing to certify an empty source volume")

    sqlite_source_paths = {rel for rel, path in files if _is_sqlite(path)}
    skip = set()
    for sqlite_rel in sqlite_source_paths:
        for suffix in ("-wal", "-shm"):
            sibling = sqlite_rel + suffix
            if any(name == sibling for name, _ in files):
                skip.add(sibling)
    if output.exists():
        raise SnapshotError("refusing to overwrite existing snapshot")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    payload = output / "payload"
    payload.mkdir(mode=0o700)
    try:
        for folder in dirs:
            (payload / folder).mkdir(mode=0o700, parents=True, exist_ok=True)
        entries = []
        total = 0
        for rel, source_file in files:
            if rel in skip:
                continue
            target = payload / rel
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            is_db = rel in sqlite_source_paths
            if is_db:
                _sqlite_backup(source_file, target)
            else:
                _file_copy(source_file, target)
            size = target.stat().st_size
            total += size
            if total > MAX_BYTES:
                raise SnapshotError("snapshot exceeds size safety bound")
            entries.append({
                "path": rel,
                "sha256": _sha_file(target),
                "bytes": size,
                "kind": "sqlite-online-backup" if is_db else "regular-file",
            })
        manifest = {
            "schema_version": SCHEMA,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_kind": "legacy-rhen-private-volume",
            "snapshot_kind": "per-database-consistent;not-globally-atomic",
            "research_completeness_proven": False,
            "broker_flat_independently_proven": False,
            "ready_for_volume_deletion": False,
            "directories": dirs,
            "files": entries,
            "sqlite_sidecars_omitted_in_favor_of_online_backup": sorted(skip),
            "totals": {"files": len(entries), "bytes": total},
        }
        data = _canonical(manifest)
        (output / "manifest.json").write_bytes(data)
        os.chmod(output / "manifest.json", 0o600)
        (output / "manifest.sha256").write_text(
            hashlib.sha256(data).hexdigest() + "\n", encoding="ascii"
        )
        os.chmod(output / "manifest.sha256", 0o600)
        _verify(output, require_ready=False)
        (output / "READY").write_text("LOCAL_SNAPSHOT_VERIFIED_ONLY\n", encoding="ascii")
        os.chmod(output / "READY", 0o600)
        return manifest
    except BaseException:
        # Keep forensic partial files for operator review, but never mark READY.
        raise


def _verify(snapshot: Path, *, require_ready: bool = True) -> dict:
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise SnapshotError("snapshot location unavailable")
    if require_ready and (not (snapshot / "READY").is_file() or
                          (snapshot / "READY").read_text(encoding="ascii") != "LOCAL_SNAPSHOT_VERIFIED_ONLY\n"):
        raise SnapshotError("snapshot has no complete local verification marker")
    manifest_path = snapshot / "manifest.json"
    hash_path = snapshot / "manifest.sha256"
    if not manifest_path.is_file() or not hash_path.is_file():
        raise SnapshotError("snapshot manifest missing")
    data = manifest_path.read_bytes()
    if len(data) > 25 * 1024 * 1024:
        raise SnapshotError("oversized manifest")
    if hashlib.sha256(data).hexdigest() != hash_path.read_text(encoding="ascii").strip():
        raise SnapshotError("manifest checksum mismatch")
    obj = json.loads(data)
    if not isinstance(obj, dict) or obj.get("schema_version") != SCHEMA:
        raise SnapshotError("unknown manifest schema")
    if obj.get("snapshot_kind") != "per-database-consistent;not-globally-atomic":
        raise SnapshotError("unsupported snapshot kind")
    entries = obj.get("files")
    if not isinstance(entries, list) or len(entries) > MAX_FILES:
        raise SnapshotError("invalid manifest file population")
    observed: set[str] = set()
    total = 0
    payload = snapshot / "payload"
    for base, dirs, names in os.walk(payload, followlinks=False):
        for name in dirs + names:
            p = Path(base) / name
            if p.is_symlink():
                raise SnapshotError("snapshot must not contain symlinks")
        for name in names:
            observed.add((Path(base) / name).relative_to(payload).as_posix())
    expected = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SnapshotError("invalid manifest row")
        key = str(entry.get("path") or "")
        _safe_path("payload/" + key)
        if key in expected:
            raise SnapshotError("duplicate manifest file entry")
        expected.add(key)
        file = payload.joinpath(*PurePosixPath(key).parts)
        if not file.is_file():
            raise SnapshotError("missing snapshot file")
        size = file.stat().st_size
        if size != entry.get("bytes") or _sha_file(file) != entry.get("sha256"):
            raise SnapshotError("snapshot file checksum mismatch")
        total += size
        if entry.get("kind") == "sqlite-online-backup":
            _check_sqlite(file, immutable=True)
        elif entry.get("kind") != "regular-file":
            raise SnapshotError("unknown snapshot file type")
    if observed != expected or total != obj.get("totals", {}).get("bytes"):
        raise SnapshotError("snapshot population differs from manifest")
    if len(expected) != obj.get("totals", {}).get("files"):
        raise SnapshotError("snapshot file count differs from manifest")
    return obj


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    take = sub.add_parser("snapshot")
    take.add_argument("--source", type=Path, required=True)
    take.add_argument("--output", type=Path, required=True)
    confirm = sub.add_parser("verify")
    confirm.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "snapshot":
            doc = _snapshot(args.source, args.output)
        else:
            doc = _verify(args.snapshot)
    except (OSError, ValueError, sqlite3.Error, SnapshotError) as exc:
        parser.exit(2, "RHEN SNAPSHOT BLOCKED: " + type(exc).__name__ + "\n")
    print(json.dumps({
        "state": "LOCAL_VERIFIED_NOT_OFFHOST",
        "kind": doc["snapshot_kind"],
        "count": doc["totals"]["files"],
        "bytes": doc["totals"]["bytes"],
        "research_completeness_proven": False,
        "ready_for_volume_deletion": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
