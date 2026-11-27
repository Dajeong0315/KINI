"""Unit conversions do not assume density or package weight."""
import math

def convert_quantity(db, quantity, unit, ingredient_id=None):
    if not math.isfinite(quantity) or quantity < 0: raise ValueError('수량이 올바르지 않습니다.')
    unit=unit.strip()
    si={'g':('g',1), 'kg':('g',1000), 'ml':('ml',1), 'mL':('ml',1), 'L':('ml',1000), 'l':('ml',1000)}
    if unit in si:
        target,factor=si[unit]
        return {'status':'converted','quantity':quantity*factor,'unit':target,'quantity_g':quantity*factor if target=='g' else None,'warning':None if target=='g' else '밀도 확인 전에는 g으로 변환하지 않습니다.'}
    rows=db.execute("SELECT factor,confidence FROM unit_conversions WHERE ingredient_id=? AND from_unit=? AND to_unit='g' AND source_type IN ('official_imported','verified_product')",(ingredient_id,unit)).fetchall() if ingredient_id is not None else []
    factors={row['factor'] for row in rows if row['factor'] is not None and math.isfinite(row['factor']) and row['factor']>0
             and row['confidence'] is not None and math.isfinite(row['confidence']) and .95<=row['confidence']<=1}
    if len(factors)==1:
        amount=quantity*next(iter(factors))
        if math.isfinite(amount):
            return {'status':'converted','quantity':amount,'unit':'g','quantity_g':amount,'warning':'상품 및 상태가 환산 기준과 일치하는지 확인하세요.'}
    return {'status':'needs_confirmation','quantity':None,'unit':None,'quantity_g':None,'warning':'제품 또는 재료별 실제 중량을 확인해 주세요.'}
