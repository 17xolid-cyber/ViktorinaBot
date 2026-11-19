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

CYR_TO_LAT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "j",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "x",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "i",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    "ў": "o'",
    "қ": "q",
    "ғ": "g'",
    "ҳ": "h",
}

NUMBER_ALIASES = {
    "0": "nol",
    "1": "bir",
    "2": "ikki",
    "3": "uch",
    "4": "to'rt",
    "5": "besh",
    "6": "olti",
    "7": "yetti",
    "8": "sakkiz",
    "9": "to'qqiz",
    "10": "o'n",
    "114": "bir yuz o'n to'rt",
}


def cyrillic_to_latin(text: str) -> str:
  return "".join(CYR_TO_LAT.get(ch, ch) for ch in text)


def normalize_answer(text: str) -> set[str]:
  cleaned = cyrillic_to_latin(text.lower().strip())
  # Apostrof variantlarini bir xillashtirish
  cleaned = (
      cleaned.replace("’", "'")
      .replace("‘", "'")
      .replace("`", "'")
      .replace("ʻ", "'")
  )
  # Ortiqcha bo'shliqlarni qisqartirish
  cleaned = " ".join(cleaned.split())

  variants = {cleaned}

  # Oxiridagi "ta", "tasi", "donasi" kabi so'zlarni olib tashlab ko'rish
  words = cleaned.split()
  if len(words) > 1 and words[-1] in {"ta", "tasi", "donasi", "nafar"}:
    no_suffix = " ".join(words[:-1])
    variants.add(no_suffix)
  else:
    no_suffix = cleaned

  # Raqam <-> so'z mosligini qo'shish
  for num, word in NUMBER_ALIASES.items():
    if no_suffix == num:
      variants.add(word)
      variants.add(f"{word} ta")
    elif no_suffix == word:
      variants.add(num)
      variants.add(f"{num} ta")

  return {v.strip() for v in variants if v.strip()}


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
      if (
          isinstance(current_q, dict)
          and "question" in current_q
          and "answer" in current_q
      ):
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
    member = await bot.get_chat_member(
        chat_id=message.chat.id, user_id=message.from_user.id
    )
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

  sorted_scores = sorted(
      scores.items(), key=lambda item: item[1]["score"], reverse=True
  )
  text = "🏆 **Shu guruh reyting jadvali:**\n\n"
  for idx, (_, data) in enumerate(sorted_scores, start=1):
    text += f"{idx}. {data['name']} — {data['score']} ball\n"

  await message.answer(text)


@dp.message(Command("end"))
async def cmd_end(message: types.Message):
  if not await is_user_admin(message):
    await message.answer(
        "⛔ Reytingni faqat guruh administratorlari yakunlashi mumkin."
    )
    return

  chat_game = get_chat_game(message.chat.id)
  scores = chat_game["scores"]

  if not scores:
    chat_game["current_question"] = None
    save_state()
    await message.answer(
        "Yakunlash uchun bu guruhda hozircha reyting ballari yo'q."
    )
    return

  sorted_scores = sorted(
      scores.items(), key=lambda item: item[1]["score"], reverse=True
  )
  text = "🏁 **Viktorina reytingi yakunlandi! Yakuniy natijalar:**\n\n"
  for idx, (_, data) in enumerate(sorted_scores, start=1):
    medal = (
        "🥇 "
        if idx == 1
        else ("🥈 " if idx == 2 else ("🥉 " if idx == 3 else ""))
    )
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

  raw_answer = current_question.get("answer", "")
  if isinstance(raw_answer, list):
    target_texts = [str(item) for item in raw_answer if str(item).strip()]
  else:
    target_texts = [str(raw_answer)] if str(raw_answer).strip() else []

  if not target_texts:
    return

  user_variants = normalize_answer(message.text)
  expected_variants = set()
  for ans in target_texts:
    expected_variants.update(normalize_answer(ans))

  if user_variants & expected_variants:
    user_id = message.from_user.id
    user_name = (
        message.from_user.full_name or message.from_user.first_name or "O'yinchi"
    )
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
