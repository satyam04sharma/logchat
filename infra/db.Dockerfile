FROM pgvector/pgvector:0.8.2-pg17
COPY --chmod=755 infra/init-db.sh /docker-entrypoint-initdb.d/00-logchat.sh
