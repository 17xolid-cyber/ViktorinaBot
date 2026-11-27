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

try:
  import asyncpg
except ImportError:
  asyncpg = None

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
CARD_NUMBER = os.getenv("CARD_NUMBER", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

bot = Bot(token=TOKEN)
dp = Dispatcher()

STATE_FILE = "games_state.json"
TEMP_STATE_FILE = "games_state.tmp"
ACCESS_FILE = "chats_access.json"
TEMP_ACCESS_FILE = "chats_access.tmp"

GLOBAL_EXCLUDED_FILE = "global_excluded.json"
TEMP_GLOBAL_EXCLUDED_FILE = "global_excluded.tmp"

TASHKENT_TZ = timezone(timedelta(hours=5))

db_pool = None
games = {}
chat_access = {}
pending_receipts = {}
pending_buyers = {}
global_excluded_users: set[int] = set()

TARIFFS = {
    "15d": {"label": "15 kunga", "days": 15, "price": "12 000 so'm", "price_num": 12000, "is_monthly": True},
    "1m": {"label": "1 oyga", "days": 30, "price": "20 000 so'm", "price_num": 20000, "is_monthly": True},
    "2m": {"label": "2 oyga", "days": 60, "price": "35 000 so'm", "price_num": 35000, "is_monthly": True},
    "3m": {"label": "3 oyga", "days": 90, "price": "50 000 so'm", "price_num": 50000, "is_monthly": True},
}

FRIDAY_PROMO_TEXT = (
    "🕌✨ <b>JUMA MUBORAK! BUGUN MAXSUS JUMA AKSIYASI!</b> ✨🕌\n\n"
    "🔥 Faqat bugun barcha tariflarga <b>50% CHEGIRMA</b> va ustiga <b>+5 KUN BONUS</b>!\n"
    "📦 (15 kunlik, 1 oylik, 2 oylik, 3 oylik — barchasiga!)\n\n"
    "⚡ <b>Qanday faollashtiriladi?</b>\n"
    "1️⃣ Botni guruhingizga qo'shing va admin huquqini bering ✅\n"
    "2️⃣ Guruhda yoki botda /start buyrug'ini bering 🚀\n"
    "3️⃣ Tarifni <b>50% chegirmada</b> oling va <b>+5 kun bonus</b>ga ega bo'ling 🎁\n\n"
    "⏳🔥 <i>Aksiya faqat bugun Juma kuni amal qiladi — ulgurib qoling!</i>\n"
    "♻️✨ <i>Tariflarni avvalroq sotib olgan guruhlar ham aksiyadan foydalanishi mumkin!</i>"
)


def is_friday_tashkent(now_dt: datetime | None = None) -> bool:
  """Toshkent vaqti bilan hozir Juma kuni (weekday == 4) ekanligini tekshiradi."""
  current = now_dt.astimezone(TASHKENT_TZ) if now_dt else datetime.now(TASHKENT_TZ)
  return current.weekday() == 4


def _format_uzs_amount(amount: int) -> str:
  return f"{amount:,}".replace(",", " ") + " so'm"


def get_effective_tariff(code: str, now_dt: datetime | None = None) -> dict | None:
  """Juma kuni tariflar uchun 50% chegirma va +5 kun bonusni hisoblab beradi."""
  base = TARIFFS.get(code)
  if not base:
    return None
  friday_active = is_friday_tashkent(now_dt) and bool(base.get("is_monthly", False))
  base_days = int(base["days"])
  base_price_num = int(base["price_num"])
  if friday_active:
    eff_days = base_days + 5
    eff_price_num = base_price_num // 2
    eff_price = _format_uzs_amount(eff_price_num)
    return {
        "code": code,
        "label": base["label"],
        "base_days": base_days,
        "days": eff_days,
        "bonus_days": 5,
        "base_price": base["price"],
        "price": eff_price,
        "price_num": eff_price_num,
        "is_monthly": True,
        "is_friday_promo": True,
    }
  return {
      "code": code,
      "label": base["label"],
      "base_days": base_days,
      "days": base_days,
      "bonus_days": 0,
      "base_price": base["price"],
      "price": base["price"],
      "price_num": base_price_num,
      "is_monthly": bool(base.get("is_monthly", False)),
      "is_friday_promo": False,
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


def format_admin_profile_link_html(user_id: int, data: dict) -> str:
  """Faqat admin (siz) uchun foydalanuvchi profiliga havola qaytaradi."""
  name = html.escape(str(data.get("name") or "Foydalanuvchi"))
  username = str(data.get("username") or "").strip().lstrip("@")
  if user_id:
    label = f"{name} (@{html.escape(username)})" if username else name
    return f'<a href="tg://user?id={user_id}">{label}</a>'
  if username:
    return f"@{html.escape(username)}"
  return name


def build_user_data(user: types.User | None) -> dict:
  if not user:
    return {"id": 0, "name": "O'yinchi", "username": ""}
  return {
      "id": user.id,
      "name": get_display_name(user),
      "username": (user.username or "").strip().lstrip("@"),
  }


def format_scores_text(
    scores: dict,
    title: str = "Ayni vaqtdagi natijalar 📊:",
    mention_all: bool = False,
    winner_id: int | None = None,
) -> str:
  if not scores:
    return "Bu guruhda hozircha hech kim ball to'plagani yo'q."
  visible_items = [
      (uid, data)
      for uid, data in scores.items()
      if isinstance(data, dict) and not data.get("hidden_in_group", False)
  ]
  if not visible_items:
    return "Bu guruhda hozircha hech kim ball to'plagani yo'q."
  sorted_scores = sorted(
      visible_items, key=lambda item: item[1]["score"], reverse=True
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


def _clean_db_url(url: str) -> str:
  cleaned = url.strip().strip('"').strip("'")
  cleaned = re.sub(r"([?&])channel_binding=[^&]*(&|$)", r"\1", cleaned)
  cleaned = cleaned.rstrip("?&")
  return cleaned


async def init_db() -> None:
  global db_pool
  if not DATABASE_URL:
    print("DB WARNING: DATABASE_URL is empty")
    return
  if asyncpg is None:
    print("DB WARNING: asyncpg is not installed")
    return

  dsn = _clean_db_url(DATABASE_URL)
  try:
    db_pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
    async with db_pool.acquire() as conn:
      await conn.execute("""
          CREATE TABLE IF NOT EXISTS chat_access (
              chat_id BIGINT PRIMARY KEY,
              active BOOLEAN NOT NULL DEFAULT FALSE,
              expires_at TIMESTAMPTZ NULL,
              title TEXT NOT NULL DEFAULT '',
              username TEXT NOT NULL DEFAULT '',
              in_group BOOLEAN NOT NULL DEFAULT TRUE,
              last_tariff_days INTEGER NULL
          );
      """)
      await conn.execute("""
          ALTER TABLE chat_access
          ADD COLUMN IF NOT EXISTS last_tariff_days INTEGER NULL;
      """)
      await conn.execute("""
          CREATE TABLE IF NOT EXISTS chat_games (
              chat_id BIGINT PRIMARY KEY,
              current_question JSONB NULL,
              scores JSONB NOT NULL DEFAULT '{}'::jsonb
          );
      """)
      await conn.execute("""
          CREATE TABLE IF NOT EXISTS global_opt_in (
              user_id BIGINT PRIMARY KEY
          );
      """)
    print("DB CONNECTED")
  except Exception as exc:
    db_pool = None
    print(f"DB ERROR in init_db: {exc}")


def _parse_iso_dt(value: str | None) -> datetime | None:
  if not value:
    return None
  try:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
      dt = dt.replace(tzinfo=timezone.utc)
    return dt
  except ValueError:
    return None


def load_access_from_json() -> dict:
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
            "username": str(info.get("username") or "").strip().lstrip("@"),
            "in_group": bool(info.get("in_group", True)),
            "last_tariff_days": info.get("last_tariff_days"),
        }
    return restored
  except (json.JSONDecodeError, OSError, ValueError):
    return {}


def save_access_to_json() -> None:
  serializable = {
      str(cid): {
          "active": bool(info.get("active", False)),
          "expires_at": info.get("expires_at"),
          "title": str(info.get("title") or ""),
          "username": str(info.get("username") or "").strip().lstrip("@"),
          "in_group": bool(info.get("in_group", True)),
          "last_tariff_days": info.get("last_tariff_days"),
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


async def load_access_async() -> dict:
  json_data = load_access_from_json()
  if not db_pool:
    return json_data
  try:
    async with db_pool.acquire() as conn:
      rows = await conn.fetch(
          "SELECT chat_id, active, expires_at, title, username, in_group,"
          " last_tariff_days FROM chat_access"
      )
    db_data = {}
    for r in rows:
      exp_dt = r["expires_at"]
      db_data[int(r["chat_id"])] = {
          "active": bool(r["active"]),
          "expires_at": exp_dt.isoformat() if exp_dt else None,
          "title": str(r["title"] or ""),
          "username": str(r["username"] or "").strip().lstrip("@"),
          "in_group": bool(r["in_group"]),
          "last_tariff_days": r["last_tariff_days"],
      }
    for cid, info in json_data.items():
      if cid not in db_data:
        db_data[cid] = info
        await upsert_chat_access_db(cid, info)
    return db_data
  except Exception as exc:
    print(f"DB ERROR in load_access_async: {exc}")
    return json_data


async def upsert_chat_access_db(chat_id: int, info: dict) -> None:
  if not db_pool:
    return
  exp_dt = _parse_iso_dt(info.get("expires_at"))
  try:
    async with db_pool.acquire() as conn:
      last_days_val = info.get("last_tariff_days")
      if last_days_val is not None:
        try:
          last_days_val = int(last_days_val)
        except (TypeError, ValueError):
          last_days_val = None
      await conn.execute(
          """
          INSERT INTO chat_access (chat_id, active, expires_at, title, username, in_group, last_tariff_days)
          VALUES ($1, $2, $3, $4, $5, $6, $7)
          ON CONFLICT (chat_id) DO UPDATE SET
              active = EXCLUDED.active,
              expires_at = EXCLUDED.expires_at,
              title = CASE WHEN EXCLUDED.title <> '' THEN EXCLUDED.title ELSE chat_access.title END,
              username = EXCLUDED.username,
              in_group = EXCLUDED.in_group,
              last_tariff_days = COALESCE(EXCLUDED.last_tariff_days, chat_access.last_tariff_days)
          """,
          chat_id,
          bool(info.get("active", False)),
          exp_dt,
          str(info.get("title") or ""),
          str(info.get("username") or "").strip().lstrip("@"),
          bool(info.get("in_group", True)),
          last_days_val,
      )
  except Exception as exc:
    print(f"DB ERROR in upsert_chat_access_db: {exc}")


async def persist_chat_access(chat_id: int | None = None) -> None:
  save_access_to_json()
  if not db_pool:
    return
  if chat_id is not None and chat_id in chat_access:
    await upsert_chat_access_db(chat_id, chat_access[chat_id])
  else:
    for cid, info in chat_access.items():
      await upsert_chat_access_db(cid, info)


async def delete_chat_access_db(chat_id: int) -> None:
  if not db_pool:
    return
  try:
    async with db_pool.acquire() as conn:
      await conn.execute("DELETE FROM chat_access WHERE chat_id = $1", chat_id)
      await conn.execute("DELETE FROM chat_games WHERE chat_id = $1", chat_id)
  except Exception as exc:
    print(f"DB ERROR in delete_chat_access_db: {exc}")


async def remove_chat_record(chat_id: int) -> None:
  chat_access.pop(chat_id, None)
  games.pop(chat_id, None)
  pending_buyers.pop(chat_id, None)
  save_access_to_json()
  save_state_to_json()
  await delete_chat_access_db(chat_id)


async def record_group_seen(
    chat: types.Chat, in_group: bool | None = None
) -> None:
  if chat.type == "private":
    return
  existing = chat_access.get(
      chat.id,
      {
          "active": False,
          "expires_at": None,
          "title": "",
          "username": "",
          "in_group": True,
      },
  )
  if chat.title:
    existing["title"] = chat.title
  existing["username"] = (
      chat.username.strip().lstrip("@")
      if getattr(chat, "username", None)
      else ""
  )
  if in_group is not None:
    existing["in_group"] = in_group
  chat_access[chat.id] = existing
  await persist_chat_access(chat.id)


async def fetch_live_chat_info(chat_id: int) -> dict | None:
  try:
    chat_obj = await bot.get_chat(chat_id=chat_id)
    if chat_obj.type == "private":
      return None

    title = (chat_obj.title or "").strip()
    username = (chat_obj.username or "").strip().lstrip("@")
    invite_link = (getattr(chat_obj, "invite_link", None) or "").strip()

    if not username and not invite_link:
      try:
        invite_link = (await bot.export_chat_invite_link(chat_id=chat_id)).strip()
      except Exception:
        invite_link = ""

    existing = chat_access.get(
        chat_id,
        {
            "active": False,
            "expires_at": None,
            "title": "",
            "username": "",
            "in_group": True,
        },
    )
    if title:
      existing["title"] = title
    existing["username"] = username
    existing["in_group"] = True
    chat_access[chat_id] = existing
    await persist_chat_access(chat_id)

    return {
        "title": title,
        "username": username,
        "invite_link": invite_link,
    }
  except Exception:
    existing = chat_access.get(chat_id)
    if existing and existing.get("in_group", True):
      existing["in_group"] = False
      await persist_chat_access(chat_id)
    return None


async def is_group_active(chat_id: int) -> bool:
  info = chat_access.get(chat_id)
  if not info or not info.get("active"):
    return False
  expires_at = info.get("expires_at")
  if not expires_at:
    return True
  exp_dt = _parse_iso_dt(expires_at)
  if exp_dt is None:
    return False
  if datetime.now(timezone.utc) >= exp_dt:
    info["active"] = False
    await persist_chat_access(chat_id)
    return False
  return True


def get_expiry_text(chat_id: int) -> str:
  info = chat_access.get(chat_id)
  if not info or not info.get("active"):
    return "Uyg'otilmagan"
  expires_at = info.get("expires_at")
  if not expires_at:
    return "Cheksiz"
  exp_dt = _parse_iso_dt(expires_at)
  if exp_dt is None:
    return str(expires_at)
  return exp_dt.astimezone(TASHKENT_TZ).strftime(
      "%Y-%m-%d %H:%M"
  )


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
  friday_now = is_friday_tashkent()
  t_15d = "🔥 15 kunga (50% + 5 kun)" if friday_now else "15 kunga"
  t_1m = "🔥 1 oyga (50% + 5 kun)" if friday_now else "1 oyga"
  t_2m = "🔥 2 oyga (50% + 5 kun)" if friday_now else "2 oyga"
  t_3m = "🔥 3 oyga (50% + 5 kun)" if friday_now else "3 oyga"
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text=t_15d, callback_data=f"tar:15d:{cid_part}"
              ),
              InlineKeyboardButton(
                  text=t_1m, callback_data=f"tar:1m:{cid_part}"
              ),
          ],
          [
              InlineKeyboardButton(
                  text=t_2m, callback_data=f"tar:2m:{cid_part}"
              ),
              InlineKeyboardButton(
                  text=t_3m, callback_data=f"tar:3m:{cid_part}"
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
  if is_friday_tashkent():
    return (
        "🕌 <b>JUMA AKSIYASI FAOL!</b> Barcha tariflarga <b>50% CHEGIRMA</b>"
        " va qo'shimcha <b>+5 KUN BONUS</b>!\n"
        f"{group_line}"
        "• 15 kun uchun — <s>12 000</s> <b>6 000 so'm</b> (15 + 5 = <b>20 kun</b>)\n"
        "• 1 oy uchun — <s>20 000</s> <b>10 000 so'm</b> (30 + 5 = <b>35 kun</b>)\n"
        "• 2 oy uchun — <s>35 000</s> <b>17 500 so'm</b> (60 + 5 = <b>65 kun</b>)\n"
        "• 3 oy uchun — <s>50 000</s> <b>25 000 so'm</b> (90 + 5 = <b>95 kun</b>)\n\n"
        "Botni uyg'otmoqchi bo'lgan muddatingizni tanlang 👇"
    )
  return (
      "Botdan foydalanish uchun to'lov qilish zarur! Iltimos, quyidagi"
      f" tariflardan birini tanlang!\n{group_line}"
      "15 kun uchun - 12 000 so'm\n"
      "1 oy uchun - 20 000 so'm\n"
      "2 oy uchun - 35 000 so'm\n"
      "3 oy uchun - 50 000 so'm!\n\n"
      "Botni uyg'otmoqchi bo'lgan muddatingizni tanlang 👇"
  )


def build_activate_command(target_cid: int, days: int | None) -> str:
  cid_str = str(target_cid) if target_cid != 0 else "<guruh_id>"
  if days is not None:
    return f"/faollashtir {cid_str} {days}"
  return f"/faollashtir {cid_str}"


def resolve_payer_data(
    target_cid: int,
    buyer_id: int | None = None,
    fallback_user: types.User | None = None,
) -> tuple[int, dict]:
  """Guruhni uyg'otgan foydalanuvchi ma'lumotini qaytaradi (avval to'lov qilgan foydalanuvchini oladi)."""
  if buyer_id and buyer_id in pending_receipts:
    r = pending_receipts[buyer_id]
    if r.get("buyer_data"):
      return int(buyer_id), r["buyer_data"]

  if target_cid in pending_buyers:
    b = pending_buyers[target_cid]
    if not buyer_id or b.get("id") == buyer_id:
      return int(b.get("id") or 0), b

  if buyer_id:
    return int(buyer_id), {
        "id": int(buyer_id),
        "name": "Foydalanuvchi",
        "username": "",
    }

  u_data = build_user_data(fallback_user)
  return u_data["id"], u_data


async def activate_group_and_notify(
    target_cid: int,
    days: int | None,
    admin_user: types.User | None,
    buyer_id: int | None = None,
) -> str:
  """Guruhni uyg'otadi, guruhga havolasiz xabar yuboradi va pin qiladi."""
  expires_iso = None
  if days is not None and days > 0:
    exp_dt = datetime.now(timezone.utc) + timedelta(days=days)
    expires_iso = exp_dt.isoformat()
  else:
    days = None

  existing = chat_access.get(target_cid, {})
  saved_tariff_days = int(days) if days is not None else 0
  chat_access[target_cid] = {
      "active": True,
      "expires_at": expires_iso,
      "title": existing.get("title", ""),
      "username": existing.get("username", ""),
      "in_group": existing.get("in_group", True),
      "last_tariff_days": saved_tariff_days,
  }
  await persist_chat_access(target_cid)

  payer_uid, payer_data = resolve_payer_data(
      target_cid, buyer_id=buyer_id, fallback_user=admin_user
  )
  payer_mention = format_mention_html(payer_uid, payer_data)

  pending_buyers.pop(target_cid, None)

  if days is not None:
    group_notice = (
        f"Botni {payer_mention} {days} kunga uyg'otdi. Bot {days} kun"
        " davomida uxlamasdan xizmatingizda!"
    )
  else:
    group_notice = (
        f"Botni {payer_mention} 1 umrlikka uyg'otdi. Bot bir umr"
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


def _normalize_restored_games(raw: dict) -> dict:
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


def load_state_from_json() -> dict:
  if not os.path.exists(STATE_FILE):
    return {}
  try:
    with open(STATE_FILE, "r", encoding="utf-8") as f:
      raw = json.load(f)
    if not isinstance(raw, dict):
      return {}
    return _normalize_restored_games(raw)
  except (json.JSONDecodeError, OSError, ValueError):
    return {}


def save_state_to_json() -> None:
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


async def upsert_chat_game_db(chat_id: int, data: dict) -> None:
  if not db_pool:
    return
  scores_serializable = {
      str(uid): {
          "name": str(info.get("name") or "O'yinchi"),
          "username": str(info.get("username") or "").strip(),
          "score": int(info.get("score", 0)),
      }
      for uid, info in data.get("scores", {}).items()
  }
  current_q_json = (
      json.dumps(data.get("current_question"), ensure_ascii=False)
      if data.get("current_question") is not None
      else None
  )
  scores_json = json.dumps(scores_serializable, ensure_ascii=False)
  try:
    async with db_pool.acquire() as conn:
      await conn.execute(
          """
          INSERT INTO chat_games (chat_id, current_question, scores)
          VALUES ($1, $2::jsonb, $3::jsonb)
          ON CONFLICT (chat_id) DO UPDATE SET
              current_question = EXCLUDED.current_question,
              scores = EXCLUDED.scores
          """,
          chat_id,
          current_q_json,
          scores_json,
      )
  except Exception as exc:
    print(f"DB ERROR in upsert_chat_game_db: {exc}")


async def load_state_async() -> dict:
  json_data = load_state_from_json()
  if not db_pool:
    return json_data
  try:
    async with db_pool.acquire() as conn:
      rows = await conn.fetch(
          "SELECT chat_id, current_question, scores FROM chat_games"
      )
    raw_map = {}
    for r in rows:
      cq = r["current_question"]
      sc = r["scores"]
      if isinstance(cq, str):
        cq = json.loads(cq)
      if isinstance(sc, str):
        sc = json.loads(sc)
      raw_map[str(r["chat_id"])] = {
          "current_question": cq,
          "scores": sc if isinstance(sc, dict) else {},
      }
    restored = _normalize_restored_games(raw_map)
    for cid, gdata in json_data.items():
      if cid not in restored:
        restored[cid] = gdata
        await upsert_chat_game_db(cid, gdata)
    return restored
  except Exception as exc:
    print(f"DB ERROR in load_state_async: {exc}")
    return json_data


async def persist_state(chat_id: int | None = None) -> None:
  save_state_to_json()
  if not db_pool:
    return
  if chat_id is not None and chat_id in games:
    await upsert_chat_game_db(chat_id, games[chat_id])
  else:
    for cid, gdata in games.items():
      await upsert_chat_game_db(cid, gdata)


async def get_chat_game(chat_id: int) -> dict:
  if chat_id not in games:
    games[chat_id] = {
        "current_question": None,
        "scores": {},
    }
    await persist_state(chat_id)
  return games[chat_id]


def load_global_excluded_from_json() -> set[int]:
  if not os.path.exists(GLOBAL_EXCLUDED_FILE):
    return set()
  try:
    with open(GLOBAL_EXCLUDED_FILE, "r", encoding="utf-8") as f:
      raw = json.load(f)
    if not isinstance(raw, list):
      return set()
    result = set()
    for item in raw:
      try:
        uid = int(item)
        if uid != 0:
          result.add(uid)
      except (TypeError, ValueError):
        continue
    return result
  except (json.JSONDecodeError, OSError, ValueError):
    return set()


def save_global_excluded_to_json() -> None:
  serializable = sorted(int(uid) for uid in global_excluded_users if int(uid) != 0)
  try:
    with open(TEMP_GLOBAL_EXCLUDED_FILE, "w", encoding="utf-8") as f:
      json.dump(serializable, f, ensure_ascii=False, indent=2)
      f.flush()
      os.fsync(f.fileno())
    os.replace(TEMP_GLOBAL_EXCLUDED_FILE, GLOBAL_EXCLUDED_FILE)
  except OSError:
    pass


async def load_global_excluded_async() -> set[int]:
  json_ids = load_global_excluded_from_json()
  if not db_pool:
    return json_ids
  try:
    async with db_pool.acquire() as conn:
      rows = await conn.fetch("SELECT user_id FROM global_opt_in")
    db_ids = {int(r["user_id"]) for r in rows if int(r["user_id"]) != 0}
    for uid in json_ids:
      if uid not in db_ids:
        db_ids.add(uid)
        await set_global_excluded_db(uid, True)
    return db_ids
  except Exception as exc:
    print(f"DB ERROR in load_global_excluded_async: {exc}")
    return json_ids


async def set_global_excluded_db(user_id: int, excluded: bool) -> None:
  if not db_pool or user_id == 0:
    return
  try:
    async with db_pool.acquire() as conn:
      if excluded:
        await conn.execute(
            """
            INSERT INTO global_opt_in (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )
      else:
        await conn.execute(
            "DELETE FROM global_opt_in WHERE user_id = $1",
            user_id,
        )
  except Exception as exc:
    print(f"DB ERROR in set_global_excluded_db: {exc}")


async def add_global_excluded_user(user_id: int) -> None:
  if user_id == 0:
    return
  global_excluded_users.add(user_id)
  save_global_excluded_to_json()
  await set_global_excluded_db(user_id, True)


async def remove_global_excluded_user(user_id: int) -> None:
  if user_id == 0:
    return
  global_excluded_users.discard(user_id)
  save_global_excluded_to_json()
  await set_global_excluded_db(user_id, False)


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
  await persist_state(message.chat.id)
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


@dp.my_chat_member()
async def on_my_chat_member(event: types.ChatMemberUpdated):
  """Bot guruhga qo'shilganda, chiqarilganda yoki nomi/username o'zgarganda saqlaydi."""
  if event.chat.type == "private":
    return
  new_status = event.new_chat_member.status
  in_group = new_status in {"member", "administrator", "creator"}
  if not in_group:
    await remove_chat_record(event.chat.id)
    return
  await record_group_seen(event.chat, in_group=True)


@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject):
  if message.chat.type != "private":
    await record_group_seen(message.chat, in_group=True)
    if not await is_group_active(message.chat.id):
      await send_sleeping_group_message(message)
      return

  args = (command.args or "").strip()
  if message.chat.type == "private" and args.startswith("pay_"):
    raw_cid = args.replace("pay_", "", 1).strip()
    target_cid = None
    try:
      target_cid = int(raw_cid)
    except ValueError:
      target_cid = None

    if target_cid is not None and message.from_user:
      pending_buyers[target_cid] = build_user_data(message.from_user)

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


async def send_long_html_blocks(message: types.Message, blocks: list[str], chunk_size: int = 3500) -> None:
  if not blocks:
    return
  current_chunk = ""
  for block in blocks:
    separator = "\n\n" if current_chunk else ""
    if len(current_chunk) + len(separator) + len(block) > chunk_size:
      if current_chunk:
        await message.answer(current_chunk, parse_mode=ParseMode.HTML)
      current_chunk = block
    else:
      current_chunk += separator + block
  if current_chunk:
    await message.answer(current_chunk, parse_mode=ParseMode.HTML)


@dp.message(Command("guruhlar"), F.chat.type == "private")
async def cmd_guruhlar(message: types.Message):
  """Faqat shaxsiy chatda va faqat ADMIN_ID uchun guruhlar ro'yxatini ko'rsatadi."""
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  known_ids = set(chat_access.keys()) | set(games.keys())
  if ADMIN_ID:
    known_ids.discard(ADMIN_ID)
  if not known_ids:
    await message.answer("Hozircha hech qanday guruh qayd etilmagan.")
    return

  lines = [f"📋 <b>Guruhlar ro'yxati (Jami: {len(known_ids)} ta):</b>\n"]
  for idx, cid in enumerate(sorted(known_ids), start=1):
    info = chat_access.get(cid, {})
    live = await fetch_live_chat_info(cid)

    if live:
      title = live["title"] or str(info.get("title") or f"Guruh ({cid})")
      group_username = live["username"]
      invite_link = live.get("invite_link", "")
      presence = "Guruhda bor"
    else:
      title = str(info.get("title") or f"Guruh ({cid})")
      group_username = str(info.get("username") or "").strip().lstrip("@") if int(cid) < 0 else ""
      invite_link = ""
      presence = "Chiqarilgan / Kirib bo'lmadi"

    safe_title = html.escape(title)
    if group_username:
      link_line = f"   • Havola: https://t.me/{html.escape(group_username)}\n"
    elif invite_link:
      link_line = f"   • Havola: {html.escape(invite_link)}\n"
    else:
      link_line = "   • Havola: Yopiq guruh (botga admin huquqi berilmagan)\n"

    active = await is_group_active(cid)
    status_label = "Uyg'oq 🟢" if active else "Uxlayabdi 😴"
    expiry_str = get_expiry_text(cid)

    lines.append(
        f"{idx}. <b>{safe_title}</b> (<code>{cid}</code>)\n"
        f"{link_line}"
        f"   • Holat: <b>{status_label}</b> ({presence})\n"
        f"   • Muddat: <b>{html.escape(expiry_str)}</b>"
    )

  await send_long_html_blocks(message, lines)


async def _bot_is_group_admin(chat_id: int) -> bool:
  try:
    me = await bot.get_me()
    member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
    return member.status in {"administrator", "creator"}
  except Exception:
    return False


async def broadcast_friday_promo() -> tuple[int, int]:
  """Bot mavjud bo'lgan barcha guruhlarga Juma aksiyasi e'lonini yuboradi."""
  known_ids = set(chat_access.keys()) | set(games.keys())
  if ADMIN_ID:
    known_ids.discard(ADMIN_ID)
  group_ids = [cid for cid in sorted(known_ids) if int(cid) < 0]

  sent_count = 0
  failed_count = 0
  for cid in group_ids:
    info = chat_access.get(cid, {})
    if info and not info.get("in_group", True):
      continue
    try:
      await bot.send_message(
          chat_id=cid,
          text=FRIDAY_PROMO_TEXT,
          parse_mode=ParseMode.HTML,
      )
      sent_count += 1
    except Exception:
      failed_count += 1
    await asyncio.sleep(0.05)
  return sent_count, failed_count


async def friday_promo_scheduler() -> None:
  """Har hafta faqat Juma kuni (Toshkent vaqti bilan) har 3 soatda avtomatik e'lon yuboradi."""
  last_slot_key: str | None = None
  while True:
    try:
      now_tashkent = datetime.now(TASHKENT_TZ)
      if now_tashkent.weekday() == 4 and (now_tashkent.hour % 3 == 0):
        slot_key = now_tashkent.strftime("%Y-%m-%d-%H")
        if slot_key != last_slot_key:
          last_slot_key = slot_key
          sent_cnt, fail_cnt = await broadcast_friday_promo()
          print(
              f"FRIDAY PROMO [{slot_key}]: sent={sent_cnt}, failed={fail_cnt}"
          )
    except Exception as exc:
      print(f"FRIDAY PROMO SCHEDULER ERROR: {exc}")
    await asyncio.sleep(30)


@dp.message(Command("juma_elon", "jumaelon"), F.chat.type == "private")
async def cmd_juma_elon(message: types.Message):
  """Faqat ADMIN_ID uchun: Juma aksiyasi e'lonini istagan vaqtda darhol guruhlarga yuboradi."""
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  sent_cnt, fail_cnt = await broadcast_friday_promo()
  await message.answer(
      "🕌 <b>Juma aksiyasi e'loni yuborildi!</b>\n\n"
      f"• Yuborildi: <b>{sent_cnt} ta guruhga</b>\n"
      f"• Yuborilmadi: <b>{fail_cnt} ta guruh</b>",
      parse_mode=ParseMode.HTML,
  )


@dp.message(Command("elon"), F.chat.type == "private")
async def cmd_elon(message: types.Message, command: CommandObject):
  """Faqat ADMIN_ID uchun: bot admin bo'lgan guruhlarga e'lon yuboradi."""
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  reply_msg = message.reply_to_message
  text_arg = (command.args or "").strip()

  if not reply_msg and not text_arg:
    await message.answer(
        "📢 <b>E'lon yuborish qoidasi:</b>\n\n"
        "• Matn bilan: <code>/elon E'lon matni</code>\n"
        "• Yoki biror xabar/rasm/videoga <b>reply</b> qilib: <code>/elon</code>",
        parse_mode=ParseMode.HTML,
    )
    return

  known_ids = set(chat_access.keys()) | set(games.keys())
  if ADMIN_ID:
    known_ids.discard(ADMIN_ID)
  group_ids = [cid for cid in sorted(known_ids) if int(cid) < 0]

  if not group_ids:
    await message.answer("Hozircha bazada hech qanday guruh topilmadi.")
    return

  sent_count = 0
  skipped_not_admin = 0
  failed_count = 0

  for cid in group_ids:
    if not await _bot_is_group_admin(cid):
      skipped_not_admin += 1
      continue

    try:
      if reply_msg:
        await bot.copy_message(
            chat_id=cid,
            from_chat_id=reply_msg.chat.id,
            message_id=reply_msg.message_id,
        )
      else:
        await bot.send_message(chat_id=cid, text=text_arg)
      sent_count += 1
    except Exception:
      failed_count += 1
    await asyncio.sleep(0.05)

  await message.answer(
      "📢 <b>E'lon tarqatish yakunlandi:</b>\n\n"
      f"• Yuborildi: <b>{sent_count} ta guruhga</b>\n"
      f"• Bot admin emas: <b>{skipped_not_admin} ta guruh</b>\n"
      f"• Yuborilmadi (xato): <b>{failed_count} ta guruh</b>",
      parse_mode=ParseMode.HTML,
  )


@dp.message(Command("chiq"))
async def cmd_chiq(message: types.Message, command: CommandObject):
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  args = (command.args or "").strip().split()
  if not args:
    await message.answer(
        "Foydalanish: <code>/chiq &lt;chat_id&gt;</code>",
        parse_mode=ParseMode.HTML,
    )
    return

  try:
    target_cid = int(args[0])
  except ValueError:
    await message.answer("Chat ID noto'g'ri formatda.")
    return

  left_ok = False
  leave_err = ""
  try:
    left_ok = bool(await bot.leave_chat(chat_id=target_cid))
  except Exception as exc:
    leave_err = str(exc)

  await remove_chat_record(target_cid)

  if left_ok:
    await message.answer(
        f"🚪 Bot <code>{target_cid}</code> guruhidan chiqdi va"
        " <code>/guruhlar</code> ro'yxatidan o'chirildi.",
        parse_mode=ParseMode.HTML,
    )
  else:
    err_note = f" ({html.escape(leave_err)})" if leave_err else ""
    await message.answer(
        f"⚠️ Guruhdan chiqishda xabar{err_note}, lekin"
        f" <code>{target_cid}</code> bazadan va <code>/guruhlar</code>"
        " ro'yxatidan o'chirildi.",
        parse_mode=ParseMode.HTML,
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
  tariff = get_effective_tariff(code)
  try:
    target_cid = int(cid_str)
  except ValueError:
    target_cid = 0

  if callback.from_user:
    buyer_data = build_user_data(callback.from_user)
    if target_cid != 0:
      pending_buyers[target_cid] = buyer_data
    if tariff:
      pending_receipts[callback.from_user.id] = {
          "code": code,
          "label": tariff["label"],
          "days": tariff["days"],
          "base_days": tariff["base_days"],
          "bonus_days": tariff["bonus_days"],
          "price": tariff["price"],
          "is_friday_promo": tariff["is_friday_promo"],
          "chat_id": target_cid,
          "buyer_data": buyer_data,
      }

  cid_note = f"\n\nGuruh ID: <code>{target_cid}</code>" if target_cid != 0 else ""
  await callback.message.answer(
      "📸 Iltimos, to'lov qilinganligi to'g'risida rasm yoki chek"
      f" (screenshot) yuboring.{cid_note}",
      parse_mode=ParseMode.HTML,
  )
  await callback.answer()


def format_previous_tariff_text(target_cid: int) -> str:
  if not target_cid:
    return "Avvalgi tarif: <b>Ma'lumot yo'q</b>"
  info = chat_access.get(target_cid)
  if not info or "last_tariff_days" not in info or info.get("last_tariff_days") is None:
    return "Avvalgi tarif: <b>Hali faollashtirilmagan (yangi guruh)</b>"
  prev_days = info.get("last_tariff_days")
  if prev_days == 0:
    return "Avvalgi tarif: <b>Cheksiz (1 umrlik) tarifda ishlagan</b>"
  if prev_days == 1:
    return "Avvalgi tarif: <b>1 kunlik tarifda ishlagan</b>"
  if prev_days == 30:
    return "Avvalgi tarif: <b>1 oylik (30 kun) tarifda ishlagan</b>"
  if prev_days == 60:
    return "Avvalgi tarif: <b>2 oylik (60 kun) tarifda ishlagan</b>"
  if prev_days == 90:
    return "Avvalgi tarif: <b>3 oylik (90 kun) tarifda ishlagan</b>"
  return f"Avvalgi tarif: <b>{prev_days} kunlik tarifda ishlagan</b>"


@dp.callback_query(F.data.startswith("tar:"))
async def cb_select_tariff(callback: types.CallbackQuery):
  parts = (callback.data or "").split(":")
  if len(parts) != 3:
    await callback.answer()
    return

  _, code, cid_str = parts
  tariff = get_effective_tariff(code)
  if not tariff:
    await callback.answer("Noto'g'ri tarif", show_alert=True)
    return

  try:
    target_cid = int(cid_str)
  except ValueError:
    target_cid = 0

  user = callback.from_user
  buyer_data = build_user_data(user)
  if target_cid != 0 and user:
    pending_buyers[target_cid] = buyer_data

  admin_user_link = format_admin_profile_link_html(
      user.id if user else 0,
      buyer_data,
  )

  duration_title = tariff["label"].replace("ga", "lik")
  card_display = (
      html.escape(CARD_NUMBER) if CARD_NUMBER else "<i>Karta kiritilmagan</i>"
  )
  cid_line = f"\nGuruh ID: <code>{target_cid}</code>" if target_cid != 0 else ""

  if tariff["is_friday_promo"]:
    promo_header = (
        f"🕌 <b>JUMA AKSIYASI:</b> {html.escape(duration_title)} obuna"
        f" (<b>{tariff['base_days']} + {tariff['bonus_days']} = {tariff['days']} kun</b>)\n"
        f"50% chegirma bilan: <s>{html.escape(tariff['base_price'])}</s> → <b>{html.escape(tariff['price'])}</b>\n\n"
    )
    text = (
        f"{promo_header}"
        f"<code>{card_display}</code>\n\n"
        f"raqamiga <b>{html.escape(tariff['price'])}</b> o'tkazasiz🙂{cid_line}\n"
        "To'lov qilganingizdan so'ng pastdagi tugmachani bosib 👇 to'lov"
        " qilinganligi to'g'risida biror nimani (screenshot, rasm va h.k)"
        " yuborasiz"
    )
  else:
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
    if tariff["is_friday_promo"]:
      days_info = (
          f"{tariff['base_days']} + {tariff['bonus_days']} = {days_val} kun"
          " (Juma 50% chegirma)"
      )
    else:
      days_info = f"{days_val} kun" if days_val is not None else "Cheksiz"
    activate_cmd = build_activate_command(target_cid, days_val)
    prev_tariff_line = format_previous_tariff_text(target_cid)
    try:
      await bot.send_message(
          chat_id=ADMIN_ID,
          text=(
              "💳 <b>Yangi tarif tanlandi!</b>\n\n"
              f"Foydalanuvchi: {admin_user_link}\n"
              f"Guruh ID: <code>{target_cid}</code>\n"
              f"{prev_tariff_line}\n"
              f"Yangi tarif: <b>{html.escape(tariff['label'])}</b>"
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
      target_cid,
      days,
      callback.from_user,
      buyer_id=buyer_id if buyer_id else None,
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
        "Masalan: <code>/faollashtir -1001234567890 15</code>\n"
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
        "Foydalanish: <code>/ochirish &lt;chat_id&gt;</code>",
        parse_mode=ParseMode.HTML,
    )
    return

  try:
    target_cid = int(args[0])
  except ValueError:
    await message.answer("Chat ID noto'g'ri formatda.")
    return

  existing = chat_access.get(
      target_cid,
      {
          "active": False,
          "expires_at": None,
          "title": "",
          "username": "",
          "in_group": True,
      },
  )
  existing["active"] = False
  existing["expires_at"] = None
  chat_access[target_cid] = existing
  await persist_chat_access(target_cid)

  sleep_notice = (
      "To'lov qilish bo'yicha ayyorlik ishlatilinib uyg'otilganini bilgan"
      " bot yana uyquga ketti!"
  )
  group_sent = False
  group_pinned = False
  try:
    sent_msg = await bot.send_message(chat_id=target_cid, text=sleep_notice)
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
    extra_status = "\n📩 Guruhga xabar yuborildi va qadab (pin) qo'yildi."
  elif group_sent:
    extra_status = (
        "\n📩 Guruhga xabar yuborildi, lekin qadab bo'lmadi (botga pin huquqi"
        " berilganini tekshiring)."
    )
  else:
    extra_status = (
        "\n⚠️ Guruhga xabar yuborib bo'lmadi (bot guruhda bormi tekshiring)."
    )

  await message.answer(
      f"🔒 Guruh o'chirildi: <code>{target_cid}</code>{extra_status}",
      parse_mode=ParseMode.HTML,
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

  active = await is_group_active(target_cid)
  status_label = "Uyg'oq" if active else "Uxlayabdi"
  await message.answer(
      f"Chat ID: <code>{target_cid}</code>\n"
      f"Holat: <b>{status_label}</b>\n"
      f"Muddat: <b>{html.escape(get_expiry_text(target_cid))}</b>",
      parse_mode=ParseMode.HTML,
  )


@dp.message(Command("next"))
async def cmd_next(message: types.Message):
  if message.chat.type != "private":
    await record_group_seen(message.chat, in_group=True)
    if not await is_group_active(message.chat.id):
      await send_sleeping_group_message(message)
      return

  questions = load_questions()
  if not questions:
    await message.answer("Hozircha questions.json faylida savollar yo'q!")
    return

  chat_game = await get_chat_game(message.chat.id)
  await send_question(message, chat_game, questions)


@dp.message(Command("reyting"))
async def cmd_reyting(message: types.Message):
  if message.chat.type != "private":
    await record_group_seen(message.chat, in_group=True)
    if not await is_group_active(message.chat.id):
      await send_sleeping_group_message(message)
      return

  chat_game = await get_chat_game(message.chat.id)
  await message.answer(
      format_scores_text(chat_game["scores"]), parse_mode=ParseMode.HTML
  )


def _canonical_entity_key(uid_int: int, info: dict) -> tuple[str, str]:
  """Bir xil kanal yoki foydalanuvchining turli ID/hash yozuvlarini bitta kalitga birlashtiradi."""
  uname = str(info.get("username") or "").strip().lstrip("@").lower()
  if uname:
    return ("username", uname)
  name = str(info.get("name") or "").strip().lower()
  if name and not name.startswith("foydalanuvchi (") and not name.startswith("kanal (-"):
    return ("name", name)
  return ("id", str(uid_int))

def _collect_excluded_entity_keys() -> set[tuple[str, str]]:
  excluded_keys: set[tuple[str, str]] = set()
  if not global_excluded_users:
    return excluded_keys
  for uid_int in global_excluded_users:
    excluded_keys.add(("id", str(uid_int)))
  for gdata in games.values():
    if not isinstance(gdata, dict):
      continue
    scores = gdata.get("scores", {})
    if not isinstance(scores, dict):
      continue
    for uid, info in scores.items():
      if not isinstance(info, dict):
        continue
      try:
        uid_int = int(uid)
      except (TypeError, ValueError):
        continue
      if uid_int in global_excluded_users:
        uname = str(info.get("username") or "").strip().lstrip("@").lower()
        name = str(info.get("name") or "").strip().lower()
        if uname:
          excluded_keys.add(("username", uname))
        if name and not name.startswith("foydalanuvchi (") and not name.startswith("kanal (-"):
          excluded_keys.add(("name", name))
  return excluded_keys


def format_global_top20_text(all_games: dict) -> str:
  totals: dict[tuple[str, str], dict] = {}
  excluded_keys = _collect_excluded_entity_keys()

  for gdata in all_games.values():
    if not isinstance(gdata, dict):
      continue
    scores = gdata.get("scores", {})
    if not isinstance(scores, dict):
      continue
    for uid, info in scores.items():
      if not isinstance(info, dict):
        continue
      try:
        uid_int = int(uid)
      except (TypeError, ValueError):
        continue
      if uid_int in global_excluded_users:
        continue
      ekey = _canonical_entity_key(uid_int, info)
      if ekey in excluded_keys or ("id", str(uid_int)) in excluded_keys:
        continue
      raw_pts = info.get("score", 0)
      try:
        pts = int(float(raw_pts))
      except (TypeError, ValueError):
        pts = 0
      if pts <= 0:
        continue
      name = str(info.get("name") or "O'yinchi").strip() or "O'yinchi"
      if ekey not in totals:
        totals[ekey] = {"name": name, "score": 0}
      else:
        if name and name != "O'yinchi":
          totals[ekey]["name"] = name
      totals[ekey]["score"] += pts

  if not totals:
    return "Hozircha guruhlarda umumiy reyting ballari yo'q."

  top_20 = sorted(
      totals.items(), key=lambda item: item[1]["score"], reverse=True
  )[:20]

  lines = ["🏆 <b>BARCHA GURUHLAR BO'YICHA UMUMIY REYTING (TOP 20):</b>\n"]
  for idx, (_, data) in enumerate(top_20, start=1):
    medal = (
        "🥇 "
        if idx == 1
        else ("🥈 " if idx == 2 else ("🥉 " if idx == 3 else "🎖️ "))
    )
    safe_name = html.escape(str(data.get("name") or "O'yinchi"))
    lines.append(f"{medal}{safe_name} — {data['score']} ball")

  return "\n".join(lines)


@dp.message(Command("umumiyreyting", "umumiyteyting"))
async def cmd_umumiyreyting(message: types.Message):
  if message.chat.type != "private":
    await record_group_seen(message.chat, in_group=True)
    if not await is_group_active(message.chat.id):
      await send_sleeping_group_message(message)
      return

  await message.answer(
      format_global_top20_text(games), parse_mode=ParseMode.HTML
  )


def _find_target_users_in_map(
    users_map: dict[int, dict], query: str, reply_user: types.User | None
) -> list[int]:
  matched_ids: list[int] = []
  if reply_user and reply_user.id in users_map:
    matched_ids.append(reply_user.id)
    return matched_ids

  q_clean = query.strip()
  if not q_clean:
    return matched_ids

  if q_clean.lstrip("-").isdigit():
    uid_target = int(q_clean)
    if uid_target in users_map:
      matched_ids.append(uid_target)
    return matched_ids

  q_lower = q_clean.lstrip("@").lower()
  for uid, info in users_map.items():
    if not isinstance(info, dict):
      continue
    uname = str(info.get("username") or "").strip().lstrip("@").lower()
    name = str(info.get("name") or "").strip().lower()
    if (uname and uname == q_lower) or (name and name == q_lower):
      matched_ids.append(uid)

  if not matched_ids and len(q_lower) >= 2:
    for uid, info in users_map.items():
      if not isinstance(info, dict):
        continue
      uname = str(info.get("username") or "").strip().lstrip("@").lower()
      name = str(info.get("name") or "").strip().lower()
      if (uname and q_lower in uname) or (name and q_lower in name):
        matched_ids.append(uid)

  return matched_ids


def _collect_all_known_users() -> dict[int, dict]:
  all_users_map: dict[int, dict] = {}
  for gdata in games.values():
    if not isinstance(gdata, dict):
      continue
    sc = gdata.get("scores", {})
    if not isinstance(sc, dict):
      continue
    for uid, info in sc.items():
      if isinstance(info, dict):
        try:
          uid_int = int(uid)
        except (TypeError, ValueError):
          continue
        if uid_int not in all_users_map:
          all_users_map[uid_int] = dict(info)
        else:
          if info.get("name") and info.get("name") != "O'yinchi":
            all_users_map[uid_int]["name"] = info["name"]
          if info.get("username"):
            all_users_map[uid_int]["username"] = info["username"]

  for uid_int in global_excluded_users:
    if uid_int not in all_users_map:
      all_users_map[uid_int] = {"name": f"Foydalanuvchi ({uid_int})", "username": ""}
  return all_users_map


@dp.message(Command("reytingdan_ochir"))
async def cmd_reytingdan_ochir(message: types.Message, command: CommandObject):
  """Faqat ADMIN_ID uchun: foydalanuvchini guruh reytingidan olib tashlaydi, BALLARNI O'CHIRMAYDI."""
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  raw_args = (command.args or "").strip()
  reply_user = (
      message.reply_to_message.from_user
      if message.reply_to_message and message.reply_to_message.from_user
      else None
  )
  if (
      not reply_user
      and message.reply_to_message
      and message.reply_to_message.sender_chat
  ):
    sc = message.reply_to_message.sender_chat
    reply_user = types.User(
        id=int(sc.id),
        is_bot=False,
        first_name=(sc.title or sc.username or "Kanal")[:64],
        username=sc.username,
    )

  target_cid = message.chat.id
  user_query = raw_args

  parts = raw_args.split(maxsplit=1)
  if parts and parts[0].startswith("-") and parts[0][1:].isdigit():
    target_cid = int(parts[0])
    user_query = parts[1].strip() if len(parts) > 1 else ""

  if target_cid not in games or not games[target_cid].get("scores"):
    await message.answer("Bu guruh reytingida hali hech kim yo'q.")
    return

  scores = games[target_cid]["scores"]
  matched_ids = _find_target_users_in_map(scores, user_query, reply_user)

  if not matched_ids:
    await message.answer(
        "❌ Foydalanuvchi topilmadi.\n"
        "Foydalanish:\n"
        "• Guruhda xabariga reply qilib: <code>/reytingdan_ochir</code>\n"
        "• Yoki: <code>/reytingdan_ochir &lt;user_id | @username | ism&gt;</code>\n"
        "• Shaxsiy chatdan: <code>/reytingdan_ochir &lt;guruh_id&gt; &lt;user_id | @username | ism&gt;</code>",
        parse_mode=ParseMode.HTML,
    )
    return

  if len(matched_ids) > 1:
    lines = ["⚠️ Bir nechta mos foydalanuvchi topildi, aniq ID bilan kiriting:\n"]
    for uid in matched_ids:
      u_name = html.escape(str(scores[uid].get("name") or "O'yinchi"))
      lines.append(f"• {u_name} — <code>{uid}</code> ({scores[uid].get('score', 0)} ball)")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
    return

  target_uid = matched_ids[0]
  scores[target_uid]["hidden_in_group"] = True
  await persist_state(target_cid)

  u_name = html.escape(str(scores[target_uid].get("name") or "O'yinchi"))
  u_score = int(scores[target_uid].get("score", 0))
  await message.answer(
      f"🚫 <b>{u_name}</b> (<code>{target_uid}</code>) guruh reytingidan"
      f" olib tashlandi (ballari saqlab qolindi: <b>{u_score} ball</b>).",
      parse_mode=ParseMode.HTML,
  )


@dp.message(Command("umumiydan_ochir"))
async def cmd_umumiydan_ochir(message: types.Message, command: CommandObject):
  """Faqat ADMIN_ID uchun: foydalanuvchini umumiy reytingdan chiqaradi, BALLARNI O'CHIRMAYDI."""
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  raw_args = (command.args or "").strip()
  reply_user = (
      message.reply_to_message.from_user
      if message.reply_to_message and message.reply_to_message.from_user
      else None
  )
  if (
      not reply_user
      and message.reply_to_message
      and message.reply_to_message.sender_chat
  ):
    sc = message.reply_to_message.sender_chat
    reply_user = types.User(
        id=int(sc.id),
        is_bot=False,
        first_name=(sc.title or sc.username or "Kanal")[:64],
        username=sc.username,
    )

  all_users_map = _collect_all_known_users()
  matched_ids = _find_target_users_in_map(all_users_map, raw_args, reply_user)

  if not matched_ids:
    await message.answer(
        "❌ Umumiy reyting ro'yxatidan foydalanuvchi topilmadi.\n"
        "Foydalanish:\n"
        "• Xabariga reply qilib: <code>/umumiydan_ochir</code>\n"
        "• Yoki: <code>/umumiydan_ochir &lt;user_id | @username | ism&gt;</code>",
        parse_mode=ParseMode.HTML,
    )
    return

  if len(matched_ids) > 1:
    lines = ["⚠️ Bir nechta mos foydalanuvchi topildi, aniq ID bilan kiriting:\n"]
    for uid in matched_ids:
      u_name = html.escape(str(all_users_map[uid].get("name") or "O'yinchi"))
      lines.append(f"• {u_name} — <code>{uid}</code>")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
    return

  target_uid = matched_ids[0]
  removed_name = html.escape(str(all_users_map[target_uid].get("name") or "O'yinchi"))
  await add_global_excluded_user(target_uid)

  await message.answer(
      f"🚫 <b>{removed_name}</b> (<code>{target_uid}</code>) umumiy"
      " reytingdan chiqarildi (ballari o'chirilmadi; yana yangi ball olsa"
      " ham umumiy reytingga qo'shilmaydi. Qayta kiritish uchun"
      f" <code>/umumiyga_qosh {target_uid}</code> buyrug'ini bering).",
      parse_mode=ParseMode.HTML,
  )


@dp.message(Command("reyting_tekshir"), F.chat.type == "private")
async def cmd_reyting_tekshir(message: types.Message, command: CommandObject):
  """Faqat ADMIN_ID uchun: foydalanuvchi yoki kanalning barcha guruhlardagi ballarini va umumiy reyting holatini ko'rsatadi."""
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  query = (command.args or "").strip()
  if not query:
    await message.answer(
        "Foydalanish: <code>/reyting_tekshir &lt;ism | @username | id | ball&gt;</code>",
        parse_mode=ParseMode.HTML,
    )
    return

  q_low = query.lstrip("@").lower()
  is_num = query.lstrip("-").isdigit()
  num_val = int(query) if is_num else None

  found_rows = []
  total_pts = 0

  for cid, gdata in games.items():
    if not isinstance(gdata, dict):
      continue
    sc = gdata.get("scores", {})
    if not isinstance(sc, dict):
      continue
    g_title = str(chat_access.get(cid, {}).get("title") or f"Guruh ({cid})")
    for uid, info in sc.items():
      if not isinstance(info, dict):
        continue
      try:
        uid_int = int(uid)
      except (TypeError, ValueError):
        continue
      u_name = str(info.get("name") or "")
      u_uname = str(info.get("username") or "").lstrip("@")
      try:
        pts = int(float(info.get("score", 0)))
      except (TypeError, ValueError):
        pts = 0
      match = False
      if is_num and (uid_int == num_val or pts == num_val):
        match = True
      elif q_low and (
          q_low in u_name.lower()
          or (u_uname and q_low in u_uname.lower())
          or (u_uname and q_low in f"@{u_uname.lower()}")
      ):
        match = True
      if match:
        total_pts += pts
        excl = "Ha (chiqarilgan)" if uid_int in global_excluded_users else "Yo'q"
        uname_str = f" (@{html.escape(u_uname)})" if u_uname else ""
        found_rows.append(
            f"• <b>{html.escape(g_title)}</b> (<code>{cid}</code>): "
            f"<b>{html.escape(u_name)}</b>{uname_str} (<code>{uid_int}</code>) — <b>{pts} ball</b> | Chiqarilganmi: <b>{excl}</b>"
        )

  if not found_rows:
    await message.answer(
        f"🔍 <code>{html.escape(query)}</code> bo'yicha hech qanday ball topilmadi.\n"
        f"Agar kanalni qo'lda 39700 ball bilan umumiy reytingga qo'shmoqchi bo'lsangiz:\n"
        f"<code>/umumiyga_qosh {html.escape(query)} | 39700</code>",
        parse_mode=ParseMode.HTML,
    )
    return

  text = (
      f"🔍 <b>Tekshiruv natijasi ({html.escape(query)}):</b>\n"
      f"Jami ball: <b>{total_pts} ball</b>\n\n"
      + "\n".join(found_rows)
  )
  await message.answer(text, parse_mode=ParseMode.HTML)


@dp.message(Command("ochirilganlar"), F.chat.type == "private")
async def cmd_ochirilganlar(message: types.Message):
  """Faqat ADMIN_ID uchun: umumiy reytingdan chiqarilgan foydalanuvchi va kanallar ro'yxatini ID bilan ko'rsatadi."""
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  if not global_excluded_users:
    await message.answer("Hozircha umumiy reytingdan chiqarilgan hech kim yo'q.")
    return

  all_users_map = _collect_all_known_users()
  lines = [f"📋 <b>Umumiy reytingdan chiqarilganlar ({len(global_excluded_users)} ta):</b>\n"]
  for idx_num, uid in enumerate(sorted(global_excluded_users), start=1):
    info = all_users_map.get(uid, {})
    u_name = html.escape(str(info.get("name") or f"Foydalanuvchi ({uid})"))
    uname = str(info.get("username") or "").strip().lstrip("@")
    uname_part = f" (@{html.escape(uname)})" if uname else ""
    lines.append(
        f"{idx_num}. <b>{u_name}</b>{uname_part} — <code>{uid}</code>\n"
        f"   Qayta qo'shish: <code>/umumiyga_qosh {uid}</code>"
    )

  await send_long_html_blocks(message, lines)


@dp.message(Command("umumiyga_qosh"))
async def cmd_umumiyga_qosh(message: types.Message, command: CommandObject):
  """Faqat ADMIN_ID uchun: foydalanuvchi yoki kanalni umumiy reytingga qo'shadi."""
  if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
    return

  raw_args = (command.args or "").strip()
  reply_user = (
      message.reply_to_message.from_user
      if message.reply_to_message and message.reply_to_message.from_user
      else None
  )
  if (
      not reply_user
      and message.reply_to_message
      and message.reply_to_message.sender_chat
  ):
    sc = message.reply_to_message.sender_chat
    reply_user = types.User(
        id=int(sc.id),
        is_bot=False,
        first_name=(sc.title or sc.username or "Kanal")[:64],
        username=sc.username,
    )

  query_part = raw_args
  explicit_points: int | None = None

  if "|" in raw_args:
    left_side, right_side = raw_args.rsplit("|", 1)
    r_clean = right_side.strip()
    if r_clean.lstrip("-").isdigit():
      explicit_points = int(r_clean)
      query_part = left_side.strip()
  else:
    tokens = raw_args.split()
    if len(tokens) >= 2 and tokens[-1].lstrip("-").isdigit():
      if tokens[0].lstrip("-").isdigit() or reply_user:
        explicit_points = int(tokens[-1])
        query_part = " ".join(tokens[:-1]).strip()

  all_users_map = _collect_all_known_users()
  matched_ids = _find_target_users_in_map(all_users_map, query_part, reply_user)

  target_uid: int | None = None
  display_label = ""
  resolved_username = ""
  found_existing = False

  if matched_ids:
    if len(matched_ids) > 1:
      lines = ["⚠️ Bir nechta mos foydalanuvchi topildi, aniq ID bilan kiriting:\n"]
      for uid in matched_ids:
        u_name = html.escape(str(all_users_map[uid].get("name") or "O'yinchi"))
        lines.append(f"• {u_name} — <code>{uid}</code>")
      await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
      return
    target_uid = matched_ids[0]
    found_existing = True
    raw_name = str(all_users_map[target_uid].get("name") or "").strip()
    resolved_username = str(all_users_map[target_uid].get("username") or "").strip().lstrip("@")
    display_label = html.escape(raw_name if raw_name else str(target_uid))
  else:
    q_clean = query_part.strip()
    if q_clean.lstrip("-").isdigit():
      target_uid = int(q_clean)
      display_label = f"<code>{target_uid}</code>"
    elif reply_user:
      target_uid = int(reply_user.id)
      raw_name = get_display_name(reply_user)
      resolved_username = (reply_user.username or "").strip().lstrip("@")
      display_label = html.escape(raw_name)
    elif q_clean:
      import hashlib
      resolved_username = q_clean.lstrip("@") if q_clean.startswith("@") else ""
      manual_name = resolved_username or q_clean
      digest_num = int(hashlib.sha256(q_clean.lower().encode("utf-8")).hexdigest()[:10], 16)
      target_uid = -100000000000 - digest_num
      display_label = html.escape(manual_name)

  if not target_uid or target_uid == 0:
    await message.answer(
        "❌ Foydalanuvchi yoki kanal topilmadi.\n"
        "Foydalanish:\n"
        "• Xabariga reply qilib: <code>/umumiyga_qosh</code> yoki <code>/umumiyga_qosh 40000</code>\n"
        "• ID / nom bilan: <code>/umumiyga_qosh -1001234567890 40000</code>\n"
        "• Yoki: <code>/umumiyga_qosh Kanal Nomi | 40000</code>",
        parse_mode=ParseMode.HTML,
    )
    return

  await remove_global_excluded_user(target_uid)

  current_total = 0
  for gdata in games.values():
    if not isinstance(gdata, dict):
      continue
    sc = gdata.get("scores", {})
    if isinstance(sc, dict) and target_uid in sc and isinstance(sc[target_uid], dict):
      try:
        current_total += int(float(sc[target_uid].get("score", 0)))
      except (TypeError, ValueError):
        pass

  points_note = ""
  if explicit_points is not None:
    if not found_existing or current_total == 0:
      score_cid = message.chat.id if message.chat.type != "private" else None
      if score_cid is None:
        active_cids = [cid for cid in games.keys() if int(cid) < 0]
        score_cid = active_cids[0] if active_cids else -1009999999999
      chat_game = await get_chat_game(score_cid)
      sc = chat_game["scores"]
      clean_name_for_score = (
          html.unescape(display_label)
          if not display_label.startswith("<code>")
          else f"Kanal ({target_uid})"
      )
      sc[target_uid] = {
          "name": clean_name_for_score,
          "username": resolved_username,
          "score": explicit_points,
          "hidden_in_group": False,
      }
      await persist_state(score_cid)
      current_total = explicit_points
      points_note = f"\n📊 Umumiy balli: <b>{current_total} ball</b>"
    else:
      updated_any = False
      for g_cid, gdata in games.items():
        if isinstance(gdata, dict) and isinstance(gdata.get("scores"), dict) and target_uid in gdata["scores"]:
          gdata["scores"][target_uid]["score"] = explicit_points
          gdata["scores"][target_uid]["hidden_in_group"] = False
          await persist_state(g_cid)
          updated_any = True
          break
      current_total = explicit_points if updated_any else current_total
      points_note = f"\n📊 Umumiy balli: <b>{current_total} ball</b>"
  else:
    if not found_existing and current_total == 0:
      points_note = (
          "\n⚠️ Bu nom/ID bazadagi guruh ballarida topilmadi (0 ball).\n"
          "Agar guruhdagi 39700 ballini berib qo'shmoqchi bo'lsangiz:\n"
          f"<code>/umumiyga_qosh {html.unescape(display_label)} | 39700</code>"
      )
    else:
      points_note = f"\n📊 Hozirgi umumiy balli: <b>{current_total} ball</b>"

  await message.answer(
      f"✅ <b>{display_label}</b> (<code>{target_uid}</code>) umumiy"
      f" reytingda faol!{points_note}",
      parse_mode=ParseMode.HTML,
  )


@dp.message(Command("tugatish"))
async def cmd_tugatish(message: types.Message):
  if message.chat.type != "private":
    await record_group_seen(message.chat, in_group=True)
    if not await is_group_active(message.chat.id):
      await send_sleeping_group_message(message)
      return

  if not await is_user_admin(message):
    await message.answer(
        "⛔ Reytingni faqat guruh administratorlari yakunlashi mumkin."
    )
    return

  chat_game = await get_chat_game(message.chat.id)
  scores = chat_game["scores"]

  if not scores:
    chat_game["current_question"] = None
    await persist_state(message.chat.id)
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
  await persist_state(message.chat.id)

  await message.answer(text, parse_mode=ParseMode.HTML)


@dp.message(F.chat.type == "private", F.photo)
async def handle_payment_photo(message: types.Message):
  if not message.from_user:
    return
  buyer_data = build_user_data(message.from_user)
  info = pending_receipts.pop(message.from_user.id, None)
  target_cid = info["chat_id"] if info else 0
  if target_cid == 0:
    for cid, b in pending_buyers.items():
      if b.get("id") == message.from_user.id:
        target_cid = cid
        break

  if target_cid != 0:
    pending_buyers[target_cid] = buyer_data

  days_val = info["days"] if info else 15
  if info:
    if info.get("is_friday_promo"):
      days_label = (
          f"{info['base_days']} + {info['bonus_days']} = {info['days']} kun"
          " (Juma aksiyasi)"
      )
    else:
      days_label = (
          f"{info['days']} kun" if info["days"] is not None else "Cheksiz"
      )
    tariff_text = f"{info['label']} ({info['price']}, {days_label})"
  else:
    days_label = f"{days_val} kun"
    tariff_text = f"Ko'rsatilmagan ({days_label})"

  auto_status = ""
  expiry_text = ""
  if target_cid != 0:
    days_arg = days_val if (days_val is not None and days_val > 0) else None
    auto_status = await activate_group_and_notify(
        target_cid,
        days_arg,
        message.from_user,
        buyer_id=message.from_user.id,
    )
    expiry_text = get_expiry_text(target_cid)
    await message.answer(
        "✅ Chek/rasm qabul qilindi va guruh avtomatik uyg'otildi!\n"
        f"Guruh ID: <code>{target_cid}</code>\n"
        f"Muddat: <b>{html.escape(expiry_text)}</b>\n\n"
        "Endi guruhda /next buyrug'i bilan o'yinni boshlashingiz mumkin.",
        parse_mode=ParseMode.HTML,
    )
  else:
    await message.answer(
        "✅ Chek/rasm qabul qilindi! Admin tekshiradi."
    )

  if ADMIN_ID and message.photo:
    admin_user_link = format_admin_profile_link_html(
        message.from_user.id, buyer_data
    )
    sleep_cmd = f"/ochirish {target_cid}" if target_cid != 0 else "/ochirish <guruh_id>"
    if target_cid != 0:
      admin_caption = (
          "🧾 <b>Yangi to'lov cheki (avtomatik uyg'otildi)!</b>\n\n"
          f"Foydalanuvchi: {admin_user_link}\n"
          f"Guruh ID: <code>{target_cid}</code>\n"
          f"Tarif: <b>{html.escape(tariff_text)}</b>\n"
          f"Muddat: <b>{html.escape(expiry_text)}</b>"
          f"{auto_status}\n\n"
          f"Soxta chek / muammo bo'lsa uxlatish:\n<code>{sleep_cmd}</code>"
      )
    else:
      admin_caption = (
          "🧾 <b>Yangi to'lov cheki (Guruh ID aniqlanmadi)!</b>\n\n"
          f"Foydalanuvchi: {admin_user_link}\n"
          f"Guruh ID: <code>{target_cid}</code>\n"
          f"Tarif: <b>{html.escape(tariff_text)}</b>\n\n"
          f"Uxlatish buyrug'i:\n<code>{sleep_cmd}</code>"
      )
    try:
      await bot.send_photo(
          chat_id=ADMIN_ID,
          photo=message.photo[-1].file_id,
          caption=admin_caption,
          parse_mode=ParseMode.HTML,
      )
    except Exception:
      pass


@dp.message()
async def check_answer(message: types.Message):
  if not message.text or message.text.startswith("/"):
    return

  if message.chat.type != "private":
    await record_group_seen(message.chat, in_group=True)
    if not await is_group_active(message.chat.id):
      return

  chat_game = await get_chat_game(message.chat.id)
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
    if message.sender_chat and not message.from_user:
      user_id = int(message.sender_chat.id)
      user_name = (
          message.sender_chat.title
          or message.sender_chat.username
          or "Kanal"
      ).strip()
      username = (message.sender_chat.username or "").strip().lstrip("@")
    elif message.from_user and message.from_user.id == 777000 and message.sender_chat:
      user_id = int(message.sender_chat.id)
      user_name = (
          message.sender_chat.title
          or message.sender_chat.username
          or get_display_name(message.from_user)
      ).strip()
      username = (message.sender_chat.username or "").strip().lstrip("@")
    else:
      user_id = message.from_user.id if message.from_user else 0
      user_name = get_display_name(message.from_user)
      username = (
          message.from_user.username
          if message.from_user and message.from_user.username
          else ""
      )
    scores = chat_game["scores"]

    u_norm = username.strip().lstrip("@").lower()
    n_norm = user_name.strip().lower()
    migrated_pts = 0
    dup_ids_to_remove = []
    for existing_uid, existing_info in list(scores.items()):
      if int(existing_uid) == int(user_id) or not isinstance(existing_info, dict):
        continue
      e_uname = str(existing_info.get("username") or "").strip().lstrip("@").lower()
      e_name = str(existing_info.get("name") or "").strip().lower()
      same_entity = (u_norm and e_uname == u_norm) or (
          not u_norm and n_norm and e_name == n_norm and not e_name.startswith("kanal (-")
      )
      if same_entity:
        try:
          migrated_pts += int(float(existing_info.get("score", 0)))
        except (TypeError, ValueError):
          pass
        dup_ids_to_remove.append(existing_uid)

    for dup_uid in dup_ids_to_remove:
      scores.pop(dup_uid, None)

    if user_id not in scores:
      scores[user_id] = {"name": user_name, "username": username, "score": migrated_pts}
    else:
      scores[user_id]["name"] = user_name
      if username:
        scores[user_id]["username"] = username
      scores[user_id]["score"] = int(float(scores[user_id].get("score", 0))) + migrated_pts

    scores[user_id]["score"] += 100
    scores[user_id]["hidden_in_group"] = False
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

    await persist_state(message.chat.id)

    questions = load_questions()
    if questions:
      await send_question(message, chat_game, questions)
    else:
      chat_game["current_question"] = None
      await persist_state(message.chat.id)


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
  await init_db()
  global global_excluded_users
  games = await load_state_async()
  chat_access = await load_access_async()
  global_excluded_users = await load_global_excluded_async()
  await start_web_server()
  asyncio.create_task(friday_promo_scheduler())
  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
