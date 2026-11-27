"""Positive demo request against live PostgreSQL; no failure injection/deletes."""
import argparse
import json
from pathlib import Path
import time

import requests


def smoke(url, credentials):
    with requests.Session() as session:
        session.trust_env=False
        ready=session.get(url+'/health/ready',timeout=20)
        assert ready.status_code==200
        assert ready.json()['storage']=='postgres' and ready.json()['external_ai']=='disabled'
        login=session.post(url+'/api/auth/login',json={key:credentials[key] for key in
            ('login_name','password','household_id')},timeout=20)
        assert login.status_code==200
        headers={'Authorization':'Bearer '+login.json()['access_token']}
        started=time.monotonic()
        response=session.post(url+'/api/recommendations',json=dict(household_id=credentials['household_id'],
            participant_ids=credentials['participant_ids'],meal_type='dinner',purchase_allowed=False),
            headers=headers,timeout=20)
        result=response.json()
        assert response.status_code==201 and result['status']=='recommendations_available'
        assert result['ai_status']=='disabled'
        candidate=result['recommendations'][0]
        assert len(candidate['inventory_usage'])==3
        assert all(row['nutrition_source']=='catalog_published' and row['nutrition_food_id'] and
                   row['nutrition_release_id']=='1d211108-0849-479d-9205-ac5060529431'
                   for row in candidate['inventory_usage'])
        # Reviewed raw weights / 4 designed servings; not cooked-food retention.
        expected=dict(energy_kcal=397.,protein_g=28.14,carbohydrate_g=63.08,fat_g=1.85,sodium_mg=59.)
        assert candidate['nutrition_per_serving']==expected
        saved=session.get(url+'/api/recommendations/'+str(result['request_id']),headers=headers,timeout=20)
        assert saved.status_code==200 and saved.json()['recommendations'][0]['nutrition_per_serving']==expected
        return dict(status=result['status'],candidates=len(result['recommendations']),
            nutrition_per_serving=expected,food_ids=[row['nutrition_food_id'] for row in candidate['inventory_usage']],
            saved_reloaded=True,ai='disabled',elapsed_seconds=round(time.monotonic()-started,3))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    args=parser.parse_args()
    try:
        result=smoke(args.url,json.loads(Path('/etc/kini-b/step2-demo.json').read_text()))
    except Exception as error:
        print(json.dumps(dict(status='not_verified',error_type=type(error).__name__)))
        raise SystemExit(1)
    print(json.dumps(result))
