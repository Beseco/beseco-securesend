---
name: outline-content-management
description: Manage Outline knowledge base content via API (read, create, update). Use when the user mentions Outline, wiki pages, knowledge base docs, or asks to read/create/edit content in Outline instances such as outline.cloud-fs.de.
---
# Outline Content Management

Use this skill to read, create, and update documents in Outline via API.

## When To Use

- User asks to read or sync docs from Outline
- User asks to create pages in Outline
- User asks to edit/update existing Outline pages
- User mentions `outline.cloud-fs.de` or an Outline workspace

## Required Environment

Recommended setup:

1. Copy `.cursor/skills/outline-content-management/.env.local.example` to `.cursor/skills/outline-content-management/.env.local`
2. Set `OUTLINE_API_TOKEN` in `.env.local`
3. Keep `OUTLINE_BASE_URL` as default unless your instance URL changes

Optional:

- `OUTLINE_COLLECTION_ID` (default collection for new pages)

## Fast Workflow

1. Validate access:
   - `./.cursor/skills/outline-content-management/outline auth-info`
2. Find target document:
   - `./.cursor/skills/outline-content-management/outline search --query "keyword"`
3. Read document:
   - `./.cursor/skills/outline-content-management/outline read --id <document_id>`
4. Create document:
   - `./.cursor/skills/outline-content-management/outline create --collection-id <id> --title "Title" --file /abs/path/doc.md`
5. Update document:
   - `./.cursor/skills/outline-content-management/outline update --id <document_id> --file /abs/path/doc.md`

## Rules

- Never log or expose `OUTLINE_API_TOKEN`.
- Prefer updating by `document id` to avoid ambiguous title matches.
- Keep markdown as source of truth in repo, then sync to Outline.
- After create/update, read the document once to verify content and title.

## Utility Script

Use the helper script in this skill:

- `.cursor/skills/outline-content-management/outline`
- `.cursor/skills/outline-content-management/scripts/outline_api.py`

For endpoint details and examples, see [reference.md](reference.md).
