-- Environment-scoped, resumable summary pipeline. Raw log events are never stored.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'logchat_worker') THEN
    CREATE ROLE logchat_worker NOLOGIN NOINHERIT NOBYPASSRLS;
  END IF;
END $$;

CREATE TABLE public.environments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  project_id uuid NOT NULL,
  name text NOT NULL CHECK (name ~ '^[a-z][a-z0-9_-]{0,62}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, project_id, owner_id),
  UNIQUE (project_id, name),
  FOREIGN KEY (project_id, owner_id) REFERENCES public.projects(id, owner_id) ON DELETE CASCADE
);

INSERT INTO public.environments (owner_id, project_id, name)
SELECT owner_id, id, 'dev' FROM public.projects ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION public.create_default_environment()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  INSERT INTO public.environments(owner_id, project_id, name)
  VALUES (NEW.owner_id, NEW.id, 'dev') ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;
CREATE TRIGGER projects_create_default_environment
AFTER INSERT ON public.projects FOR EACH ROW EXECUTE FUNCTION public.create_default_environment();

ALTER TABLE public.sources
  ADD COLUMN environment_id uuid,
  ADD COLUMN connector_config jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(connector_config) = 'object'),
  ADD COLUMN credential_ref text CHECK (credential_ref IS NULL OR credential_ref ~ '^[A-Za-z0-9._/-]{1,240}$'),
  ADD COLUMN enabled boolean NOT NULL DEFAULT true;
UPDATE public.sources s SET environment_id = e.id
FROM public.environments e WHERE e.project_id = s.project_id AND e.name = 'dev';
ALTER TABLE public.sources ALTER COLUMN environment_id SET NOT NULL;
ALTER TABLE public.sources DROP CONSTRAINT sources_project_id_connector_source_project_id_key;
ALTER TABLE public.sources ADD CONSTRAINT sources_environment_fk
  FOREIGN KEY (environment_id, project_id, owner_id)
  REFERENCES public.environments(id, project_id, owner_id) ON DELETE CASCADE;
ALTER TABLE public.sources ADD CONSTRAINT sources_environment_connector_key
  UNIQUE (project_id, environment_id, connector, source_project_id);
ALTER TABLE public.sources ADD CONSTRAINT sources_full_scope_key
  UNIQUE (id, project_id, environment_id, owner_id);

CREATE OR REPLACE FUNCTION public.fill_default_environment()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.environment_id IS NULL THEN
    SELECT id INTO NEW.environment_id FROM public.environments
    WHERE project_id = NEW.project_id AND owner_id = NEW.owner_id AND name = 'dev';
  END IF;
  IF NEW.environment_id IS NULL THEN RAISE EXCEPTION 'project has no dev environment'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER sources_fill_default_environment
BEFORE INSERT ON public.sources FOR EACH ROW EXECUTE FUNCTION public.fill_default_environment();

ALTER TABLE public.log_clusters ADD COLUMN environment_id uuid;
UPDATE public.log_clusters c SET environment_id = s.environment_id FROM public.sources s WHERE s.id = c.source_id;
ALTER TABLE public.log_clusters ALTER COLUMN environment_id SET NOT NULL;
ALTER TABLE public.log_clusters ADD CONSTRAINT log_clusters_environment_fk
  FOREIGN KEY (environment_id, project_id, owner_id)
  REFERENCES public.environments(id, project_id, owner_id) ON DELETE CASCADE;
ALTER TABLE public.log_clusters ADD CONSTRAINT log_clusters_source_environment_fk
  FOREIGN KEY (source_id, project_id, environment_id, owner_id)
  REFERENCES public.sources(id, project_id, environment_id, owner_id) ON DELETE CASCADE;

ALTER TABLE public.pipeline_runs ADD COLUMN environment_id uuid;
UPDATE public.pipeline_runs r SET environment_id = e.id FROM public.environments e
WHERE e.project_id = r.project_id AND e.name = 'dev';
ALTER TABLE public.pipeline_runs ALTER COLUMN environment_id SET NOT NULL;
ALTER TABLE public.pipeline_runs ADD CONSTRAINT pipeline_runs_environment_fk
  FOREIGN KEY (environment_id, project_id, owner_id)
  REFERENCES public.environments(id, project_id, owner_id) ON DELETE CASCADE;

