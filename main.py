import asyncio
import logging
import math
import os
import calendar
from datetime import date

import jdatetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from services import (
    fetch_historical_crypto_price,
    fetch_current_prices,
    fetch_historical_toman_rate,
    generate_profit_chart,
    close_http_session,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

ASSETS = {
    "bitcoin": "₿ بیت‌کوین (BTC)",
    "ethereum": "Ξ اتریوم (ETH)",
    "tether": "💵 تتر (USDT)",
    "ripple": "🔷 ریپل (XRP)",
    "pax-gold": "🪙 پکس گلد (PAXG)",
}

ASSET_START_DATES = {
    "bitcoin": date(2009, 1, 3),
    "ethereum": date(2015, 7, 30),
    "tether": date(2014, 10, 6),
    "ripple": date(2012, 6, 2),
    "pax-gold": date(2019, 9, 5),
}

BOT_PROXY = os.getenv("BOT_PROXY", "").strip()

AMOUNTS = [
    1_000_000,
    2_500_000,
    5_000_000,
    7_500_000,
    10_000_000,
    25_000_000,
    50_000_000,
    75_000_000,
    100_000_000,
    250_000_000,
    500_000_000,
    750_000_000,
    1_000_000_000,
    2_500_000_000,
    5_000_000_000,
    7_500_000_000,
    10_000_000_000,
    25_000_000_000,
    50_000_000_000,
    100_000_000_000,
]

JALALI_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد",
    "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر",
    "دی", "بهمن", "اسفند",
]

GREGORIAN_MONTHS = list(calendar.month_name)[1:]


class InvestmentStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_year = State()
    waiting_for_month = State()
    waiting_for_day = State()


def format_number(value, decimals=2):
    return f"{value:,.{decimals}f}"


def normalize_digits(text):
    """پشتیبانی از اعداد فارسی و عربی."""
    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    )
    return text.translate(table)


def asset_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"asset:{asset_id}")]
        for asset_id, label in ASSETS.items()
    ])


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📊 شروع محاسبه سرمایه‌گذاری",
            callback_data="start_calculation",
        )
    ]])


def cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✖️ لغو عملیات",
            callback_data="cancel_operation",
        )
    ]])


