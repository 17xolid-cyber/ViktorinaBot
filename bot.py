import json
import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# O'yin holatini saqlash uchun o'zgaruvchilar
current_question = None  # Hozirgi berilgan savol
scores = {}              # Foydalanuvchi ballari {user_id: {"name": "Ism", "score": ball}}

def load_questions():
    try:
        with open("questions.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

# /start buyrug'i
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Assalomu alaykum! Diniy viktorina botiga xush kelibsiz.\n"
        "Guruhda viktorina boshlash uchun /quiz buyrug'ini yuboring."
    )

# /quiz - Yangi savol berish buyrug'i
@dp.message(Command("quiz"))
async def cmd_quiz(message: types.Message):
    global current_question
    questions = load_questions()
    
    if not questions:
        await message.answer("Hozircha `questions.json` faylida savollar yo'q!")
        return
    
    # Tasodifiy yoki ketma-ket bitta savolni tanlaymiz (masalan, birinchisini)
    # Kelgusida buni o'zgartirish mumkin
    current_question = questions[0] 
    
    await message.answer(
        f"❓ **Yangi savol:**\n\n{current_question['question']}\n\n"
        f"*(Javobingizni to'g'ridan-to'g'ri guruhga yozing!)*"
    )

# /score - Ballar jadvalini ko'rish
@dp.message(Command("score"))
async def cmd_score(message: types.Message):
    if not scores:
        await message.answer("Hozircha hech kim ball to'plagani yo'q.")
        return
    
    text = "🏆 **Reyting jadvali:**\n\n"
    for user_id, data in scores.items():
        text += f"• {data['name']}: {data['score']} ball\n"
    
    await message.answer(text)

# Foydalanuvchilarning xabarlarini tekshirib borish (javobni aniqlash uchun)
@dp.message()
async def check_answer(message: types.Message):
    global current_question
    
    # Agar savol berilmagan bo'lsa yoki xabar botnikiga o'xshasa chiqib ketamiz
    if not current_question or message.text.startswith("/"):
        return
    
    # Foydalanuvchi yozgan javobni to'g'ri javob bilan solishtiramiz (katta-kichik harflarni e'tiborga olmasdan)
    user_answer = message.text.strip().lower()
    correct_answer = current_question["answer"].strip().lower()
    
    if user_answer == correct_answer:
        user_id = message.from_user.id
        user_name = message.from_user.first_name
        
        # Ball qo'shish
        if user_id not in scores:
            scores[user_id] = {"name": user_name, "score": 0}
        
        scores[user_id]["score"] += 1
        
        # To'g'ri topilganini e'lon qilamiz va savolni yopamiz
        await message.answer(
            f"🎉 Tabriklayman, {user_name}! To'g'ri javob.\n"
            f"To'g'ri javob: **{current_question['answer']}**\n"
            f"Sizga 1 ball berildi! 🎯"
        )
        
        # Savol tugadi, yangisini /quiz orqali chaqirish mumkin
        current_question = None

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())