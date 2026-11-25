from scripts.evaluate_recommendations import run

def test_offline_evaluation_has_at_least_40_cases():
    result=run()
    assert result['cases']>=40 and result['failures']==0
