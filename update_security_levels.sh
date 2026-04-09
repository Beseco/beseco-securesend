#!/bin/bash
# Security Levels Update Script
# Führt SQL aus, um alle Organisationen auf alle 6 Security Levels zu setzen

# Database connection from environment
DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://securesend:securesend_pw_change_me@localhost:5432/securesend}"

# Extract connection details from DATABASE_URL
# Format: postgresql+asyncpg://user:password@host:port/dbname
DB_USER=$(echo "$DATABASE_URL" | sed -E 's|.*://([^:]+):.*|\1|')
DB_PASS=$(echo "$DATABASE_URL" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
DB_HOST=$(echo "$DATABASE_URL" | sed -E 's|.*@([^:]+):.*|\1|')
DB_PORT=$(echo "$DATABASE_URL" | sed -E 's|.*:([0-9]+)/.*|\1|')
DB_NAME=$(echo "$DATABASE_URL" | sed -E 's|.*/([^?]+).*|\1|')

echo "Updating security levels for all organizations..."

PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
UPDATE organizations 
SET settings_json = COALESCE(settings_json, '{}'::jsonb) || 
  '{\"allowed_security_levels\": [\"normal\", \"standard\", \"secure\", \"extended\", \"advanced\", \"maximal\"]}'::jsonb
WHERE settings_json IS NOT NULL 
AND (
  settings_json->'allowed_security_levels' IS NULL 
  OR jsonb_array_length(settings_json->'allowed_security_levels') < 6
);
"

echo "Done!"