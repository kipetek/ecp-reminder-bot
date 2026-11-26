# db.py
import aiosqlite
from datetime import datetime, timedelta

DB_PATH = "clients.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expiry_date TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT DEFAULT 'другое',   -- 'ЭЦП' | 'база' | '1С' | 'другое'
                renewed INTEGER DEFAULT 0,
                UNIQUE(expiry_date, name)
            )
        """)
        await db.commit()


async def add_client(expiry_date: str, name: str, ctype: str = "другое") -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO clients (expiry_date, name, type) VALUES (?, ?, ?)",
                (expiry_date, name, ctype)
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_client(expiry_date: str, name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM clients WHERE expiry_date = ? AND name = ?",
            (expiry_date, name)
        )
        await db.commit()
        return cur.rowcount > 0


async def get_upcoming(days_ahead: int) -> list[tuple[str, str, str]]:
    """(expiry_date, name, type) на today + N дней, где renewed=0."""
    target = (datetime.now().date() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT expiry_date, name, type FROM clients WHERE expiry_date = ? AND renewed = 0",
            (target,)
        )
        return await cur.fetchall()


async def mark_renewed(expiry_date: str, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE clients SET renewed = 1 WHERE expiry_date = ? AND name = ?",
            (expiry_date, name)
        )
        await db.commit()


async def get_all_upcoming(days: int, ctype: str | None = None) -> list[tuple[str, str, str, int]]:
    """(expiry_date, name, type, renewed) в ближайшие N дней, опционально по типу."""
    today = datetime.now().date()
    end = (today + timedelta(days=days)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    sql = "SELECT expiry_date, name, type, renewed FROM clients WHERE expiry_date BETWEEN ? AND ?"
    params: list = [today_str, end]
    if ctype:
        sql += " AND type = ?"
        params.append(ctype)
    sql += " ORDER BY expiry_date, name"

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, params)
        return await cur.fetchall()


async def get_nearest() -> tuple[str, str, str, int] | None:
    """Ближайшая будущая запись (для дайджеста)."""
    today_str = datetime.now().date().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT expiry_date, name, type, renewed FROM clients "
            "WHERE expiry_date >= ? ORDER BY expiry_date LIMIT 1",
            (today_str,)
        )
        return await cur.fetchone()


async def seed_initial_data(clients: list[tuple[str, str, str]]):
    """Заливает стартовый список, если база пустая."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM clients")
        count = (await cur.fetchone())[0]
        if count == 0:
            await db.executemany(
                "INSERT OR IGNORE INTO clients (expiry_date, name, type) VALUES (?, ?, ?)",
                clients
            )
            await db.commit()