import asyncio
import os
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback, get_user_locale
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

import db

# --- КОНФИГ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]

if not BOT_TOKEN or not ADMIN_IDS:
    raise RuntimeError("Не заданы BOT_TOKEN или ADMIN_IDS в .env")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

INITIAL_CLIENTS = [
    ("2026-12-02", "Минибаев", "ЭЦП"),
    ("2026-12-05", "РОО Наше коньково", "ЭЦП"),
    ("2026-12-11", "Зеленая лампа", "ЭЦП"),
    ("2026-12-20", "Фарафонова", "ЭЦП"),
    ("2026-12-25", "РОО Ясное Ясенево", "ЭЦП"),
    ("2026-12-25", "Леберн", "база"),
    ("2026-12-26", "Минибаев", "база"),
    ("2027-01-06", "ЛКА", "ЭЦП"),
    ("2027-01-06", "Степанов", "ЭЦП"),
    ("2027-01-06", "Половенко", "ЭЦП"),
    ("2027-01-07", "Театр теней", "ЭЦП"),
    ("2027-01-07", "Слепова", "ЭЦП"),
    ("2027-01-09", "Дебтус", "ЭЦП"),
    ("2027-01-15", "Крячко", "ЭЦП"),
    ("2027-01-15", "Домостроитель", "база"),
    ("2027-01-17", "Наследова", "ЭЦП"),
    ("2027-01-25", "ЖУК (1с биз)", "1С"),
    ("2027-01-29", "Домостроитель", "ЭЦП"),
]

TYPE_EMOJI = {"ЭЦП": "🔐", "база": "🗄", "1С": "💼", "другое": "📌"}


class AddClient(StatesGroup):
    waiting_for_type = State()
    waiting_for_date = State()
    waiting_for_name = State()


class RenewClient(StatesGroup):
    waiting_for_new_date = State()


# --- ОТПРАВКА ВСЕМ АДМИНАМ ---
async def broadcast(text: str, **kwargs):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, **kwargs)
        except Exception as e:
            logging.warning(f"Не смог отправить {admin_id}: {e}")


# --- НАПОМИНАНИЯ ---
async def send_reminder_for_date(days_left: int):
    items = await db.get_upcoming(days_left)
    if not items:
        return
    target_date = (datetime.now().date() + timedelta(days=days_left)).strftime("%d.%m.%Y")
    header = f"⚠️ <b>Напоминание</b>\n\nЧерез {days_left} дн. ({target_date}) истекает срок:"
    await broadcast(header, parse_mode="HTML")

    for expiry_date, name, ctype in items:
        emoji = TYPE_EMOJI.get(ctype, "📌")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Продлено", callback_data=f"renew|{expiry_date}|{name}")
        ]])
        await broadcast(f"{emoji} {name} <i>({ctype})</i>", parse_mode="HTML", reply_markup=kb)


async def check_expiry():
    logging.info("Проверка напоминаний (7, 3, 1 день)...")
    for days in (7, 3, 1):
        await send_reminder_for_date(days)


async def send_digest():
    nearest = await db.get_nearest()
    if not nearest:
        await broadcast("🌅 Доброе утро! В базе нет предстоящих сроков.")
        return

    expiry_date, name, ctype, renewed = nearest
    d = datetime.strptime(expiry_date, "%Y-%m-%d").date()
    days_left = (d - datetime.now().date()).days
    emoji = TYPE_EMOJI.get(ctype, "📌")
    mark = " ✅ (продлён)" if renewed else ""

    if days_left == 0:
        when = "сегодня"
    elif days_left == 1:
        when = "завтра"
    else:
        when = f"через {days_left} дн."

    await broadcast(
        f"🌅 <b>Доброе утро!</b>\n\n"
        f"Ближайший срок: {emoji} <b>{name}</b> ({ctype}) — "
        f"{d.strftime('%d.%m.%Y')}, {when}{mark}",
        parse_mode="HTML"
    )


# --- REPLY-КЛАВИАТУРА ---
def main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Список"), KeyboardButton(text="➕ Добавить")],
            [KeyboardButton(text="🗑 Удалить")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Слежу за сроками ЭЦП, баз и 1С.\n\n"
        "Кнопки внизу или команды:\n"
        "/add — добавить\n"
        "/list — ближайшие 30 дней\n"
        "/remove — удалить",
        reply_markup=main_reply_kb()
    )


