import logging
import os
import threading
import asyncio
from hashlib import sha256
import pandas as pd
import requests
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from io import StringIO
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.dispatcher import FSMContext
from aiogram.utils.exceptions import TelegramAPIError
from flask import Flask, request, jsonify
from functools import wraps
from openpyxl import load_workbook
import re

# === Логирование =====================================================================================================
logging.basicConfig(level=logging.INFO)

# === Переменные окружения =========================================================================================
API_TOKEN = os.getenv('BOT_TOKEN')
admin = int(os.getenv('ADMIN_ID', '0'))
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SHEET_NAME = os.getenv('SHEET_NAME')
FLASK_API_KEY = os.getenv('FLASK_API_KEY')

if not API_TOKEN:
    raise Exception("Переменная окружения BOT_TOKEN обязательна.")
if not FLASK_API_KEY:
    raise Exception("Переменная окружения FLASK_API_KEY обязательна.")

FETCH_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet={SHEET_NAME}"

# === Инициализация бота и диспетчера =============================================================================
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

# === В роли ДБ ==================================================================================================
SVlist = {}

class SV:
    def __init__(self, name, id):
        self.name = name
        self.id = id
        self.table = ''
        self.calls = {}

# === Flask-сервер ===============================
app = Flask(__name__)

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if api_key and api_key == FLASK_API_KEY:
            return f(*args, **kwargs)
        else:
            return jsonify({"error": "Invalid or missing API key"}), 401
    return decorated

@app.route('/')
def index():
    return "Bot is alive!", 200

@app.route('/api/call_evaluation', methods=['POST'])
@require_api_key
def receive_call_evaluation():
    global SVlist
    try:
        data = request.get_json()
        required_fields = ['evaluator', 'operator', 'month', 'call_number', 'phone_number', 'score', 'comment']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing or invalid required fields"}), 400

        for field in required_fields:
            if not isinstance(data[field], (str, int, float)):
                return jsonify({"error": f"Invalid type for {field}"}), 400
        b = 1
        hint = ""
        for t in SVlist:
            if SVlist[t].name == data['evaluator']:
                b = 0
                if data['month'] in SVlist[t].calls:
                    if data['call_number'] in SVlist[t].calls[data['month']]:
                        hint += " - Корректировка оценки!"
                    else:
                        SVlist[t].calls[data['month']][data['call_number']] = data
                else:
                    SVlist[t].calls[data['month']] = {}
                    SVlist[t].calls[data['month']][data['call_number']] = data
                break
        
        if b:
            hint += " Оценивающего нет в списке супервайзеров!"
                
        message = (
            f"📞 <b>Оценка звонка</b>\n" 
            f"👤 Оценивающий: <b>{data['evaluator']}</b>\n"
            f"📋 Оператор: <b>{data['operator']}</b>\n"
            f"📄 За месяц: <b>{data['month']}</b>\n"
            f"📞 Звонок: <b>№{data['call_number']}</b>\n"
            f"📱 Номер телефона: <b>{data['phone_number']}</b>\n"
            f"💯 Оценка: <b>{data['score']}</b>\n"
        )
        if data['score'] < 100 and data['comment']:
            message += f"\n💬 Комментарий: \n{data['comment']}\n"
        message += "\n" + hint

        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        payload = {
            "chat_id": admin,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(telegram_url, json=payload, timeout=10)
        
        if response.status_code != 200:
            error_detail = response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error: {error_detail}")
            return jsonify({"error": f"Failed to send Telegram message: {error_detail}"}), 500

        return jsonify({"status": "success"}), 200
    except requests.RequestException as re:
        logging.error(f"HTTP request error: {re}")
        return jsonify({"error": f"Failed to send Telegram message: {str(re)}"}), 500
    except Exception as e:
        logging.error(f"Error processing call evaluation: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

def run_flask():
    app.run(host='0.0.0.0', port=8080, debug=False)

# === Глобальное состояние =======================================================================================
last_hash = None

# === Классы =====================================================================================================
class new_sv(StatesGroup):
    svname = State()
    svid = State()

class sv(StatesGroup):
    crtable = State()
    delete = State()
    verify_table = State()
    view_evaluations = State()
    change_table = State()  # New state for changing SV table

# Helper function to create cancel keyboard
def get_cancel_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton('Отмена ❌'))
    return kb

# Helper function to create admin keyboard
def get_admin_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Редактор СВ📝'))
    kb.insert(KeyboardButton('Оценки📊'))
    return kb

# Helper function to create verification keyboard
def get_verify_keyboard():
    ikb = InlineKeyboardMarkup(row_width=2)
    ikb.add(
        InlineKeyboardButton("Да ✅", callback_data="verify_yes"),
        InlineKeyboardButton("Нет ❌", callback_data="verify_no")
    )
    return ikb

# Helper function to create editor keyboard
def get_editor_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Добавить СВ➕'))
    kb.insert(KeyboardButton('Убрать СВ❌'))
    kb.add(KeyboardButton('Изменить таблицу СВ🔄'))  # New button
    kb.add(KeyboardButton('Назад 🔙'))
    return kb

# Global cancel handler
@dp.message_handler(regexp='Отмена ❌', state='*')
async def cancel_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.finish()
    kb = get_admin_keyboard() if message.from_user.id == admin else ReplyKeyboardRemove()
    await bot.send_message(
        chat_id=message.from_user.id,
        text="Действие отменено.",
        parse_mode='HTML',
        reply_markup=kb
    )
    await message.delete()

# === Команды ====================================================================================================
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    await message.delete()
    if message.from_user.id == admin:
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Бобро пожаловать!</b>\nЭто бот для прослушки прослушек.",
            parse_mode='HTML',
            reply_markup=get_admin_keyboard()
        )
    else:
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        if message.from_user.id in SVlist:
            kb.add(KeyboardButton('Добавить таблицу📑'))
        await bot.send_message(
            chat_id=message.from_user.id,
            text=f"<b>Бобро пожаловать!</b>\nТвой <b>ID</b> что бы присоединиться к команде:\n\n<pre>{message.from_user.id}</pre>",
            parse_mode='HTML',
            reply_markup=kb
        )

