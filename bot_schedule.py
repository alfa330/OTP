import logging
import os
import threading
import asyncio
import pandas as pd
import requests
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from io import StringIO

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton,InlineKeyboardMarkup
from aiogram.dispatcher import FSMContext
from flask import Flask

# === Логирование =====================================================================================================
logging.basicConfig(level=logging.INFO)





# === Переменные окружения ============================================================================================
API_TOKEN = os.getenv('BOT_TOKEN')
admin     = int(os.getenv('ADMIN_ID', '0'))  # ADMIN_ID должен быть числом
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SHEET_NAME     = os.getenv('SHEET_NAME')

if not API_TOKEN:
    raise Exception("Переменная окружения BOT_TOKEN обязателен.")

FETCH_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet={SHEET_NAME}"





# === Инициализация бота и диспетчера =================================================================================
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)




# === Flask-сервер для Render — для пинга =============================================================================
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is alive!", 200

def run_flask():
    app.run(host='0.0.0.0', port=8080)




# === Глобальное состояние ============================================================================================
last_hash = None




# === Классы ==========================================================================================================

class new_sv(StatesGroup):
    svname = State()
    svid   = State()

class sv(StatesGroup):
    crtable = State()
    delete = State()

class SV:
    def __init__(self, name,id):
        self.name=name
        self.id=id
        self.table=''

SVlist={}



# === Команды =========================================================================================================
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    await message.delete()
    if message.from_user.id == admin:
        kb=ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(KeyboardButton('Добавить СВ➕'))
        kb.insert(KeyboardButton('Убрать СВ❌'))
        await bot.send_message(
            chat_id=      message.from_user.id,
            text=         "<b>Бобро пожаловать!</b>\nЭто бот для прослушки прослушек.",
            parse_mode=   'HTML',
            reply_markup= kb 
        )
    else:
        await bot.send_message(
            chat_id=      message.from_user.id,
            text=         f"<b>Бобро пожаловать!</b>\nТвой <b>ID</b> что бы присоедениться к команде:\n\n<pre>{message.from_user.id}</pre>",
            parse_mode=   'HTML'
        )




# === Админка =========================================================================================================
@dp.message_handler(regexp='Добавить СВ➕')                              #Добавление нового СВ
async def newSv(message: types.message):
    await bot.send_message(text='<b>Добавление СВ, этап</b>: 1 из 2📍\n\nФИО нового СВ🖊',
                            chat_id=message.from_user.id,
                            parse_mode='HTML',
                            reply_markup= ReplyKeyboardRemove())
    await new_sv.svname.set()

@dp.message_handler(state=new_sv.svname)                                #ИМЯ
async def newSVname(message: types.message, state: FSMContext):
    async with state.proxy() as data:
        data['svname'] = message.text
    await message.answer(text=f'Класс, ФИО - <b>{message.text}</b>\n\n<b>Добавление СВ, этап</b>: 2 из 2📍\n\nНапишите <b>ID</b> нового СВ🆔',parse_mode='HTML')
    await new_sv.next()
    await message.delete()

@dp.message_handler(state=new_sv.svid)                                  #id
async def newSVid(message: types.message, state: FSMContext):
    kb=ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Добавить таблицу📑'))
    try:
        async with state.proxy() as data:
            data['svid'] = int(message.text)
        await bot.send_message(
                chat_id=      int(message.text),
                text=         f"Принятие в команду прошло успешно <b>успешно✅</b>\n\nОсталось отправить таблицу вашей группы. Нажмите <b>Добавить таблицу📑</b> что бы сделать это.",
                parse_mode=   'HTML',
                reply_markup= kb
        )

        SVlist[data['svid']] = SV(data['svname'],data['svid'])          #Добавил в лист СВ
        kb=ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(KeyboardButton('Добавить СВ➕'))
        kb.insert(KeyboardButton('Убрать СВ❌'))
        await message.answer(text=f'Класс, ID - <b>{message.text}</b>\n\nДобавление СВ прошло <b>успешно✅</b>. Новому супервайзеру осталось лишь отправить таблицу этого месяца👌🏼',
                             parse_mode='HTML',
                             reply_markup= kb
                             )
        await state.finish()
    except: 
        await message.answer(text='Ой, похоже вы отправили не тот <b>ID</b>❌\n\n<b>Пожалуйста повторите попытку!</b>',parse_mode='HTML')
    await message.delete()

