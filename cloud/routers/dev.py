"""
routers/dev.py — Dev-only endpoints for debugging and database inspection.
Only active when DEV_MODE=true environment variable is set.
"""

import os
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db

router = APIRouter(prefix="/dev", tags=["dev"])


def require_dev_mode():
    """Only allow if DEV_MODE is enabled."""
    if not os.getenv("DEV_MODE", "").lower() == "true":
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Dev mode not enabled")
    return True


@router.get("/db/tables")
async def list_tables(
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_dev_mode),
):
    """List all tables in the database."""
    result = await db.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
    )
    tables = [row[0] for row in result.fetchall()]
    return {"tables": tables}


@router.get("/db/table/{table_name}")
async def inspect_table(
    table_name: str,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_dev_mode),
):
    """Inspect a table (rows, columns)."""
    # Get columns
    cols_result = await db.execute(
        text(f"""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = :table_name
    """),
        {"table_name": table_name},
    )
    columns = [{"name": r[0], "type": r[1]} for r in cols_result.fetchall()]

    # Get rows
    try:
        rows_result = await db.execute(
            text(f"SELECT * FROM {table_name} LIMIT :limit OFFSET :offset"),
            {"limit": limit, "offset": offset},
        )
        rows = [dict(zip(rows_result.keys(), row)) for row in rows_result.fetchall()]
    except Exception as e:
        rows = []
        error = str(e)

    return {
        "table": table_name,
        "columns": columns,
        "rows": rows,
        "count": len(rows),
        "error": error if "rows" in locals() and not "rows" else None,
    }


@router.get("/db/query")
async def execute_query(
    q: str,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_dev_mode),
):
    """Execute a raw SQL query (SELECT only)."""
    if any(kw in q.lower() for kw in ["drop", "delete", "update", "insert", "alter"]):
        return {"error": "Only SELECT queries allowed"}

    try:
        result = await db.execute(text(q))
        columns = result.keys()
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
        return {"columns": list(columns), "rows": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


@router.get("/org/{org_id}/settings")
async def org_settings(
    org_id: int,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_dev_mode),
):
    """Get organization settings JSON."""
    result = await db.execute(
        text("SELECT id, name, slug, settings_json FROM organizations WHERE id = :id"),
        {"id": org_id},
    )
    row = result.fetchone()
    if not row:
        return {"error": "Organization not found"}
    return {"id": row[0], "name": row[1], "slug": row[2], "settings_json": row[3]}


@router.get("/env")
async def show_env(
    _: bool = Depends(require_dev_mode),
):
    """Show environment variables (masked)."""
    import os

    public_vars = ["DATABASE_URL", "PUBLIC_BASE_URL", "SECRET_KEY", "DEV_MODE"]
    return {
        k: (
            os.getenv(k, "")[:10] + "..."
            if len(os.getenv(k, "")) > 10
            else os.getenv(k, "")
        )
        if os.getenv(k)
        else None
        for k in public_vars
    }
