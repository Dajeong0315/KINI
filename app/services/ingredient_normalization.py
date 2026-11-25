"""Conservative local name matching; never infers product composition."""
import re
import unicodedata

def normalize_text(value):
    value=unicodedata.normalize('NFKC',value).strip()
    value=re.sub(r'\s+', ' ', value)
    value=re.sub(r'\s*\(국산\)\s*', ' ', value).strip()
    value=re.sub(r'\s*\d+(?:\.\d+)?\s*(?:kg|g|ml|L|개|컵)\s*$', '', value, flags=re.I)
    value=re.sub(r'(?:은|는|을|를)$', '', value).strip()
    return value

def normalize_ingredient(db, text):
    normalized=normalize_text(text)
    result={'original_text':text,'normalized_text':normalized,'ingredient_id':None,'canonical_name':None,'match_status':'unknown','confidence':0.0,'warning':'등록된 재료를 직접 확인해 주세요.'}
    if not normalized: return result
    rows=db.execute("SELECT DISTINCT i.* FROM ingredients i LEFT JOIN ingredient_aliases a ON a.ingredient_id=i.id WHERE i.active=1 AND (i.name=? OR i.canonical_name=? OR a.alias=? OR a.normalized_alias=?) ORDER BY i.id",(normalized,normalized,normalized,normalized)).fetchall()
    if len(rows)>1:
        result.update(match_status='ambiguous',warning='여러 재료가 일치합니다. 직접 선택해 주세요.')
        return result
    if not rows:
        if any(x in normalized for x in ('(',')','제품','통조림','소스','스팸')):
            result.update(match_status='needs_label_check',warning='제품과 성분표를 확인해 주세요.')
        return result
    row=rows[0]
    result.update(ingredient_id=row['id'],canonical_name=row['canonical_name'] or row['name'],confidence=row['data_confidence'] or 0.0)
    if normalized not in (row['name'],row['canonical_name']):
        aliases=db.execute('SELECT * FROM ingredient_aliases WHERE ingredient_id=? AND (alias=? OR normalized_alias=?)',(row['id'],normalized,normalized)).fetchall()
        if not any(a['source_type']!='unknown' and a['confidence'] is not None and a['confidence']>=.9 for a in aliases):
            result.update(match_status='ambiguous',warning='별칭의 출처 또는 신뢰도를 확인해 주세요.')
            return result
    if row['is_composite'] or row['requires_label_check']:
        result.update(match_status='needs_label_check',warning='복합 재료 또는 제품의 성분표와 교차접촉 표시를 확인해 주세요.')
    elif row['source_type']=='unknown':
        result.update(match_status='ambiguous',warning='재료 출처 확인이 필요합니다.')
    else:
        result.update(match_status='matched',warning='데모 데이터입니다. 실제 제품을 확인하세요.' if row['source_type']=='demo' else None)
    return result