def amount_keyboard():
    rows = []
    row = []

    for amount in AMOUNTS:
        label = f"{amount:,}"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"amount:{amount}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            text="✍️ واردکردن مبلغ دلخواه (به تومان وارد کنید)",
            callback_data="amount:custom",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="✖️ لغو عملیات",
            callback_data="cancel_operation",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def year_keyboard():
    years = list(range(1390, 1406))
    rows = []
    row = []

    for year in years:
        row.append(InlineKeyboardButton(
            text=str(year),
            callback_data=f"year:{year}",
        ))
        if len(row) == 4:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            text="✍️ ورود دستی سال (شمسی یا میلادی)",
            callback_data="year:custom",
        )
    ])
    rows.append([
        InlineKeyboardButton(

text="✖️ لغو عملیات",
            callback_data="cancel_operation",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def month_keyboard(calendar_type):
    names = JALALI_MONTHS if calendar_type == "jalali" else GREGORIAN_MONTHS
    rows = []
    row = []

    for index, name in enumerate(names, start=1):
        row.append(InlineKeyboardButton(
            text=name,
            callback_data=f"month:{index}",
        ))
        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            text="✍️ ورود دستی ماه",
            callback_data="month:custom",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="🔙 بازگشت به انتخاب سال",
            callback_data="back:year",
        ),
        InlineKeyboardButton(
            text="✖️ لغو",
            callback_data="cancel_operation",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_keyboard(max_day):
    rows = []
    row = []

    for day in range(1, max_day + 1):
        row.append(InlineKeyboardButton(
            text=str(day),
            callback_data=f"day:{day}",
        ))
        if len(row) == 7:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            text="✍️ ورود دستی روز",
            callback_data="day:custom",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="🔙 بازگشت به انتخاب ماه",
            callback_data="back:month",
        ),
        InlineKeyboardButton(
            text="✖️ لغو",
            callback_data="cancel_operation",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def prompt_for_month(message, state):
    data = await state.get_data()
    calendar_type = data["calendar_type"]
    await state.set_state(InvestmentStates.waiting_for_month)

    if calendar_type == "jalali":
        prompt = "ماه شمسی موردنظر را انتخاب کنید:"
    else:
        prompt = "ماه میلادی موردنظر را انتخاب کنید:"

    await message.answer(
        prompt,
        reply_markup=month_keyboard(calendar_type),
    )


async def prompt_for_day(message, state):
    data = await state.get_data()
    year = data["year"]
    month = data["month"]
    calendar_type = data["calendar_type"]

    if calendar_type == "jalali":
        max_day = 31 if month <= 6 else 30
        if month == 12:
            max_day = 30 if jdatetime.date(year, 12, 1).isleap() else 29
    else:
        max_day = calendar.monthrange(year, month)[1]

    today = jdatetime.date.today() if calendar_type == "jalali" else date.today()
    is_current_year = year == today.year

    if is_current_year and month == today.month:
        max_day = min(max_day, today.day)

    await state.set_state(InvestmentStates.waiting_for_day)
    await message.answer(
        f"روز را انتخاب کنید (۱ تا {max_day}):",
        reply_markup=day_keyboard(max_day),
    )


async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "سلام و خوش آمدید.\n\n"
        "به سامانه شبیه‌سازی عملکرد سرمایه‌گذاری خوش آمدید.\n"
        "با انتخاب دارایی، مبلغ و تاریخ خرید فرضی، می‌توانید "
        "ارزش فعلی سرمایه و سود یا زیان برآوردی را مشاهده کنید.\n\n"
        "توجه: نتایج به داده‌های بازار و نرخ‌های تاریخیِ در دسترس "
        "وابسته‌اند و هزینه‌های معاملاتی در محاسبات لحاظ نمی‌شوند.",
        reply_markup=main_keyboard(),
    )


async def help_handler(message: Message):
    await message.answer(
        "📘 راهنمای استفاده\n\n"
        "۱. شروع محاسبه را انتخاب کنید.\n"
        "۲. دارایی موردنظر را برگزینید.\n"
        "۳. مبلغ آماده را انتخاب کنید یا مبلغ دلخواه را وارد کنید.\n"
        "۴. تاریخ خرید را انتخاب کنید. سال شمسی یا میلادی قابل ورود است.\n"
        "۵. گزارش محاسبات و نمودار برای شما ارسال می‌شود.\n\n"
        "/start — شروع مجدد\n"
        "/help — راهنما\n"
        "/support — پشتیبانی\n"
        "/cancel — لغو عملیات\n\n"
        "برای جلوگیری از گزارش نادرست، در صورت نبود داده معتبر، "
        "محاسبه متوقف می‌شود و نرخ ساختگی جایگزین نخواهد شد."
    )


async def support_handler(message: Message):
    await message.answer(
        "🛠️ راهنمای پشتیبانی\n\n"
        "در صورت بروز خطا، تاریخ انتخاب‌شده و متن خطا را برای "
        "مدیر ربات ارسال کنید. لطفاً توکن ربات یا اطلاعات محرمانه "
        "را برای دیگران ارسال نکنید."
    )


async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "عملیات لغو شد. برای شروع مجدد، دستور /start را ارسال کنید."
    )


async def start_calculation_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "لطفاً دارایی موردنظر را انتخاب کنید:",
            reply_markup=asset_keyboard(),
        )


async def asset_selected_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    if not callback.data:
        return

    asset_id = callback.data.split(":", 1)[1]
    if asset_id not in ASSETS:
        if callback.message:
            await callback.message.answer("دارایی انتخاب‌شده معتبر نیست.")
        return

    await state.clear()
    await state.update_data(
        asset_id=asset_id,
        asset_label=ASSETS[asset_id],
    )
    await state.set_state(InvestmentStates.waiting_for_amount)

    if callback.message:
        await callback.message.answer(
    f"دارایی انتخاب‌شده: {ASSETS[asset_id]}\n\n"
    "💰 تمام قیمت‌ها و مبالغ این ربات به تومان هستند.\n"
    "مبلغ سرمایه‌گذاری را انتخاب کنید:",
    reply_markup=amount_keyboard(),
)