ALTER TABLE public.incidents ADD COLUMN environment_id uuid;
UPDATE public.incidents i SET environment_id = e.id FROM public.environments e
WHERE e.project_id = i.project_id AND e.name = 'dev';
ALTER TABLE public.incidents ALTER COLUMN environment_id SET NOT NULL;
ALTER TABLE public.incidents ADD CONSTRAINT incidents_environment_fk
  FOREIGN KEY (environment_id, project_id, owner_id)
  REFERENCES public.environments(id, project_id, owner_id) ON DELETE CASCADE;
ALTER TABLE public.incidents ADD CONSTRAINT incidents_full_scope_key
  UNIQUE (id, project_id, environment_id, owner_id);

ALTER TABLE public.incident_evidence ADD COLUMN environment_id uuid;
UPDATE public.incident_evidence e SET environment_id = i.environment_id FROM public.incidents i WHERE i.id = e.incident_id;
ALTER TABLE public.incident_evidence ALTER COLUMN environment_id SET NOT NULL;
ALTER TABLE public.incident_evidence ADD CONSTRAINT incident_evidence_environment_fk
  FOREIGN KEY (environment_id, project_id, owner_id)
  REFERENCES public.environments(id, project_id, owner_id) ON DELETE CASCADE;
ALTER TABLE public.incident_evidence ADD CONSTRAINT incident_evidence_incident_environment_fk
  FOREIGN KEY (incident_id, project_id, environment_id, owner_id)
  REFERENCES public.incidents(id, project_id, environment_id, owner_id) ON DELETE CASCADE;

CREATE OR REPLACE FUNCTION public.fill_legacy_environment()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.environment_id IS NULL THEN
    IF TG_TABLE_NAME = 'log_clusters' THEN
      SELECT environment_id INTO NEW.environment_id FROM public.sources WHERE id = NEW.source_id;
    ELSIF TG_TABLE_NAME = 'incident_evidence' THEN
      SELECT environment_id INTO NEW.environment_id FROM public.incidents WHERE id = NEW.incident_id;
    ELSE
      SELECT id INTO NEW.environment_id FROM public.environments
      WHERE project_id = NEW.project_id AND owner_id = NEW.owner_id AND name = 'dev';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER log_clusters_fill_environment BEFORE INSERT ON public.log_clusters
FOR EACH ROW EXECUTE FUNCTION public.fill_legacy_environment();
CREATE TRIGGER pipeline_runs_fill_environment BEFORE INSERT ON public.pipeline_runs
FOR EACH ROW EXECUTE FUNCTION public.fill_legacy_environment();
CREATE TRIGGER incidents_fill_environment BEFORE INSERT ON public.incidents
FOR EACH ROW EXECUTE FUNCTION public.fill_legacy_environment();
CREATE TRIGGER incident_evidence_fill_environment BEFORE INSERT ON public.incident_evidence
FOR EACH ROW EXECUTE FUNCTION public.fill_legacy_environment();

CREATE TABLE public.pipeline_source_state (
  source_id uuid PRIMARY KEY,
  owner_id uuid NOT NULL,
  project_id uuid NOT NULL,
  environment_id uuid NOT NULL,
  cursor_ts timestamptz,
  cursor_event_id text,
  next_check_at timestamptz NOT NULL DEFAULT now(),
  interval_seconds integer NOT NULL DEFAULT 1800 CHECK (interval_seconds > 0 AND interval_seconds <= 21600),
  ema_events_per_minute double precision CHECK (ema_events_per_minute IS NULL OR ema_events_per_minute >= 0),
  last_checked_at timestamptz,
  last_event_count bigint CHECK (last_event_count IS NULL OR last_event_count >= 0),
  check_lease_token uuid,
  check_lease_expires_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (source_id, project_id, environment_id, owner_id) REFERENCES public.sources(id, project_id, environment_id, owner_id) ON DELETE CASCADE,
  FOREIGN KEY (environment_id, project_id, owner_id) REFERENCES public.environments(id, project_id, owner_id) ON DELETE CASCADE
);

