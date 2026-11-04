
import asyncio
import json
import os
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DB_NAME = "home_chef.db"

if not TOKEN:
    raise RuntimeError("Токен не найден в файле .env")

dp = Dispatcher(storage=MemoryStorage())


class RecipeForm(StatesGroup):
    ingredients = State()


class ShoppingForm(StatesGroup):
    items = State()


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(
            text="📱 Открыть приложение",
            web_app=WebAppInfo(url="https://enot194.github.io/home-chef/")
        )],
        [KeyboardButton(text="🍳 Найти рецепт")],
        [KeyboardButton(text="🥘 Что приготовить?")],
        [KeyboardButton(text="🛒 Список покупок")],
        [KeyboardButton(text="📋 Показать покупки")],
    ],
    resize_keyboard=True
)

MENU_BUTTONS = [
    "🍳 Найти рецепт",
    "🥘 Что приготовить?",
    "🛒 Список покупок",
    "📋 Показать покупки",
]

RECIPES = [
    {
        "name": "Курица с картошкой под сыром",
        "ingredients": ["курица", "картошка", "сыр"],
        "steps": "Нарежь курицу и картошку, выложи в форму. "
                 "Добавь масло и специи. Запекай до готовности, "
                 "затем посыпь сыром."
    },
    {
        "name": "Омлет с сыром",
        "ingredients": ["яйца", "сыр", "молоко"],
        "steps": "Взбей яйца с молоком, добавь сыр и готовь "
                 "на сковороде под крышкой."
    },
    {
        "name": "Картошка с сыром",
        "ingredients": ["картошка", "сыр", "масло"],
        "steps": "Нарежь картошку, запеки до готовности, "
                 "добавь сыр и дождись, пока он расплавится."
    },
    {
        "name": "Салат из помидоров и огурцов",
        "ingredients": ["помидоры", "огурцы", "масло"],
        "steps": "Нарежь овощи, добавь масло и перемешай."
    },
    {
        "name": "Макароны с сыром",
        "ingredients": ["макароны", "сыр", "масло"],
        "steps": "Отвари макароны, добавь масло и тёртый сыр."
    },
]

recipe_missing_by_user = {}


def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shopping_items (
                user_id INTEGER NOT NULL,
                item TEXT NOT NULL,
                item_key TEXT NOT NULL,
                purchased INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, item_key)
            )
        """)

        columns = [
            row[1] for row in conn.execute(
                "PRAGMA table_info(shopping_items)"
            ).fetchall()
        ]

        if "purchased" not in columns:
            conn.execute("""
                ALTER TABLE shopping_items
                ADD COLUMN purchased INTEGER NOT NULL DEFAULT 0
            """)

        conn.execute("""
            DELETE FROM shopping_items
            WHERE item_key = ?
        """, ("📋 показать покупки",))


def add_items_to_db(user_id, items):
    with sqlite3.connect(DB_NAME) as conn:
        for item in items:
            item = item.strip()

            if not item or item in MENU_BUTTONS:
                continue

            conn.execute("""
                INSERT OR IGNORE INTO shopping_items
                (user_id, item, item_key)
                VALUES (?, ?, ?)
            """, (user_id, item, item.lower()))


def sync_items_from_webapp(user_id, items):
    """Replace the user's bot shopping list with items sent from the Mini App."""
    cleaned = []
    seen = set()

    for entry in items[:100]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()[:120]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        cleaned.append((name, 1 if entry.get("done") else 0))

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM shopping_items WHERE user_id = ?", (user_id,))
        for name, purchased in cleaned:
            conn.execute(
                """INSERT INTO shopping_items
                   (user_id, item, item_key, purchased)
                   VALUES (?, ?, ?, ?)""",
                (user_id, name, name.lower(), purchased)
            )


