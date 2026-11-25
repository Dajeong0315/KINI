"""Offline synthetic safety and determinism benchmark; prints aggregate timings only."""
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.recommendation_repository import open_db
from app.services.recommendation_engine import recommend
from app.services.recommendation_rag import SQLiteRetriever
from app.services.unit_conversion import convert_quantity


def run():
    combinations = [[1],[2],[3],[4],[1,3],[3,4]]
    variants = [({},'base'),({'meal_type':'breakfast'},'breakfast'),({'cook_time_limit_minutes':20},'time'),({'purchase_allowed':True},'purchase'),({'excluded_ingredients':['두부']},'exclusion')]
    elapsed=[]
    with tempfile.TemporaryDirectory() as directory:
        db=open_db(str(Path(directory)/'evaluation.sqlite3'))
        try:
            for participants in combinations:
                for extra,_label in variants:
                    req={'household_id':1,'participant_ids':participants,'meal_type':'dinner','purchase_allowed':False, **extra}
                    start=perf_counter(); result=recommend(db,req); elapsed.append((perf_counter()-start)*1000)
                    again=recommend(db,req)
                    assert [c['recipe_id'] for c in result['recommendations']]==[c['recipe_id'] for c in again['recommendations']]
                    assert len(result['recommendations'])<=3
                    if 3 in participants: assert not {3,4}.intersection(c['recipe_id'] for c in result['recommendations'])
                    if 4 in participants: assert 5 not in {c['recipe_id'] for c in result['recommendations']}
                    if extra.get('excluded_ingredients'): assert all(all(i['ingredient']!='두부' for i in c['inventory_usage']) for c in result['recommendations'])
            for unit in ('개','컵','스푼','반 통','한 봉지'):
                start=perf_counter(); converted=convert_quantity(db,1,unit,1); elapsed.append((perf_counter()-start)*1000)
                assert converted['quantity_g'] is None and converted['status']=='needs_confirmation'
            for unit, expected in (('g','g'),('kg','g'),('ml','ml'),('L','ml')):
                start=perf_counter(); converted=convert_quantity(db,1,unit); elapsed.append((perf_counter()-start)*1000)
                assert converted['unit']==expected
            db.execute('UPDATE ingredients SET is_composite=1 WHERE id=4')
            start=perf_counter(); result=recommend(db,{'household_id':1,'participant_ids':[3],'meal_type':'dinner','purchase_allowed':False}); elapsed.append((perf_counter()-start)*1000)
            assert 'label_confirmation_required' in next(c for c in result['excluded'] if c['recipe_id']==2)['reason_codes']
            db.execute('UPDATE ingredient_nutrition SET protein_g=NULL')
            start=perf_counter(); result=recommend(db,{'household_id':1,'participant_ids':[1],'meal_type':'dinner','purchase_allowed':False}); elapsed.append((perf_counter()-start)*1000)
            assert result['status']=='no_safe_recommendation'
            start=perf_counter(); SQLiteRetriever(db).search('보관 상태'); retrieval_ms=(perf_counter()-start)*1000
        finally:
            db.close()
    elapsed.sort()
    return {'cases':len(elapsed),'failures':0,'case_ms_median':round(elapsed[len(elapsed)//2],2),'case_ms_max':round(max(elapsed),2),'retrieval_ms':round(retrieval_ms,2),'llm_ms':None,'llm_note':'미호출; 모델 비교는 benchmark_models.py 사용'}


if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False))
