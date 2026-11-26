# Vision → A 재고 이벤트 연결

이 브랜치는 Vision 인식 결과를 KINI A의 로컬 재고 이벤트 API에 연결하는 경계만 담는다.

## 동작 경계

- 인식 결과는 `POST /api/data/inventory/events`로 `pending_confirmation` 이벤트만 만든다.
- `detected_label`과 `confidence`는 후보 정보다. 표준 `ingredient_id`, 실측 `quantity_g`, 필요한 출고 `inventory_item_id`는 확인 시 사용자가 제공한다.
- 상자 개수로 중량을 계산하거나 자동 확정하지 않는다. Unknown·미매칭·중량 미확인은 pending으로 둔다.
- 같은 감지 재시도에는 같은 `client_event_id`를 유지한다. 서로 다른 촬영의 같은 물체를 서버 event ID만으로 중복 제거한다고 가정하지 않는다. 촬영 간 중복은 후속 식별/확인 범위다.
- `confirm`은 같은 확인 payload로 다시 호출해도 A 서버의 멱등 계약을 따른다. `reject`는 재고를 변경하지 않는다.
- 큐에는 재전송에 필요한 operation/payload만 저장하며 토큰·비밀번호·이미지는 저장하지 않는다. 401/403/409는 큐에 넣지 않고, 연결 실패와 408/429/5xx만 재시도한다.

## 실행

```powershell
$env:KINI_A_API_URL = 'http://127.0.0.1:5000'
$env:KINI_A_API_TOKEN = '<local token>'
python -m pytest tests/test_kini_inventory_client.py -q
```

테스트는 외부 AI·Supabase 없이 임시 SQLite와 Flask의 실제 로컬 A API를 사용한다. 실카메라/실제 모델은 실행하지 않으며, 합성 Vision 입력만 사용한다. 따라서 결과는 서버 연결·확인·멱등성 검증이지 모델 인식 정확도 측정이 아니다.

이번 범위에는 문열림 센서, 클라우드 HTTPS/장치 인증, OCR·튜닝·추적·배포, Supabase 직접 쓰기, 서버 서비스/migration 변경이 없다. 독립 Vision 모드의 기존 Supabase 경로도 삭제하지 않았다.
