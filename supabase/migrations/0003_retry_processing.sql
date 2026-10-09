-- Recovery is explicitly requested by the owner; never skip a failed window.
CREATE FUNCTION public.retry_failed_jobs(p_project_id uuid)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE retried_ids uuid[]; retry_count integer;
BEGIN
  IF auth.uid() IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
  WITH retried AS (
    UPDATE public.pipeline_jobs j
    SET status = 'queued', attempts = 0, available_at = now(),
        lease_token = NULL, lease_expires_at = NULL,
        error_summary = NULL, finished_at = NULL
    WHERE j.project_id = p_project_id AND j.owner_id = auth.uid()
      AND j.status = 'failed' AND j.error_summary IS DISTINCT FROM 'batch_limit_exceeded'
      AND EXISTS (SELECT 1 FROM public.sources s WHERE s.id = j.source_id AND s.enabled)
    RETURNING j.id
  ) SELECT array_agg(id), count(*) INTO retried_ids, retry_count FROM retried;
  DELETE FROM public.source_coverage
    WHERE job_id = ANY(retried_ids) AND status = 'failed' AND owner_id = auth.uid();
  RETURN retry_count;
END $$;
REVOKE ALL ON FUNCTION public.retry_failed_jobs(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.retry_failed_jobs(uuid) TO authenticated;
