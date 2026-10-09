-- Legacy incident links also retain the same environment as their cluster.
ALTER TABLE public.log_clusters ADD CONSTRAINT log_clusters_full_scope_key
  UNIQUE (id, project_id, environment_id, owner_id);
ALTER TABLE public.incident_evidence ADD CONSTRAINT incident_evidence_cluster_environment_fk
  FOREIGN KEY (cluster_id, project_id, environment_id, owner_id)
  REFERENCES public.log_clusters(id, project_id, environment_id, owner_id) ON DELETE CASCADE;
