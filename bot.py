import asyncio
from datetime import datetime, timedelta, timezone
import html
import json
import os
import random
import re
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
CARD_NUMBER = os.getenv("CARD_NUMBER", "").strip()

bot = Bot(token=TOKEN)
dp = Dispatcher()

STATE_FILE = "games_state.json"
TEMP_STATE_FILE = "games_state.tmp"
ACCESS_FILE = "chats_access.json"
TEMP_ACCESS_FILE = "chats_access.tmp"

games = {}
chat_access = {}
pending_receipts = {}

TARIFFS = {
    "10d": {"label": "10 kunga", "days": 10, "price": "6 000 so'm"},
    "1m": {"label": "1 oyga", "days": 30, "price": "19 000 so'm"},
    "2m": {"label": "2 oyga", "days": 60, "price": "39 000 so'm"},
    "3m": {"label": "3 oyga", "days": 90, "price": "59 000 so'm"},
}

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
    r"\broziyallohu[\s\-]*anhu(?:mo)?\b",
    r"\brodiyallohu[\s\-]*anhu(?:mo)?\b",
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
    title: str = "Ayni vaqtdagi natijalar 📊:",
    mention_all: bool = False,
    winner_id: int | None = None,
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
        else ("🥈 " if idx == 2 else ("🥉 " if idx == 3 else "🎖️ "))
    )
    if mention_all:
      player_label = format_mention_html(uid, data)
    elif winner_id is not None and uid == winner_id:
      player_label = format_mention_html(uid, data)
    else:
      player_label = html.escape(str(data.get("name") or "O'yinchi"))
    lines.append(f"{medal}{player_label} — {data['score']} ball")
  return "\n".join(lines)


def format_question_text(question_text: str) -> str:
  safe_q = html.escape(str(question_text).strip())
  return (
      "🌸 🌸  𝚂 𝙰 𝚅 𝙾 𝙻  🌸 🌸\n\n"
      f"{safe_q}\n\n"
      "P.s: Agarda savolga javob topa olmasangiz /next buyrug'i yordamida"
      " keyingi savolga o'tishingiz mumkin."
  )


def load_access() -> dict:
  if not os.path.exists(ACCESS_FILE):
    return {}
  try:
    with open(ACCESS_FILE, "r", encoding="utf-8") as f:
      raw = json.load(f)
    if not isinstance(raw, dict):
      return {}
    restored = {}
    for cid_str, info in raw.items():
      if isinstance(info, dict):
        restored[int(cid_str)] = {
            "active": bool(info.get("active", False)),
            "expires_at": info.get("expires_at"),
            "title": str(info.get("title") or ""),
        }
    return restored
  except (json.JSONDecodeError, OSError, ValueError):
    return {}


def save_access() -> None:
  serializable = {
      str(cid): {
          "active": bool(info.get("active", False)),
          "expires_at": info.get("expires_at"),
          "title": str(info.get("title") or ""),
      }
      for cid, info in chat_access.items()
  }
  try:
    with open(TEMP_ACCESS_FILE, "w", encoding="utf-8") as f:
      json.dump(serializable, f, ensure_ascii=False, indent=2)
      f.flush()
      os.fsync(f.fileno())
    os.replace(TEMP_ACCESS_FILE, ACCESS_FILE)
  except OSError:
    pass


def is_group_active(chat_id: int) -> bool:
  info = chat_access.get(chat_id)
  if not info or not info.get("active"):
    return False
  expires_at = info.get("expires_at")
  if not expires_at:
    return True
  try:
    exp_dt = datetime.fromisoformat(expires_at)
    if exp_dt.tzinfo is None:
      exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= exp_dt:
      info["active"] = False
      save_access()
      return False
    return True
  except ValueError:
    return False


def get_expiry_text(chat_id: int) -> str:
  info = chat_access.get(chat_id)
  if not info or not info.get("active"):
    return "Uyg'otilmagan"
  expires_at = info.get("expires_at")
  if not expires_at:
    return "Cheksiz"
  try:
    exp_dt = datetime.fromisoformat(expires_at)
    if exp_dt.tzinfo is None:
      exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    return exp_dt.astimezone(timezone(timedelta(hours=5))).strftime(
        "%Y-%m-%d %H:%M"
    )
  except ValueError:
    return str(expires_at)


