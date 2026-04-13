# Outline API Reference (for this skill)

This skill uses the Outline API with `POST` JSON requests.

Base URL example:

- `https://outline.cloud-fs.de/api`

Quick runner:

- `./.cursor/skills/outline-content-management/outline <command>`
- The Python helper auto-loads `.cursor/skills/outline-content-management/.env.local` if present.

Common endpoints:

- `auth.info` - validate token and workspace
- `collections.list` - list collections
- `documents.search` - find documents by query
- `documents.info` - fetch one document by `id`
- `documents.create` - create a document
- `documents.update` - update document content/title

## Minimal cURL examples

```bash
curl -sS -X POST "$OUTLINE_BASE_URL/auth.info" \
  -H "Authorization: Bearer $OUTLINE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

```bash
curl -sS -X POST "$OUTLINE_BASE_URL/documents.search" \
  -H "Authorization: Bearer $OUTLINE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"SecureSend","limit":10}'
```

```bash
curl -sS -X POST "$OUTLINE_BASE_URL/documents.info" \
  -H "Authorization: Bearer $OUTLINE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id":"<doc-id>"}'
```

## Notes

- Some Outline deployments vary by version. If an endpoint fails, run `auth.info` first and inspect error details.
- Prefer `documents.search` then `documents.info` for reliable reads.
- Use markdown body from files for deterministic updates.
