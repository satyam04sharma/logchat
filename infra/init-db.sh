#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v password="$POSTGRES_PASSWORD" <<'SQL'
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN BYPASSRLS;
CREATE ROLE authenticator LOGIN NOINHERIT PASSWORD :'password';
GRANT anon, authenticated, service_role TO authenticator;
CREATE ROLE supabase_auth_admin LOGIN NOINHERIT CREATEROLE PASSWORD :'password';
CREATE SCHEMA auth AUTHORIZATION supabase_auth_admin;
ALTER ROLE supabase_auth_admin SET search_path = auth;
GRANT ALL ON DATABASE postgres TO supabase_auth_admin;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
ALTER DATABASE postgres SET log_statement = 'none';
ALTER DATABASE postgres SET log_min_error_statement = 'panic';
ALTER DATABASE postgres SET log_parameter_max_length_on_error = 0;
SQL