@dp.message_handler(regexp='Убрать СВ❌')                                #Удаление СВ
async def delSv(message: types.message):
    if SVlist:
        await bot.send_message(text='<b>Выберете СВ которого надо исключить🖊</b>',
                            chat_id=admin,
                            parse_mode='HTML',
                            reply_markup= ReplyKeyboardRemove()
                            )
        ikb=InlineKeyboardMarkup(row_width=1)
        for i in SVlist:
            ikb.insert(InlineKeyboardButton(text=SVlist[i].name,callback_data=str(i)))
        await bot.send_message(text='<b>Лист СВ:</b>',
                                chat_id=admin,
                                parse_mode='HTML',
                                reply_markup=ikb
                                )
        await sv.delete.set()
    else:
        kb=ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(KeyboardButton('Добавить СВ➕'))
        kb.insert(KeyboardButton('Убрать СВ❌'))
        await bot.send_message(text='<b>В команде нет СВ🤥</b>',
                                chat_id=admin,
                                parse_mode='HTML',
                                reply_markup= kb
                                )
    await message.delete()
    
@dp.callback_query_handler(state=sv.delete)
async def delSVcall(callback: types.CallbackQuery, state: FSMContext):
    SV = SVlist[int(callback.data)]
    del SVlist[int(callback.data)]
    kb=ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Добавить СВ➕'))
    kb.insert(KeyboardButton('Убрать СВ❌'))
    await bot.send_message(text=f"Супервайзер <b>{SV.name}</b> успешно исключен из вашей команды✅",
                            chat_id=admin,
                            parse_mode='HTML',
                            reply_markup= kb
    )

    await bot.delete_message(chat_id = callback.from_user.id, message_id = callback.message.message_id)

    await bot.send_message(text=f"Вы были исключены из команды❌",
                           chat_id=SV.id,
                           parse_mode='HTML',
                           reply_markup= ReplyKeyboardRemove()
    )
    await state.finish()



# === Работа с СВ =====================================================================================================
@dp.message_handler(regexp='Добавить таблицу📑')                            
async def crtablee(message: types.message):
    await bot.send_message(text='<b>Отправьте вашу таблицу ОКК🖊</b>',
                            chat_id = message.from_user.id,
                            parse_mode = 'HTML',
                            reply_markup= ReplyKeyboardRemove()
                            )
    await sv.crtable.set()
    await message.delete()

@dp.message_handler(state=sv.crtable)
async def tableName(message: types.Message, state: FSMContext):
    try:
        # Проверяем, существует ли пользователь в SVlist
        if message.from_user.id not in SVlist:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="Ошибка: Вы не зарегистрированы как супервайзер! Пожалуйста, добавьтесь через администратора.",
                parse_mode="HTML"
            )
            await state.finish()
            return

        # Сохраняем таблицу
        SVlist[message.from_user.id].table = message.text

        # Отправляем подтверждение
        await bot.send_message(
            chat_id=message.from_user.id,
            text='<b>Таблица успешно получена✅</b>',
            parse_mode='HTML'
        )

        # Удаляем сообщение пользователя
        try:
            await message.delete()
        except Exception as e:
            print(f"Ошибка при удалении сообщения: {e}")

        # Завершаем состояние
        await state.finish()

    except Exception as e:
        # Логируем ошибку для диагностики
        print(f"Ошибка в tableName: {e}")
        await bot.send_message(
            chat_id=message.from_user.id,
            text="Произошла ошибка при обработке таблицы. Попробуйте снова или свяжитесь с администратором.",
            parse_mode="HTML"
        )
        await state.finish()
    

# === Работа с таблицей ===============================================================================================
def sync_fetch_text():
    response = requests.get(FETCH_URL)
    response.raise_for_status()
    return response.text

async def fetch_text_async():
    return await asyncio.to_thread(sync_fetch_text)

async def check_for_updates():
    global last_hash
    try:
        content = await fetch_text_async()
        current_hash = hash(content)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if last_hash is None:
            await bot.send_message(admin, f"[{now}] ✅ Первая загрузка данных.", parse_mode='HTML')
            last_hash = current_hash
        elif current_hash != last_hash:
            await bot.send_message(admin, f"[{now}] 📌 Таблица обновилась!", parse_mode='HTML')
            last_hash = current_hash
    except Exception as e:
        print(f"[{datetime.now()}] ❌ Ошибка при загрузке: {e}")

async def generate_report():
    try:
        content = await fetch_text_async()
        df = pd.read_csv(StringIO(content))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await bot.send_message(admin, f"[{now}] 📊 Отчет: {len(df)} строк, {len(df.columns)} столбцов.", parse_mode='HTML')
    except Exception as e:
        print(f"[{datetime.now()}] ⚠️ Ошибка при генерации отчета: {e}")





# === Главный запуск ==================================================================================================
if __name__ == '__main__':
    # Запускаем Flask-сервер в отдельном потоке
    threading.Thread(target=run_flask).start()

    # Настраиваем планировщик
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_for_updates, "interval", minutes=1)
    scheduler.add_job(generate_report, CronTrigger(day="10,20,30", hour=9, minute=0))
    scheduler.start()
    print("🔄 Планировщик запущен.")

    # Запускаем бота
    executor.start_polling(dp, skip_updates=True)