CREATE OR REPLACE FUNCTION public.create_pipeline_source_state()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  INSERT INTO public.pipeline_source_state(source_id, owner_id, project_id, environment_id)
  VALUES (NEW.id, NEW.owner_id, NEW.project_id, NEW.environment_id) ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;
INSERT INTO public.pipeline_source_state(source_id, owner_id, project_id, environment_id)
SELECT id, owner_id, project_id, environment_id FROM public.sources ON CONFLICT DO NOTHING;
CREATE TRIGGER sources_create_pipeline_state AFTER INSERT ON public.sources
FOR EACH ROW EXECUTE FUNCTION public.create_pipeline_source_state();

CREATE TABLE public.pipeline_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL,
  project_id uuid NOT NULL,
  environment_id uuid NOT NULL,
  source_id uuid NOT NULL,
  requested_start timestamptz NOT NULL,
  window_start timestamptz NOT NULL,
  window_end timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
  available_at timestamptz NOT NULL DEFAULT now(),
  lease_token uuid,
  lease_expires_at timestamptz,
  error_summary text CHECK (error_summary IS NULL OR length(error_summary) <= 500),
  events_fetched bigint NOT NULL DEFAULT 0 CHECK (events_fetched >= 0),
  chunks_written bigint NOT NULL DEFAULT 0 CHECK (chunks_written >= 0),
  interval_seconds integer NOT NULL CHECK (interval_seconds > 0 AND interval_seconds <= 21600),
  interval_reason text NOT NULL CHECK (length(interval_reason) <= 240),
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  CHECK (window_end > window_start),
  CHECK (window_start >= requested_start),
  UNIQUE (source_id, window_start, window_end),
  UNIQUE (id, project_id, environment_id, owner_id, source_id),
  FOREIGN KEY (source_id, project_id, environment_id, owner_id) REFERENCES public.sources(id, project_id, environment_id, owner_id) ON DELETE CASCADE,
  FOREIGN KEY (environment_id, project_id, owner_id) REFERENCES public.environments(id, project_id, owner_id) ON DELETE CASCADE
);

CREATE TABLE public.summary_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid NOT NULL, project_id uuid NOT NULL,
  environment_id uuid NOT NULL, source_id uuid NOT NULL, job_id uuid NOT NULL,
  bucket_start timestamptz NOT NULL, bucket_end timestamptz NOT NULL, chunk_index integer NOT NULL DEFAULT 0 CHECK (chunk_index >= 0),
  fingerprint text NOT NULL, service text NOT NULL, level text NOT NULL, release text NOT NULL DEFAULT '',
  event_count bigint NOT NULL CHECK (event_count > 0),
  duration_count bigint NOT NULL DEFAULT 0 CHECK (duration_count >= 0),
  duration_sum_ms double precision NOT NULL DEFAULT 0 CHECK (duration_sum_ms >= 0),
  duration_min_ms double precision CHECK (duration_min_ms >= 0),
  duration_max_ms double precision CHECK (duration_max_ms >= duration_min_ms),
  status_counts jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(status_counts) = 'object'),
  summary text NOT NULL,
  redacted_samples jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (redacted_samples = '[]'::jsonb),
  embedding vector(768) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (bucket_end > bucket_start),
  UNIQUE (job_id, bucket_start, bucket_end, fingerprint, service, level, release, chunk_index),
  FOREIGN KEY (job_id, project_id, environment_id, owner_id, source_id)
    REFERENCES public.pipeline_jobs(id, project_id, environment_id, owner_id, source_id) ON DELETE CASCADE
);