async def amount_button_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.data or not callback.message:
        return

    choice = callback.data.split(":", 1)[1]

    if choice == "custom":
        await callback.message.answer(
            "مبلغ دلخواه را به تومان وارد کنید.\n"
            "مثال: 12500000",
            reply_markup=cancel_keyboard(),
        )
        return

    amount = int(choice)
    await state.update_data(amount_toman=amount)
    await state.set_state(InvestmentStates.waiting_for_year)
    await callback.message.answer(
        f"مبلغ انتخاب‌شده: {format_number(amount, 0)} تومان\n\n"
        "سال خرید فرضی را انتخاب کنید یا سال شمسی/میلادی را دستی وارد کنید:",
        reply_markup=year_keyboard(),
    )


async def amount_handler(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("لطفاً مبلغ را به‌صورت عددی وارد کنید.")
        return

    raw = normalize_digits(message.text.strip())
    raw = raw.replace(",", "").replace("٬", "").replace(" ", "")

    try:
        amount = float(raw)
    except ValueError:
        await message.answer("مبلغ معتبر نیست. لطفاً فقط عدد وارد کنید.")
        return

    if not math.isfinite(amount) or amount <= 0:
        await message.answer("مبلغ باید یک عدد مثبت و معتبر باشد.")
        return

    await state.update_data(amount_toman=amount)
    await state.set_state(InvestmentStates.waiting_for_year)
    await message.answer(
        f"مبلغ ثبت شد: {format_number(amount, 0)} تومان\n\n"
        "سال خرید را انتخاب کنید یا به‌صورت دستی وارد کنید:",
        reply_markup=year_keyboard(),
    )


async def year_button_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.data or not callback.message:
        return

    value = callback.data.split(":", 1)[1]
    if value == "custom":
        await callback.message.answer(
            "سال را وارد کنید.\n"
            "نمونه شمسی: ۱۴۰۰\n"
            "نمونه میلادی: 2021",
            reply_markup=cancel_keyboard(),
        )
        return

    year = int(value)
    await state.update_data(year=year, calendar_type="jalali")
    await prompt_for_month(callback.message, state)


async def year_handler(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("لطفاً سال را به‌صورت عدد واردکنید.")
        return

    try:
        year = int(normalize_digits(message.text.strip()))
    except ValueError:
        await message.answer("سال معتبر نیست. برای مثال ۱۴۰۰ یا 2021 وارد کنید.")
        return

    current_jalali = jdatetime.date.today()
    current_gregorian = date.today()

    if 1380 <= year <= current_jalali.year:
        calendar_type = "jalali"
    elif 2009 <= year <= current_gregorian.year:
        calendar_type = "gregorian"
    else:
        await message.answer(
            f"سال شمسی باید بین ۱۳۸۰ و {current_jalali.year} "
            f"یا سال میلادی بین ۲۰۰۹ و {current_gregorian.year} باشد."
        )
        return

    await state.update_data(year=year, calendar_type=calendar_type)
    await prompt_for_month(message, state)


def parse_month(text, calendar_type):
    normalized = normalize_digits(text.strip().lower())

    if normalized.isdigit():
        month = int(normalized)
        if 1 <= month <= 12:
            return month
        return None

    if calendar_type == "jalali":
        for i, name in enumerate(JALALI_MONTHS, 1):
            if normalized == name.lower():
                return i
        return None

    for i, name in enumerate(GREGORIAN_MONTHS, 1):
        if normalized == name.lower() or normalized[:3] == name[:3].lower():
            return i

    return None


async def month_button_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.data or not callback.message:
        return

    value = callback.data.split(":", 1)[1]
    if value == "custom":
        data = await state.get_data()
        if data.get("calendar_type") == "jalali":
            example = "فروردین یا ۱"
        else:
            example = "January یا 1"

        await callback.message.answer(
            f"نام ماه یا شماره آن را وارد کنید. نمونه: {example}",
            reply_markup=cancel_keyboard(),
        )
        return

    month = int(value)
    await state.update_data(month=month)
    await prompt_for_day(callback.message, state)


async def month_handler(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("لطفاً ماه را وارد کنید.")
        return

    data = await state.get_data()
    calendar_type = data["calendar_type"]
    month = parse_month(message.text, calendar_type)

    if month is None:
        example = "فروردین یا ۱" if calendar_type == "jalali" else "January یا 1"
        await message.answer(f"نام یا شماره ماه معتبر نیست. نمونه: {example}")
        return

    await state.update_data(month=month)
    await prompt_for_day(message, state)


async def day_button_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.data or not callback.message:
        return

    value = callback.data.split(":", 1)[1]
    if value == "custom":
        await callback.message.answer(
            "شماره روز را وارد کنید؛ برای مثال ۹.",
            reply_markup=cancel_keyboard(),
        )
        return

    await finish_date_selection(
        callback.message,
        state,
        int(value),
    )


async def day_handler(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("لطفاً روز را به‌صورت عدد وارد کنید.")
        return

    try:
        day = int(normalize_digits(message.text.strip()))
    except ValueError:
        await message.answer("روز معتبر نیست. لطفاً عدد وارد کنید.")
        return

    await finish_date_selection(message, state, day)


async def finish_date_selection(message: Message, state: FSMContext, day: int):
    data = await state.get_data()
    year = data["year"]
    month = data["month"]
    calendar_type = data["calendar_type"]

    try:
        if calendar_type == "jalali":
            selected_date = jdatetime.date(year, month, day)
            today = jdatetime.date.today()
        else:
            selected_date = date(year, month, day)
            today = date.today()
    except (ValueError, TypeError):
        await message.answer(
            "این تاریخ در تقویم انتخاب‌شده معتبر نیست. لطفاً روز را دوباره انتخاب کنید."
        )
        await prompt_for_day(message, state)
        return

    if selected_date > today:
        await message.answer(
            "تاریخ خرید نمی‌تواند در آینده باشد. لطفاً تاریخ دیگری انتخاب کنید."
        )
        await prompt_for_day(message, state)
        return

    if calendar_type == "jalali":
        jalali_date = selected_date
        gregorian_date = selected_date.togregorian()
    else:
        gregorian_date = selected_date
        jalali_date = jdatetime.date.fromgregorian(date=selected_date)

    asset_id = data.get("asset_id")
    asset_start_date = ASSET_START_DATES.get(asset_id)

    if asset_start_date and gregorian_date < asset_start_date:
        await message.answer(
            "⚠️ <b>امکان انجام محاسبه برای تاریخ انتخاب‌شده وجود ندارد.</b>\n\n"
            "تاریخ انتخابی شما پیش از ایجاد یا عرضه این دارایی است؛ "
            "بنابراین امکان شبیه‌سازی سرمایه‌گذاری روی این دارایی در آن تاریخ وجود ندارد.\n\n"
            "لطفاً تاریخ دیگری، پس از عرضه این دارایی، انتخاب فرمایید.\n\n"
            "از همراهی شما سپاسگزاریم. 🙏",
            parse_mode="HTML",
        )
        await state.clear()
        return

    await message.answer(
        "تاریخ ثبت شد. در حال دریافت داده‌های تاریخی و قیمت‌های فعلی هستم؛ "
        "لطفاً چند لحظه صبر کنید."
    )

    try:
        await process_final_calculation(
            message,
            state,
            jalali_date,
            gregorian_date,
        )
    except Exception as exc:
        logger.exception("محاسبه نهایی ناموفق بود")
        await message.answer(
            "محاسبه انجام نشد؛ داده معتبر برای یکی از موارد موردنیاز دریافت نشد "
            "یا خطایی در سرویس رخ داد.\n\n"
            f"جزئیات: {str(exc)[:500]}\n\n"
            "لطفاً بعداً دوباره تلاش کنید."
        )
    finally:
        await state.clear()


async def process_final_calculation(
    message: Message,
    state: FSMContext,
    jalali_date,
    gregorian_date,
):
    data = await state.get_data()
    asset_id = data["asset_id"]
    asset_label = data["asset_label"]
    amount_toman = float(data["amount_toman"])

    greg_date_str = gregorian_date.strftime("%d-%m-%Y")
    shamsi_date_str = jalali_date.strftime("%Y/%m/%d")

    hist_price, hist_toman_rate = await asyncio.gather(
        fetch_historical_crypto_price(asset_id, greg_date_str),
        fetch_historical_toman_rate(shamsi_date_str, jalali_date.year),
    )

    curr_price, curr_toman_rate = await fetch_current_prices(asset_id)

    checks = [
        (hist_price, "قیمت تاریخی دارایی"),
        (hist_toman_rate, "نرخ تاریخی دلار"),
        (curr_price, "قیمت فعلی دارایی"),
        (curr_toman_rate, "نرخ فعلی تتر به تومان"),
    ]
    for value, label in checks:
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{label} معتبر دریافت نشد.")

    purchase_price_toman = hist_price * hist_toman_rate
    if purchase_price_toman <= 0:
        raise ValueError("قیمت خرید محاسبه‌شده معتبر نیست.")

    units_bought = amount_toman / purchase_price_toman
    curr_price_toman = curr_price * curr_toman_rate
    curr_value_toman = units_bought * curr_price_toman

    initial_value_usd = amount_toman / hist_toman_rate
    curr_value_usd = units_bought * curr_price

    profit_toman = curr_value_toman - amount_toman
    profit_usd = curr_value_usd - initial_value_usd
    profit_percent = (profit_toman / amount_toman) * 100

    if profit_toman > 0:
        result_emoji, result_title = "🟢", "سود"
    elif profit_toman < 0:
        result_emoji, result_title = "🔴", "زیان"
    else:
        result_emoji, result_title = "⚪", "بدون سود یا زیان"

    sign_toman = "+" if profit_toman > 0 else ""
    sign_usd = "+" if profit_usd > 0 else ""
    sign_percent = "+" if profit_percent > 0 else ""

    report = (
        "📊 <b>گزارش شبیه‌سازی سرمایه‌گذاری</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💎 <b>دارایی:</b> {asset_label}\n"
        f"📅 <b>تاریخ خرید فرضی شمسی:</b> {shamsi_date_str}\n"
        f"📅 <b>تاریخ میلادی:</b> {gregorian_date.isoformat()}\n\n"
        "💰 <b>اطلاعات خرید</b>\n"
        f"• مبلغ اولیه: {format_number(amount_toman, 0)} تومان\n"
        f"• قیمت تاریخی هر واحد: {format_number(hist_price, 6)} دلار\n"
        f"• نرخ تاریخی دلار آزاد: {format_number(hist_toman_rate, 2)} تومان\n"
        f"• قیمت خرید هر واحد: {format_number(purchase_price_toman, 2)} تومان\n"
        f"• تعداد واحد خریداری‌شده: {format_number(units_bought, 8)}\n\n"
        "📈 <b>وضعیت فعلی</b>\n"
        f"• قیمت فعلی هر واحد: {format_number(curr_price, 6)} دلار\n"
        f"• نرخ فعلی تتر: {format_number(curr_toman_rate, 2)} تومان\n"
        f"• ارزش فعلی سرمایه: {format_number(curr_value_toman, 2)} تومان\n"
        f"• ارزش فعلی دلاری: {format_number(curr_value_usd, 2)} دلار\n\n"
        f"{result_emoji} <b>نتیجه: {result_title}</b>\n"
        f"• {'سود' if profit_toman > 0 else 'زیان' if profit_toman < 0 else 'مبلغ تغییرکرده'} تومانی: {sign_toman}{format_number(profit_toman, 2)} تومان\n"
        f"• {'سود' if profit_usd > 0 else 'زیان' if profit_usd < 0 else 'مبلغ تغییرکرده'} دلاری: {sign_usd}{format_number(profit_usd, 2)} دلار\n" 
        f"• درصد تغییر ارزش: {sign_percent}{format_number(profit_percent, 2)}٪\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ این گزارش شبیه‌سازی است. کارمزدها، اختلاف قیمت خرید و فروش "
        "و سایر هزینه‌ها لحاظ نشده‌اند. نتیجه به پوشش و کیفیت منابع داده وابسته است."
    )
    await message.answer(report)

    try:
        chart = generate_profit_chart(
            labels=["Initial Investment", "Current Value"],
            values=[amount_toman, curr_value_toman],
            title=f"Investment Performance - {asset_id.upper()}",
        )
        if chart is not None and chart.getvalue():
            await message.answer_photo(
                BufferedInputFile(
                    chart.getvalue(),
                    filename="investment_report.png",
                ),
                caption="📊 مقایسه مبلغ اولیه با ارزش فعلی سرمایه",
            )
        else:
            await message.answer("گزارش متنی آماده شد، اما ساخت نمودار ناموفق بود.")
    except Exception:
        logger.exception("ساخت یا ارسال نمودار ناموفق بود")
        await message.answer("گزارش متنی آماده شد، اما نمودار قابل ارسال نبود.")

    await message.answer(
        "برای محاسبه جدید، دکمه زیر را انتخاب کنید.",
        reply_markup=main_keyboard(),
    )


async def back_to_year_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(InvestmentStates.waiting_for_year)
    if callback.message:
        await callback.message.answer(
            "سال خرید را انتخاب کنید:",
            reply_markup=year_keyboard(),
        )


async def back_to_month_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.message:
        await prompt_for_month(callback.message, state)


async def cancel_callback_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message:
        await callback.message.answer(
            "عملیات لغو شد. برای شروع مجدد /start را ارسال کنید."
        )


async def main():
    if not BOT_TOKEN or not BOT_TOKEN.strip():
        raise RuntimeError("متغیر BOT_TOKEN تنظیم نشده است.")

    session = AiohttpSession(proxy=BOT_PROXY) if BOT_PROXY else AiohttpSession()
    bot = Bot(
        token=BOT_TOKEN.strip(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(start_handler, CommandStart())
    dp.message.register(help_handler, Command("help"))
    dp.message.register(support_handler, Command("support"))
    dp.message.register(cancel_handler, Command("cancel"))

    dp.callback_query.register(start_calculation_handler, F.data == "start_calculation")
    dp.callback_query.register(asset_selected_handler, F.data.startswith("asset:"))
    dp.callback_query.register(amount_button_handler, F.data.startswith("amount:"))
    dp.callback_query.register(year_button_handler, F.data.startswith("year:"))
    dp.callback_query.register(month_button_handler, F.data.startswith("month:"))
    dp.callback_query.register(day_button_handler, F.data.startswith("day:"))
    dp.callback_query.register(back_to_year_handler, F.data == "back:year")
    dp.callback_query.register(back_to_month_handler, F.data == "back:month")
    dp.callback_query.register(cancel_callback_handler, F.data == "cancel_operation")

    dp.message.register(amount_handler, InvestmentStates.waiting_for_amount)
    dp.message.register(year_handler, InvestmentStates.waiting_for_year)
    dp.message.register(month_handler, InvestmentStates.waiting_for_month)
    dp.message.register(day_handler, InvestmentStates.waiting_for_day)

    try:
        me = await bot.get_me()
        logger.info("ربات آماده اجراست: @%s", me.username)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        try:
            await close_http_session()
        except Exception:
            logger.exception("خطا هنگام بستن نشست سرویس‌ها")
        try:
            await bot.session.close()
        except Exception:
            logger.exception("خطا هنگام بستن نشست تلگرام")
        logger.info("ربات خاموش شد.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("اجرای ربات متوقف شد.")
    except Exception:
        logger.exception("اجرای ربات با خطا متوقف شد.")
        