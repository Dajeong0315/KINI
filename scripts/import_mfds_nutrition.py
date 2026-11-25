from __future__ import annotations

import argparse, json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.recommendation_repository import open_db
from app.services.data_import.mfds_nutrition import import_mfds
from app.services.data_import.publish import publish_nutrition_release
from app.services.data_import.sources import fetch_mfds_json, load_json

def main() -> int:
    parser=argparse.ArgumentParser(description='Offline MFDS nutrition importer; default is dry-run.')
    source=parser.add_mutually_exclusive_group(required=True); source.add_argument('--input'); source.add_argument('--from-api',action='store_true')
    mode=parser.add_mutually_exclusive_group(); mode.add_argument('--dry-run',action='store_true'); mode.add_argument('--validate',action='store_true'); mode.add_argument('--publish',action='store_true')
    parser.add_argument('--confirm-publish',action='store_true')
    args=parser.parse_args()
    if args.publish and not args.confirm_publish: parser.error('--publish requires --confirm-publish.')
    raw=fetch_mfds_json() if args.from_api else load_json(args.input)
    run_mode='dry_run' if args.dry_run or not (args.validate or args.publish) else 'import'
    with open_db() as db:
        result=import_mfds(db,raw,input_reference='MFDS API' if args.from_api else args.input,mode=run_mode)
        if args.publish:
            publish_nutrition_release(db,result['release_id']); result['published']=True
    print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