CREATE TABLE public.source_coverage (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid NOT NULL, project_id uuid NOT NULL,
  environment_id uuid NOT NULL, source_id uuid NOT NULL, job_id uuid,
  window_start timestamptz NOT NULL, window_end timestamptz NOT NULL,
  status text NOT NULL CHECK (status IN ('complete','empty','gap','failed')),
  gap_reason text CHECK ((status = 'gap' AND gap_reason IN ('cursor_expired','source_unavailable','fetch_failed')) OR (status <> 'gap' AND gap_reason IS NULL)),
  detail text NOT NULL DEFAULT '' CHECK (length(detail) <= 500),
  event_count bigint NOT NULL DEFAULT 0 CHECK (event_count >= 0),
  duration_count bigint NOT NULL DEFAULT 0 CHECK (duration_count >= 0),
  duration_sum_ms double precision NOT NULL DEFAULT 0 CHECK (duration_sum_ms >= 0),
  duration_min_ms double precision CHECK (duration_min_ms >= 0),
  duration_max_ms double precision CHECK (duration_max_ms >= duration_min_ms),
  status_counts jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(status_counts) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (window_end > window_start),
  UNIQUE (source_id, window_start, window_end, status),
  FOREIGN KEY (source_id, project_id, environment_id, owner_id) REFERENCES public.sources(id, project_id, environment_id, owner_id) ON DELETE CASCADE,
  FOREIGN KEY (environment_id, project_id, owner_id) REFERENCES public.environments(id, project_id, owner_id) ON DELETE CASCADE
);

