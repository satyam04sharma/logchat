-- Preserve unknown probe counts rather than persisting them as observed zero.
CREATE OR REPLACE FUNCTION public.enqueue_pipeline_job(p_source_id uuid, p_check_token uuid,
 p_observed_at timestamptz, p_observed_events bigint, p_interval_seconds integer, p_reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE st public.pipeline_source_state%ROWTYPE; s public.sources%ROWTYPE; v_requested timestamptz; v_start timestamptz; v_id uuid;
BEGIN
  IF session_user <> 'logchat_worker' THEN RAISE EXCEPTION 'worker role required'; END IF;
  SELECT * INTO st FROM public.pipeline_source_state WHERE source_id=p_source_id FOR UPDATE;
  -- Fail closed before any mutation, including after enqueue clears the lease.
  IF NOT FOUND OR p_check_token IS NULL OR st.check_lease_token IS NULL
     OR st.check_lease_expires_at IS NULL
     OR st.check_lease_token IS DISTINCT FROM p_check_token
     OR st.check_lease_expires_at < now() THEN
    RAISE EXCEPTION 'stale scheduler lease';
  END IF;
  SELECT * INTO s FROM public.sources WHERE id=p_source_id;
  v_requested := coalesce(st.cursor_ts, s.created_at);
  v_start := greatest(v_requested, p_observed_at - make_interval(secs=>s.retention_seconds));
  IF EXISTS (SELECT 1 FROM public.pipeline_jobs WHERE source_id=p_source_id
             AND (status IN ('queued','running') OR (status='failed' AND error_summary <> 'batch_limit_exceeded'))) THEN
    UPDATE public.pipeline_source_state SET next_check_at=p_observed_at+make_interval(secs=>greatest(1,least(p_interval_seconds,21600))),
      interval_seconds=greatest(1,least(p_interval_seconds,21600)),last_checked_at=p_observed_at,last_event_count=CASE WHEN p_observed_events IS NULL THEN NULL ELSE greatest(0,p_observed_events) END,
      check_lease_token=NULL,check_lease_expires_at=NULL,updated_at=now() WHERE source_id=p_source_id;
    RETURN NULL;
  END IF;
  IF p_observed_at > v_start THEN
    INSERT INTO public.pipeline_jobs(owner_id,project_id,environment_id,source_id,requested_start,window_start,window_end,interval_seconds,interval_reason)
    VALUES(s.owner_id,s.project_id,s.environment_id,s.id,v_requested,v_start,p_observed_at,
      greatest(1,least(p_interval_seconds,21600)),left(p_reason,240))
    ON CONFLICT (source_id,window_start,window_end) DO UPDATE SET available_at=least(public.pipeline_jobs.available_at,now())
    RETURNING id INTO v_id;
  END IF;
  UPDATE public.pipeline_source_state SET next_check_at=p_observed_at+make_interval(secs=>greatest(1,least(p_interval_seconds,21600))),
    interval_seconds=greatest(1,least(p_interval_seconds,21600)), last_checked_at=p_observed_at,
    last_event_count=CASE WHEN p_observed_events IS NULL THEN NULL ELSE greatest(0,p_observed_events) END, check_lease_token=NULL,check_lease_expires_at=NULL,updated_at=now()
  WHERE source_id=p_source_id;
  RETURN v_id;
END $$;

-- Latest completed job only: bounded indexed lookup, no failed/split parents.
CREATE INDEX pipeline_jobs_feedback_idx ON public.pipeline_jobs(source_id, window_end DESC)
  WHERE status='completed';
CREATE INDEX source_coverage_job_feedback_idx ON public.source_coverage(job_id);

CREATE FUNCTION public.pipeline_completed_feedback(p_source_id uuid, p_check_token uuid)
RETURNS TABLE(source_id uuid, owner_id uuid, project_id uuid, environment_id uuid,
 event_count bigint, window_seconds double precision, window_end timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  WITH latest AS (
    SELECT j.* FROM public.pipeline_source_state st
    JOIN public.pipeline_jobs j ON j.source_id=st.source_id
      AND j.owner_id=st.owner_id AND j.project_id=st.project_id AND j.environment_id=st.environment_id
    WHERE st.source_id=p_source_id AND st.check_lease_token=p_check_token
      AND st.check_lease_expires_at>=now() AND session_user='logchat_worker'
      AND j.status='completed'
    ORDER BY j.window_end DESC LIMIT 1
  ), bounded AS MATERIALIZED (
    -- At most three rows inspected. The builder emits one successful window
    -- and optionally one expired-cursor gap; malformed extra rows fail closed.
    SELECT sc.* FROM latest j JOIN public.source_coverage sc ON sc.job_id=j.id LIMIT 3
  )
  SELECT j.source_id,j.owner_id,j.project_id,j.environment_id,c.event_count,
    extract(epoch FROM (c.window_end-c.window_start))::double precision,c.window_end
  FROM latest j JOIN bounded c ON c.job_id=j.id
  WHERE (SELECT count(*) FROM bounded)<=2 AND c.status IN ('complete','empty')
    AND c.source_id=j.source_id AND c.owner_id=j.owner_id
    AND c.project_id=j.project_id AND c.environment_id=j.environment_id
    AND c.window_start>=j.window_start AND c.window_end=j.window_end
    AND c.event_count=j.events_fetched
    AND (SELECT count(*) FROM bounded WHERE status IN ('complete','empty'))=1
    AND NOT EXISTS (SELECT 1 FROM bounded WHERE status='failed')
$$;
REVOKE ALL ON FUNCTION public.pipeline_completed_feedback(uuid,uuid) FROM PUBLIC,authenticated;
GRANT EXECUTE ON FUNCTION public.pipeline_completed_feedback(uuid,uuid) TO logchat_worker;
