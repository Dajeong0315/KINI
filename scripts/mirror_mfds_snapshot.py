"""Mirror a verified cloud-plan source snapshot into the local public catalog.

Uses the same release UUID as Supabase. It does not copy family data or call source APIs.
Run only after the corresponding cloud release has passed validation/publication.
"""
import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.services.recommendation_repository import open_db
from app.services.catalog_repository import stage,validate,publish
from app.services.data_import.mfds_batch import normalize_food
from app.services.data_import.mfds_recipe import normalize as normalize_recipe


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--db',type=Path,default=ROOT/'app/data/kini.sqlite3')
    args=parser.parse_args()
    plan=json.loads((args.plan/'manifest.json').read_text(encoding='utf-8'))
    snapshot=json.loads((args.snapshot/'manifest.json').read_text(encoding='utf-8'))
    if not snapshot.get('complete') or snapshot['source']!=plan['source']:
        parser.error('완료된 같은 출처 snapshot만 사용할 수 있습니다.')
    for name,digest in plan['sha256'].items():
        if hashlib.sha256((args.plan/name).read_bytes()).hexdigest()!=digest:
            parser.error('준비한 적재 계획 파일이 변경됐습니다.')
    normalize=normalize_food if plan['source']=='mfds_nutrition' else normalize_recipe
    issues=[];rows=[]
    for page in range(1,snapshot['last_page']+1):
        with gzip.open(args.snapshot/f'page-{page:06d}.json.gz','rt',encoding='utf-8') as handle:raw=json.load(handle)['rows']
        for row in raw:
            try:
                normalized=normalize(row)
                if normalized['kind']=='recipe' and (not normalized['name'].strip() or not normalized['ingredients_text'].strip() or not normalized['steps']):
                    raise ValueError('missing_recipe_fields')
                if len(normalized.get('ingredients_text',''))>2000:raise ValueError('ingredient_text_too_large')
                rows.append(normalized)
            except ValueError:
                issues.append(str(row.get('FOOD_CD') or row.get('RCP_SEQ') or ''))
    if len(rows)!=plan['accepted_records'] or len(issues)!=plan['quarantined_records']:
        parser.error('계획과 원본 정규화 건수가 다릅니다.')
    if len({row['external_id'] for row in rows})!=len(rows):parser.error('원본 ID가 중복입니다.')
    db=open_db(str(args.db),seed_demo=False)
    try:
        old=db.execute('SELECT source_code,status FROM data_releases WHERE id=?',(plan['release_id'],)).fetchone()
        if old:
            active=db.execute('SELECT release_id FROM active_data_releases WHERE source_code=?',(plan['source'],)).fetchone()
            if old['source_code']!=plan['source'] or old['status']!='published' or not active or active[0]!=plan['release_id']:
                parser.error('동일 release ID가 다른 상태로 존재합니다. 자동 덮어쓰지 않습니다.')
            count=db.execute('SELECT count(*) FROM source_records WHERE release_id=?',(plan['release_id'],)).fetchone()[0]
            if count!=len(rows):parser.error('기존 local release 행 수가 다릅니다.')
            print(json.dumps({'release_id':plan['release_id'],'local_records':count,'already_published':True}));return 0
        db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code=?",(plan['source'],));db.commit()
        release=stage(db,plan['source'],rows,source_revision=plan['revision'],release_id=plan['release_id'])
        errors=validate(db,release)
        if errors:raise ValueError('Local validation failed; release not published')
        publish(db,release)
        with db:
            db.execute('UPDATE data_releases SET validation_report=? WHERE id=?',
                (json.dumps({'coverage':plan['coverage'],'accepted_records':len(rows),'quarantined_ids':issues}),release))
        print(json.dumps({'release_id':release,'local_records':len(rows),'quarantined':len(issues),'status':'published','foreign_key_errors':len(db.execute('PRAGMA foreign_key_check').fetchall())}))
    finally:db.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