-- Scheduler claims are short and independently fenced from builder work.
CREATE OR REPLACE FUNCTION public.claim_due_sources(p_limit integer DEFAULT 25, p_lease_seconds integer DEFAULT 60)
RETURNS TABLE(source_id uuid, connector text, connector_config jsonb, credential_ref text,
 owner_id uuid, project_id uuid, environment_id uuid, retention_seconds bigint,
 cursor_ts timestamptz, cursor_event_id text, interval_seconds integer, lease_token uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF session_user <> 'logchat_worker' THEN RAISE EXCEPTION 'worker role required'; END IF;
  RETURN QUERY WITH due AS (
    SELECT st.source_id FROM public.pipeline_source_state st JOIN public.sources s ON s.id=st.source_id
    WHERE s.enabled AND st.next_check_at <= now()
      AND (st.check_lease_expires_at IS NULL OR st.check_lease_expires_at < now())
    ORDER BY st.next_check_at FOR UPDATE OF st SKIP LOCKED LIMIT greatest(1, least(p_limit, 100))
  ), claimed AS (
    UPDATE public.pipeline_source_state st SET check_lease_token=gen_random_uuid(),
      check_lease_expires_at=now()+make_interval(secs=>greatest(10,least(p_lease_seconds,300))), updated_at=now()
    FROM due WHERE st.source_id=due.source_id RETURNING st.*
  ) SELECT s.id,s.connector,s.connector_config,s.credential_ref,s.owner_id,s.project_id,s.environment_id,
      s.retention_seconds,c.cursor_ts,c.cursor_event_id,c.interval_seconds,c.check_lease_token
    FROM claimed c JOIN public.sources s ON s.id=c.source_id;
END $$;

CREATE OR REPLACE FUNCTION public.enqueue_pipeline_job(p_source_id uuid, p_check_token uuid,
 p_observed_at timestamptz, p_observed_events bigint, p_interval_seconds integer, p_reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE st public.pipeline_source_state%ROWTYPE; s public.sources%ROWTYPE; v_requested timestamptz; v_start timestamptz; v_id uuid;
BEGIN
  IF session_user <> 'logchat_worker' THEN RAISE EXCEPTION 'worker role required'; END IF;
  SELECT * INTO st FROM public.pipeline_source_state WHERE source_id=p_source_id FOR UPDATE;
  IF st.check_lease_token IS DISTINCT FROM p_check_token OR st.check_lease_expires_at < now() THEN RAISE EXCEPTION 'stale scheduler lease'; END IF;
  SELECT * INTO s FROM public.sources WHERE id=p_source_id;
  v_requested := coalesce(st.cursor_ts, s.created_at);
  v_start := greatest(v_requested, p_observed_at - make_interval(secs=>s.retention_seconds));
  IF EXISTS (SELECT 1 FROM public.pipeline_jobs WHERE source_id=p_source_id
             AND (status IN ('queued','running') OR (status='failed' AND error_summary <> 'batch_limit_exceeded'))) THEN
    UPDATE public.pipeline_source_state SET next_check_at=p_observed_at+make_interval(secs=>greatest(1,least(p_interval_seconds,21600))),
      interval_seconds=greatest(1,least(p_interval_seconds,21600)),last_checked_at=p_observed_at,last_event_count=greatest(0,p_observed_events),
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
    last_event_count=greatest(0,p_observed_events), check_lease_token=NULL,check_lease_expires_at=NULL,updated_at=now()
  WHERE source_id=p_source_id;
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION public.claim_pipeline_jobs(p_limit integer DEFAULT 10, p_lease_seconds integer DEFAULT 300)
RETURNS SETOF public.pipeline_jobs LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF session_user <> 'logchat_worker' THEN RAISE EXCEPTION 'worker role required'; END IF;
  UPDATE public.pipeline_jobs SET status='failed',finished_at=now(),error_summary='lease_expired',lease_token=NULL,lease_expires_at=NULL
  WHERE status='running' AND lease_expires_at<now() AND attempts>=max_attempts;
  INSERT INTO public.source_coverage(owner_id,project_id,environment_id,source_id,job_id,window_start,window_end,status,detail)
  SELECT owner_id,project_id,environment_id,source_id,id,window_start,window_end,'failed','lease_expired'
  FROM public.pipeline_jobs WHERE status='failed' AND error_summary='lease_expired' AND finished_at>=transaction_timestamp()
  ON CONFLICT(source_id,window_start,window_end,status) DO UPDATE SET detail=EXCLUDED.detail,created_at=now();
  RETURN QUERY WITH candidates AS (
    SELECT j.id FROM public.pipeline_jobs j WHERE (
      (j.status='queued' AND j.available_at<=now()) OR
      (j.status='running' AND j.lease_expires_at<now() AND j.attempts<j.max_attempts))
      AND NOT EXISTS (
        SELECT 1 FROM public.pipeline_jobs earlier
        WHERE earlier.source_id=j.source_id AND earlier.window_start<j.window_start
          AND (earlier.status IN ('queued','running') OR
               (earlier.status='failed' AND earlier.error_summary <> 'batch_limit_exceeded'))
      )
    ORDER BY j.available_at,j.created_at FOR UPDATE SKIP LOCKED LIMIT greatest(1,least(p_limit,100))
  ) UPDATE public.pipeline_jobs j SET status='running',attempts=j.attempts+1,started_at=coalesce(j.started_at,now()),
      lease_token=gen_random_uuid(),lease_expires_at=now()+make_interval(secs=>greatest(30,least(p_lease_seconds,3600))),error_summary=NULL
    FROM candidates c WHERE j.id=c.id RETURNING j.*;
END $$;

CREATE OR REPLACE FUNCTION public.get_job_source(p_job_id uuid, p_lease_token uuid)
RETURNS TABLE(connector text, connector_config jsonb, credential_ref text, retention_seconds bigint)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT s.connector,s.connector_config,s.credential_ref,s.retention_seconds
  FROM public.pipeline_jobs j JOIN public.sources s ON s.id=j.source_id
  WHERE j.id=p_job_id AND j.status='running' AND j.lease_token=p_lease_token AND j.lease_expires_at>=now()
    AND session_user='logchat_worker'
$$;

CREATE OR REPLACE FUNCTION public.renew_pipeline_job(p_job_id uuid,p_lease_token uuid,p_lease_seconds integer DEFAULT 300)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE changed integer;
BEGIN
  IF session_user <> 'logchat_worker' THEN RAISE EXCEPTION 'worker role required'; END IF;
  UPDATE public.pipeline_jobs SET lease_expires_at=now()+make_interval(secs=>greatest(30,least(p_lease_seconds,3600)))
  WHERE id=p_job_id AND status='running' AND lease_token=p_lease_token AND lease_expires_at>=now();
  GET DIAGNOSTICS changed=ROW_COUNT; RETURN changed=1;
END $$;

CREATE OR REPLACE FUNCTION public.split_pipeline_job(p_job_id uuid,p_lease_token uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE j public.pipeline_jobs%ROWTYPE; midpoint timestamptz;
BEGIN
  IF session_user <> 'logchat_worker' THEN RAISE EXCEPTION 'worker role required'; END IF;
  SELECT * INTO j FROM public.pipeline_jobs WHERE id=p_job_id FOR UPDATE;
  IF j.status <> 'running' OR j.lease_token IS DISTINCT FROM p_lease_token OR j.lease_expires_at < now() THEN RETURN false; END IF;
  midpoint := j.window_start + ((j.window_end-j.window_start)/2);
  IF midpoint <= j.window_start OR midpoint >= j.window_end THEN RETURN false; END IF;
  UPDATE public.pipeline_jobs SET status='failed',finished_at=now(),error_summary='batch_limit_exceeded',lease_token=NULL,lease_expires_at=NULL WHERE id=j.id;
  INSERT INTO public.pipeline_jobs(owner_id,project_id,environment_id,source_id,requested_start,window_start,window_end,interval_seconds,interval_reason,max_attempts)
  VALUES
    (j.owner_id,j.project_id,j.environment_id,j.source_id,j.requested_start,j.window_start,midpoint,j.interval_seconds,'split oversized window',j.max_attempts),
    (j.owner_id,j.project_id,j.environment_id,j.source_id,midpoint,midpoint,j.window_end,j.interval_seconds,'split oversized window',j.max_attempts)
  ON CONFLICT(source_id,window_start,window_end) DO NOTHING;
  RETURN true;
END $$;

CREATE OR REPLACE FUNCTION public.complete_pipeline_job(p_job_id uuid, p_lease_token uuid,
 p_cursor_ts timestamptz, p_cursor_event_id text, p_chunks jsonb, p_coverage jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE j public.pipeline_jobs%ROWTYPE; st public.pipeline_source_state%ROWTYPE; c jsonb; v_chunks bigint := 0; v_events bigint := 0;
BEGIN
  IF session_user <> 'logchat_worker' THEN RAISE EXCEPTION 'worker role required'; END IF;
  SELECT * INTO j FROM public.pipeline_jobs WHERE id=p_job_id FOR UPDATE;
  IF j.status <> 'running' OR j.lease_token IS DISTINCT FROM p_lease_token OR j.lease_expires_at < now() THEN RETURN false; END IF;
  SELECT * INTO st FROM public.pipeline_source_state WHERE source_id=j.source_id FOR UPDATE;
  IF st.cursor_ts IS NOT NULL AND st.cursor_ts IS DISTINCT FROM j.window_start
     AND NOT (st.cursor_ts=j.requested_start AND j.requested_start<j.window_start) THEN
    RAISE EXCEPTION 'job window is not cursor-contiguous';
  END IF;
  IF p_cursor_ts IS DISTINCT FROM j.window_end THEN RAISE EXCEPTION 'completion must cover the full immutable window'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(coalesce(p_coverage,'[]'::jsonb)) item
    WHERE item.value->>'status' IN ('complete','empty','gap')
      AND (item.value->>'window_end')::timestamptz=j.window_end
  ) THEN RAISE EXCEPTION 'completion coverage missing'; END IF;
  IF j.requested_start<j.window_start AND NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(coalesce(p_coverage,'[]'::jsonb)) item
    WHERE item.value->>'status'='gap' AND item.value->>'gap_reason'='cursor_expired'
      AND (item.value->>'window_start')::timestamptz=j.requested_start
      AND (item.value->>'window_end')::timestamptz>=j.window_start
      AND (item.value->>'window_end')::timestamptz<=j.window_end
  ) THEN RAISE EXCEPTION 'expired cursor gap missing'; END IF;
  DELETE FROM public.summary_chunks WHERE job_id=j.id;
  DELETE FROM public.source_coverage WHERE job_id=j.id;
  FOR c IN SELECT value FROM jsonb_array_elements(coalesce(p_chunks,'[]'::jsonb)) LOOP
    IF (c->>'bucket_start')::timestamptz < j.window_start OR (c->>'bucket_end')::timestamptz > j.window_end
       OR (c->>'bucket_end')::timestamptz <= (c->>'bucket_start')::timestamptz THEN
      RAISE EXCEPTION 'chunk outside immutable job window';
    END IF;
    INSERT INTO public.summary_chunks(owner_id,project_id,environment_id,source_id,job_id,bucket_start,bucket_end,chunk_index,
      fingerprint,service,level,release,event_count,duration_count,duration_sum_ms,duration_min_ms,duration_max_ms,status_counts,summary,embedding)
    VALUES(j.owner_id,j.project_id,j.environment_id,j.source_id,j.id,(c->>'bucket_start')::timestamptz,(c->>'bucket_end')::timestamptz,coalesce((c->>'chunk_index')::integer,0),
      c->>'fingerprint',c->>'service',c->>'level',coalesce(c->>'release',''),(c->>'event_count')::bigint,
      coalesce((c->>'duration_count')::bigint,0),coalesce((c->>'duration_sum_ms')::double precision,0),
      (c->>'duration_min_ms')::double precision,(c->>'duration_max_ms')::double precision,coalesce(c->'status_counts','{}'::jsonb),
      c->>'summary',(c->>'embedding')::vector);
    v_chunks := v_chunks + 1;
  END LOOP;
  FOR c IN SELECT value FROM jsonb_array_elements(coalesce(p_coverage,'[]'::jsonb)) LOOP
    INSERT INTO public.source_coverage(owner_id,project_id,environment_id,source_id,job_id,window_start,window_end,status,gap_reason,detail,event_count,
      duration_count,duration_sum_ms,duration_min_ms,duration_max_ms,status_counts)
    VALUES(j.owner_id,j.project_id,j.environment_id,j.source_id,j.id,(c->>'window_start')::timestamptz,(c->>'window_end')::timestamptz,
      c->>'status',nullif(c->>'gap_reason',''),left(coalesce(c->>'detail',''),500),coalesce((c->>'event_count')::bigint,0),
      coalesce((c->>'duration_count')::bigint,0),coalesce((c->>'duration_sum_ms')::double precision,0),
      (c->>'duration_min_ms')::double precision,(c->>'duration_max_ms')::double precision,coalesce(c->'status_counts','{}'::jsonb));
    v_events := v_events + coalesce((c->>'event_count')::bigint,0);
  END LOOP;
  UPDATE public.pipeline_source_state SET cursor_ts=greatest(coalesce(cursor_ts,'-infinity'),p_cursor_ts),cursor_event_id=p_cursor_event_id,updated_at=now()
    WHERE source_id=j.source_id;
  UPDATE public.pipeline_jobs SET status='completed',finished_at=now(),events_fetched=v_events,chunks_written=v_chunks,
    lease_token=NULL,lease_expires_at=NULL WHERE id=j.id;
  RETURN true;
END $$;

CREATE OR REPLACE FUNCTION public.retry_pipeline_job(p_job_id uuid,p_lease_token uuid,p_error_summary text,p_delay_seconds integer DEFAULT 30)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE changed integer;
BEGIN
  IF session_user <> 'logchat_worker' THEN RAISE EXCEPTION 'worker role required'; END IF;
  UPDATE public.pipeline_jobs SET status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,
    available_at=now()+make_interval(secs=>greatest(1,least(p_delay_seconds,3600))),finished_at=CASE WHEN attempts>=max_attempts THEN now() ELSE NULL END,
    error_summary=left(p_error_summary,500),lease_token=NULL,lease_expires_at=NULL
  WHERE id=p_job_id AND status='running' AND lease_token=p_lease_token AND lease_expires_at>=now();
  GET DIAGNOSTICS changed=ROW_COUNT;
  IF changed=1 THEN
    INSERT INTO public.source_coverage(owner_id,project_id,environment_id,source_id,job_id,window_start,window_end,status,detail)
    SELECT owner_id,project_id,environment_id,source_id,id,window_start,window_end,'failed',left(p_error_summary,500)
    FROM public.pipeline_jobs WHERE id=p_job_id AND status='failed'
    ON CONFLICT(source_id,window_start,window_end,status) DO UPDATE SET detail=EXCLUDED.detail,created_at=now();
  END IF;
  RETURN changed=1;
END $$;

ALTER TABLE public.environments ENABLE ROW LEVEL SECURITY; ALTER TABLE public.environments FORCE ROW LEVEL SECURITY;
ALTER TABLE public.pipeline_source_state ENABLE ROW LEVEL SECURITY; ALTER TABLE public.pipeline_source_state FORCE ROW LEVEL SECURITY;
ALTER TABLE public.pipeline_jobs ENABLE ROW LEVEL SECURITY; ALTER TABLE public.pipeline_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.summary_chunks ENABLE ROW LEVEL SECURITY; ALTER TABLE public.summary_chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE public.source_coverage ENABLE ROW LEVEL SECURITY; ALTER TABLE public.source_coverage FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_access ON public.environments FOR ALL TO authenticated USING(owner_id=(SELECT auth.uid())) WITH CHECK(owner_id=(SELECT auth.uid()));
CREATE POLICY owner_read ON public.summary_chunks FOR SELECT TO authenticated USING(owner_id=(SELECT auth.uid()));
CREATE POLICY owner_read ON public.source_coverage FOR SELECT TO authenticated USING(owner_id=(SELECT auth.uid()));
CREATE POLICY owner_read ON public.pipeline_source_state FOR SELECT TO authenticated USING(owner_id=(SELECT auth.uid()));
CREATE POLICY owner_read ON public.pipeline_jobs FOR SELECT TO authenticated USING(owner_id=(SELECT auth.uid()));
GRANT SELECT,INSERT,UPDATE,DELETE ON public.environments TO authenticated;
GRANT SELECT ON public.summary_chunks,public.source_coverage TO authenticated;
REVOKE ALL ON public.pipeline_source_state,public.pipeline_jobs,public.summary_chunks,public.source_coverage FROM PUBLIC,authenticated,logchat_worker;
GRANT SELECT ON public.pipeline_source_state,public.pipeline_jobs,public.summary_chunks,public.source_coverage TO authenticated;
REVOKE ALL ON FUNCTION public.claim_due_sources(integer,integer),public.enqueue_pipeline_job(uuid,uuid,timestamptz,bigint,integer,text),
 public.claim_pipeline_jobs(integer,integer),public.get_job_source(uuid,uuid),public.renew_pipeline_job(uuid,uuid,integer),public.split_pipeline_job(uuid,uuid),
 public.complete_pipeline_job(uuid,uuid,timestamptz,text,jsonb,jsonb),public.retry_pipeline_job(uuid,uuid,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_due_sources(integer,integer),public.enqueue_pipeline_job(uuid,uuid,timestamptz,bigint,integer,text),
 public.claim_pipeline_jobs(integer,integer),public.get_job_source(uuid,uuid),public.renew_pipeline_job(uuid,uuid,integer),public.split_pipeline_job(uuid,uuid),
 public.complete_pipeline_job(uuid,uuid,timestamptz,text,jsonb,jsonb),public.retry_pipeline_job(uuid,uuid,text,integer) TO logchat_worker;
CREATE INDEX pipeline_jobs_claim_idx ON public.pipeline_jobs(available_at,created_at) WHERE status IN ('queued','running');
CREATE INDEX summary_chunks_scope_idx ON public.summary_chunks(project_id,environment_id,bucket_start,bucket_end);
CREATE INDEX summary_chunks_embedding_idx ON public.summary_chunks USING hnsw(embedding vector_cosine_ops);
CREATE INDEX source_coverage_scope_idx ON public.source_coverage(project_id,environment_id,window_start,window_end);
