-- =====================================================================
-- Vision AI 냉장고 시연 — 고내 식재료 재고 DB (Supabase / PostgreSQL)
-- 사용법: Supabase 대시보드 > SQL Editor 에 이 파일 전체를 붙여넣고 Run
-- 테이블 4개 + 뷰 1개 + 함수 3개 + 권한/RLS + 시연용 초기 데이터
-- 설명 문서: docs/DB_재고테이블_설계_설명.md
--
-- 팀 초안에서 바꾼 점 (fridge_vision 연동용)
--   1) food_item 에 인식 코드(config.ITEMS)의 나머지 19종 추가 (demo_init_qty 0)
--      → stock_event.food_id 외래키 때문에, 목록에 없는 품목(오렌지·버터 등)은 저장할 수 없음
--   2) service_role 에 identity 시퀀스 사용 권한 명시
--   3) 파일 끝에 확인 쿼리 주석 추가
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. 식재료 목록 (마스터)
-- ---------------------------------------------------------------------
create table public.food_item (
  food_id        text primary key,                      -- 인식 코드와 같은 ID: 'milk'
  food_name      text not null,                         -- 화면 표시명: '우유'
  unit           text not null default '개',
  shelf_days     smallint not null check (shelf_days > 0),            -- 넣은 날부터 보관 가능 일수 (소비기한 계산용)
  default_loc_x  text check (default_loc_x in ('L', 'C', 'R')),       -- 기본 칸: 왼쪽/가운데/오른쪽
  default_loc_y  smallint check (default_loc_y in (1, 2)),            -- 기본 층: 1=위 칸, 2=아래 칸
  demo_init_qty  smallint not null default 0 check (demo_init_qty >= 0), -- 데모 초기화 시 넣어 둘 개수
  nutri_code     text,                                  -- 식약처 영양성분 DB 식품코드
  created_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 2. 촬영 기록 — 사진 1쌍과 Vision 결과 원본 (오류 판단의 근거)
-- ---------------------------------------------------------------------
create table public.capture (
  capture_id          text primary key,                 -- 'capture-20261120-094338-584'
  engine              text not null,                    -- 'yolo:yoloe-v8m-seg.pt' | 'openai:gpt-5.4-mini' | 'mock' | 'manual'
  before_path         text,                             -- Supabase Storage 경로 또는 로컬 경로
  after_path          text,
  result_json         jsonb not null,                   -- result.json 원본 그대로
  needs_confirmation  boolean not null default false,
  latency_ms          integer check (latency_ms >= 0),
  created_at          timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 3. 재고 변경 이벤트 — 확인 전 결과는 여기에만 저장
-- ---------------------------------------------------------------------
create table public.stock_event (
  event_id     bigint generated always as identity primary key,
  capture_id   text references public.capture (capture_id) on delete set null, -- 수동 입력이면 NULL 가능
  food_id      text not null references public.food_item (food_id),
  delta_qty    smallint not null check (delta_qty <> 0),               -- +2 추가 / -1 제거
  loc_x        text check (loc_x in ('L', 'C', 'R')),                  -- 위치를 아는 경우만 (YOLOE 등)
  loc_y        smallint check (loc_y in (1, 2)),
  source       text not null default 'vision' check (source in ('vision', 'manual')),
  status       text not null default 'pending' check (status in ('pending', 'confirmed', 'rejected')),
  created_at   timestamptz not null default now(),
  decided_at   timestamptz                                             -- 확인·거절된 시각
);

-- ---------------------------------------------------------------------
-- 4. 냉장고 재고 — 식재료 1개 = 1행, date_out 이 NULL 이면 지금 냉장고 안에 있음
-- ---------------------------------------------------------------------
create table public.fridge_stock (
  stock_id      bigint generated always as identity primary key,
  food_id       text not null references public.food_item (food_id),
  date_in       timestamptz not null default now(),
  date_out      timestamptz,
  expire_date   date,                                                  -- 소비기한 = 넣은 날 + shelf_days
  out_reason    text check (out_reason in ('consumed', 'discarded')),  -- 먹음 / 폐기
  loc_x         text check (loc_x in ('L', 'C', 'R')),
  loc_y         smallint check (loc_y in (1, 2)),
  in_event_id   bigint references public.stock_event (event_id),
  out_event_id  bigint references public.stock_event (event_id),
  constraint fridge_stock_out_pair check ((date_out is null) = (out_reason is null))
);

-- 지금 냉장고 안에 있는 것만 빠르게 찾기 위한 인덱스
create index fridge_stock_in_fridge_idx on public.fridge_stock (food_id, date_in) where date_out is null;
create index stock_event_pending_idx on public.stock_event (created_at) where status = 'pending';
-- 촬영 1건의 이벤트를 찾는 조회 (서버가 재전송할 때 중복 저장 방지에 사용)
create index stock_event_capture_idx on public.stock_event (capture_id);

-- ---------------------------------------------------------------------
-- 5. 현재 재고 뷰 — 화면에서는 이것만 읽으면 됨
-- ---------------------------------------------------------------------
create view public.current_stock with (security_invoker = true) as
select f.food_id,
       f.food_name,
       f.unit,
       count(s.stock_id)::int as qty,
       min(s.expire_date)     as nearest_expire
from public.food_item f
left join public.fridge_stock s
       on s.food_id = f.food_id and s.date_out is null
group by f.food_id, f.food_name, f.unit;

-- ---------------------------------------------------------------------
-- 6. 함수 (RPC) — 여러 테이블을 한 번에 바꾸는 작업은 함수로만
--    supabase.rpc() 로 호출하면 함수 전체가 하나의 트랜잭션으로 실행되고,
--    중간에 오류가 나면 전부 취소됨
-- ---------------------------------------------------------------------

-- 6-1. 이벤트 확인 → 재고 반영 (추가는 INSERT, 제거는 먼저 들어온 것부터 꺼냄)
create or replace function public.confirm_stock_event(p_event_id bigint)
returns integer
language plpgsql
set search_path = public
as $$
declare
  e      public.stock_event%rowtype;
  f      public.food_item%rowtype;
  n      integer;
  moved  integer;
begin
  select * into e from public.stock_event where event_id = p_event_id for update;
  if not found then
    raise exception 'event % not found', p_event_id;
  end if;
  if e.status <> 'pending' then
    raise exception 'event % is already %', p_event_id, e.status;
  end if;
  select * into f from public.food_item where food_id = e.food_id;

  if e.delta_qty > 0 then
    insert into public.fridge_stock (food_id, expire_date, loc_x, loc_y, in_event_id)
    select e.food_id,
           (now() at time zone 'Asia/Seoul')::date + f.shelf_days,
           coalesce(e.loc_x, f.default_loc_x),
           coalesce(e.loc_y, f.default_loc_y),
           e.event_id
    from generate_series(1, e.delta_qty);
    moved := e.delta_qty;
  else
    n := -e.delta_qty;
    with picked as (
      select stock_id
      from public.fridge_stock
      where food_id = e.food_id and date_out is null
      order by date_in, stock_id          -- 선입선출: 가장 먼저 들어온 것부터
      limit n
      for update
    )
    update public.fridge_stock s
       set date_out     = now(),
           out_reason   = case when s.expire_date < (now() at time zone 'Asia/Seoul')::date then 'discarded' else 'consumed' end,
           out_event_id = e.event_id
      from picked
     where s.stock_id = picked.stock_id;
    get diagnostics moved = row_count;
    if moved < n then
      -- 재고보다 많이 뺐다는 결과 → 오인식 가능성. 전체 취소하고 화면에서 확인받기
      raise exception 'stock mismatch: % has % in fridge, tried to remove %', e.food_id, moved, n;
    end if;
  end if;

  update public.stock_event set status = 'confirmed', decided_at = now() where event_id = e.event_id;
  return moved;
end;
$$;

-- 6-2. 이벤트 거절 (재고는 그대로)
create or replace function public.reject_stock_event(p_event_id bigint)
returns void
language plpgsql
set search_path = public
as $$
begin
  update public.stock_event
     set status = 'rejected', decided_at = now()
   where event_id = p_event_id and status = 'pending';
  if not found then
    raise exception 'event % is not pending', p_event_id;
  end if;
end;
$$;

-- 6-3. 데모 초기화 (기록을 지우고 food_item.demo_init_qty 만큼 다시 채움)
create or replace function public.reset_demo()
returns integer
language plpgsql
set search_path = public
as $$
declare
  cnt integer;
begin
  delete from public.fridge_stock where true;
  delete from public.stock_event where true;
  delete from public.capture where true;
  insert into public.fridge_stock (food_id, expire_date, loc_x, loc_y)
  select f.food_id, (now() at time zone 'Asia/Seoul')::date + f.shelf_days, f.default_loc_x, f.default_loc_y
  from public.food_item f
  cross join lateral generate_series(1, f.demo_init_qty);
  get diagnostics cnt = row_count;
  return cnt;
end;
$$;

-- ---------------------------------------------------------------------
-- 7. 권한 + RLS
--    읽기: iPad·모니터 화면 (anon 키)      → 모든 테이블 SELECT 만
--    쓰기: 노트북 서버 (service_role 키)   → 테이블 쓰기 + 함수 실행
-- ---------------------------------------------------------------------
alter table public.food_item    enable row level security;
alter table public.capture      enable row level security;
alter table public.stock_event  enable row level security;
alter table public.fridge_stock enable row level security;

revoke all on public.food_item, public.capture, public.stock_event, public.fridge_stock, public.current_stock
  from anon, authenticated;
grant select on public.food_item, public.capture, public.stock_event, public.fridge_stock, public.current_stock
  to anon, authenticated;
grant select, insert, update, delete on public.food_item, public.capture, public.stock_event, public.fridge_stock
  to service_role;
grant select on public.current_stock to service_role;
-- identity(자동 증가) 열의 시퀀스 — 서버가 행을 넣을 때 사용
grant usage, select on sequence public.stock_event_event_id_seq, public.fridge_stock_stock_id_seq to service_role;

create policy "demo screens can read" on public.food_item    for select to anon, authenticated using (true);
create policy "demo screens can read" on public.capture      for select to anon, authenticated using (true);
create policy "demo screens can read" on public.stock_event  for select to anon, authenticated using (true);
create policy "demo screens can read" on public.fridge_stock for select to anon, authenticated using (true);

revoke execute on function public.confirm_stock_event(bigint), public.reject_stock_event(bigint), public.reset_demo()
  from public, anon, authenticated;
grant execute on function public.confirm_stock_event(bigint), public.reject_stock_event(bigint), public.reset_demo()
  to service_role;

-- ---------------------------------------------------------------------
-- 8. 시연용 초기 데이터 (shelf_days 는 임시값 — 팀에서 확정 필요)
--    칸 배치 (정면에서 본 모습)
--          L(왼쪽)   C(가운데)  R(오른쪽)
--    1층   우유      두부       달걀
--    2층   브로콜리  버섯       닭고기
-- ---------------------------------------------------------------------
insert into public.food_item (food_id, food_name, unit, shelf_days, default_loc_x, default_loc_y, demo_init_qty) values
  ('milk',     '우유',     '개', 10, 'L', 1, 0),
  ('tofu',     '두부',     '모',  7, 'C', 1, 1),
  ('egg',      '달걀',     '판', 21, 'R', 1, 1),
  ('broccoli', '브로콜리', '개',  5, 'L', 2, 1),
  ('mushroom', '버섯',     '팩',  5, 'C', 2, 1),
  ('chicken',  '닭고기',   '팩',  2, 'R', 2, 1);

-- 인식 코드(config.ITEMS)의 나머지 품목 — 시연 초기 재고 0, 기본 칸 없음(YOLOE 가 칸을 알려 줌)
-- shelf_days 는 일반적인 냉장 보관 기간을 참고한 임시값입니다 [결정 필요]
insert into public.food_item (food_id, food_name, unit, shelf_days, demo_init_qty) values
  ('butter',      '버터',      '개', 30, 0),
  ('watermelon',  '수박',      '조각', 3, 0),
  ('orange',      '오렌지',    '개', 14, 0),
  ('apple',       '사과',      '개', 21, 0),
  ('lemon',       '레몬',      '개', 21, 0),
  ('tomato',      '토마토',    '개',  7, 0),
  ('strawberry',  '딸기',      '팩',  3, 0),
  ('grape',       '포도',      '송이', 5, 0),
  ('carrot',      '당근',      '개', 14, 0),
  ('cucumber',    '오이',      '개',  7, 0),
  ('cabbage',     '양배추',    '통', 14, 0),
  ('green_onion', '대파',      '단',  7, 0),
  ('cheese',      '치즈',      '개', 14, 0),
  ('yogurt',      '요거트',    '개', 10, 0),
  ('ham',         '햄·소시지', '팩',  7, 0),
  ('kimchi',      '김치',      '통', 30, 0),
  ('juice',       '주스',      '병',  7, 0),
  ('water',       '생수',      '병', 180, 0),
  ('can',         '캔음료',    '캔', 180, 0);

select public.reset_demo();

-- 확인: 25개 품목, 우유 0 / 두부·달걀·브로콜리·버섯·닭고기 1 / 나머지 0
-- select * from current_stock order by food_id;
