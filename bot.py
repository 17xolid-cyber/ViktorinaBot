import asyncio
import json
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

STATE_FILE = "games_state.json"
TEMP_STATE_FILE = "games_state.tmp"
games = {}


def load_state() -> dict:
    """Servis uyg'onganda yoki qayta ishga tushganda o'yin holatini fayldan tiklaydi."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}

        restored = {}
        for chat_id_str, data in raw.items():
            if not isinstance(data, dict):
                continue
            chat_id = int(chat_id_str)
            raw_scores = data.get("scores", {})
            scores = {}
            if isinstance(raw_scores, dict):
                for uid, info in raw_scores.items():
                    if isinstance(info, dict):
                        scores[int(uid)] = {
                            "name": str(info.get("name") or "O'yinchi"),
                            "score": int(info.get("score", 0)),
                        }

            current_q = data.get("current_question")
            if isinstance(current_q, dict) and "question" in current_q and "answer" in current_q:
                valid_q = current_q
            else:
                valid_q = None

            restored[chat_id] = {
                "current_question": valid_q,
                "scores": scores,
            }
        return restored
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def save_state() -> None:
    """O'yin holatini vaqtinchalik fayl orqali xavfsiz (atomar) saqlaydi."""
    serializable = {}
    for chat_id, data in games.items():
        serializable[str(chat_id)] = {
            "current_question": data.get("current_question"),
            "scores": {
                str(uid): {
                    "name": str(info.get("name") or "O'yinchi"),
                    "score": int(info.get("score", 0)),
                }
                for uid, info in data.get("scores", {}).items()
            },
        }
    try:
        with open(TEMP_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(TEMP_STATE_FILE, STATE_FILE)
    except OSError:
        pass


def get_chat_game(chat_id: int) -> dict:
    if chat_id not in games:
        games[chat_id] = {
            "current_question": None,
            "scores": {},
        }
        save_state()
    return games[chat_id]


def load_questions():
    try:
        with open("questions.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


async def is_user_admin(message: types.Message) -> bool:
    """Foydalanuvchi guruh/superguruhda admin yoki creator ekanligini tekshiradi."""
    if message.chat.type == "private":
        return True
    if not message.from_user:
        return False
    try:
        member = await bot.get_chat_member(chat_id=message.chat.id, user_id=message.from_user.id)
        return member.status in {"creator", "administrator"}
    except Exception:
        return False


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Assalomu alaykum! Diniy viktorina botiga xush kelibsiz.\n"
        "Buyruqlar:\n"
        "• /quiz — yangi savol berish\n"
        "• /score — joriy reytingni ko'rish\n"
        "• /end — reytingni yakunlash (faqat adminlar uchun)"
    )


@dp.message(Command("quiz"))
async def cmd_quiz(message: types.Message):
    questions = load_questions()
    if not questions:
        await message.answer("Hozircha `questions.json` faylida savollar yo'q!")
        return

    chat_game = get_chat_game(message.chat.id)
    chat_game["current_question"] = questions[0]
    save_state()

    await message.answer(
        f"❓ **Yangi savol:**\n\n{chat_game['current_question']['question']}\n\n"
        f"*(Javobingizni to'g'ridan-to'g'ri guruhga yozing!)*"
    )


@dp.message(Command("score"))
async def cmd_score(message: types.Message):
    chat_game = get_chat_game(message.chat.id)
    scores = chat_game["scores"]

    if not scores:
        await message.answer("Bu guruhda hozircha hech kim ball to'plagani yo'q.")
        return

    sorted_scores = sorted(scores.items(), key=lambda item: item[1]["score"], reverse=True)
    text = "🏆 **Shu guruh reyting jadvali:**\n\n"
    for idx, (_, data) in enumerate(sorted_scores, start=1):
        text += f"{idx}. {data['name']} — {data['score']} ball\n"

    await message.answer(text)


@dp.message(Command("end"))
async def cmd_end(message: types.Message):
    if not await is_user_admin(message):
        await message.answer("⛔ Reytingni faqat guruh administratorlari yakunlashi mumkin.")
        return

    chat_game = get_chat_game(message.chat.id)
    scores = chat_game["scores"]

    if not scores:
        chat_game["current_question"] = None
        save_state()
        await message.answer("Yakunlash uchun bu guruhda hozircha reyting ballari yo'q.")
        return

    sorted_scores = sorted(scores.items(), key=lambda item: item[1]["score"], reverse=True)
    text = "🏁 **Viktorina reytingi yakunlandi! Yakuniy natijalar:**\n\n"
    for idx, (_, data) in enumerate(sorted_scores, start=1):
        medal = "🥇 " if idx == 1 else ("🥈 " if idx == 2 else ("🥉 " if idx == 3 else ""))
        text += f"{idx}. {medal}{data['name']} — {data['score']} ball\n"

    text += "\n🔄 Reyting tozalandi. Yangi o'yinni /quiz orqali boshlashingiz mumkin!"

    chat_game["scores"] = {}
    chat_game["current_question"] = None
    save_state()

    await message.answer(text)


@dp.message()
async def check_answer(message: types.Message):
    if not message.text or message.text.startswith("/"):
        return

    chat_game = get_chat_game(message.chat.id)
    current_question = chat_game["current_question"]
    if not current_question:
        return

    user_answer = message.text.strip().lower()
    correct_answer = str(current_question.get("answer", "")).strip().lower()

    if correct_answer and user_answer == correct_answer:
        user_id = message.from_user.id
        user_name = message.from_user.full_name or message.from_user.first_name or "O'yinchi"
        scores = chat_game["scores"]

        if user_id not in scores:
            scores[user_id] = {"name": user_name, "score": 0}
        else:
            scores[user_id]["name"] = user_name

        scores[user_id]["score"] += 1
        chat_game["current_question"] = None
        save_state()

        await message.answer(
            f"🎉 Tabriklayman, {user_name}! To'g'ri javob.\n"
            f"To'g'ri javob: **{current_question['answer']}**\n"
            f"Sizga 1 ball berildi! 🎯"
        )


async def health_check(request):
    return web.Response(text="Bot is running")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    global games
    games = load_state()
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
