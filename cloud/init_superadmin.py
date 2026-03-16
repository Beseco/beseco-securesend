"""
cloud/init_superadmin.py — Ersten Superadmin-User anlegen.

Verwendung:
    cd cloud
    python3 init_superadmin.py
    python3 init_superadmin.py --email admin@example.com --password MeinPasswort123

Der Script legt den User in der konfigurierten Datenbank an (aus .env / DATABASE_URL).
Bereits vorhandene Superadmins werden übersprungen (idempotent).
"""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

# Projekt-Root für core.* imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from database import engine, Base
from models.user import User, UserRole
from models import *  # noqa — populate Base.metadata
from dependencies import hash_password

# Async-Session ohne FastAPI-Dependency-Injection
from sqlalchemy.ext.asyncio import AsyncSession


async def create_superadmin(email: str, password: str):
    # Tabellen anlegen falls noch nicht vorhanden
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        # Prüfen ob bereits ein Superadmin existiert
        result = await session.execute(
            select(User).where(User.email == email)
        )
        existing = result.scalar_one_or_none()

        if existing:
            if existing.role == UserRole.superadmin:
                print(f"✓ Superadmin '{email}' existiert bereits — nichts geändert.")
            else:
                existing.role = UserRole.superadmin
                existing.password_hash = hash_password(password)
                await session.commit()
                print(f"✓ Bestehender User '{email}' auf superadmin hochgestuft.")
            return

        user_id = str(uuid.uuid4())
        user = User(
            id=user_id,
            org_id=None,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.superadmin,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        print(f"✓ Superadmin angelegt:")
        print(f"  E-Mail:   {email}")
        print(f"  Passwort: {password}")
        print(f"  ID:       {user_id}")
        print()
        print("→ Login unter: http://localhost:8000/ui/login")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ersten Superadmin anlegen")
    parser.add_argument("--email",    default="admin@securesend.local")
    parser.add_argument("--password", default="Admin1234!")
    args = parser.parse_args()

    asyncio.run(create_superadmin(args.email, args.password))
