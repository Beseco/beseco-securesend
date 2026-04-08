#!/usr/bin/env python3
"""
Memory MCP Server für beseco_sicheres_senden
Speichert projektbezogenes Wissen in einer JSON-Datei.
"""

import json
import sys
import datetime
from pathlib import Path

MEMORY_FILE = Path(__file__).parent / "memories.json"


def load_memories() -> dict:
    if MEMORY_FILE.exists():
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_memories(memories: dict) -> None:
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memories, f, ensure_ascii=False, indent=2)


def send_response(response: dict) -> None:
    line = json.dumps(response, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def send_error(id_, code: int, message: str) -> None:
    send_response({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def handle_remember(args: dict) -> dict:
    memories = load_memories()
    key = args.get("key", "").strip()
    value = args.get("value", "").strip()
    category = args.get("category", "allgemein").strip()

    if not key or not value:
        return {"error": "key und value sind erforderlich"}

    memories[key] = {
        "value": value,
        "category": category,
        "updated_at": datetime.datetime.now().isoformat(),
    }
    save_memories(memories)
    return {"message": f"Gespeichert: '{key}' in Kategorie '{category}'"}


def handle_recall(args: dict) -> dict:
    memories = load_memories()
    query = args.get("query", "").lower().strip()
    category = args.get("category", "").strip()

    results = {}
    for key, data in memories.items():
        cat_match = not category or data.get("category", "") == category
        text_match = not query or query in key.lower() or query in data.get("value", "").lower()
        if cat_match and text_match:
            results[key] = data

    if not results:
        return {"message": "Keine passenden Einträge gefunden.", "results": {}}
    return {"results": results, "count": len(results)}


def handle_forget(args: dict) -> dict:
    memories = load_memories()
    key = args.get("key", "").strip()

    if key not in memories:
        return {"error": f"Eintrag '{key}' nicht gefunden"}

    del memories[key]
    save_memories(memories)
    return {"message": f"Eintrag '{key}' gelöscht"}


def handle_list_memories(args: dict) -> dict:
    memories = load_memories()
    category = args.get("category", "").strip()

    if category:
        filtered = {k: v for k, v in memories.items() if v.get("category") == category}
    else:
        filtered = memories

    # Gruppiert nach Kategorie für bessere Lesbarkeit
    by_category: dict = {}
    for key, data in filtered.items():
        cat = data.get("category", "allgemein")
        by_category.setdefault(cat, {})[key] = data.get("value", "")

    return {"total": len(filtered), "by_category": by_category}


TOOLS = [
    {
        "name": "remember",
        "description": "Speichert eine projektbezogene Information dauerhaft.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Eindeutiger Name/Schlüssel für die Information"},
                "value": {"type": "string", "description": "Der zu speichernde Inhalt"},
                "category": {
                    "type": "string",
                    "description": "Kategorie, z.B. 'architektur', 'entscheidungen', 'bugs', 'todo', 'allgemein'",
                    "default": "allgemein",
                },
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "recall",
        "description": "Sucht und gibt gespeicherte Informationen zurück.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriff (optional)"},
                "category": {"type": "string", "description": "Auf diese Kategorie filtern (optional)"},
            },
        },
    },
    {
        "name": "forget",
        "description": "Löscht einen gespeicherten Eintrag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Schlüssel des zu löschenden Eintrags"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "list_memories",
        "description": "Listet alle gespeicherten Einträge, optional gefiltert nach Kategorie.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Kategorie-Filter (optional)"},
            },
        },
    },
]

TOOL_HANDLERS = {
    "remember": handle_remember,
    "recall": handle_recall,
    "forget": handle_forget,
    "list_memories": handle_list_memories,
}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        if method == "initialize":
            send_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "beseco-memory", "version": "1.0.0"},
                },
            })

        elif method == "notifications/initialized":
            pass  # Keine Antwort nötig

        elif method == "tools/list":
            send_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS},
            })

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            handler = TOOL_HANDLERS.get(tool_name)

            if not handler:
                send_error(req_id, -32601, f"Unbekanntes Tool: {tool_name}")
                continue

            try:
                result = handler(tool_args)
                send_response({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
                    },
                })
            except Exception as e:
                send_error(req_id, -32603, str(e))

        elif method == "ping":
            send_response({"jsonrpc": "2.0", "id": req_id, "result": {}})

        else:
            send_error(req_id, -32601, f"Unbekannte Methode: {method}")


if __name__ == "__main__":
    main()
