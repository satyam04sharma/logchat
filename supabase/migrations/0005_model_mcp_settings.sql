CREATE TABLE public.model_settings (
  owner_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  provider text NOT NULL CHECK (provider IN ('ollama','openai_compatible')),
  chat_model text NOT NULL CHECK (length(chat_model) BETWEEN 1 AND 200),
  base_url text CHECK (base_url IS NULL OR length(base_url) BETWEEN 8 AND 1000),
  api_key_ref uuid,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((provider='ollama' AND base_url IS NULL AND api_key_ref IS NULL)
      OR (provider='openai_compatible' AND base_url IS NOT NULL))
);

CREATE TABLE public.mcp_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  url text NOT NULL CHECK (length(url) BETWEEN 8 AND 1000),
  transport text NOT NULL CHECK (transport IN ('streamable_http','sse')),
  enabled boolean NOT NULL DEFAULT true,
  api_key_ref uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, owner_id),
  UNIQUE (owner_id, name)
);

ALTER TABLE public.model_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_settings FORCE ROW LEVEL SECURITY;
ALTER TABLE public.mcp_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mcp_connections FORCE ROW LEVEL SECURITY;

CREATE POLICY owner_access ON public.model_settings FOR ALL TO authenticated
  USING (owner_id=(SELECT auth.uid())) WITH CHECK (owner_id=(SELECT auth.uid()));
CREATE POLICY owner_access ON public.mcp_connections FOR ALL TO authenticated
  USING (owner_id=(SELECT auth.uid())) WITH CHECK (owner_id=(SELECT auth.uid()));
GRANT SELECT,INSERT,UPDATE,DELETE ON public.model_settings,public.mcp_connections TO authenticated;
CREATE INDEX model_settings_owner_idx ON public.model_settings(owner_id);
CREATE INDEX mcp_connections_owner_idx ON public.mcp_connections(owner_id);
