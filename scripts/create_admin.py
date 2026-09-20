#!/usr/bin/env python3
"""
Create the first admin/user account interactively.

Usage:
    cd /path/to/backend
    python scripts/create_admin.py
"""

import asyncio
import sys
import os

# Allow running from the backend/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from app.core.database import AsyncSessionLocal, init_db
from app.core.security import get_password_hash
from app.models.user import User


async def create_user():
    print("=== WOI AutoTrader — Create User ===\n")

    name = input("Full name: ").strip()
    if not name:
        print("Name cannot be empty.")
        sys.exit(1)

    email = input("Email: ").strip().lower()
    if not email or "@" not in email:
        print("Invalid email address.")
        sys.exit(1)

    import getpass
    password = getpass.getpass("Password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("Passwords do not match.")
        sys.exit(1)

    await init_db()

    async with AsyncSessionLocal() as db:
        # Check for existing user
        result = await db.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none():
            print(f"\nUser with email '{email}' already exists.")
            sys.exit(1)

        user = User(
            email=email,
            password_hash=get_password_hash(password),
            name=name,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    print(f"\nUser created successfully!")
    print(f"  ID    : {user.id}")
    print(f"  Name  : {user.name}")
    print(f"  Email : {user.email}")
    print(f"\nYou can now log in via POST /api/v1/auth/login")


if __name__ == "__main__":
    asyncio.run(create_user())