# --- СПИСОК С ФИЛЬТРАМИ И КНОПКАМИ ПРОДЛЕНИЯ ---
@dp.message(F.text == "📋 Список")
@dp.message(Command("list"))
async def cmd_list(message: Message):
    await show_list(message.chat.id, 30, None)


async def show_list(chat_id: int, days: int, ctype: str | None):
    rows = await db.get_all_upcoming(days, ctype)
    title = f"📋 <b>Ближайшие {days} дней"
    if ctype:
        title += f" — только {ctype}"
    title += ":</b>"

    if not rows:
        await bot.send_message(chat_id, f"В ближайшие {days} дней сроков нет.")
        return

    today = datetime.now().date()
    week_ahead = today + timedelta(days=10)

    lines = []
    inline_rows = []
    for expiry_date, name, t, renewed in rows:
        d = datetime.strptime(expiry_date, "%Y-%m-%d").date()
        emoji = TYPE_EMOJI.get(t, "📌")
        mark = " ✅" if renewed else ""
        lines.append(f"{emoji} {d.strftime('%d.%m.%Y')} — {name} <i>({t})</i>{mark}")

        # Кнопку показываем только для непродлённых записей в ближайшие 7 дней
        if not renewed and d <= week_ahead:
            inline_rows.append([InlineKeyboardButton(
                text=f"✅ {d.strftime('%d.%m.%Y')} — {name}",
                callback_data=f"renew|{expiry_date}|{name}"
            )])

    filters = [
        ("Все", "list|30|all"),
        ("🔐 ЭЦП", "list|30|ЭЦП"),
        ("🗄 базы", "list|30|база"),
        ("💼 1С", "list|30|1С"),
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in filters],
        *inline_rows,
    ])

    await bot.send_message(chat_id, title + "\n\n" + "\n".join(lines), parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data.startswith("list|"))
async def cb_list(call: CallbackQuery):
    _, days_str, ctype_raw = call.data.split("|", 2)
    ctype = None if ctype_raw == "all" else ctype_raw
    await show_list(call.message.chat.id, int(days_str), ctype)
    await call.answer()


# --- ДОБАВЛЕНИЕ ---
@dp.message(F.text == "➕ Добавить")
@dp.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext):
    parts = message.text.split(maxsplit=3)
    if len(parts) == 4 and parts[0].startswith("/add"):
        date_str, ctype, name = parts[1], parts[2], parts[3].strip()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await message.answer("Неверный формат даты. Нужно: ГГГГ-ММ-ДД")
            return
        added = await db.add_client(date_str, name, ctype)
        if added:
            await message.answer(
                f"✅ Добавлено: {TYPE_EMOJI.get(ctype,'📌')} <b>{name}</b> ({ctype}) — {date_str}",
                parse_mode="HTML"
            )
        else:
            await message.answer(f"⚠️ Такая запись уже есть: {name} на {date_str}")
        return

    await state.clear()
    await state.set_state(AddClient.waiting_for_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔐 ЭЦП", callback_data="type|ЭЦП"),
        InlineKeyboardButton(text="🗄 база", callback_data="type|база"),
        InlineKeyboardButton(text="💼 1С", callback_data="type|1С"),
    ]])
    await message.answer("Что добавляем?", reply_markup=kb)


@dp.callback_query(AddClient.waiting_for_type, F.data.startswith("type|"))
async def add_type(call: CallbackQuery, state: FSMContext):
    ctype = call.data.split("|", 1)[1]
    await state.update_data(ctype=ctype)
    await state.set_state(AddClient.waiting_for_date)
    await call.message.edit_text(
        f"Тип: <b>{ctype}</b>\n\nВыбери дату истечения:",
        parse_mode="HTML",
        reply_markup=await SimpleCalendar().start_calendar()
    )
    await call.answer()


@dp.callback_query(AddClient.waiting_for_date, SimpleCalendarCallback.filter())
async def add_date(call: CallbackQuery, callback_data: SimpleCalendarCallback, state: FSMContext):
    selected, date = await SimpleCalendar().process_selection(call, callback_data)
    if selected:
        await state.update_data(new_expiry_date=date.strftime("%Y-%m-%d"))
        await state.set_state(AddClient.waiting_for_name)
        await call.message.edit_text(
            f"Дата: {date.strftime('%d.%m.%Y')}\n\nТеперь напиши название:"
        )
    await call.answer()


