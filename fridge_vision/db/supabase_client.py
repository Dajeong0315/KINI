"""Supabase 클라이언트 2개.

  reader: Publishable 키 (sb_publishable_..., anon 역할) — 읽기만 됨
  writer: Secret 키 (sb_secret_..., service_role 역할) — 쓰기와 RPC 함수 실행

키 값은 .env 에만 둡니다. 이 모듈의 오류 메시지·repr 에는 키 값을 넣지 않습니다.
"""
from __future__ import annotations

import config


class SupabaseConfigError(RuntimeError):
    """URL·키가 없거나 잘못 넣음 (키 값은 메시지에 넣지 않음)."""


def _client(url: str, key: str, timeout: float):
    """요청 타임아웃은 httpx 클라이언트에 (supabase-py 2.34 권장 방식, PostgREST·Storage 공통).
    라이브러리 자체 재시도(조회 실패 시 1·2·4초 간격 3회)는 끔: 실패하면 바로 state/db_outbox.jsonl 에 쌓고
    다음 촬영 때 다시 보내므로, 재시도로 전송 스레드가 십수 초씩 묶이지 않게 (실측: 닫힌 포트에서 작업 1건당 약 16초 → 즉시)."""
    import httpx
    from supabase import ClientOptions, create_client
    return create_client(url, key, options=ClientOptions(httpx_client=httpx.Client(timeout=timeout),
                                                         postgrest_client_retry=False,
                                                         auto_refresh_token=False, persist_session=False))


def _check_url(url: str) -> None:
    if not url:
        raise SupabaseConfigError("SUPABASE_URL 이 비어 있습니다 (.env). 예: https://프로젝트ID.supabase.co")
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
        raise SupabaseConfigError("SUPABASE_URL 은 https:// 로 시작해야 합니다 (.env)")


def make_reader(url: str | None = None, key: str | None = None, timeout: float | None = None):
    """읽기 전용 클라이언트 (Publishable 키)."""
    url = config.SUPABASE_URL if url is None else url
    key = config.SUPABASE_PUBLISHABLE_KEY if key is None else key
    _check_url(url)
    if not key:
        raise SupabaseConfigError("SUPABASE_PUBLISHABLE_KEY 가 비어 있습니다 (.env). "
                                  "Supabase 대시보드 → Project Settings → API Keys → Publishable key")
    if key.startswith("sb_secret_"):
        raise SupabaseConfigError("SUPABASE_PUBLISHABLE_KEY 자리에 Secret 키가 들어 있습니다. "
                                  "읽기용 키는 sb_publishable_ 로 시작해야 합니다 (.env 확인)")
    return _client(url, key, config.SUPABASE_TIMEOUT if timeout is None else timeout)


def make_writer(url: str | None = None, key: str | None = None, timeout: float | None = None):
    """쓰기·RPC 클라이언트 (Secret 키)."""
    url = config.SUPABASE_URL if url is None else url
    key = config.SUPABASE_SECRET_KEY if key is None else key
    _check_url(url)
    if not key:
        raise SupabaseConfigError("SUPABASE_SECRET_KEY 가 비어 있습니다 (.env). "
                                  "Supabase 대시보드 → Project Settings → API Keys → Secret keys 에서 복사해 넣어 주세요")
    if key.startswith("sb_publishable_"):
        raise SupabaseConfigError("SUPABASE_SECRET_KEY 자리에 Publishable 키가 들어 있습니다. "
                                  "쓰기용 키는 sb_secret_ 로 시작해야 합니다 (.env 확인)")
    return _client(url, key, config.SUPABASE_TIMEOUT if timeout is None else timeout)


class Clients:
    """필요할 때 만드는 reader/writer 묶음. 테스트에서는 가짜 클라이언트를 넣음."""

    def __init__(self, reader=None, writer=None) -> None:
        self._reader, self._writer = reader, writer

    @property
    def reader(self):
        if self._reader is None:
            self._reader = make_reader()
        return self._reader

    @property
    def writer(self):
        if self._writer is None:
            self._writer = make_writer()
        return self._writer

    def __repr__(self) -> str:                      # 키·클라이언트 내부 값이 출력되지 않게
        return f"Clients(reader={'set' if self._reader else 'lazy'}, writer={'set' if self._writer else 'lazy'})"


def configured_for_write() -> bool:
    import os
    return bool(config.VISION_ALLOW_LEGACY_SUPABASE_WRITE
                and not os.getenv('KINI_API_URL') and not os.getenv('KINI_A_API_URL')
                and config.SUPABASE_URL and config.SUPABASE_SECRET_KEY)
