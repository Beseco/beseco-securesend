#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, request


DEFAULT_BASE_URL = "https://outline.cloud-fs.de/api"


def load_local_env() -> None:
    env_file = Path(__file__).resolve().parents[1] / ".env.local"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def call_api(endpoint: str, payload: dict) -> dict:
    base_url = os.environ.get("OUTLINE_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    if not base_url.endswith("/api"):
        base_url = f"{base_url}/api"
    token = env("OUTLINE_API_TOKEN")
    url = f"{base_url}/{endpoint.lstrip('/')}"
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} calling {endpoint}: {details}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Network error calling {endpoint}: {exc}") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from {endpoint}: {body}") from exc

    if parsed.get("ok") is False:
        raise RuntimeError(f"Outline API returned ok=false for {endpoint}: {parsed}")
    return parsed


def read_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"File does not exist: {path}")
    return p.read_text(encoding="utf-8")


def cmd_auth_info(_args: argparse.Namespace) -> dict:
    return call_api("auth.info", {})


def cmd_collections(_args: argparse.Namespace) -> dict:
    return call_api("collections.list", {})


def cmd_search(args: argparse.Namespace) -> dict:
    payload = {"query": args.query, "limit": args.limit}
    if args.collection_id:
        payload["collectionId"] = args.collection_id
    return call_api("documents.search", payload)


def cmd_read(args: argparse.Namespace) -> dict:
    return call_api("documents.info", {"id": args.id})


def cmd_create(args: argparse.Namespace) -> dict:
    collection_id = args.collection_id or os.environ.get("OUTLINE_COLLECTION_ID", "").strip()
    if not collection_id:
        raise RuntimeError("Missing collection id. Use --collection-id or OUTLINE_COLLECTION_ID.")
    text = read_text(args.file)
    payload = {
        "collectionId": collection_id,
        "title": args.title,
        "text": text,
        "publish": bool(args.publish),
    }
    return call_api("documents.create", payload)


def cmd_update(args: argparse.Namespace) -> dict:
    text = read_text(args.file)
    payload = {"id": args.id, "text": text, "publish": bool(args.publish)}
    if args.title:
        payload["title"] = args.title
    return call_api("documents.update", payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Outline API helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("auth-info", help="Validate token and workspace access")
    p.set_defaults(func=cmd_auth_info)

    p = sub.add_parser("collections", help="List collections")
    p.set_defaults(func=cmd_collections)

    p = sub.add_parser("search", help="Search documents")
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--collection-id")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("read", help="Read document by id")
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("create", help="Create document from markdown file")
    p.add_argument("--collection-id")
    p.add_argument("--title", required=True)
    p.add_argument("--file", required=True, help="Absolute path to markdown file")
    p.add_argument("--publish", action="store_true")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("update", help="Update existing document from markdown file")
    p.add_argument("--id", required=True)
    p.add_argument("--title")
    p.add_argument("--file", required=True, help="Absolute path to markdown file")
    p.add_argument("--publish", action="store_true")
    p.set_defaults(func=cmd_update)

    return parser


def main() -> int:
    load_local_env()
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.func(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
