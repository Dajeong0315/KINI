"""Authenticated, read-only meal history and stored-nutrition reports."""
from contextlib import closing
from datetime import date, timedelta
from functools import wraps
import re

from flask import Blueprint, jsonify, request

from . import meal_reporting as reports
from .local_identity import authorize, AuthenticationError, AuthorizationError
from .recommendation_repository import open_db

api = Blueprint('meal_reports',__name__)


def _integer(value, *, minimum=1, maximum=9223372036854775807):
    if not re.fullmatch('[0-9]+',value or ''): raise ValueError
    number = int(value)
    if not minimum <= number <= maximum: raise ValueError
    return number


def _day(value):
    if not re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}',value or ''): raise ValueError
    return date.fromisoformat(value)


def _period(args):
    period = args.get('period') or ('custom' if 'start_date' in args or 'end_date' in args else 'week')
    if period not in ('today','week','custom'): raise ValueError
    if period=='custom':
        start, end = _day(args.get('start_date')), _day(args.get('end_date'))
    else:
        if 'start_date' in args or 'end_date' in args: raise ValueError
        end = reports.kst_today(); start = end-timedelta(days=6 if period=='week' else 0)
    if start>end or (end-start).days>=366 or end==date.max: raise ValueError
    return {'period':period,'start_date':start.isoformat(),'end_date':end.isoformat(),'timezone':'Asia/Seoul'}, start, end


def read_session(*, detail=False, history=False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            scheme, _, token = request.headers.get('Authorization','').partition(' ')
            if scheme.lower()!='bearer' or not token.strip():
                return jsonify({'error':'가구 로그인 세션이 필요합니다.'}),401
            try:
                allowed = {'household_id'} if detail else {'household_id','member_id','period','start_date','end_date'}
                if history: allowed |= {'limit','offset'}
                if set(request.args)-allowed or any(len(request.args.getlist(k))!=1 for k in request.args): raise ValueError
                household_id = _integer(request.args['household_id']) if 'household_id' in request.args else None
                with closing(open_db(seed_demo=False)) as db, db:
                    if getattr(db,'is_postgres',False): db.execute('SET TRANSACTION READ ONLY')
                    else: db.execute('PRAGMA query_only=ON')
                    identity = authorize(db,token.strip(),household_id=household_id)
                    hid = identity['household_id']
                    if detail: return fn(db,hid,*args,**kwargs)
                    member_id = _integer(request.args['member_id']) if 'member_id' in request.args else None
                    if member_id is not None and not db.execute('SELECT 1 FROM members WHERE id=? AND household_id=?',(member_id,hid)).fetchone():
                        raise AuthorizationError
                    period, start, end = _period(request.args)
                    result = fn(db,hid,start,end,member_id=member_id,*args,**kwargs)
                    return jsonify({'household_id':hid,'member_id':member_id,**period,'units':reports.UNITS,**result})
            except AuthenticationError:
                return jsonify({'error':'유효한 가구 로그인 세션이 필요합니다.'}),401
            except AuthorizationError:
                return jsonify({'error':'해당 가구 또는 구성원에 접근할 수 없습니다.'}),403
            except (ValueError,OverflowError):
                return jsonify({'error':'조회 기간·구성원·페이지 값을 확인해 주세요.'}),400
        return wrapped
    return decorate


@api.after_request
def no_cache(response):
    response.headers['Cache-Control']='no-store'
    response.headers['Pragma']='no-cache'
    return response


@api.get('/api/meal-records')
@read_session(history=True)
def meal_history(db,household_id,start,end,*,member_id):
    limit = _integer(request.args.get('limit','20'),maximum=100)
    offset = _integer(request.args.get('offset','0'),minimum=0,maximum=1000000)
    return reports.history(db,household_id,start,end,member_id=member_id,limit=limit,offset=offset)


@api.get('/api/meal-records/<int:record_id>')
@read_session(detail=True)
def meal_detail(db,household_id,record_id):
    result = reports.detail(db,household_id,record_id)
    if result is None: return jsonify({'error':'식사 기록을 찾을 수 없습니다.'}),404
    return jsonify({'household_id':household_id,'units':reports.UNITS,**result})


@api.get('/api/reports/nutrition')
@read_session()
def nutrition_report(db,household_id,start,end,*,member_id):
    return reports.nutrition_report(db,household_id,start,end,member_id=member_id)
