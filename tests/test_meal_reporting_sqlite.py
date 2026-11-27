"""SQLite compatibility for the authenticated read contract, no UI changes."""
from contextlib import closing
import pytest
from app.services.recommendation_repository import open_db
from app.services.local_identity import create_account, login


def test_legacy_snapshot_reading_and_authorization(tmp_path,monkeypatch):
    monkeypatch.setenv('KINI_DB_BACKEND','sqlite')
    monkeypatch.setenv('KINI_DB_PATH',str(tmp_path/'reports.sqlite3'))
    with closing(open_db(seed_demo=False)) as db:
        db.execute("INSERT INTO households(id,name) VALUES (1,'synthetic A'),(2,'synthetic B')")
        db.execute("INSERT INTO members(id,household_id,name,age) VALUES (11,1,'synthetic member',30)")
        db.execute("INSERT INTO recipes(id,name,servings) VALUES (501,'synthetic legacy recipe',2)")
        meal=db.execute("INSERT INTO meal_sessions(household_id,meal_type) VALUES (1,'dinner')").lastrowid
        rid=db.execute("INSERT INTO meal_records(session_id,recipe_id,eaten_at) VALUES (?,501,'2026-11-26T12:00:00')",(meal,)).lastrowid
        item=db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (?,11,0.5)',(rid,)).lastrowid
        db.executemany('INSERT INTO nutrition_intakes VALUES (?,?,?)',[(item,n,v) for n,v in [('energy_kcal',50),('protein_g',5),('carbohydrate_g',0),('fat_g',0),('sodium_mg',0)]])
        create_account(db,'report-test','synthetic-password',1); db.commit()
        token=login(db,'report-test','synthetic-password',1); db.commit()
    from app.main import app
    client=app.test_client(); auth={'Authorization':'Bearer '+token}
    path='/api/reports/nutrition?start_date=2026-11-26&end_date=2026-11-26'
    assert client.get(path).status_code==401
    assert client.get(path+'&household_id=2',headers=auth).status_code==403
    report=client.get(path,headers=auth)
    assert report.status_code==200 and report.json['nutrition_totals']['energy_kcal']==50
    assert report.json['total_servings']==0.5 and report.json['data_quality']=='recorded'
    detail=client.get(f'/api/meal-records/{rid}',headers=auth).json
    assert detail['recipe_name_source']=='current_catalog'
    with closing(open_db(seed_demo=False)) as db:
        assert db.execute('SELECT COUNT(*) FROM meal_records').fetchone()[0]==1


@pytest.mark.parametrize('value',[None,'bad date','9999-12-31T23:59:59-12:00'])
def test_invalid_legacy_timestamps_are_not_guessed(value):
    from app.services.meal_reporting import eaten_time
    assert eaten_time(value) is None