async def get_bot_username() -> str:
  global BOT_USERNAME
  if BOT_USERNAME:
    return BOT_USERNAME
  me = await bot.get_me()
  BOT_USERNAME = (me.username or "").lstrip("@")
  return BOT_USERNAME


async def build_activation_keyboard(chat_id: int) -> InlineKeyboardMarkup:
  username = await get_bot_username()
  url = (
      f"https://t.me/{username}?start=pay_{chat_id}"
      if username
      else "https://t.me"
  )
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="Uyg'otish", url=url)]
      ]
  )


async def send_sleeping_group_message(message: types.Message) -> None:
  kb = await build_activation_keyboard(message.chat.id)
  await message.reply(
      "Bot uxlayabdi. Uyg'onishi uchun to'lov qilish kerak.",
      reply_markup=kb,
  )


def build_tariffs_keyboard(target_chat_id: int | None) -> InlineKeyboardMarkup:
  cid_part = str(target_chat_id) if target_chat_id is not None else "0"
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="10 kunga", callback_data=f"tar:10d:{cid_part}"
              ),
              InlineKeyboardButton(
                  text="1 oyga", callback_data=f"tar:1m:{cid_part}"
              ),
          ],
          [
              InlineKeyboardButton(
                  text="2 oyga", callback_data=f"tar:2m:{cid_part}"
              ),
              InlineKeyboardButton(
                  text="3 oyga", callback_data=f"tar:3m:{cid_part}"
              ),
          ],
      ]
  )


def build_payment_action_keyboard(
    code: str, target_chat_id: int
) -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="✅ To'lov qildim ✅",
                  callback_data=f"paid:{code}:{target_chat_id}",
              )
          ],
          [
              InlineKeyboardButton(
                  text="⬅️ Ortga ⬅️",
                  callback_data=f"back:{target_chat_id}",
              )
          ],
      ]
  )


def build_admin_approve_keyboard(
    target_cid: int, days: int | None, buyer_id: int
) -> InlineKeyboardMarkup:
  days_part = str(days) if days is not None else "0"
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="✅ Tasdiqlash va uyg'otish",
                  callback_data=f"approve:{target_cid}:{days_part}:{buyer_id}",
              )
          ]
      ]
  )


def format_tariffs_text(target_chat_id: int | None) -> str:
  group_line = (
      f"Guruh ID: <code>{target_chat_id}</code>\n\n"
      if target_chat_id is not None
      else ""
  )
  return (
      "Botdan foydalanish uchun to'lov qilish zarur! Iltimos, quyidagi"
      f" tariflardan birini tanlang!\n{group_line}"
      "10 kun uchun - 6 000 so'm\n"
      "1 oy uchun - 19 000 so'm\n"
      "2 oy uchun - 39 000 so'm\n"
      "3 oy uchun - 59 000 so'm!\n\n"
      "Botni uyg'otmoqchi bo'lgan muddatingizni tanlang 👇"
  )


def build_activate_command(target_cid: int, days: int | None) -> str:
  cid_str = str(target_cid) if target_cid != 0 else "<guruh_id>"
  if days is not None:
    return f"/faollashtir {cid_str} {days}"
  return f"/faollashtir {cid_str}"


