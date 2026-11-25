"""Unit conversions do not assume density or package weight."""
import math

def convert_quantity(db, quantity, unit, ingredient_id=None):
    if not math.isfinite(quantity) or quantity < 0: raise ValueError('수량이 올바르지 않습니다.')
    unit=unit.strip()
    si={'g':('g',1), 'kg':('g',1000), 'ml':('ml',1), 'mL':('ml',1), 'L':('ml',1000), 'l':('ml',1000)}
    if unit in si:
        target,factor=si[unit]
        return {'status':'converted','quantity':quantity*factor,'unit':target,'quantity_g':quantity*factor if target=='g' else None,'warning':None if target=='g' else '밀도 확인 전에는 g으로 변환하지 않습니다.'}
    row=db.execute("SELECT factor,source_type,confidence FROM unit_conversions WHERE ingredient_id=? AND from_unit=? AND to_unit='g' AND source_type IN ('official_imported','verified_product') AND confidence>=0.95 AND factor>0 ORDER BY confidence DESC,rowid LIMIT 1",(ingredient_id,unit)).fetchone() if ingredient_id is not None else None
    if row:
        return {'status':'converted','quantity':quantity*row['factor'],'unit':'g','quantity_g':quantity*row['factor'],'warning':'상품 및 상태가 환산 기준과 일치하는지 확인하세요.'}
    return {'status':'needs_confirmation','quantity':None,'unit':None,'quantity_g':None,'warning':'제품 또는 재료별 실제 중량을 확인해 주세요.'}
