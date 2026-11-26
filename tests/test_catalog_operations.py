import pytest
from app.services.catalog_repository import stage, validate, publish
from app.services.recommendation_repository import open_db
from app.services.integrations.virtual_thinq import VirtualFridge
from app.services.integrations.operational_metrics import validate_metric

def test_interrupted_snapshot_keeps_active_release():
    db = open_db(":memory:", seed_demo=False)
    row = dict(external_id="F1", kind="food", name="fixture", basis_unit="g",
               basis_amount=100, nutrients={"energy_kcal":5})
    db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code='mfds_nutrition'")
    original = stage(db,"mfds_nutrition",[row],source_revision="one")
    assert not validate(db,original)
    publish(db,original)
    def interrupted():
        yield row
        raise ConnectionError("interrupted fixture")
    with pytest.raises(ConnectionError):
        stage(db,"mfds_nutrition",interrupted(),source_revision="two")
    assert db.execute("SELECT release_id FROM active_data_releases").fetchone()[0] == original
    assert db.execute("SELECT count(*) FROM data_releases").fetchone()[0] == 1
    db.close()

def test_virtual_fridge_retains_last_version():
    device = VirtualFridge()
    assert device.sync(connected=True) == ("synced",1)
    assert device.sync(connected=False) == ("interrupted",1)

@pytest.mark.parametrize("field", ["inventory","family","auth_session","token"])
def test_private_fields_rejected(field):
    with pytest.raises(ValueError):
        validate_metric({"metric_date":"2026-11-26","metric_code":"cache_hit",
                         "sample_count":1,"total_value":1,field:"synthetic"})