async def activate_group_and_notify(
    target_cid: int, days: int | None, admin_user: types.User | None
) -> str:
  """Guruhni uyg'otadi, guruhga xabar yuboradi, pin qiladi va status matnini qaytaradi."""
  expires_iso = None
  if days is not None and days > 0:
    exp_dt = datetime.now(timezone.utc) + timedelta(days=days)
    expires_iso = exp_dt.isoformat()
  else:
    days = None

  existing = chat_access.get(target_cid, {})
  chat_access[target_cid] = {
      "active": True,
      "expires_at": expires_iso,
      "title": existing.get("title", ""),
  }
  save_access()

  admin_mention = format_mention_html(
      admin_user.id if admin_user else 0,
      {
          "name": get_display_name(admin_user),
          "username": admin_user.username if admin_user and admin_user.username else "",
      },
  )
  if days is not None:
    group_notice = (
        f"Botni {admin_mention} {days} kunga uyg'otdi. Bot {days} kun"
        " davomida uxlamasdan xizmatingizda!"
    )
  else:
    group_notice = (
        f"Botni {admin_mention} 1 umrlikka uyg'otdi. Bot bir umr"
        " davomida uxlamasdan xizmatingizda!\n\n"
        "Eslatma: bir umrlik tarif bot yoki ushbu guruh o'chirilguncha amal"
        " qiladi."
    )

  group_sent = False
  group_pinned = False
  try:
    sent_msg = await bot.send_message(
        chat_id=target_cid, text=group_notice, parse_mode=ParseMode.HTML
    )
    group_sent = True
    try:
      await bot.pin_chat_message(
          chat_id=target_cid,
          message_id=sent_msg.message_id,
          disable_notification=True,
      )
      group_pinned = True
    except Exception:
      group_pinned = False
  except Exception:
    group_sent = False

  if group_sent and group_pinned:
    return "\n📩 Guruhga xabar yuborildi va qadab (pin) qo'yildi."
  if group_sent:
    return (
        "\n📩 Guruhga xabar yuborildi, lekin qadab bo'lmadi (botga pin huquqi"
        " berilganini tekshiring)."
    )
  return "\n⚠️ Guruhga xabar yuborib bo'lmadi (bot guruhda bormi tekshiring)."


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
  await message.answer(
      format_question_text(next_q["question"]),
      parse_mode=ParseMode.HTML,
  )


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
async def cmd_start(message: types.Message, command: CommandObject):
  args = (command.args or "").strip()

  if message.chat.type != "private":
    if not is_group_active(message.chat.id):
      await send_sleeping_group_message(message)
      return

  if message.chat.type == "private" and args.startswith("pay_"):
    raw_cid = args.replace("pay_", "", 1).strip()
    target_cid = None
    try:
      target_cid = int(raw_cid)
    except ValueError:
      target_cid = None

    await message.answer(
        format_tariffs_text(target_cid),
        reply_markup=build_tariffs_keyboard(target_cid),
        parse_mode=ParseMode.HTML,
    )
    return

  await message.answer(
      "Assalomu alaykum! Diniy viktorina botiga xush kelibsiz.\n\n"
      "Buyruqlar:\n"
      "• /next — yangi savol berish yoki keyingi savolga o'tish\n"
      "• /reyting — joriy reytingni ko'rish\n"
      "• /tugatish — reytingni yakunlash (faqat adminlar uchun)"
  )


@dp.callback_query(F.data.startswith("back:"))
async def cb_back_to_tariffs(callback: types.CallbackQuery):
  cid_str = (callback.data or "").split(":", 1)[1]
  try:
    target_cid = int(cid_str)
  except ValueError:
    target_cid = 0

  if callback.from_user:
    pending_receipts.pop(callback.from_user.id, None)

  await callback.message.edit_text(
      format_tariffs_text(target_cid if target_cid != 0 else None),
      reply_markup=build_tariffs_keyboard(
          target_cid if target_cid != 0 else None
      ),
      parse_mode=ParseMode.HTML,
  )
  await callback.answer()


@dp.callback_query(F.data.startswith("paid:"))
async def cb_paid_clicked(callback: types.CallbackQuery):
  parts = (callback.data or "").split(":")
  if len(parts) != 3:
    await callback.answer()
    return

  _, code, cid_str = parts
  tariff = TARIFFS.get(code)
  try:
    target_cid = int(cid_str)
  except ValueError:
    target_cid = 0

  if callback.from_user and tariff:
    pending_receipts[callback.from_user.id] = {
        "code": code,
        "label": tariff["label"],
        "days": tariff["days"],
        "price": tariff["price"],
        "chat_id": target_cid,
    }

  cid_note = f"\n\nGuruh ID: <code>{target_cid}</code>" if target_cid != 0 else ""
  await callback.message.answer(
      "📸 Iltimos, to'lov qilinganligi to'g'risida rasm yoki chek"
      f" (screenshot) yuboring.{cid_note}",
      parse_mode=ParseMode.HTML,
  )
  await callback.answer()