@dp.message(AddClient.waiting_for_name, F.text)
async def add_name(message: Message, state: FSMContext):
    data = await state.get_data()
    expiry_date = data.get("new_expiry_date")
    ctype = data.get("ctype", "другое")
    if not expiry_date:
        await message.answer("Что-то пошло не так. Начни заново: /add")
        await state.clear()
        return

    name = message.text.strip()
    added = await db.add_client(expiry_date, name, ctype)
    if added:
        await message.answer(
            f"✅ Добавлено: {TYPE_EMOJI.get(ctype,'📌')} <b>{name}</b> ({ctype}) — {expiry_date}",
            parse_mode="HTML"
        )
    else:
        await message.answer(f"⚠️ Такая запись уже есть: {name} на {expiry_date}")
    await state.clear()


# --- УДАЛЕНИЕ ---
@dp.message(F.text == "🗑 Удалить")
@dp.message(Command("remove"))
async def cmd_remove(message: Message):
    parts = message.text.split(maxsplit=2)
    if len(parts) == 3 and parts[0].startswith("/remove"):
        date_str, name = parts[1], parts[2].strip()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await message.answer("Неверный формат даты. Нужно: ГГГГ-ММ-ДД")
            return
        ok = await db.remove_client(date_str, name)
        if ok:
            await message.answer(f"🗑 Удалено: <b>{name}</b> на {date_str}", parse_mode="HTML")
        else:
            await message.answer(f"⚠️ Не нашёл: {name} на {date_str}")
        return

    rows = await db.get_all_upcoming(90)
    if not rows:
        await message.answer("В ближайшие 90 дней нечего удалять.")
        return

    kb_rows = []
    for expiry_date, name, t, renewed in rows[:20]:
        d = datetime.strptime(expiry_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        emoji = TYPE_EMOJI.get(t, "📌")
        kb_rows.append([InlineKeyboardButton(
            text=f"🗑 {d} — {emoji} {name}",
            callback_data=f"del|{expiry_date}|{name}"
        )])
    await message.answer(
        "Выбери, что удалить (ближайшие 20 записей):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )


@dp.callback_query(F.data.startswith("del|"))
async def cb_delete(call: CallbackQuery):
    _, expiry_date, name = call.data.split("|", 2)
    ok = await db.remove_client(expiry_date, name)
    if ok:
        await call.message.edit_text(f"🗑 Удалено: <b>{name}</b> на {expiry_date}", parse_mode="HTML")
    else:
        await call.message.edit_text("⚠️ Уже удалено или не найдено.")
    await call.answer()


# --- ПРОДЛЕНИЕ (ИЗ НАПОМИНАНИЯ ИЛИ ИЗ СПИСКА) ---
@dp.callback_query(F.data.startswith("renew|"))
async def cb_renew(call: CallbackQuery, state: FSMContext):
    _, expiry_date, name = call.data.split("|", 2)
    await state.update_data(renew_old_date=expiry_date, renew_name=name)
    await state.set_state(RenewClient.waiting_for_new_date)
    await call.message.edit_text(
        f"Продление: <b>{name}</b>\nСтарый срок: {expiry_date}\n\nВыбери новую дату:",
        parse_mode="HTML",
        reply_markup=await SimpleCalendar().start_calendar()
    )
    await call.answer()


@dp.callback_query(RenewClient.waiting_for_new_date, SimpleCalendarCallback.filter())
async def renew_date(call: CallbackQuery, callback_data: SimpleCalendarCallback, state: FSMContext):
    selected, date = await SimpleCalendar().process_selection(call, callback_data)
    if selected:
        data = await state.get_data()
        old_date, name = data["renew_old_date"], data["renew_name"]
        new_date = date.strftime("%Y-%m-%d")

        rows = await db.get_all_upcoming(9999)
        ctype = next((t for ed, n, t, r in rows if ed == old_date and n == name), "другое")

        await db.mark_renewed(old_date, name)
        await db.add_client(new_date, name, ctype)

        await call.message.edit_text(
            f"✅ <b>{name}</b> продлён.\nНовый срок: {date.strftime('%d.%m.%Y')}",
            parse_mode="HTML"
        )
        await state.clear()
    await call.answer()


# --- ЗАПУСК ---
async def main():
    await db.init_db()
    await db.seed_initial_data(INITIAL_CLIENTS)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_expiry, 'cron', hour=10, minute=0)
    scheduler.add_job(send_digest, 'cron', day_of_week='mon', hour=9, minute=0)
    scheduler.start()

    await check_expiry()
    await send_digest()
    logging.info("Бот запущен: БД, календарь, фильтры, удаление, дайджест, продление из списка.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())