# === Админка ===================================================================================================
@dp.message_handler(regexp='Редактор СВ📝')
async def editor_sv(message: types.Message):
    if message.from_user.id == admin:
        await bot.send_message(
            chat_id=message.from_user.id,
            text='<b>Редактор супервайзеров</b>',
            parse_mode='HTML',
            reply_markup=get_editor_keyboard()
        )
    await message.delete()

@dp.message_handler(regexp='Назад 🔙')
async def back_to_admin(message: types.Message):
    if message.from_user.id == admin:
        await bot.send_message(
            chat_id=message.from_user.id,
            text='<b>Главное меню</b>',
            parse_mode='HTML',
            reply_markup=get_admin_keyboard()
        )
    await message.delete()

@dp.message_handler(regexp='Добавить СВ➕')
async def newSv(message: types.Message):
    if message.from_user.id == admin:
        await bot.send_message(
            text='<b>Добавление СВ, этап</b>: 1 из 2📍\n\nФИО нового СВ🖊',
            chat_id=message.from_user.id,
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await new_sv.svname.set()
    await message.delete()

@dp.message_handler(state=new_sv.svname)
async def newSVname(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['svname'] = message.text
    await message.answer(
        text=f'Класс, ФИО - <b>{message.text}</b>\n\n<b>Добавление СВ, этап</b>: 2 из 2📍\n\nНапишите <b>ID</b> нового СВ🆔',
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    await new_sv.next()
    await message.delete()

@dp.message_handler(state=new_sv.svid)
async def newSVid(message: types.Message, state: FSMContext):
    try:
        sv_id = int(message.text)
        async with state.proxy() as data:
            data['svid'] = sv_id
        kb_sv = ReplyKeyboardMarkup(resize_keyboard=True)
        kb_sv.add(KeyboardButton('Добавить таблицу📑'))
        await bot.send_message(
            chat_id=sv_id,
            text=f"Принятие в команду прошло успешно <b>успешно✅</b>\n\nОсталось отправить таблицу вашей группы. Нажмите <b>Добавить таблицу📑</b> что бы сделать это.",
            parse_mode='HTML',
            reply_markup=kb_sv
        )
        SVlist[sv_id] = SV(data['svname'], sv_id)
        await message.answer(
            text=f'Класс, ID - <b>{message.text}</b>\n\nДобавление СВ прошло <b>успешно✅</b>. Новому супервайзеру осталось лишь отправить таблицу этого месяца👌🏼',
            parse_mode='HTML',
            reply_markup=get_editor_keyboard()
        )
        await state.finish()
    except:
        await message.answer(
            text='Ой, похоже вы отправили не тот <b>ID</b>❌\n\n<b>Пожалуйста повторите попытку!</b>',
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    await message.delete()

@dp.message_handler(regexp='Убрать СВ❌')
async def delSv(message: types.Message):
    if message.from_user.id == admin:
        if SVlist:
            await bot.send_message(
                text='<b>Выберете СВ которого надо исключить🖊</b>',
                chat_id=admin,
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
            ikb = InlineKeyboardMarkup(row_width=1)
            for i in SVlist:
                ikb.insert(InlineKeyboardButton(text=SVlist[i].name, callback_data=str(i)))
            await bot.send_message(
                text='<b>Лист СВ:</b>',
                chat_id=admin,
                parse_mode='HTML',
                reply_markup=ikb
            )
            await sv.delete.set()
        else:
            await bot.send_message(
                text='<b>В команде нет СВ🤥</b>',
                chat_id=admin,
                parse_mode='HTML',
                reply_markup=get_editor_keyboard()
            )
    await message.delete()

@dp.callback_query_handler(state=sv.delete)
async def delSVcall(callback: types.CallbackQuery, state: FSMContext):
    SV = SVlist[int(callback.data)]
    del SVlist[int(callback.data)]
    await bot.send_message(
        text=f"Супервайзер <b>{SV.name}</b> успешно исключен из вашей команды✅",
        chat_id=admin,
        parse_mode='HTML',
        reply_markup=get_editor_keyboard()
    )
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    await bot.send_message(
        text=f"Вы были исключены из команды❌",
        chat_id=SV.id,
        parse_mode='HTML',
        reply_markup=ReplyKeyboardRemove()
    )
    await state.finish()

@dp.message_handler(regexp='Изменить таблицу СВ🔄')
async def change_sv_table(message: types.Message):
    if message.from_user.id == admin:
        if SVlist:
            await bot.send_message(
                text='<b>Выберите СВ, чью таблицу нужно изменить🖊</b>',
                chat_id=admin,
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
            ikb = InlineKeyboardMarkup(row_width=1)
            for i in SVlist:
                ikb.insert(InlineKeyboardButton(text=SVlist[i].name, callback_data=f"change_table_{i}"))
            await bot.send_message(
                text='<b>Лист СВ:</b>',
                chat_id=admin,
                parse_mode='HTML',
                reply_markup=ikb
            )
            await sv.change_table.set()
        else:
            await bot.send_message(
                text='<b>В команде нет СВ🤥</b>',
                chat_id=admin,
                parse_mode='HTML',
                reply_markup=get_editor_keyboard()
            )
    await message.delete()

@dp.callback_query_handler(state=sv.change_table)
async def select_sv_for_table_change(callback: types.CallbackQuery, state: FSMContext):
    sv_id = int(callback.data.split('_')[2])
    async with state.proxy() as data:
        data['sv_id'] = sv_id
    await bot.send_message(
        chat_id=admin,
        text=f'<b>Отправьте новую таблицу ОКК для {SVlist[sv_id].name}🖊</b>',
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    await sv.crtable.set()

@dp.message_handler(regexp='Оценки📊')
async def view_evaluations(message: types.Message):
    if message.from_user.id == admin:
        if SVlist:
            await bot.send_message(
                text='<b>Выберите чьи оценки просмотреть</b>',
                chat_id=admin,
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
            ikb = InlineKeyboardMarkup(row_width=1)
            for i in SVlist:
                ikb.insert(InlineKeyboardButton(text=SVlist[i].name, callback_data=f"eval_{i}"))
            await bot.send_message(
                text='<b>Лист СВ:</b>',
                chat_id=admin,
                parse_mode='HTML',
                reply_markup=ikb
            )
            await sv.view_evaluations.set()
        else:
            await bot.send_message(
                text='<b>В команде нет СВ🤥</b>',
                chat_id=admin,
                parse_mode='HTML',
                reply_markup=get_admin_keyboard()
            )
    await message.delete()

@dp.callback_query_handler(state=sv.view_evaluations)
async def show_evaluations(callback: types.CallbackQuery, state: FSMContext):
    sv_id = int(callback.data.split('_')[1])
    sv = SVlist[sv_id]
    
    # Get operators from SV's table
    sheet_name, operators, error = extract_fio_and_links(sv.table) if sv.table else (None, [], "Таблица не найдена")
    
    if error:
        await bot.send_message(
            chat_id=admin,
            text=f"Ошибка: {error}",
            parse_mode='HTML',
            reply_markup=get_admin_keyboard()
        )
        await state.finish()
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        return

    # Count calls per operator
    operator_counts = {op['name']: 0 for op in operators}
    for month in sv.calls:
        for call in sv.calls[month].values():
            operator_name = call['operator']
            if operator_name in operator_counts:
                operator_counts[operator_name] += 1

    # Format message with right-aligned counts
    max_name_length = 20  # Max length before truncation
    message_text = f"<b>Оценки {sv.name}:</b>\n\n"
    if operator_counts:
        # Find max count length for alignment
        max_count_length = max(len(str(count)) for count in operator_counts.values())
        for op_name, count in operator_counts.items():
            # Truncate name if too long
            display_name = op_name[:max_name_length] + '…' if len(op_name) > max_name_length else op_name
            # Right-align count
            formatted_count = str(count).rjust(max_count_length)
            message_text += f"👤 {display_name.ljust(max_name_length)} {formatted_count}\n"
    else:
        message_text += "Оценок пока нет\n"

    await bot.send_message(
        chat_id=admin,
        text=message_text,
        parse_mode='HTML',
        reply_markup=get_admin_keyboard()
    )
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    await state.finish()

# === Работа с СВ и таблицами ===================================================================================
def extract_fio_and_links(spreadsheet_url):
    try:
        # Extract file_id from Google Sheets URL
        match = re.search(r"/d/([a-zA-Z0-9_-]+)", spreadsheet_url)
        if not match:
            return None, None, "Ошибка: Неверный формат ссылки на Google Sheets."
        file_id = match.group(1)
        export_url = f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx"

        # Download the file
        response = requests.get(export_url)
        if response.status_code != 200:
            return None, None, "Ошибка: Не удалось скачать таблицу. Проверьте, доступна ли таблица публично."
        
        # Save the file temporarily
        temp_file = "temp_table.xlsx"
        with open(temp_file, "wb") as f:
            f.write(response.content)

        # Load the Excel file
        wb = load_workbook(temp_file)
        ws = wb.worksheets[-1]  # Use the last sheet
        sheet_name = ws.title

        # Find the ФИО column
        fio_column = None
        for col in ws.iter_cols(min_row=1, max_row=1):
            for cell in col:
                if cell.value and "ФИО" in str(cell.value).strip():
                    fio_column = cell.column
                    break
            if fio_column:
                break

        if not fio_column:
            os.remove(temp_file)
            return None, None, "Ошибка: Колонка ФИО не найдена на листе."

        # Extract ФИО and hyperlinks
        operators = []
        for row in ws.iter_rows(min_row=2):
            cell = row[fio_column - 1]
            if not cell.value:
                break
            operator_info = {
                "name": cell.value,
                "link": cell.hyperlink.target if cell.hyperlink else None
            }
            operators.append(operator_info)

        # Clean up
        os.remove(temp_file)
        return sheet_name, operators, None
    except Exception as e:
        return None, None, f"Ошибка при обработке таблицы: {str(e)}"

@dp.message_handler(regexp='Добавить таблицу📑')
async def crtablee(message: types.Message):
    await bot.send_message(
        text='<b>Отправьте вашу таблицу ОКК🖊</b>',
        chat_id=message.from_user.id,
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    await sv.crtable.set()
    await message.delete()

@dp.message_handler(state=sv.crtable)
async def tableName(message: types.Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        is_admin_changing = await state.get_state() == sv.change_table.state and user_id == admin
        if not is_admin_changing or user_id not in SVlist:
            await bot.send_message(
                chat_id=user_id,
                text="Ошибка: Вы не зарегистрированы как супервайзер! Пожалуйста, добавьтесь через администратора.",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardRemove()
            )
            await state.finish()
            return

        # Extract ФИО and links from the provided spreadsheet URL
        sheet_name, operators, error = extract_fio_and_links(message.text)
        
        if error:
            await bot.send_message(
                chat_id=user_id,
                text=f"{error}\n\n<b>Пожалуйста, отправьте корректную ссылку на таблицу.</b>",
                parse_mode="HTML",
                reply_markup=get_cancel_keyboard()
            )
            return

        # Store the table URL and target SV ID (if admin)
        async with state.proxy() as data:
            data['table_url'] = message.text
            if is_admin_changing:
                data.setdefault('sv_id', user_id)  # Preserve sv_id if set

        # Format the message
        message_text = f"<b>Название листа:</b> {sheet_name}\n\n<b>ФИО операторов:</b>\n"
        for op in operators:
            if op['link']:
                message_text += f"👤 {op['name']} → <a href='{op['link']}'>Ссылка</a>\n"
            else:
                message_text += f"👤 {op['name']} → Ссылка отсутствует\n"
        message_text += "\n<b>Это все ваши операторы?</b>"

        # Send the message with verification buttons
        await bot.send_message(
            chat_id=user_id,
            text=message_text,
            parse_mode="HTML",
            reply_markup=get_verify_keyboard(),
            disable_web_page_preview=True
        )
        await sv.verify_table.set()
        await message.delete()
    except Exception as e:
        logging.error(f"Ошибка в tableName: {e}")
        await bot.send_message(
            chat_id=message.from_user.id,
            text="Произошла ошибка при обработке таблицы. Попробуйте снова или свяжитесь с администратором.",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )

@dp.callback_query_handler(state=sv.verify_table)
async def verify_table(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        table_url = data.get('table_url')
        sv_id = data.get('sv_id', callback.from_user.id)  # Use sv_id if set, else user_id
    
    if callback.data == "verify_yes":
        # Save the table URL to SVlist
        SVlist[sv_id].table = table_url
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(KeyboardButton('Добавить таблицу📑'))
        reply_markup = kb if sv_id == callback.from_user.id else get_editor_keyboard()
        target_id = callback.from_user.id if sv_id == callback.from_user.id else admin
        await bot.send_message(
            chat_id=target_id,
            text=f'<b>Таблица успешно подтверждена и сохранена для {SVlist[sv_id].name}✅</b>',
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        await state.finish()
    elif callback.data == "verify_no":
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f'<b>Отправьте корректную таблицу ОКК для {SVlist[sv_id].name}🖊</b>',
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        await sv.crtable.set()

# === Работа с таблицей ==========================================================================================
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
        current_hash = sha256(content.encode()).hexdigest()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if last_hash is None:
            await bot.send_message(admin, f"[{now}] ✅ Первая загрузка данных.", parse_mode='HTML')
            last_hash = current_hash
        elif current_hash != last_hash:
            await bot.send_message(admin, f"[{now}] 📌 Таблица обновилась!", parse_mode='HTML')
            last_hash = current_hash
        else:
            logging.info(f"[{now}] No changes in spreadsheet data.")
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

# === Главный запуск =============================================================================================
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_for_updates, "interval", minutes=1)
    scheduler.add_job(generate_report, CronTrigger(day="10,20,30", hour=9, minute=0))
    scheduler.start()
    print("🔄 Планировщик запущен.")
    executor.start_polling(dp, skip_updates=True)