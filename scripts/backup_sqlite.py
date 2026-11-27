"""Create a consistent SQLite transfer snapshot, without modifying the source.

This is a backup, not a SQLite-to-PostgreSQL converter. It contains private data.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def backup(source: Path, destination: Path):
    source = source.resolve(strict=True)
    destination = destination.resolve()
    # Exclusive creation prevents overwriting any existing backup or source.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise RuntimeError('SQLite snapshot failed integrity check')
        with destination.open('rb') as snapshot:
            digest = hashlib.file_digest(snapshot, 'sha256').hexdigest()
        return {'status': 'ok', 'bytes': destination.stat().st_size, 'sha256': digest}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(backup(args.source, args.destination)))
    except Exception:
        parser.exit(1, 'SQLite backup failed; check source, destination and permissions.\n')


if __name__ == '__main__':
    main()
