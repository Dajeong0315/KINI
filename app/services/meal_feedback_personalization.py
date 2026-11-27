"""Read current meal facts once; no append-only learning projection or AI calls."""
import os

POLICY_VERSION = 'meal-feedback-v1'
TASTE_POINTS = {'taste_satisfied': 2., 'taste_neutral': 0., 'taste_dissatisfied': -2.}
GUIDANCE = {
    'seasoning_bland': '이 메뉴가 싱겁다는 평가가 있었습니다. 식이·나트륨 제한 안에서 간을 직접 확인하세요.',
    'seasoning_balanced': '이 메뉴의 간이 적당하다는 평가가 있었습니다. 실제 재료와 양념을 다시 확인하세요.',
    'seasoning_salty': '이 메뉴가 짜다는 평가가 있었습니다. 양념을 넣기 전에 간과 나트륨 제한을 확인하세요.',
    'difficulty_easy': '이 메뉴가 쉬웠다는 평가가 있었습니다. 이번에도 조리 순서를 확인하세요.',
    'difficulty_moderate': '이 메뉴의 난이도가 적당하다는 평가가 있었습니다. 순서대로 진행하세요.',
    'difficulty_hard': '이 메뉴가 어렵다는 평가가 있었습니다. 조리 순서를 먼저 읽고 재료를 준비하세요.',
}


def current_facts(db, household_id, participant_ids):
    if os.getenv('KINI_B_MEAL_FEEDBACK_ENABLED', '0') != '1':
        return []
    placeholders = ','.join('?' for _ in participant_ids)
    if not placeholders:
        return []
    # The meal evaluation is shared by its participants, not attributed as an
    # individual taste preference to the first respondent or every ingredient.
    return [dict(row) for row in db.execute(f'''SELECT f.*,r.recipe_id
        FROM meal_feedback f JOIN meal_records r ON r.id=f.record_id
        JOIN meal_sessions s ON s.id=r.session_id
        WHERE s.household_id=? AND f.status='submitted'
        AND EXISTS (SELECT 1 FROM meal_session_participants p WHERE p.session_id=s.id
            AND p.member_id IN ({placeholders}))
        ORDER BY r.id DESC''', (household_id, *participant_ids))]


def recipe_effect(facts, recipe_id):
    # Average of at most five current meal evaluations. Repeated edits/retries
    # never add samples. Bounded ±2 points within the existing ±15 preference cap.
    rows = [row for row in facts if row['recipe_id'] == recipe_id][:5]
    tastes = [TASTE_POINTS[row['taste']] for row in rows if row['taste'] in TASTE_POINTS]
    points = round(sum(tastes) / len(tastes), 4) if tastes else 0.
    advice = []
    for dimension in ('seasoning', 'difficulty'):
        row = next((row for row in rows if row[dimension] in GUIDANCE), None)
        if row:
            advice.append(dict(dimension=dimension, code=row[dimension], record_id=row['record_id'],
                               revision=row['revision'], message=GUIDANCE[row[dimension]]))
    evidence = [dict(record_id=row['record_id'], revision=row['revision'], taste=row['taste'])
                for row in rows if row['taste'] in TASTE_POINTS]
    return points, evidence, advice
