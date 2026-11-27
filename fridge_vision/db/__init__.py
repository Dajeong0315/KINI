"""Supabase 고내 재고 DB 연동 (구조: supabase/schema.sql, 설명: docs/DB_재고테이블_설계_설명.md).

- supabase_client.py: 읽기(Publishable 키)·쓰기(Secret 키) 클라이언트
- repository.py    : 촬영 기록·변경 후보 저장, 확인·거절·초기화(RPC), 현재 재고·대기 이벤트 조회

재고(fridge_stock)는 절대 직접 INSERT·UPDATE 하지 않고 RPC 함수(confirm/reject/reset_demo)로만 바꿉니다.
"""