@dp.callback_query(F.data.startswith("tar:"))
async def cb_select_tariff(callback: types.CallbackQuery):
  parts = (callback.data or "").split(":")
  if len(parts) != 3:
    await callback.answer()
    return

  _, code, cid_str = parts
  tariff = TARIFFS.get(code)
  if not tariff:
    await callback.answer("Noto'g'ri tarif", show_alert=True)
    return

  try:
    target_cid = int(cid_str)
  except ValueError:
    target_cid = 0

  user = callback.from_user
  buyer_mention = format_mention_html(
      user.id if user else 0,
      {
          "name": get_display_name(user),
          "username": user.username if user and user.username else "",
      },
  )

  duration_title = tariff["label"].replace("ga", "lik")
  card_display = (
      html.escape(CARD_NUMBER) if CARD_NUMBER else "<i>Karta kiritilmagan</i>"
  )
  cid_line = f"\nGuruh ID: <code>{target_cid}</code>" if target_cid != 0 else ""

  text = (
      f"{html.escape(duration_title)} obuna uchun\n\n"
      f"<code>{card_display}</code>\n\n"
      f"raqamiga {html.escape(tariff['price'])} o'tkazasiz🙂{cid_line}\n"
      "To'lov qilganingizdan so'ng pastdagi tugmachani bosib 👇 to'lov"
      " qilinganligi to'g'risida biror nimani (screenshot, rasm va h.k)"
      " yuborasiz"
  )

  await callback.message.answer(
      text,
      reply_markup=build_payment_action_keyboard(code, target_cid),
      parse_mode=ParseMode.HTML,
  )

  if ADMIN_ID:
    days_val = tariff["days"]
    days_info = f"{days_val} kun" if days_val is not None else "Cheksiz"
    activate_cmd = build_activate_command(target_cid, days_val)
    try:
      await bot.send_message(
          chat_id=ADMIN_ID,
          text=(
              "💳 <b>Yangi tarif tanlandi!</b>\n\n"
              f"Foydalanuvchi: {buyer_mention}\n"
              f"Guruh ID: <code>{target_cid}</code>\n"
              f"Tarif: <b>{html.escape(tariff['label'])}</b>"
              f" ({html.escape(tariff['price'])}, {days_info})\n\n"
              f"Uyg'otish buyrug'i:\n<code>{activate_cmd}</code>"
          ),
          parse_mode=ParseMode.HTML,
      )
    except Exception:
      pass

  await callback.answer()


@dp.callback_query(F.data.startswith("approve:"))
async def cb_admin_approve(callback: types.CallbackQuery):
  if (
      not ADMIN_ID
      or not callback.from_user
      or callback.from_user.id != ADMIN_ID
  ):
    await callback.answer("Siz admin emassiz!", show_alert=True)
    return

  parts = (callback.data or "").split(":")
  if len(parts) != 4:
    await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
    return

  _, cid_str, days_str, buyer_str = parts
  try:
    target_cid = int(cid_str)
    days_raw = int(days_str)
    buyer_id = int(buyer_str)
  except ValueError:
    await callback.answer("ID formatida xatolik", show_alert=True)
    return

  if target_cid == 0:
    await callback.answer(
        "Guruh ID topilmadi! Qo'lda /faollashtir <guruh_id> <kun> yuboring.",
        show_alert=True,
    )
    return

  days = days_raw if days_raw > 0 else None
  extra_status = await activate_group_and_notify(
      target_cid, days, callback.from_user
  )
  expiry_text = get_expiry_text(target_cid)

  try:
    old_caption = callback.message.caption or ""
    await callback.message.edit_caption(
        caption=(
            f"{old_caption}\n\n"
            f"✅ <b>Tasdiqlandi va uyg'otildi!</b>\n"
            f"Muddat: <b>{html.escape(expiry_text)}</b>"
            f"{extra_status}"
        ),
        reply_markup=None,
        parse_mode=ParseMode.HTML,
    )
  except Exception:
    await callback.message.answer(
        f"✅ Bot uyg'otildi!\n"
        f"Chat ID: <code>{target_cid}</code>\n"
        f"Amal qilish muddati: <b>{html.escape(expiry_text)}</b>"
        f"{extra_status}",
        parse_mode=ParseMode.HTML,
    )

  if buyer_id:
    try:
      await bot.send_message(
          chat_id=buyer_id,
          text=(
              "🎉 <b>To'lovingiz tasdiqlandi va bot uyg'otildi!</b>\n"
              f"Amal qilish muddati: <b>{html.escape(expiry_text)}</b>\n\n"
              "Endi guruhda /next buyrug'i bilan viktorinani boshlashingiz"
              " mumkin."
          ),
          parse_mode=ParseMode.HTML,
      )
    except Exception:
      pass

  await callback.answer("✅ Bot uyg'otildi!")


