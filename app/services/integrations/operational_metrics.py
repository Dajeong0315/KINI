"""Fixed allowlist daily aggregates. No arbitrary event dictionaries are exported."""
from datetime import date
import math
import os
import requests

METRICS = frozenset(('recommendation_latency', 'recommendation_success', 'api_error',
                     'cache_hit', 'cache_miss', 'import_success', 'import_failure'))


def validate_metric(payload):
    if set(payload) != {'metric_date', 'metric_code', 'sample_count', 'total_value'}:
        raise ValueError('운영 지표 필드만 전송할 수 있습니다.')
    if date.fromisoformat(payload['metric_date']).isoformat() != payload['metric_date'] or payload['metric_code'] not in METRICS:
        raise ValueError('지표 날짜 또는 코드가 잘못되었습니다.')
    count, value = payload['sample_count'], payload['total_value']
    if type(count) is not int or count < 1 or count > 9223372036854775807:
        raise ValueError('집계 건수가 잘못되었습니다.')
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('집계 값이 잘못되었습니다.')
    return dict(payload)


class SupabaseMetricsRepository:
    """Idempotent absolute daily snapshots. Caller must be the sole aggregate writer."""
    def __init__(self, *, url=None, secret_key=None, session=None):
        self.url = (url or os.getenv('SUPABASE_URL', '')).rstrip('/')
        self.secret_key = secret_key or os.getenv('SUPABASE_SECRET_KEY', '')
        if self.url != 'https://zyjrwychceuxxfdproev.supabase.co' or not self.secret_key:
            raise ValueError('KINI Supabase 서버 설정이 필요합니다.')
        self.session = session or requests.Session()

    def write_daily_snapshot(self, payload):
        data = validate_metric(payload)
        # Never expose raw HTTP errors: they may include authorization or request details.
        try:
            response = self.session.post(
                self.url + '/rest/v1/kini_daily_metrics?on_conflict=metric_date,metric_code',
                headers={'apikey': self.secret_key, 'Content-Type': 'application/json',
                         'Prefer': 'resolution=merge-duplicates,return=minimal'},
                json=data, timeout=10, allow_redirects=False)
            if not 200 <= response.status_code < 300:
                raise ValueError('운영 지표 업로드 실패')
        except requests.RequestException:
            raise ValueError('운영 지표 연결 실패') from None
