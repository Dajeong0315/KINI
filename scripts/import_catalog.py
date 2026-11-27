"""Stage normalized, licensed public food JSON; validate/publish/rollback explicitly."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.recommendation_repository import open_db
from app.services.catalog_repository import stage, validate, publish, rollback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    staging = sub.add_parser('stage')
    staging.add_argument('--source', required=True, choices=['mfds_recipe', 'mfds_nutrition', '10000recipe'])
    staging.add_argument('--revision', required=True)
    staging.add_argument('--input', type=Path, required=True)
    staging.add_argument('--format', choices=['normalized', 'cookrcp01'], default='normalized')
    for name in ('validate', 'publish'):
        sub.add_parser(name).add_argument('--release', required=True)
    restoring = sub.add_parser('rollback')
    restoring.add_argument('--source', required=True)
    restoring.add_argument('--release', required=True)
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error('먼저 init_database.py로 DB를 생성하세요.')
    db = open_db(str(args.db))
    try:
        if args.action == 'stage':
            rows = json.loads(args.input.read_text(encoding='utf-8-sig'))
            if args.format == 'cookrcp01':
                if args.source != 'mfds_recipe':
                    parser.error('cookrcp01 형식은 mfds_recipe 출처만 지원합니다.')
                from app.services.data_import.mfds_recipe import normalize
                if isinstance(rows, dict):
                    rows = rows['COOKRCP01']['row']
                rows = [normalize(row) for row in rows]
            print(stage(db, args.source, rows, source_revision=args.revision))
        elif args.action == 'validate':
            issues = validate(db, args.release)
            print(json.dumps({'release': args.release, 'issues': issues}, ensure_ascii=False))
            return 1 if issues else 0
        elif args.action == 'publish':
            publish(db, args.release)
            print('Published:', args.release)
        else:
            rollback(db, args.source, args.release)
            print('Activated:', args.release)
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
