-- Auth owns its bootstrap functions; extend uid after its migrations complete.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid
$$;
GRANT EXECUTE ON FUNCTION auth.uid() TO anon, authenticated, service_role;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE public.projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, owner_id)
);

CREATE TABLE public.sources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  project_id uuid NOT NULL,
  connector text NOT NULL CHECK (connector IN ('sentry','vercel','supabase','docker')),
  source_project_id text NOT NULL,
  retention_seconds bigint NOT NULL CHECK (retention_seconds > 0),
  cursor text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, project_id, owner_id),
  UNIQUE (project_id, connector, source_project_id),
  FOREIGN KEY (project_id, owner_id) REFERENCES public.projects(id, owner_id) ON DELETE CASCADE
);

CREATE TABLE public.log_clusters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  project_id uuid NOT NULL,
  source_id uuid NOT NULL,
  fingerprint text NOT NULL,
  service text NOT NULL,
  level text NOT NULL,
  first_seen timestamptz NOT NULL,
  last_seen timestamptz NOT NULL CHECK (last_seen >= first_seen),
  event_count bigint NOT NULL CHECK (event_count > 0),
  summary text NOT NULL,
  redacted_samples jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(redacted_samples) = 'array' AND jsonb_array_length(redacted_samples) <= 3),
  embedding vector(768),
  UNIQUE (id, project_id, owner_id),
  UNIQUE (source_id, fingerprint, service, level, first_seen),
  FOREIGN KEY (source_id, project_id, owner_id) REFERENCES public.sources(id, project_id, owner_id) ON DELETE CASCADE
);

CREATE TABLE public.pipeline_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  project_id uuid NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz CHECK (finished_at >= started_at),
  status text NOT NULL CHECK (status IN ('running','completed','failed')),
  events_fetched bigint NOT NULL DEFAULT 0 CHECK (events_fetched >= 0),
  clusters_touched bigint NOT NULL DEFAULT 0 CHECK (clusters_touched >= 0),
  interval_seconds integer NOT NULL CHECK (interval_seconds > 0 AND interval_seconds <= 21600),
  interval_reason text NOT NULL,
  FOREIGN KEY (project_id, owner_id) REFERENCES public.projects(id, owner_id) ON DELETE CASCADE
);

CREATE TABLE public.incidents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  project_id uuid NOT NULL,
  title text NOT NULL,
  summary text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('low','medium','high','critical')),
  status text NOT NULL CHECK (status IN ('open','resolved')),
  started_at timestamptz NOT NULL,
  resolved_at timestamptz CHECK (resolved_at >= started_at),
  embedding vector(768),
  UNIQUE (id, project_id, owner_id),
  FOREIGN KEY (project_id, owner_id) REFERENCES public.projects(id, owner_id) ON DELETE CASCADE
);

CREATE TABLE public.incident_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  project_id uuid NOT NULL,
  incident_id uuid NOT NULL,
  cluster_id uuid,
  source_url text CHECK (source_url ~ '^https?://'),
  notes text NOT NULL DEFAULT '',
  FOREIGN KEY (incident_id, project_id, owner_id) REFERENCES public.incidents(id, project_id, owner_id) ON DELETE CASCADE,
  FOREIGN KEY (cluster_id, project_id, owner_id) REFERENCES public.log_clusters(id, project_id, owner_id) ON DELETE CASCADE
);

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['projects','sources','log_clusters','pipeline_runs','incidents','incident_evidence'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format('CREATE POLICY owner_access ON public.%I FOR ALL TO authenticated USING (owner_id = (SELECT auth.uid())) WITH CHECK (owner_id = (SELECT auth.uid()))', table_name);
    EXECUTE format('CREATE INDEX ON public.%I (owner_id)', table_name);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO authenticated', table_name);
  END LOOP;
END $$;

-- Incident writes are reserved for a future MAF approval-gated server operation.
REVOKE INSERT, UPDATE, DELETE ON public.incidents, public.incident_evidence FROM authenticated;
CREATE INDEX ON public.log_clusters (project_id, last_seen DESC);
CREATE INDEX ON public.pipeline_runs (project_id, started_at DESC);
CREATE INDEX ON public.log_clusters USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON public.incidents USING hnsw (embedding vector_cosine_ops);
