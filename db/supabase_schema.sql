-- Operational metrics migration. Public catalog is in supabase_food_catalog.sql.
-- Canonical SQL for MCP migration kini_operational_metrics_v1.
CREATE TABLE public.kini_daily_metrics (
    metric_date date NOT NULL,
    metric_code text NOT NULL CHECK (metric_code IN
        ('recommendation_latency','recommendation_success','api_error','cache_hit','cache_miss','import_success','import_failure')),
    sample_count bigint NOT NULL CHECK (sample_count >= 1),
    total_value double precision NOT NULL CHECK
        (total_value >= 0 AND total_value < 'Infinity'::double precision),
    PRIMARY KEY (metric_date, metric_code)
);
COMMENT ON TABLE public.kini_daily_metrics IS
    'Daily aggregates only. No household/member/session/device IDs, text payloads, health data or secrets.';
ALTER TABLE public.kini_daily_metrics ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.kini_daily_metrics FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.kini_daily_metrics TO service_role;
-- Deliberately no anon/authenticated policies: only the trusted backend can access.
