import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiohttp


DB_PATH = Path(__file__).resolve().parent / "crypto_history.db"
API_URL = "https://min-api.cryptocompare.com/data/v2/histoday"

ASSETS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "ripple": "XRP",
    "pax-gold": "PAXG",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def prepare_database():
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crypto_prices (
                asset_id TEXT NOT NULL,
                price_date TEXT NOT NULL,
                price REAL NOT NULL,
                PRIMARY KEY (asset_id, price_date)
            )
        """)


def save_batch(asset_id, rows):
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.executemany("""
            INSERT INTO crypto_prices (asset_id, price_date, price)
            VALUES (?, ?, ?)
            ON CONFLICT(asset_id, price_date)
            DO UPDATE SET price = excluded.price
        """, [
            (asset_id, day, price)
            for day, price in rows
            if price > 0
        ])


async def fetch_batch(session, symbol, end_timestamp):
    params = {
        "fsym": symbol,
        "tsym": "USD",
        "limit": 2000,
        "toTs": end_timestamp,
    }

    for attempt in range(3):
        try:
            async with session.get(
                API_URL,
                params=params,
            ) as response:
                response.raise_for_status()
                result = await response.json()

            if result.get("Response") != "Success":
                message = result.get("Message", "Unknown API error")
                raise RuntimeError(message)

            return result.get("Data", {}).get("Data", [])

        except Exception:
            if attempt == 2:
                raise

            await asyncio.sleep(2 * (attempt + 1))


async def import_asset(session, asset_id, symbol):
    logger.info("شروع دریافت تاریخچه %s", symbol)

    end_timestamp = int(datetime.now(timezone.utc).timestamp())
    earliest_seen = None
    total_saved = 0

    while True:
        candles = await fetch_batch(
            session,
            symbol,
            end_timestamp,
        )

        if not candles:
            break

        rows = []

        for candle in candles:
            close_price = candle.get("close", 0)
            timestamp = candle.get("time")

            if not timestamp or not close_price or close_price <= 0:
                continue

            day = datetime.fromtimestamp(
                timestamp,
                timezone.utc,
            ).date().isoformat()

            rows.append((day, float(close_price)))

        if not rows:
            break

        rows.sort(key=lambda item: item[0])
        oldest_day = rows[0][0]

        if earliest_seen is not None and oldest_day >= earliest_seen:
            logger.warning(
                "تاریخ قدیمی‌تری برای %s دریافت نشد؛ توقف.",
                symbol,
            )
            break

        save_batch(asset_id, rows)
        total_saved += len(rows)

        logger.info(
            "%s | تا تاریخ %s | %s رکورد دریافت شد",
            symbol,
            oldest_day,
            total_saved,
        )

        earliest_seen = oldest_day

        oldest_timestamp = min(
            int(candle["time"])
            for candle in candles
            if candle.get("time") is not None
        )

        # رفتن به روز قبل از قدیمی‌ترین رکورد این دسته
        end_timestamp = oldest_timestamp - 1

        # رعایت فاصله بین درخواست‌ها
        await asyncio.sleep(1.2)

    logger.info(
        "پایان %s؛ مجموع رکوردهای پردازش‌شده: %s",
        symbol,
        total_saved,
    )


async def main():
    prepare_database()

    timeout = aiohttp.ClientTimeout(total=40, connect=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:

        for asset_id, symbol in ASSETS.items():
            try:
                await import_asset(session, asset_id, symbol)
            except Exception:
                logger.exception(
                    "دریافت تاریخچه %s ناموفق بود. "
                    "بعداً می‌توان دوباره اجرا کرد.",
                    symbol,
                )

            await asyncio.sleep(2)

    logger.info("پایان کار. فایل دیتابیس: %s", DB_PATH)


if __name__ == "__main__":
    asyncio.run(main())