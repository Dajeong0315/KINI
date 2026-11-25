"""Shared deterministic checks for recommendation and cooking."""
import math

NUTRIENTS=('energy_kcal','protein_g','carbohydrate_g','fat_g','sodium_mg')

def inspect_ingredient(db, ingredient_id, people, inventory):
    item=db.execute('SELECT i.*,n.basis_amount_g,n.source_type AS nutrition_source,n.confidence AS nutrition_confidence,n.energy_kcal,n.protein_g,n.carbohydrate_g,n.fat_g,n.sodium_mg FROM ingredients i LEFT JOIN ingredient_nutrition n ON n.ingredient_id=i.id WHERE i.id=?',(ingredient_id,)).fetchone()
    codes=[]; warnings=[]
    if not item or not item['active']:
        return ['ingredient_unknown'], ['재료 정보 확인이 필요합니다.'], None
    basis=item['basis_amount_g']
    if not basis or not math.isfinite(basis) or basis<=0 or any(item[n] is None or not math.isfinite(item[n]) or item[n]<0 for n in NUTRIENTS): codes.append('nutrition_unknown')
    allergies={a for p in people for a in p['allergies']}
    for allergy in list(allergies):
        aliases=db.execute("SELECT allergen_code FROM allergen_aliases WHERE alias=? AND source_type!='unknown' AND confidence>=0.9",(allergy,)).fetchall()
        allergies.update(r[0] for r in aliases)
    tags=[dict(r) for r in db.execute('SELECT * FROM ingredient_allergen_tags WHERE ingredient_id=?',(ingredient_id,))]
    if allergies.intersection({item['name'],item['category']} | {t['allergen_code'] for t in tags}): codes.append('allergen_conflict')
    requires_label=bool(item['is_composite'] or item['requires_label_check'] or any(t['label_required'] for t in tags) or any(i.get('product_name') for i in inventory))
    verified_label=bool(inventory) and all(i.get('label_confirmed') and i.get('data_status')=='verified' for i in inventory) and item['source_type']=='verified_product'
    if requires_label or item['source_type']=='unknown':
        warnings.append('제품 성분표와 교차접촉 표시를 확인해 주세요.')
        if allergies and not verified_label: codes.append('label_confirmation_required')
    if item['source_type'] in ('unknown','demo') or item['nutrition_source'] in (None,'unknown','demo'):
        warnings.append('재료 또는 영양 정보가 데모이거나 출처 확인이 필요합니다.')
    if item['data_confidence'] is None or item['nutrition_confidence'] is None: warnings.append('데이터 신뢰도 확인이 필요합니다.')
    if any(i.get('data_status') != 'verified' or i.get('quantity_g') is None for i in inventory): warnings.append('재고의 재료명 또는 중량 확인이 필요합니다.')
    warnings.append('교차접촉은 자동 판정하지 않습니다.')
    return sorted(set(codes)), sorted(set(warnings)), dict(item)
