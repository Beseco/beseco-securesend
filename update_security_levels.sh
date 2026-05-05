#!/bin/bash
# Security Levels Update Script (Docker)
# Führt SQL aus, um alle Organisationen auf das 4-Stufen-Modell zu setzen

echo "Updating security levels for all organizations..."

docker exec securesend_db psql -U securesend -d securesend -c "
UPDATE organizations 
SET settings_json = 
  '{\"allowed_security_levels\": [\"level1\", \"level2\", \"level3\", \"level4\"], \"default_security_level\": \"level2\"}'::jsonb
WHERE settings_json IS NOT NULL 
AND settings_json->>'allowed_security_levels' NOT LIKE '%level4%';
"

echo "Done!"