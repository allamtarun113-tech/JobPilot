"""Encrypted database backup.

pg_dump (custom format) of DATABASE_URL, encrypted with Fernet (BACKUP_KEY, `make backup-key`), written to
Data/user/backups/ (git-ignored). Store a copy of the key somewhere other than this laptop. The PDFs live in
Supabase Storage / Data/user/storage and are backed up separately (see docs/restore.md).

Usage: uv run python scripts/backup.py            -> Data/user/backups/jobpilot-<time>.dump.enc
       uv run python scripts/backup.py --decrypt FILE OUT   -> a plain dump for pg_restore (restore drill)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet

from jobpilot.core.config import REPO_ROOT, get_settings

OUT_DIR = REPO_ROOT / "Data" / "user" / "backups"


def _fernet() -> Fernet:
    """Return the Fernet cipher from BACKUP_KEY, or exit with setup steps."""
    key = os.environ.get("BACKUP_KEY") or ""
    if not key:
        raise SystemExit("BACKUP_KEY is not set (make backup-key, then put it in .env and somewhere safe)")
    return Fernet(key.encode())


def server_major(dsn: str) -> int:
    """Return the database server's major PostgreSQL version."""
    import psycopg

    with psycopg.connect(dsn) as conn:
        return int(str(conn.execute("show server_version_num").fetchone()[0])) // 10000  # type: ignore[index]


def local_major(binary: str) -> int:
    """Return the major version of a local pg_dump binary (0 if missing)."""
    try:
        out = subprocess.run([binary, "--version"], check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return 0
    match = re.search(r"\(PostgreSQL\) (\d+)", out)
    return int(match.group(1)) if match else 0


def dump_command(dsn: str) -> list[str]:
    """A pg_dump at least as new as the server (pg_dump refuses newer servers): PG_BIN, a Homebrew install,
    or else the official postgres:<major> Docker image."""
    major = server_major(dsn)
    candidates = [str(Path(os.environ["PG_BIN"]) / "pg_dump")] if os.environ.get("PG_BIN") else []
    candidates += [f"/opt/homebrew/opt/postgresql@{v}/bin/pg_dump" for v in (18, 17, 16, 15)]
    candidates += ["/opt/homebrew/opt/libpq/bin/pg_dump", "pg_dump"]
    for binary in candidates:
        if local_major(binary) >= major:
            return [binary, "--format=custom", "--no-owner", dsn]
    return ["docker", "run", "--rm", f"postgres:{major}", "pg_dump", "--format=custom", "--no-owner", dsn]


def backup() -> Path:
    """Dump the database, encrypt it and write it to Data/user/backups.

    Returns:
        The path of the encrypted dump (readable by the owner only).
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dsn = get_settings().psycopg_dsn
    dump = subprocess.run(dump_command(dsn), check=True, capture_output=True).stdout
    out = OUT_DIR / f"jobpilot-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.dump.enc"
    out.write_bytes(_fernet().encrypt(dump))
    out.chmod(0o600)
    return out


def decrypt(src: Path, dst: Path) -> None:
    """Decrypt a backup file into a plain dump for pg_restore."""
    dst.write_bytes(_fernet().decrypt(src.read_bytes()))
    dst.chmod(0o600)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--decrypt":
        decrypt(Path(sys.argv[2]), Path(sys.argv[3]))
        print(f"decrypted to {sys.argv[3]}")
    else:
        print(f"backup written: {backup()}")
