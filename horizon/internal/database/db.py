"""
Async database module for Horizon Trading Platform.

Provides async context manager for database connections and migration handling.
"""

import os
import aiosqlite
from contextlib import asynccontextmanager
from typing import Optional


@asynccontextmanager
async def get_db(db_path: str):
    """
    Async context manager yielding aiosqlite connection with row_factory set.

    Args:
        db_path: Path to the SQLite database file.

    Yields:
        aiosqlite.Connection: Database connection with row_factory set to aiosqlite.Row.
    """
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    try:
        yield conn
    finally:
        await conn.close()


async def init_db(db_path: str, migrations_dir: Optional[str] = None) -> aiosqlite.Connection:
    """
    Creates parent dir if needed, opens connection, runs migrations, returns connection.

    Args:
        db_path: Path to the SQLite database file.
        migrations_dir: Directory containing SQL migration files. If None, no migrations run.

    Returns:
        aiosqlite.Connection: Database connection after migrations have been applied.
    """
    # Create parent directory if it doesn't exist
    parent_dir = os.path.dirname(db_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    # Open connection
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row

    try:
        # Run migrations if directory provided
        if migrations_dir:
            await run_migrations(conn, migrations_dir)
        return conn
    except Exception:
        await conn.close()
        raise


async def run_migrations(conn: aiosqlite.Connection, migrations_dir: str):
    """
    Reads .sql files in lexicographic order, splits on ';', executes each via executescript().

    Args:
        conn: Database connection to execute migrations on.
        migrations_dir: Directory containing SQL migration files.
    """
    # Get all .sql files sorted lexicographically
    sql_files = sorted(
        f for f in os.listdir(migrations_dir)
        if f.endswith('.sql')
    )

    for sql_file in sql_files:
        file_path = os.path.join(migrations_dir, sql_file)
        with open(file_path, 'r', encoding='utf-8') as f:
            sql_content = f.read()

        # Split on ';' and execute each statement
        statements = [s.strip() for s in sql_content.split(';') if s.strip()]
        for statement in statements:
            await conn.execute(statement)

        await conn.commit()


async def close_db(conn: aiosqlite.Connection):
    """
    Closes the database connection.

    Args:
        conn: Database connection to close.
    """
    await conn.close()