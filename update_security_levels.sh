#!/bin/bash
# Security Levels Update Script (Docker)
# Führt SQL aus, um alle Organisationen auf alle 6 Security Levels zu setzen

echo "Updating security levels for all organizations..."

docker exec securesend_db psql -U securesend -d securesend -c "
UPDATE organizations 
SET settings_json = 
  '{\"allowed_security_levels\": [\"normal\", \"standard\", \"secure\", \"extended\", \"advanced\", \"maximal\"]}'::jsonb
WHERE settings_json IS NOT NULL 
AND settings_json->>'allowed_security_levels' NOT LIKE '%maximal%';
"

echo "Done!"