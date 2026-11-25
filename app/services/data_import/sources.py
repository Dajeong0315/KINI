from __future__ import annotations

import os
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MFDS_BASE_URL = 'https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo03'

def load_json(path: str) -> bytes:
    return Path(path).read_bytes()

class MfdsApiClient:
    """CLI-only adapter with explicit pagination, timeout and retry boundaries."""
    def __init__(self, service_key: str, *, timeout=(5, 30), retries=2):
        if not service_key: raise RuntimeError('DATA_GO_KR_SERVICE_KEY is required for --from-api.')
        self.service_key=service_key; self.timeout=timeout
        self.session=requests.Session()
        self.session.mount('https://',HTTPAdapter(max_retries=Retry(total=retries,backoff_factor=.5,status_forcelist=(429,500,502,503,504),allowed_methods=frozenset({'GET'}))))
    def fetch_page(self, page_no: int = 1, num_of_rows: int = 100) -> bytes:
        response=self.session.get(MFDS_BASE_URL,params={'serviceKey':self.service_key,'pageNo':page_no,'numOfRows':num_of_rows,'type':'json'},timeout=self.timeout)
        response.raise_for_status(); return response.content

def fetch_mfds_json(*, page_no: int = 1, num_of_rows: int = 100) -> bytes:
    """Explicit CLI-only network access. Never import this from request code."""
    return MfdsApiClient(os.getenv('DATA_GO_KR_SERVICE_KEY','')).fetch_page(page_no,num_of_rows)
