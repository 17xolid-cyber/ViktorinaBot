import asyncio
import html
import json
import os
import random
import re
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiohttp import web
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

HONORIFIC_PATTERNS = [
    r"\balayhissalom\b",
    r"\balayhis[\s\-]*salom\b",
    r"\balayhi[\s\-]*as[\s\-]*salom\b",
    r"\ba\.?\s*s\.?\b",
    r"\bpayg'?ambar(?:i)?(?:imiz)?\b",
    r"\broziyallohu[\s\-]*anhu(?:mo)?\b",
    r"\brodiyallohu[\s\-]*anhu(?:mo)?\b",
    r"\broziallohu[\s\-]*anhu(?:mo)?\b",
    r"\brodiallohu[\s\-]*anhu(?:mo)?\b",
    r"\broziyallohu[\s\-]*anho\b",
    r"\brodiyallohu[\s\-]*anho\b",
    r"\broziyallohu[\s\-]*anhum\b",
    r"\brodiyallohu[\s\-]*anhum\b",
    r"\br\.?\s*a\.?\b",
    r"\bsahoba(?:si)?\b",
]


def cyrillic_to_latin(text: str) -> str:
  return "".join(CYR_TO_LAT.get(ch, ch) for ch in text)


def strip_honorifics(text: str) -> str:
  cleaned = text
  for pattern in HONORIFIC_PATTERNS:
    cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
  cleaned = re.sub(r"[.,!?()\"«»“”]+", " ", cleaned)
  return " ".join(cleaned.split()).strip()


def normalize_answer(text: str) -> set[str]:
  cleaned = cyrillic_to_latin(text.lower().strip())
  cleaned = (
      cleaned.replace("’", "'")
      .replace("‘", "'")
      .replace("`", "'")
      .replace("ʻ", "'")
  )
  cleaned = " ".join(cleaned.split())

  variants = {cleaned}

  without_honorifics = strip_honorifics(cleaned)
  if without_honorifics:
    variants.add(without_honorifics)

  base_for_suffix = without_honorifics if without_honorifics else cleaned
  words = base_for_suffix.split()
  if len(words) > 1 and words[-1] in {"ta", "tasi", "donasi", "nafar"}:
    no_suffix = " ".join(words[:-1])
    variants.add(no_suffix)
  else:
    no_suffix = base_for_suffix

  for num, word in NUMBER_ALIASES.items():
    if no_suffix == num:
      variants.add(word)
      variants.add(f"{word} ta")
    elif no_suffix == word:
      variants.add(num)
      variants.add(f"{num} ta")

  return {v.strip() for v in variants if v.strip()}


def get_display_name(user: types.User | None) -> str:
  if not user:
    return "O'yinchi"
  for candidate in (user.full_name, user.first_name, user.username):
    if candidate and candidate.strip():
      return candidate.strip()
  return "O'yinchi"


def format_mention_html(user_id: int, data: dict) -> str:
  username = str(data.get("username") or "").strip().lstrip("@")
  name = html.escape(str(data.get("name") or "O'yinchi"))
  if username:
    return f"@{html.escape(username)}"
  if user_id:
    return f'<a href="tg://user?id={user_id}">{name}</a>'
  return name


def format_scores_text(
    scores: dict,
    title: str = "🏆 Shu guruh reyting jadvali:",
    mention_all: bool = False,
) -> str:
  if not scores:
    return "Bu guruhda hozircha hech kim ball to'plagani yo'q."
  sorted_scores = sorted(
      scores.items(), key=lambda item: item[1]["score"], reverse=True
  )
  lines = [f"{html.escape(title)}\n"]
  for idx, (uid, data) in enumerate(sorted_scores, start=1):
    medal = (
        "🥇 "
        if idx == 1
        else ("🥈 " if idx == 2 else ("🥉 " if idx == 3 else ""))
    )
    if mention_all:
      player_label = format_mention_html(uid, data)
    else:
      player_label = html.escape(str(data.get("name") or "O'yinchi"))
    lines.append(f"{idx}. {medal}{player_label} — {data['score']} ball")
  return "\n".join(lines)


def load_state() -> dict:
  """Servis uyg'onganda yoki qayta ishga tushganda o'yin holatini tiklaydi."""
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
                "username": str(info.get("username") or "").strip(),
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
                "username": str(info.get("username") or "").strip(),
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


def pick_next_question(questions: list, current_q: dict | None) -> dict:
  if len(questions) == 1:
    return questions[0]
  candidates = [
      q
      for q in questions
      if not current_q or q.get("question") != current_q.get("question")
  ]
  return random.choice(candidates if candidates else questions)


async def send_question(
    message: types.Message, chat_game: dict, questions: list
) -> None:
  next_q = pick_next_question(questions, chat_game.get("current_question"))
  chat_game["current_question"] = next_q
  save_state()
  await message.answer(f"❓ Yangi savol:\n\n{next_q['question']}")


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
    await message.answer("Hozircha questions.json faylida savollar yo'q!")
    return

  chat_game = get_chat_game(message.chat.id)
  await send_question(message, chat_game, questions)


@dp.message(Command("score"))
async def cmd_score(message: types.Message):
  chat_game = get_chat_game(message.chat.id)
  await message.answer(
      format_scores_text(chat_game["scores"]), parse_mode=ParseMode.HTML
  )


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

  text = format_scores_text(
      scores,
      title="🏁 Viktorina reytingi yakunlandi! Yakuniy natijalar:",
      mention_all=True,
  )
  text += "\n\n🔄 Reyting tozalandi. Yangi o'yinni /quiz orqali boshlashingiz mumkin!"

  chat_game["scores"] = {}
  chat_game["current_question"] = None
  save_state()

  await message.answer(text, parse_mode=ParseMode.HTML)


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
    user_id = message.from_user.id if message.from_user else 0
    user_name = get_display_name(message.from_user)
    username = (
        message.from_user.username if message.from_user and message.from_user.username else ""
    )
    scores = chat_game["scores"]

    if user_id not in scores:
      scores[user_id] = {"name": user_name, "username": username, "score": 0}
    else:
      scores[user_id]["name"] = user_name
      if username:
        scores[user_id]["username"] = username

    scores[user_id]["score"] += 100
    correct_ans_display = html.escape(str(current_question.get("answer", "")))
    safe_user_name = html.escape(user_name)

    await message.reply(
        f"🎉 Tabriklayman, {safe_user_name}! To'g'ri javob.\n"
        f"To'g'ri javob: {correct_ans_display}\n"
        f"Sizga 100 ball berildi! 🎯\n\n"
        f"{format_scores_text(scores, title='📊 Joriy reyting:')}",
        parse_mode=ParseMode.HTML,
    )

    questions = load_questions()
    if questions:
      await send_question(message, chat_game, questions)
    else:
      chat_game["current_question"] = None
      save_state()


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
