# Vision → A 재고 이벤트 연결

Vision 인식 결과는 KINI A 로컬 재고 이벤트 API에 확인 대기 상태로 접수된다.

## 동작 경계

- 인식 결과는 `POST /api/data/inventory/events`로 `pending_confirmation` 이벤트만 만든다.
- `detected_label`과 `confidence`는 후보 정보다. 표준 `ingredient_id`, 실측 `quantity_g`, 필요한 출고 `inventory_item_id`는 확인 시 사용자가 제공한다.
- 상자 개수로 중량을 계산하거나 자동 확정하지 않는다. Unknown·미매칭·중량 미확인은 pending으로 둔다.
- 같은 감지 재시도에는 같은 `client_event_id`를 유지한다. 서로 다른 촬영의 같은 물체를 서버 event ID만으로 중복 제거한다고 가정하지 않는다. 촬영 간 중복은 후속 식별/확인 범위다.
- `confirm`은 같은 확인 payload로 다시 호출해도 A 서버의 멱등 계약을 따른다. `reject`는 재고를 변경하지 않는다.
- 로컬 API 주소는 HTTP 루프백 origin만 받는다. `localhost`, 루프백 IPv4 및 괄호를 붙인 루프백 IPv6를 지원한다. 기본 전송은 redirect와 환경 프록시를 사용하지 않는다.
- 최초 접수에서 전송 실패나 재시도 가능 상태(408, 429, 500, 502, 503, 504)를 받으면 UUID가 포함된 payload를 큐에 보존한다. 재처리 중 401은 `KiniAPIError.reauthentication_required`로 알리며 해당 항목과 뒤의 항목을 보존한다.
- 재처리 중 403·409는 `KiniAPIError.manual_review_required`로 알리고 상태 코드와 함께 큐에 보존한다. 이후 자동 재처리는 해당 항목을 건너뛴다. 권한 또는 충돌을 확인한 호출자는 `retry_pending(include_manual=True)`로 명시적으로 재처리할 수 있다. 기존 A API는 만료되거나 유효하지 않은 세션도 403으로 응답한다. 이 경우 다시 로그인하고 권한을 확인한 뒤 명시적으로 재처리한다.
- 큐에서 성공 항목만 제거한다. 보존 항목은 같은 UUID를 사용한다. 큐는 단일 작성자가 사용하는 로컬 JSONL 파일이며 토큰·비밀번호·이미지를 담지 않는다.

## 실행

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_vision_postgres.ps1
```

실제 HTTP 시험은 Flask 앱을 임의 루프백 포트의 스레드형 Werkzeug 서버에서 실행한다. 합성 계정으로 로그인한 뒤 클라이언트의 기본 `requests` 경로로 합성 Vision 입력을 보낸다. 임시 큐와 실제 PostgreSQL에서 대기·확정·거절, 지정 lot 동시 출고, 중복 요청, 가구 권한과 응답 유실 후 재처리, SQL 오류 롤백을 검사한다. 응답 유실은 실제 HTTP 응답을 받은 뒤 클라이언트에 전송 실패를 주입한다. 카메라·YOLO와 인식 정확도는 이번 측정 범위 밖이다.

반복 횟수, SQLite 회귀 명령과 운영 환경 검증 한계는 [PostgreSQL 재고 동시성 검증](PostgreSQL_재고_동시성_검증.md)에 기록했다.
