import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "crypto_history.db"


def save_price(asset_id: str, date_str: str, price: float) -> None:
    """ذخیره قیمت تاریخی در آرشیو محلی."""
    if price is None or price <= 0:
        return

    try:
        day = date.fromisoformat(date_str).isoformat()
    except (TypeError, ValueError):
        return

    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crypto_prices (
                asset_id TEXT NOT NULL,
                price_date TEXT NOT NULL,
                price REAL NOT NULL,
                PRIMARY KEY (asset_id, price_date)
            )
        """)
        conn.execute("""
            INSERT INTO crypto_prices (asset_id, price_date, price)
            VALUES (?, ?, ?)
            ON CONFLICT(asset_id, price_date)
            DO UPDATE SET price = excluded.price
        """, (asset_id, day, float(price)))


def get_nearest_price(asset_id: str, date_str: str):
    """قیمت ذخیره‌شده در نزدیک‌ترین تاریخ موجود؛ در نبود داده None."""
    try:
        target = date.fromisoformat(date_str).isoformat()
    except (TypeError, ValueError):
        return None

    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crypto_prices (
                asset_id TEXT NOT NULL,
                price_date TEXT NOT NULL,
                price REAL NOT NULL,
                PRIMARY KEY (asset_id, price_date)
            )
        """)
        row = conn.execute("""
            SELECT price_date, price
            FROM crypto_prices
            WHERE asset_id = ?
            ORDER BY ABS(julianday(price_date) - julianday(?)) ASC,
                     CASE WHEN price_date <= ? THEN 0 ELSE 1 END,
                     price_date DESC
            LIMIT 1
        """, (asset_id, target, target)).fetchone()

    if row:
        return row[1], row[0]

    return None