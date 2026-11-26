"""Map completed source snapshots to typed Supabase batch plans. No automatic publication."""
import argparse
import gzip
import json
import hashlib
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.services.data_import.mfds_batch import normalize_food
from app.services.data_import.mfds_recipe import normalize as normalize_recipe
from app.services.data_import.cloud_plan import prepare


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',choices=['mfds_recipe','mfds_nutrition'],required=True)
    parser.add_argument('--input',type=Path,required=True,help='Source directory containing manifest.json and page files')
    parser.add_argument('--revision',required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'output/catalog-build/plans')
    parser.add_argument('--exclude-invalid',action='store_true',help='Explicitly quarantine invalid rows and prepare only usable candidates')
    args=parser.parse_args()
    manifest=json.loads((args.input/'manifest.json').read_text(encoding='utf-8'))
    if not manifest.get('complete') or manifest.get('source')!=args.source:
        parser.error('같은 출처의 전체 수집 완료 snapshot만 사용할 수 있습니다.')
    records=[]; issues=[]; rejected=[]; raw_count=0
    for page in range(1,manifest['last_page']+1):
        with gzip.open(args.input/f'page-{page:06d}.json.gz','rt',encoding='utf-8') as handle:
            rows=json.load(handle)['rows']
        for row in rows:
            raw_count+=1
            try:
                normalized=(normalize_food if args.source=='mfds_nutrition' else normalize_recipe)(row)
                if normalized['kind']=='recipe' and (not normalized['name'].strip() or not normalized['ingredients_text'].strip() or not normalized['steps']):
                    raise ValueError('레시피명·재료·단계가 없습니다.')
                if len(normalized.get('ingredients_text',''))>2000:
                    raise ValueError('미분해 재료 목록이 운영 기준을 초과합니다.')
                records.append(normalized)
            except ValueError as error:
                issues.append({'external_id':row.get('FOOD_CD') or row.get('RCP_SEQ'),'reason':str(error)})
                rejected.append({'external_id':str(row.get('FOOD_CD') or row.get('RCP_SEQ') or ''),
                    'kind':'food' if args.source=='mfds_nutrition' else 'recipe','reason_code':'source_mapping_or_required_fields',
                    'safe_detail':str(error)[:300],'payload_sha256':hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()})
    args.output.mkdir(parents=True,exist_ok=True)
    if raw_count!=manifest['rows']:
        parser.error('manifest 행 수가 실제 snapshot과 다릅니다.')
    if issues:
        report=args.output/f'{args.source}_quarantine.json'
        report.write_text(json.dumps({'source':args.source,'rows':raw_count,'issues':issues},ensure_ascii=False,indent=2),encoding='utf-8')
        print('일부 행 mapping/단위 검토 필요. 격리 보고서:',report)
        if not args.exclude_invalid:return 1
    report=prepare(records,args.source,args.revision,args.output,quarantined_records=rejected,
        coverage={'source_rows':raw_count,'source_total':manifest.get('expected_total'),'complete_source_capture':True})
    print(json.dumps({'release_id':report['release_id'],'record_count':report['record_count'],
                      'counts':report['counts'],'batch_count':len(report['batch_files'])},ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
