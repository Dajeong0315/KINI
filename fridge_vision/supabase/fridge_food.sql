-- Supabase 고내 재고 테이블 (Vision AI 냉장고 → supabase_sync.py 가 사용)
-- Supabase 대시보드 → SQL Editor 에 붙여 넣고 Run.
--
-- 한 행 = 냉장고 안 식재료 1개
--   넣음  : INSERT (food_name, food_date_in = 오늘, loc)
--   꺼냄  : 같은 이름·아직 안 꺼낸 행의 food_date_out = 오늘 (같은 loc 우선, 없으면 가장 오래 넣은 행)
--   현재 재고 = food_date_out IS NULL
--
-- 이름 규칙: PostgreSQL 은 따옴표 없는 이름을 소문자로 저장합니다 (FOOD_NAME → food_name).
-- Oracle 의 VARCHAR2 는 PostgreSQL 에서 VARCHAR 입니다.

create table if not exists public.fridge_food (
    food_id        bigint generated always as identity primary key,  -- PK
    food_name      varchar(50) not null,                             -- 품목 한글 이름 (예: 오렌지, 포도)
    food_date_in   date        not null default current_date,        -- 넣은 날
    food_date_out  date,                                             -- 꺼낸 날 (안에 있으면 NULL)
    loc            varchar(10),                                      -- 칸 "X,Y": X = L(왼쪽)/C(가운데)/R(오른쪽), Y = 층(1 = 맨 아래)
    constraint fridge_food_dates check (food_date_out is null or food_date_out >= food_date_in)
);

-- 꺼낼 행을 찾는 조회(이름 + 아직 안 꺼냄)를 빠르게
create index if not exists fridge_food_open_idx
    on public.fridge_food (food_name, food_date_in)
    where food_date_out is null;

-- 보안: RLS 를 켜면 anon 키로는 읽기·쓰기가 막힙니다.
--   부스 PC 는 .env 의 SUPABASE_KEY 에 service_role 키를 넣어 사용 (RLS 를 우회, 이 PC 밖으로 유출 금지)
--   anon 키로 쓰고 싶다면 아래 정책의 주석을 풀어 필요한 권한만 허용하세요.
alter table public.fridge_food enable row level security;

-- create policy "fridge_food anon read"   on public.fridge_food for select to anon using (true);
-- create policy "fridge_food anon insert" on public.fridge_food for insert to anon with check (true);
-- create policy "fridge_food anon close"  on public.fridge_food for update to anon using (food_date_out is null);

-- 현재 재고 보기 (품목·칸별 개수)
-- security_invoker: 보는 사람의 권한(RLS)을 그대로 적용 (anon 이 뷰로 우회하지 못하게)
create or replace view public.fridge_food_current with (security_invoker = true) as
select food_name, loc, count(*) as quantity, min(food_date_in) as oldest_in
from public.fridge_food
where food_date_out is null
group by food_name, loc;
