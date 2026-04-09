#!/bin/bash
# Security Levels Update Script (Docker)
# Führt SQL aus, um alle Organisationen auf alle 6 Security Levels zu setzen

echo "Updating security levels for all organizations..."

docker exec securesend_db psql -U securesend -d securesend -c "
UPDATE organizations 
SET settings_json = 
  CASE 
    WHEN settings_json IS NULL THEN '{\"allowed_security_levels\": [\"normal\", \"standard\", \"secure\", \"extended\", \"advanced\", \"maximal\"]}'::jsonb
    WHEN settings_json->>'allowed_security_levels' NOT LIKE '%maximal%' THEN 
      (settings_json || '{\"allowed_security_levels\": [\"normal\", \"standard\", \"secure\", \"extended\", \"advanced\", \"maximal\"]}'::jsonb)::jsonb
    ELSE settings_json
  END
WHERE settings_json IS NOT NULL;
"

echo "Done!"