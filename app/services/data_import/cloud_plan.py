"""Prepare bounded, resumable administrative SQL batches from normalized public records.

Only typed columns and compact provenance go to Supabase. No source API calls at service time.
The caller executes numbered batches through the connected administrative DB tool.
"""
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4
from .storage_budget import batches, policy

COLUMNS = {
    'source_records': ('release_id','external_id','kind','payload_json','payload_sha256'),
    'catalog_foods': ('release_id','external_id','name','category','basis_amount','basis_unit'),
    'catalog_food_nutrients': ('release_id','food_id','nutrient','amount'),
    'catalog_recipes': ('release_id','external_id','name','servings','cook_time_minutes','ingredients_text','method','category','image_url','source_url'),
    'catalog_recipe_steps': ('release_id','recipe_id','step_no','instruction'),
    'catalog_recipe_ingredients': ('release_id','recipe_id','line_no','ingredient_name','original_text'),
    'catalog_recipe_nutrients': ('release_id','recipe_id','nutrient','amount','basis_amount','basis_unit'),
}


def literal(value):
    if value is None:
        return 'NULL'
    if type(value) in (int, float):
        if not math.isfinite(value):
            raise ValueError('비유한 수치를 SQL로 저장할 수 없습니다.')
        return str(value)
    if isinstance(value, dict):
        return literal(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)) + '::jsonb'
    if not isinstance(value, str) or '\x00' in value:
        raise ValueError('지원하지 않는 SQL 값입니다.')
    return "'" + value.replace("'", "''") + "'"


def prepare(records, source, revision, directory):
    if source not in ('mfds_recipe', 'mfds_nutrition') or not revision or len(revision) > 200:
        raise ValueError('출처와 revision을 확인하세요.')
    release = str(uuid4())
    output = Path(directory) / release
    output.mkdir(parents=True, exist_ok=False)
    tables = {table: [] for table in COLUMNS}
    seen = set()
    for record in records:
        external_id = record['external_id']
        if external_id in seen:
            raise ValueError('원본 ID 중복입니다. 적재 전에 중복/변경 행을 검토하세요.')
        seen.add(external_id)
        kind = record['kind']
        if kind != ('food' if source=='mfds_nutrition' else 'recipe'):
            raise ValueError('출처와 데이터 종류가 다릅니다.')
        digest = hashlib.sha256(json.dumps(record,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
        provenance = {'schema_version':1,'kind':kind,'representation':'typed_columns','source_revision':revision}
        tables['source_records'].append((release,external_id,kind,provenance,digest))
        if kind=='food':
            tables['catalog_foods'].append((release,external_id,record['name'],record.get('category'),record['basis_amount'],record['basis_unit']))
            tables['catalog_food_nutrients'].extend((release,external_id,n,v) for n,v in record['nutrients'].items() if v is not None)
        else:
            metadata = record.get('source_metadata',{})
            tables['catalog_recipes'].append((release,external_id,record['name'],record.get('servings'),record.get('cook_time_minutes'),
                record['ingredients_text'],metadata.get('RCP_WAY2'),metadata.get('RCP_PAT2'),metadata.get('ATT_FILE_NO_MAIN'),
                'https://www.foodsafetykorea.go.kr/api/newDatasetDetail.do?svc_no=COOKRCP01'))
            tables['catalog_recipe_steps'].extend((release,external_id,i,step) for i,step in enumerate(record['steps'],1))
            # Keep whole original text unreviewed; never invent quantities or split a recipe unsafely.
            tables['catalog_recipe_ingredients'].append((release,external_id,1,'미분해 재료 목록',record['ingredients_text']))
            nutrition = record.get('nutrition')
            if nutrition:
                tables['catalog_recipe_nutrients'].extend((release,external_id,n,v,nutrition['basis_amount'],nutrition['basis_unit'])
                    for n,v in nutrition['nutrients'].items() if v is not None)
    if not seen:
        raise ValueError('빈 데이터는 배포할 수 없습니다.')
    counts = {table:len(rows) for table,rows in tables.items()}
    setup = 'BEGIN;\nSELECT public.assert_catalog_capacity(0);\n'
    setup += ('INSERT INTO public.data_releases(id,source_code,source_revision,status,validation_report) VALUES ('+
        ','.join(literal(v) for v in (release,source,revision,'staging',{'prepared_counts':counts}))+') ON CONFLICT(id) DO NOTHING;\nCOMMIT;\n')
    (output/'0000_setup.sql').write_text(setup,encoding='utf-8')
    files = ['0000_setup.sql']
    number = 1
    for table,rows in tables.items():
        for batch in batches(rows):
            payload_size=len(json.dumps(batch,ensure_ascii=False,allow_nan=False).encode())
            text='BEGIN;\nSELECT public.assert_catalog_capacity('+str(payload_size)+');\n'
            text+='INSERT INTO public.'+table+'('+','.join(COLUMNS[table])+') VALUES\n'
            text+=',\n'.join('('+','.join(literal(v) for v in row)+')' for row in batch)
            text+='\nON CONFLICT DO NOTHING;\nCOMMIT;\n'
            filename=f'{number:04d}_{table}.sql'
            (output/filename).write_text(text,encoding='utf-8')
            files.append(filename); number+=1
    verification='BEGIN;\nDO $$ BEGIN\n'
    for table,count in counts.items():
        verification+=f'IF (SELECT count(*) FROM public.{table} WHERE release_id={literal(release)})<>{count} THEN RAISE EXCEPTION \'Incomplete catalog release\'; END IF;\n'
    verification+='END $$;\nUPDATE public.data_releases SET status=\'validated\' WHERE id='+literal(release)+" AND status='staging';\nCOMMIT;\n"
    (output/'validate.sql').write_text(verification,encoding='utf-8')
    publishing='BEGIN;\nDO $$ BEGIN\nIF NOT EXISTS(SELECT 1 FROM public.data_sources WHERE code='+literal(source)+" AND rights_status='approved') THEN RAISE EXCEPTION 'Source rights review required'; END IF;\n"
    publishing+='IF NOT EXISTS(SELECT 1 FROM public.data_releases WHERE id='+literal(release)+" AND status IN ('validated','published')) THEN RAISE EXCEPTION 'Release validation required'; END IF;\nEND $$;\n"
    publishing+='UPDATE public.data_releases SET status=\'published\',published_at=coalesce(published_at,now()) WHERE id='+literal(release)+';\n'
    publishing+='INSERT INTO public.active_data_releases(source_code,release_id,cache_version) VALUES ('+literal(source)+','+literal(release)+',1) ON CONFLICT(source_code) DO UPDATE SET release_id=excluded.release_id,cache_version=public.active_data_releases.cache_version+1,updated_at=now() WHERE public.active_data_releases.release_id<>excluded.release_id;\nCOMMIT;\n'
    (output/'publish.sql').write_text(publishing,encoding='utf-8')
    manifest={'release_id':release,'source':source,'revision':revision,'record_count':len(seen),'counts':counts,
              'batch_files':files,'validation_file':'validate.sql','publish_file':'publish.sql',
              'full_raw_in_cloud':False,'storage_policy':policy(),
              'sha256':{name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in files+['validate.sql','publish.sql']}}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest
