import asyncio
import io
import logging
import time
from datetime import datetime, timezone

import aiohttp
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


logger = logging.getLogger(__name__)

# Iran Market
IRAN_MARKET_POPULAR_URL = (
    "https://raw.githubusercontent.com/iran-market/"
    "iran-market.github.io/main/data/popular.json"
)
IRAN_MARKET_USD_HISTORY_URL = (
    "https://raw.githubusercontent.com/iran-market/"
    "iran-market.github.io/main/data/history/USD_IRR_FREE.json"
)

# منابع عمومی قیمت رمزارز
BINANCE_BASE_URLS = (
    "https://api.binance.com",
    "https://data-api.binance.vision",
)
CRYPTOCOMPARE_URL = (
    "https://min-api.cryptocompare.com/data/pricehistorical"
)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=7)
CACHE_SECONDS = 300
LIVE_CACHE_SECONDS = 60

# فقط دارایی‌های مورد تأیید
BINANCE_SYMBOLS = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "tether": None,
    "ripple": "XRPUSDT",
    "pax-gold": "PAXGUSDT",
}

# نمادهای منبع جایگزین؛ از روی نام نماد Binance حدس زده نمی‌شوند.
CRYPTOCOMPARE_SYMBOLS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "ripple": "XRP",
    "pax-gold": "PAXG",
}

_http_session = None
_cache = {}
_cache_lock = asyncio.Lock()


async def _get_session():
    """استفادهٔ مجدد از نشست HTTP برای کاهش زمان اتصال."""
    global _http_session

    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(
            timeout=REQUEST_TIMEOUT
        )

    return _http_session


async def close_http_session():
    """بستن نشست مشترک هنگام خاموش‌شدن ربات."""
    global _http_session

    if _http_session is not None and not _http_session.closed:
        await _http_session.close()

    _http_session = None


async def _get_json(url, params=None):
    """دریافت JSON همراه با کنترل خطا و timeout."""
    session = await _get_session()

    async with session.get(url, params=params) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


async def _cached_json(key, url, params=None, ttl=CACHE_SECONDS):
    """کش ساده برای کاهش درخواست‌های تکراری."""
    now = time.monotonic()

    async with _cache_lock:
        cached = _cache.get(key)

        if cached and now - cached["time"] < ttl:
            return cached["data"]

    data = await _get_json(url, params=params)

    async with _cache_lock:
        _cache[key] = {
            "time": time.monotonic(),
            "data": data,
        }

    return data


def _number(value):
    """تبدیل امن مقدار به عدد مثبت."""
    try:
        result = float(value)

        if result > 0:
            return result

    except (TypeError, ValueError):
        pass

    return 0.0


def _normalize_date(date_text):
    """
    تبدیل تاریخ میلادی به نیمه‌شب UTC.
    فرمت‌های قابل‌قبول:
    DD-MM-YYYY ، YYYY-MM-DD ، YYYY/MM/DD
    """
    if not date_text:
        raise ValueError("تاریخ میلادی وارد نشده است.")

    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(
                date_text, fmt
            ).replace(tzinfo=timezone.utc)

        except ValueError:
            continue

    raise ValueError(
        "فرمت تاریخ میلادی نامعتبر است. "
        "از DD-MM-YYYY یا YYYY-MM-DD استفاده کن."
    )


def _normalize_jalali_date(date_text):
    """یکسان‌سازی تاریخ شمسی به شکل YYYY/MM/DD."""
    if not date_text:
        return ""

    parts = (
        str(date_text)
        .strip()
        .replace("-", "/")
        .replace(".", "/")
        .split("/")
    )

    if len(parts) != 3:
        return ""

    try:
        year, month, day = (int(part) for part in parts)

        return f"{year:04d}/{month:02d}/{day:02d}"

    except (TypeError, ValueError):
        return ""


async def _iran_market_data():
    return await _cached_json(
        "iran_market_popular",
        IRAN_MARKET_POPULAR_URL,
        ttl=LIVE_CACHE_SECONDS,
    )


def _extract_iran_market_price(data, symbol):
    """استخراج قیمت و تبدیل ریال به تومان در صورت نیاز."""
    for item in data.get("data", []):
        if item.get("symbol") != symbol:
            continue

        price = _number(item.get("price"))

        if price <= 0:
            return 0.0

        unit = str(
            data.get("meta", {}).get("unit", "")
        ).strip().lower()

        if unit == "toman":
            return price

        if unit == "irr":
            return price / 10

        logger.error(
            "واحد قیمت %s مشخص نیست: %s",
            symbol,
            unit,
        )
        return 0.0

    return 0.0


async def fetch_live_toman_rate():
    """نرخ فعلی دلار آزاد به تومان."""
    try:
        data = await _iran_market_data()

        return _extract_iran_market_price(
            data,
            "USD_IRR_FREE",
        )

    except Exception:
        logger.exception(
            "دریافت نرخ فعلی دلار ناموفق بود"
        )
        return 0.0


async def fetch_live_usdt_toman_rate():
    """
    نرخ فعلی تتر به تومان برای تبدیل ارزش دارایی‌ها.
    قیمت خود تتر در محاسبات دلاری برابر 1 USDT فرض می‌شود.
    """
    try:
        data = await _iran_market_data()

        return _extract_iran_market_price(
            data,
            "USDT_IRR",
        )

    except Exception:
        logger.exception(
            "دریافت نرخ فعلی تتر ناموفق بود"
        )
        return 0.0


async def fetch_historical_toman_rate(
    shamsi_date_str,
    year=None,
):
    """
    نرخ تاریخی دلار آزاد به تومان در تاریخ شمسی انتخاب‌شده.

    هیچ نرخ ثابت، سالانه یا قیمت روز دیگری جایگزین نمی‌شود.
    """
    target_date = _normalize_jalali_date(
        shamsi_date_str
    )

    if not target_date:
        raise ValueError("فرمت تاریخ شمسی نامعتبر است.")

    try:
        data = await _cached_json(
            "iran_market_usd_history",
            IRAN_MARKET_USD_HISTORY_URL,
            ttl=3600,
        )

    except Exception as exc:
        logger.exception(
            "دریافت فایل تاریخچه دلار ناموفق بود"
        )

        raise ValueError(
            "دریافت تاریخچه دلار ناموفق بود؛ "
            "لطفاً بعداً دوباره امتحان کن."
        ) from exc

    items = (
        data.get("data", [])
        if isinstance(data, dict)
        else []
    )

    for item in items:
        item_date = _normalize_jalali_date(
            item.get("date_jalali", "")
        )

        if item_date != target_date:
            continue

        price = _number(item.get("close"))

        if price <= 0:
            price = _number(item.get("open"))

        if price <= 0:
            raise ValueError(
                f"برای تاریخ {target_date} "
                "قیمت معتبر دلار ثبت نشده است."
            )

        # این سری در فایل مورد استفاده با واحد تومان است.
        return price

    first_date = (
        _normalize_jalali_date(
            items[0].get("date_jalali", "")
        )
        if items
        else "نامشخص"
    )

    last_date = (
        _normalize_jalali_date(
            items[-1].get("date_jalali", "")
        )
        if items
        else "نامشخص"
    )

    raise ValueError(
        f"نرخ تاریخی دلار برای {target_date} "
        "در فایل موجود نیست. "
        f"بازهٔ ثبت‌شده در فایل: {first_date} تا {last_date}. "
        "برای جلوگیری از گزارش اشتباه، نرخ روز دیگری جایگزین نشد."
    )


async def _binance_json(path, params):
    """تلاش روی دو میزبان عمومی Binance."""
    last_error = None

    for base_url in BINANCE_BASE_URLS:
        try:
            return await _get_json(
                f"{base_url}{path}",
                params=params,
            )

        except Exception as exc:
            last_error = exc

            logger.warning(
                "درخواست Binance به %s ناموفق بود: %s",
                base_url,
                exc,
            )

    raise RuntimeError(
        "دریافت داده از میزبان‌های عمومی Binance ناموفق بود."
    ) from last_error


async def _fetch_binance_historical(
    symbol,
    day,
):
    """دریافت قیمت بسته‌شدن کندل روزانهٔ UTC."""
    start_ms = int(day.timestamp() * 1000)
    end_ms = start_ms + 86_400_000 - 1

    params = {

"symbol": symbol,
        "interval": "1d",
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1,
    }

    cache_key = (
        f"binance_kline_{symbol}_"
        f"{day.strftime('%Y-%m-%d')}"
    )

    # تلاش مستقیم از میزبان اصلی با کش.
    try:
        data = await _cached_json(
            cache_key,
            f"{BINANCE_BASE_URLS[0]}/api/v3/klines",
            params=params,
            ttl=3600,
        )

    except Exception:
        # در صورت مشکل میزبان اول، میزبان دادهٔ عمومی امتحان می‌شود.
        data = await _binance_json(
            "/api/v3/klines",
            params=params,
        )

    if not isinstance(data, list) or not data:
        return 0.0

    try:
        candle_start_ms = int(data[0][0])
        close_price = _number(data[0][4])

    except (IndexError, TypeError, ValueError):
        return 0.0

    # مطمئن می‌شویم کندل متعلق به همان روز UTC است.
    if candle_start_ms != start_ms:
        return 0.0

    return close_price


async def _fetch_cryptocompare_historical(
    asset_id,
    day,
):
    """
    منبع جایگزین برای تاریخچه.
    CryptoCompare ممکن است پوشش تاریخی متفاوتی داشته باشد؛
    فقط اگر قیمت معتبر برگرداند، پذیرفته می‌شود.
    """
    symbol = CRYPTOCOMPARE_SYMBOLS.get(asset_id)

    if not symbol:
        return 0.0

    # درخواست برای انتهای روز UTC، نه ابتدای روز.
    timestamp = int(day.timestamp()) + 86_400 - 1

    try:
        data = await _cached_json(
            (
                f"cryptocompare_{symbol}_"
                f"{day.strftime('%Y-%m-%d')}"
            ),
            CRYPTOCOMPARE_URL,
            params={
                "fsym": symbol,
                "tsyms": "USD",
                "ts": timestamp,
            },
            ttl=3600,
        )

        entry = data.get(symbol, {})
        price_usd = _number(entry.get("USD"))

        # در این مدل، یک دلار تقریباً برابر یک USDT فرض می‌شود.
        return price_usd

    except Exception:
        logger.warning(
            "قیمت تاریخی %s از CryptoCompare دریافت نشد",
            asset_id,
            exc_info=True,
        )
        return 0.0

async def _fetch_coingecko_historical(asset_id, day):
    """دریافت قیمت تاریخی از CoinGecko."""
    url = (
        f"https://api.coingecko.com/api/v3/coins/"
        f"{asset_id}/history"
    )

    params = {
        "date": day.strftime("%d-%m-%Y"),
        "localization": "false",
    }

    try:
        data = await _get_json(url, params)

        price = (
            data.get("market_data", {})
            .get("current_price", {})
            .get("usd")
        )

        if price is not None and float(price) > 0:
            return float(price)

    except Exception:
        logger.warning(
            "قیمت تاریخی %s از CoinGecko دریافت نشد",
            asset_id,
            exc_info=True,
        )

    return 0.0

async def _fetch_arzdigital_historical(asset_id, day):
    """خواندن تاریخچه قابل‌استخراج از صفحه ارزدیجیتال."""
    import re
    from html.parser import HTMLParser
    from datetime import datetime
    from history_archive import save_price

    slugs = {
        "bitcoin": "bitcoin",
        "ethereum": "ethereum",
        "ripple": "xrp",
        "pax-gold": "pax-gold",
    }

    slug = slugs.get(asset_id)
    if not slug:
        return 0.0

    url = f"https://arzdigital.com/coins/{slug}/historical-data/"

    class TextParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    try:
        session = await _get_session()

        async with session.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as response:
            response.raise_for_status()
            html = await response.text()

        parser = TextParser()
        parser.feed(html)
        text = " ".join(parser.parts)

        pattern = (
            r"\b([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})"
            r"\s+\$([\d,]+(?:\.\d+)?)"
        )

        records = {}

        for date_text, price_text in re.findall(pattern, text):
            try:
                actual_day = datetime.strptime(
                    date_text, "%b %d, %Y"
                ).date().isoformat()

                price = float(price_text.replace(",", ""))

                if price > 0:
                    records[actual_day] = price

            except (ValueError, TypeError):
                continue

        # تمام رکوردهای استخراج‌شده را با تاریخ واقعی ذخیره کن.
        for actual_day, price in records.items():
            save_price(asset_id, actual_day, price)

        requested_day = day.date().isoformat()
        return records.get(requested_day, 0.0)

    except Exception:
        logger.warning(
            "دریافت تاریخچه %s از ارزدیجیتال ناموفق بود",
            asset_id,
            exc_info=True,
        )
        return 0.0

async def fetch_historical_crypto_price(asset_id, date_str):
    from history_archive import save_price, get_nearest_price

    day = _normalize_date(date_str)
    day_str = day.strftime("%Y-%m-%d")

    if asset_id == "tether":
        save_price(asset_id, day_str, 1.0)
        return 1.0

    symbol = BINANCE_SYMBOLS.get(asset_id)

    # 1. Binance
    if symbol:
        try:
            price = await _fetch_binance_historical(symbol, day)
            if price and price > 0:
                save_price(asset_id, day_str, price)
                return float(price)
        except Exception:
            logger.warning("Binance failed for %s", asset_id, exc_info=True)

    # 2. CryptoCompare
    try:
        price = await _fetch_cryptocompare_historical(asset_id, day)
        if price and price > 0:
            save_price(asset_id, day_str, price)
            return float(price)
    except Exception:
        logger.warning("CryptoCompare failed for %s", asset_id, exc_info=True)

    # 3. CoinGecko
    try:
        price = await _fetch_coingecko_historical(asset_id, day)
        if price and price > 0:
            save_price(asset_id, day_str, price)
            return float(price)
    except Exception:
        logger.warning("CoinGecko failed for %s", asset_id, exc_info=True)

    # 4. ArzDigital
    try:
        price = await _fetch_arzdigital_historical(asset_id, day)

        if price and price > 0:
            return float(price)

    except Exception:
        logger.warning(
            "ArzDigital failed for %s",
            asset_id,
            exc_info=True,
        )    

    # 5. آرشیو محلی
    archived = get_nearest_price(asset_id, day_str)

    if archived:
        archived_price, archived_date = archived
        logger.warning(
            "Using archived price for %s: requested=%s, actual=%s",
            asset_id, day_str, archived_date,
        )
        return float(archived_price)

    raise ValueError(
        f"قیمت تاریخی {asset_id.upper()} برای {day_str} "
        "در منابع آنلاین یا آرشیو پیدا نشد."
    )


async def _fetch_binance_current(symbol):
    """قیمت فعلی جفت‌ارز برحسب USDT."""
    params = {"symbol": symbol}

    try:
        data = await _cached_json(
            f"binance_ticker_{symbol}",
            f"{BINANCE_BASE_URLS[0]}/api/v3/ticker/price",
            params=params,
            ttl=LIVE_CACHE_SECONDS,
        )

    except Exception:
        data = await _binance_json(
            "/api/v3/ticker/price",
            params=params,
        )

    return _number(data.get("price"))


async def fetch_current_prices(asset_id):
    """
    خروجی:
    (قیمت فعلی دارایی به USDT، نرخ فعلی تتر به تومان).

    برای تتر، قیمت دلاری همیشه 1.0 است.
    """
    toman_rate = await fetch_live_usdt_toman_rate()

    if toman_rate <= 0:
        return (0.0, 0.0)

    if asset_id == "tether":
        return (1.0, toman_rate)

    symbol = BINANCE_SYMBOLS.get(asset_id)

    if not symbol:
        logger.error(
            "نماد قیمت فعلی برای %s تعریف نشده است",
            asset_id,
        )
        return (0.0, toman_rate)

    try:
        price_usdt = await _fetch_binance_current(
            symbol
        )

    except Exception:
        logger.exception(
            "دریافت قیمت فعلی %s ناموفق بود",
            asset_id,
        )
        price_usdt = 0.0

    return (price_usdt, toman_rate)


