"""Create an empty private database; demo seeding requires --demo explicitly."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.recommendation_repository import open_db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', type=Path, default=ROOT / 'app/data/kini.sqlite3')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    if args.path.exists():
        parser.error('기존 DB를 변경하지 않습니다. migration은 앱 초기화를 사용하세요.')
    args.path.parent.mkdir(parents=True, exist_ok=True)
    db = open_db(str(args.path), seed_demo=args.demo)
    try:
        print('Database:', args.path.resolve())
        print('Mode:', 'demo' if args.demo else 'private (empty; HTTP auth not enabled)')
        print('Foreign keys:', db.execute('PRAGMA foreign_keys').fetchone()[0])
        print('Integrity:', db.execute('PRAGMA integrity_check').fetchone()[0])
    finally:
        db.close()


if __name__ == '__main__':
    main()