def get_items_from_db(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.execute("""
            SELECT rowid, item, purchased
            FROM shopping_items
            WHERE user_id = ?
            ORDER BY rowid
        """, (user_id,)).fetchall()


def toggle_item(user_id, item_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            UPDATE shopping_items
            SET purchased = 1 - purchased
            WHERE rowid = ? AND user_id = ?
        """, (item_id, user_id))


def delete_item(user_id, item_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            DELETE FROM shopping_items
            WHERE rowid = ? AND user_id = ?
        """, (item_id, user_id))


def clear_items(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            DELETE FROM shopping_items
            WHERE user_id = ?
        """, (user_id,))


def make_shopping_keyboard(items):
    keyboard = []

    for item_id, item, purchased in items:
        mark = "✅" if purchased else "⬜"

        keyboard.append([
            InlineKeyboardButton(
                text=f"{mark} {item}",
                callback_data=f"buy:{item_id}"
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=f"delete:{item_id}"
            )
        ])

    if items:
        keyboard.append([
            InlineKeyboardButton(
                text="🧹 Очистить весь список",
                callback_data="clear:confirm"
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def find_recipes(user_ingredients):
    matches = []

    for recipe in RECIPES:
        found = [
            item for item in recipe["ingredients"]
            if item in user_ingredients
        ]

        if len(found) >= 2:
            missing = [
                item for item in recipe["ingredients"]
                if item not in user_ingredients
            ]
            matches.append((recipe, found, missing))

    matches.sort(key=lambda item: len(item[1]), reverse=True)
    return matches[:3]


async def show_shopping_list(message: Message):
    user_id = message.from_user.id
    items = get_items_from_db(user_id)

    if not items:
        await message.answer(
            "Твой список пока пуст. 🛒",
            reply_markup=main_keyboard
        )
        return

    bought = sum(1 for item in items if item[2])

    await message.answer(
        f"🛒 <b>Твой список покупок</b>\n\n"
        f"Куплено: {bought} из {len(items)}\n\n"
        "Нажимай на продукт, чтобы отметить покупку.\n"
        "Нажимай 🗑, чтобы удалить продукт:",
        reply_markup=make_shopping_keyboard(items),
        parse_mode="HTML"
    )


async def refresh_shopping_message(message: Message, user_id: int):
    items = get_items_from_db(user_id)

    if not items:
        await message.edit_text("Твой список покупок пуст. 🛒")
        return

    bought = sum(1 for item in items if item[2])

    await message.edit_text(
        f"🛒 <b>Твой список покупок</b>\n\n"
        f"Куплено: {bought} из {len(items)}\n\n"
        "Нажимай на продукт, чтобы отметить покупку.\n"
        "Нажимай 🗑, чтобы удалить продукт:",
        reply_markup=make_shopping_keyboard(items),
        parse_mode="HTML"
    )


@dp.message(F.web_app_data)
async def receive_mini_app_data(message: Message):
    try:
        payload = json.loads(message.web_app_data.data)
    except (TypeError, json.JSONDecodeError):
        await message.answer("Не удалось прочитать данные приложения.")
        return

    if payload.get("type") != "shopping_sync":
        await message.answer("Получил данные из приложения.")
        return

    items = payload.get("items", [])
    if not isinstance(items, list):
        await message.answer("Список покупок имеет неверный формат.")
        return

    sync_items_from_webapp(message.from_user.id, items)
    await message.answer(
        "✅ Список покупок из приложения синхронизирован с ботом!",
        reply_markup=main_keyboard
    )
    await show_shopping_list(message)


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я Домашний Шеф 👨‍🍳\n\n"
        "Помогу выбрать блюдо и составить список покупок!\n\n"
        "Что будем делать сегодня?",
        reply_markup=main_keyboard
    )


@dp.message(F.text.in_(["🍳 Найти рецепт", "🥘 Что приготовить?"]))
async def ask_ingredients(message: Message, state: FSMContext):
    await state.set_state(RecipeForm.ingredients)
    await message.answer(
        "Напиши продукты, которые есть дома.\n\n"
        "Например: курица, картошка, сыр 🥔"
    )


@dp.message(RecipeForm.ingredients)
async def get_ingredients(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Напиши продукты обычным текстом.")
        return

    if message.text in MENU_BUTTONS:
        await state.clear()
        await route_menu(message, state)
        return

    user_ingredients = [
        item.strip().lower()
        for item in message.text.split(",")
        if item.strip()
    ]

    recipes = find_recipes(user_ingredients)
    await state.clear()

    if not recipes:
        await message.answer(
            "Пока не нашёл подходящих рецептов 😕",
            reply_markup=main_keyboard
        )
        return

    user_id = message.from_user.id
    recipe_missing_by_user[user_id] = {}

    await message.answer(
        "Вот что можно приготовить! 👨‍🍳",
        reply_markup=main_keyboard
    )

    for recipe, found, missing in recipes:
        answer = f"<b>🍽 {recipe['name']}</b>\n\n"
        answer += f"Есть дома: {', '.join(found)}\n"

        if missing:
            answer += f"Нужно докупить: {', '.join(missing)}\n"
        else:
            answer += "Все основные продукты уже есть! 🎉\n"

        answer += f"\nКак готовить:\n{recipe['steps']}"

        keyboard = None

        if missing:
            index = RECIPES.index(recipe)
            recipe_missing_by_user[user_id][index] = missing

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="➕ Добавить недостающее в покупки",
                        callback_data=f"add:{index}"
                    )
                ]]
            )

        await message.answer(
            answer,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


@dp.callback_query(F.data.startswith("add:"))
async def add_recipe_ingredients(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    missing = recipe_missing_by_user.get(user_id, {}).get(index)

    if missing is None:
        await callback.answer(
            "Найди рецепт ещё раз.",
            show_alert=True
        )
        return

    add_items_to_db(user_id, missing)
    await callback.answer("Продукты добавлены! ✅")

    await callback.message.answer(
        "Добавил недостающие продукты в покупки! 🛒"
    )


@dp.callback_query(F.data.startswith("buy:"))
async def buy_item(callback: CallbackQuery):
    user_id = callback.from_user.id
    item_id = int(callback.data.split(":")[1])

    toggle_item(user_id, item_id)
    await refresh_shopping_message(callback.message, user_id)

    await callback.answer("Статус покупки изменён!")


@dp.callback_query(F.data.startswith("delete:"))
async def delete_shopping_item(callback: CallbackQuery):
    user_id = callback.from_user.id
    item_id = int(callback.data.split(":")[1])

    delete_item(user_id, item_id)
    await refresh_shopping_message(callback.message, user_id)

    await callback.answer("Продукт удалён! 🗑")


@dp.callback_query(F.data == "clear:confirm")
async def confirm_clear(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, очистить",
                    callback_data="clear:yes"
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="clear:no"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        "⚠️ Ты действительно хочешь удалить все продукты из списка?",
        reply_markup=keyboard
    )

    await callback.answer()


@dp.callback_query(F.data == "clear:yes")
async def clear_shopping_list(callback: CallbackQuery):
    user_id = callback.from_user.id

    clear_items(user_id)

    await callback.message.edit_text(
        "Список полностью очищен! 🧹"
    )

    await callback.answer("Список очищен!")


@dp.callback_query(F.data == "clear:no")
async def cancel_clear(callback: CallbackQuery):
    await refresh_shopping_message(
        callback.message,
        callback.from_user.id
    )

    await callback.answer("Очистка отменена.")


async def shopping_list(message: Message, state: FSMContext):
    await state.set_state(ShoppingForm.items)
    await message.answer(
        "Напиши продукты для покупки через запятую.\n\n"
        "Например: молоко, хлеб, яйца 🛒"
    )


async def route_menu(message: Message, state: FSMContext):
    if message.text == "📋 Показать покупки":
        await show_shopping_list(message)
    elif message.text == "🛒 Список покупок":
        await shopping_list(message, state)
    elif message.text in ["🍳 Найти рецепт", "🥘 Что приготовить?"]:
        await ask_ingredients(message, state)


@dp.message(ShoppingForm.items)
async def add_shopping_items(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Напиши продукты обычным текстом.")
        return

    if message.text in MENU_BUTTONS:
        await state.clear()
        await route_menu(message, state)
        return

    user_id = message.from_user.id

    items = [
        item.strip()
        for item in message.text.split(",")
        if item.strip()
    ]

    add_items_to_db(user_id, items)
    await state.clear()

    await message.answer(
        "Добавил продукты в список! ✅",
        reply_markup=main_keyboard
    )

    await show_shopping_list(message)


@dp.message(F.text == "🛒 Список покупок")
async def open_shopping_list(message: Message, state: FSMContext):
    await shopping_list(message, state)


@dp.message(F.text == "📋 Показать покупки")
async def show_purchases(message: Message):
    await show_shopping_list(message)


async def main():
    init_db()
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