def generate_profit_chart(*args, **kwargs):
    """
    ساخت نمودار حرفه‌ای مقایسه سرمایه اولیه و ارزش فعلی.
    خروجی: فایل PNG در حافظه.
    """
    import matplotlib.ticker as mticker
    import numpy as np

    labels = kwargs.get("labels")
    values = kwargs.get("values")
    title = kwargs.get("title", "Investment Performance Report")

    if values is None:
        numeric_args = [
            item for item in args
            if isinstance(item, (int, float))
        ]
        values = numeric_args[-2:] if len(numeric_args) >= 2 else [0, 0]

    try:
        values = [float(value) for value in values]
    except (TypeError, ValueError):
        logger.error("مقادیر نمودار نامعتبر هستند")
        return None

    if len(values) != 2:
        logger.error("نمودار به دو مقدار نیاز دارد")
        return None

    if labels is None or len(labels) != 2:
        labels = ["Initial Investment", "Current Value"]

    initial_value, current_value = values
    difference = current_value - initial_value
    roi = (
        (difference / initial_value) * 100
        if initial_value > 0 else 0
    )

    buffer = io.BytesIO()
    fig = None

    try:
        plt.rcParams["font.family"] = "DejaVu Sans"

        fig, ax = plt.subplots(figsize=(11, 6.5))
        fig.patch.set_facecolor("#101827")
        ax.set_facecolor("#101827")

        positions = np.arange(2)
        bar_colors = ["#64748b", "#22c55e" if difference >= 0 else "#ef4444"]

        bars = ax.bar(
            positions,
            values,
            width=0.48,
            color=bar_colors,
            edgecolor="#e2e8f0",
            linewidth=0.7,
            zorder=3,
        )

        ax.set_xticks(positions)
        ax.set_xticklabels(
            labels,
            color="#e2e8f0",
            fontsize=11,
            fontweight="bold",
        )

        max_value = max(max(values), 1)
        ax.set_ylim(0, max_value * 1.28)

        def format_toman(value, _position=None):
            value = abs(value)
            if value >= 1_000_000_000:
                return f"{value / 1_000_000_000:.1f}B"
            if value >= 1_000_000:
                return f"{value / 1_000_000:.1f}M"
            if value >= 1_000:
                return f"{value / 1_000:.1f}K"
            return f"{value:.0f}"

        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(format_toman)
        )

        ax.tick_params(axis="y", colors="#cbd5e1", labelsize=9)
        ax.tick_params(axis="x", length=0, pad=12)

        ax.grid(
            axis="y",
            color="#334155",
            linestyle="--",
            linewidth=0.8,
            alpha=0.75,
            zorder=0,
        )

        for spine in ax.spines.values():
            spine.set_visible(False)

        for bar, value in zip(bars, values):
            ax.annotate(
                f"{value:,.0f} Toman",
                xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 9),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color="#f8fafc",
                fontsize=9,
                fontweight="bold",
            )

        result_color = "#22c55e" if difference >= 0 else "#ef4444"
        result_sign = "+" if difference > 0 else ""

        fig.suptitle(
            str(title),
            color="#f8fafc",
            fontsize=17,
            fontweight="bold",
            y=0.97,
        )

        fig.text(
            0.5,
            0.87,
            f"{'PROFIT' if difference >= 0 else 'LOSS'}: "
            f"{result_sign}{difference:,.0f} Toman"
            f"    |    ROI: {roi:+.2f}%",
            ha="center",
            color=result_color,
            fontsize=12,
            fontweight="bold",
        )

        fig.text(
            0.5,
            0.035,
            "Historical investment comparison | Values are estimates based on available market data",
            ha="center",
            color="#94a3b8",
            fontsize=8,
        )

        fig.tight_layout(rect=[0.04

, 0.08, 0.96, 0.82])
        fig.savefig(
            buffer,
            format="png",
            dpi=220,
            facecolor=fig.get_facecolor(),
            bbox_inches="tight",
        )

        buffer.seek(0)
        return buffer

    except Exception:
        logger.exception("ساخت نمودار ناموفق بود")
        return None

    finally:
        if fig is not None:
            plt.close(fig)
            