@dp.message(Command("faollashtir"))
async def cmd_faollashtir(message: types.Message, command: CommandObject):
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  args = (command.args or "").strip().split()
  if len(args) < 1:
    await message.answer(
        "Foydalanish: <code>/faollashtir <chat_id> [kun]</code>\n"
        "Masalan: <code>/faollashtir -1001234567890 10</code>\n"
        "Cheksiz muddat uchun kun yozmang: <code>/faollashtir"
        " -1001234567890</code>",
        parse_mode=ParseMode.HTML,
    )
    return

  try:
    target_cid = int(args[0])
  except ValueError:
    await message.answer("Chat ID noto'g'ri formatda.")
    return

  days = None
  if len(args) >= 2:
    try:
      days = int(args[1])
      if days <= 0:
        days = None
    except ValueError:
      days = None

  extra_status = await activate_group_and_notify(
      target_cid, days, message.from_user
  )

  await message.answer(
      f"✅ Bot uyg'otildi!\n"
      f"Chat ID: <code>{target_cid}</code>\n"
      f"Amal qilish muddati: <b>{html.escape(get_expiry_text(target_cid))}</b>"
      f"{extra_status}",
      parse_mode=ParseMode.HTML,
  )


@dp.message(Command("ochirish"))
async def cmd_ochirish(message: types.Message, command: CommandObject):
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  args = (command.args or "").strip().split()
  if not args:
    await message.answer(
        "Foydalanish: <code>/ochirish <chat_id></code>",
        parse_mode=ParseMode.HTML,
    )
    return

  try:
    target_cid = int(args[0])
  except ValueError:
    await message.answer("Chat ID noto'g'ri formatda.")
    return

  if target_cid in chat_access:
    chat_access[target_cid]["active"] = False
    save_access()
  await message.answer(
      f"🔒 Guruh o'chirildi: <code>{target_cid}</code>", parse_mode=ParseMode.HTML
  )


@dp.message(Command("holat"))
async def cmd_holat(message: types.Message, command: CommandObject):
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  args = (command.args or "").strip().split()
  if not args:
    await message.answer(
        "Foydalanish: <code>/holat <chat_id></code>",
        parse_mode=ParseMode.HTML,
    )
    return

  try:
    target_cid = int(args[0])
  except ValueError:
    await message.answer("Chat ID noto'g'ri formatda.")
    return

  active = is_group_active(target_cid)
  await message.answer(
      f"Chat ID: <code>{target_cid}</code>\n"
      f"Holat: <b>{'Uyg\'oq' if active else 'Uxlayabdi'}</b>\n"
      f"Muddat: <b>{html.escape(get_expiry_text(target_cid))}</b>",
      parse_mode=ParseMode.HTML,
  )


@dp.message(Command("next"))
async def cmd_next(message: types.Message):
  if message.chat.type != "private" and not is_group_active(message.chat.id):
    await send_sleeping_group_message(message)
    return

  questions = load_questions()
  if not questions:
    await message.answer("Hozircha questions.json faylida savollar yo'q!")
    return

  chat_game = get_chat_game(message.chat.id)
  await send_question(message, chat_game, questions)


