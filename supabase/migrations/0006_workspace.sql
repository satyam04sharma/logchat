-- Persist redacted investigations and their verified result snapshots, never raw events.
CREATE TABLE public.conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  project_id uuid NOT NULL,
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  pending_token uuid,
  pending_until timestamptz,
  UNIQUE(id, project_id, owner_id),
  FOREIGN KEY(project_id, owner_id) REFERENCES public.projects(id, owner_id) ON DELETE CASCADE
);
CREATE TABLE public.conversation_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  project_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  request_id uuid NOT NULL,
  role text NOT NULL CHECK(role IN ('user','assistant')),
  content text NOT NULL CHECK(length(content) BETWEEN 1 AND 12000),
  request jsonb,
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(conversation_id, request_id, role),
  FOREIGN KEY(conversation_id, project_id, owner_id) REFERENCES public.conversations(id, project_id, owner_id) ON DELETE CASCADE
);
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['conversations','conversation_messages'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY owner_access ON public.%I FOR ALL TO authenticated USING(owner_id = (SELECT auth.uid())) WITH CHECK(owner_id = (SELECT auth.uid()))', t);
    EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON public.%I TO authenticated', t);
    EXECUTE format('CREATE INDEX ON public.%I(owner_id)', t);
  END LOOP;
END $$;
CREATE INDEX ON public.conversations(project_id, updated_at DESC);
CREATE INDEX ON public.conversation_messages(conversation_id, created_at);
