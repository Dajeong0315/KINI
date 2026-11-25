from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.recommendation_repository import open_db
from app.services.data_import.allergen_reference import import_allergen_reference
from app.services.data_import.publish import publish_allergen_version

def main() -> int:
    parser=argparse.ArgumentParser(description='Import reviewed, versioned allergen reference JSON.')
    parser.add_argument('--input',required=True); parser.add_argument('--publish',action='store_true'); parser.add_argument('--confirm-publish',action='store_true')
    args=parser.parse_args()
    if args.publish and not args.confirm_publish: parser.error('--publish requires --confirm-publish.')
    with open_db() as db:
        result=import_allergen_reference(db,Path(args.input).read_bytes(),input_reference=args.input)
        if args.publish: publish_allergen_version(db,result['version_id']); result['published']=True
    print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