@dp.message(Command("reyting"))
async def cmd_reyting(message: types.Message):
  if message.chat.type != "private" and not is_group_active(message.chat.id):
    await send_sleeping_group_message(message)
    return

  chat_game = get_chat_game(message.chat.id)
  await message.answer(
      format_scores_text(chat_game["scores"]), parse_mode=ParseMode.HTML
  )


@dp.message(Command("tugatish"))
async def cmd_tugatish(message: types.Message):
  if message.chat.type != "private" and not is_group_active(message.chat.id):
    await send_sleeping_group_message(message)
    return

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
  text += (
      "\n\n🔄 Reyting tozalandi. Yangi o'yinni /next orqali boshlashingiz"
      " mumkin!"
  )

  chat_game["scores"] = {}
  chat_game["current_question"] = None
  save_state()

  await message.answer(text, parse_mode=ParseMode.HTML)


@dp.message(F.chat.type == "private", F.photo)
async def handle_payment_photo(message: types.Message):
  if not message.from_user:
    return
  info = pending_receipts.pop(message.from_user.id, None)
  target_cid = info["chat_id"] if info else 0
  days_val = info["days"] if info else 10
  if info:
    days_label = (
        f"{info['days']} kun" if info["days"] is not None else "Cheksiz"
    )
    tariff_text = f"{info['label']} ({info['price']}, {days_label})"
    activate_cmd = build_activate_command(target_cid, info["days"])
  else:
    tariff_text = "Ko'rsatilmagan"
    activate_cmd = build_activate_command(target_cid, 10)

  await message.answer(
      "✅ Chek/rasm qabul qilindi! Admin tekshirib, botni uyg'otadi."
  )

  if ADMIN_ID and message.photo:
    buyer_mention = format_mention_html(
        message.from_user.id,
        {
            "name": get_display_name(message.from_user),
            "username": message.from_user.username or "",
        },
    )
    approve_kb = (
        build_admin_approve_keyboard(
            target_cid, days_val, message.from_user.id
        )
        if target_cid != 0
        else None
    )
    extra_line = (
        "\n\nTasdiqlash uchun tugmani bosing 👇"
        if target_cid != 0
        else f"\n\nUyg'otish:\n<code>{activate_cmd}</code>"
    )
    try:
      await bot.send_photo(
          chat_id=ADMIN_ID,
          photo=message.photo[-1].file_id,
          caption=(
              "🧾 <b>Yangi to'lov cheki!</b>\n\n"
              f"Foydalanuvchi: {buyer_mention}\n"
              f"Guruh ID: <code>{target_cid}</code>\n"
              f"Tarif: <b>{html.escape(tariff_text)}</b>"
              f"{extra_line}"
          ),
          reply_markup=approve_kb,
          parse_mode=ParseMode.HTML,
      )
    except Exception:
      pass


@dp.message()
async def check_answer(message: types.Message):
  if not message.text or message.text.startswith("/"):
    return

  if message.chat.type != "private" and not is_group_active(message.chat.id):
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
        message.from_user.username
        if message.from_user and message.from_user.username
        else ""
    )
    scores = chat_game["scores"]

    if user_id not in scores:
      scores[user_id] = {"name": user_name, "username": username, "score": 0}
    else:
      scores[user_id]["name"] = user_name
      if username:
        scores[user_id]["username"] = username

    scores[user_id]["score"] += 100
    raw_disp = (
        raw_answer[0]
        if isinstance(raw_answer, list) and raw_answer
        else raw_answer
    )
    correct_ans_display = html.escape(str(raw_disp).capitalize())
    winner_mention = format_mention_html(user_id, scores[user_id])

    await message.reply(
        "😇😇 TO'G'RI 😇😇\n"
        f"🕯 {correct_ans_display}\n\n"
        f"{winner_mention} sizga 100 ball berildi\n"
        f"{format_scores_text(scores, title='Ayni vaqtdagi natijalar 📊:', mention_all=False, winner_id=user_id)}",
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
  global games, chat_access
  games = load_state()
  chat_access = load_access()
  await start_web_server()
  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
