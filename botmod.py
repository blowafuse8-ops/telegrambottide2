import logging
import asyncio
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.error import TelegramError

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8252427456:AAHy6BciCd7zJKI_7oqclHOUPfjneVhfaq4"
CHANNEL_ID = "-1003157439297"  # ID вашего канала
ADMIN_IDS = [7817856373]  # Ваш ID

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ==========
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('bans.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
    
    def create_tables(self):
        # Таблица для банов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS bans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                user_type TEXT,
                action TEXT,
                admin_id INTEGER,
                date TEXT,
                status TEXT
            )
        ''')
        
        # Таблица для статистики
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                bans_count INTEGER DEFAULT 0,
                channels_count INTEGER DEFAULT 0,
                users_count INTEGER DEFAULT 0
            )
        ''')
        self.conn.commit()
    
    def add_ban(self, user_id, user_type, action, admin_id, status="success"):
        """Добавить запись о бане"""
        self.cursor.execute('''
            INSERT INTO bans (user_id, user_type, action, admin_id, date, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, user_type, action, admin_id, datetime.now().isoformat(), status))
        self.conn.commit()
        
        # Обновляем статистику
        self.update_stats(user_type)
    
    def update_stats(self, user_type):
        """Обновить статистику"""
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Проверяем, есть ли запись за сегодня
        self.cursor.execute('SELECT id FROM stats WHERE date = ?', (today,))
        if self.cursor.fetchone():
            if user_type == 'channel':
                self.cursor.execute('''
                    UPDATE stats 
                    SET channels_count = channels_count + 1,
                        bans_count = bans_count + 1
                    WHERE date = ?
                ''', (today,))
            else:
                self.cursor.execute('''
                    UPDATE stats 
                    SET users_count = users_count + 1,
                        bans_count = bans_count + 1
                    WHERE date = ?
                ''', (today,))
        else:
            # Создаем новую запись
            channels = 1 if user_type == 'channel' else 0
            users = 1 if user_type == 'user' else 0
            self.cursor.execute('''
                INSERT INTO stats (date, bans_count, channels_count, users_count)
                VALUES (?, ?, ?, ?)
            ''', (today, 1, channels, users))
        
        self.conn.commit()
    
    def get_stats(self):
        """Получить общую статистику"""
        # Общее количество банов
        self.cursor.execute('SELECT COUNT(*) FROM bans WHERE status = "success"')
        total = self.cursor.fetchone()[0]
        
        # Баны каналов
        self.cursor.execute('SELECT COUNT(*) FROM bans WHERE user_type = "channel" AND status = "success"')
        channels = self.cursor.fetchone()[0]
        
        # Баны пользователей
        self.cursor.execute('SELECT COUNT(*) FROM bans WHERE user_type = "user" AND status = "success"')
        users = self.cursor.fetchone()[0]
        
        # Статистика за сегодня
        today = datetime.now().strftime('%Y-%m-%d')
        self.cursor.execute('SELECT bans_count FROM stats WHERE date = ?', (today,))
        today_stats = self.cursor.fetchone()
        today_bans = today_stats[0] if today_stats else 0
        
        # Последние 5 банов
        self.cursor.execute('''
            SELECT user_id, user_type, date FROM bans 
            WHERE status = "success" 
            ORDER BY date DESC LIMIT 5
        ''')
        recent = self.cursor.fetchall()
        
        return {
            'total': total,
            'channels': channels,
            'users': users,
            'today': today_bans,
            'recent': recent
        }
    
    def check_user(self, user_id):
        """Проверить, был ли пользователь забанен ранее"""
        self.cursor.execute('''
            SELECT user_type, action, date FROM bans 
            WHERE user_id = ? AND status = "success"
            ORDER BY date DESC LIMIT 1
        ''', (user_id,))
        return self.cursor.fetchone()

# Создаем экземпляр БД
db = Database()

# Временное хранилище для ID целей
target_storage = {}

# ========== ГЛАВНОЕ МЕНЮ ==========
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать главное меню с кнопками"""
    
    keyboard = [
        [InlineKeyboardButton("🚫 Забанить пользователя", callback_data="ban_user")],
        [InlineKeyboardButton("📢 Забанить канал", callback_data="ban_channel")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("🔍 Проверить пользователя", callback_data="check_user")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🤖 **Модератор бот**\n\n"
        "Выберите действие:"
    )
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# ========== ОБРАБОТКА КНОПОК ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки"""
    
    query = update.callback_query
    await query.answer()
    
    # Проверка прав администратора
    if query.from_user.id not in ADMIN_IDS:
        await query.edit_message_text("❌ У вас нет прав на использование этого бота.")
        return
    
    if query.data == "ban_user":
        target_storage[query.from_user.id] = {'action': 'ban_user'}
        await query.edit_message_text(
            "👤 **Бан пользователя**\n\n"
            "Отправьте ID пользователя:\n"
            "_(например: 123456789)_\n\n"
            "Или нажмите кнопку ниже для отмены:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="main_menu")
            ]]),
            parse_mode='Markdown'
        )
    
    elif query.data == "ban_channel":
        target_storage[query.from_user.id] = {'action': 'ban_channel'}
        await query.edit_message_text(
            "📢 **Бан канала**\n\n"
            "Отправьте ID канала:\n"
            "_(например: -1001234567890)_\n\n"
            "Или нажмите кнопку ниже для отмены:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="main_menu")
            ]]),
            parse_mode='Markdown'
        )
    
    elif query.data == "stats":
        await show_stats(query)
    
    elif query.data == "check_user":
        target_storage[query.from_user.id] = {'action': 'check_user'}
        await query.edit_message_text(
            "🔍 **Проверка пользователя**\n\n"
            "Отправьте ID пользователя/канала для проверки:\n\n"
            "Или нажмите кнопку ниже для отмены:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="main_menu")
            ]]),
            parse_mode='Markdown'
        )
    
    elif query.data == "help":
        await show_help(query)
    
    elif query.data == "main_menu":
        await show_main_menu(update, context)

# ========== СТАТИСТИКА ==========
async def show_stats(query):
    """Показать статистику из БД"""
    stats = db.get_stats()
    
    # Формируем список последних банов
    recent_text = ""
    for user_id, user_type, date in stats['recent']:
        emoji = "📢" if user_type == "channel" else "👤"
        date_formatted = date[:10]  # Только дата
        recent_text += f"{emoji} `{user_id}` - {date_formatted}\n"
    
    if not recent_text:
        recent_text = "Нет данных"
    
    text = (
        "📊 **СТАТИСТИКА**\n\n"
        f"📅 **Сегодня:** {stats['today']}\n"
        f"📈 **Всего банов:** {stats['total']}\n"
        f"👤 **Пользователей:** {stats['users']}\n"
        f"📢 **Каналов:** {stats['channels']}\n\n"
        f"**Последние баны:**\n{recent_text}"
    )
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад", callback_data="main_menu")
        ]]),
        parse_mode='Markdown'
    )

# ========== ПОМОЩЬ ==========
async def show_help(query):
    """Показать справку"""
    text = (
        "📚 **Как пользоваться ботом:**\n\n"
        "1️⃣ **Нажмите кнопку** с нужным действием\n"
        "2️⃣ **Отправьте ID** нарушителя\n"
        "3️⃣ **Подтвердите** действие\n\n"
        "**Где взять ID?**\n"
        "• Перешлите сообщение @getidsbot\n"
        "• ID каналов начинается с -100\n\n"
        "**Возможности:**\n"
        "• Бан пользователей\n"
        "• Бан каналов\n"
        "• Просмотр статистики\n"
        "• Проверка пользователей\n\n"
        "⚠️ Бот должен быть администратором канала!"
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад", callback_data="main_menu")
        ]]),
        parse_mode='Markdown'
    )

# ========== ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ (ID) ==========
async def handle_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка введенного ID"""
    
    user_id = update.effective_user.id
    
    # Проверка прав
    if user_id not in ADMIN_IDS:
        return
    
    # Проверка, что ожидаем ввод ID
    if user_id not in target_storage:
        await update.message.reply_text(
            "Сначала выберите действие в меню:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Открыть меню", callback_data="main_menu")
            ]])
        )
        return
    
    action = target_storage[user_id]['action']
    text = update.message.text.strip()
    
    try:
        target_id = int(text)
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом!")
        return
    
    # Если это проверка пользователя - сразу показываем результат
    if action == "check_user":
        result = db.check_user(target_id)
        
        if result:
            user_type, action_type, date = result
            type_emoji = "📢" if user_type == "channel" else "👤"
            action_text = "забанен" if "ban" in action_type else "другое действие"
            
            await update.message.reply_text(
                f"🔍 **Результат проверки**\n\n"
                f"{type_emoji} **ID:** `{target_id}`\n"
                f"**Статус:** Был {action_text}\n"
                f"**Дата:** {date[:16]}\n"
                f"**Тип:** {user_type}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ В меню", callback_data="main_menu")
                ]]),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                f"✅ **Пользователь `{target_id}` не найден в базе банов**",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ В меню", callback_data="main_menu")
                ]]),
                parse_mode='Markdown'
            )
        
        # Очищаем временные данные
        del target_storage[user_id]
        return
    
    # Сохраняем ID для подтверждения (для банов)
    target_storage[user_id]['target_id'] = target_id
    
    # Запрашиваем подтверждение
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{action}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    action_names = {
        'ban_user': '👤 бан пользователя',
        'ban_channel': '📢 бан канала'
    }
    
    await update.message.reply_text(
        f"⚠️ **Подтверждение**\n\n"
        f"Вы хотите выполнить: **{action_names[action]}**\n"
        f"Цель: `{target_id}`\n\n"
        f"Подтвердите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ========== ПОДТВЕРЖДЕНИЕ ДЕЙСТВИЙ ==========
async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка подтверждения действий"""
    
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if user_id not in target_storage or 'target_id' not in target_storage[user_id]:
        await query.edit_message_text("❌ Сессия истекла. Начните заново.")
        return
    
    target_id = target_storage[user_id]['target_id']
    action = target_storage[user_id]['action']
    
    if query.data == f"confirm_{action}":
        # Выполняем действие
        result, error_msg = await execute_action(query, context, action, target_id)
        
        if result:
            # Успешно - сохраняем в БД
            user_type = 'channel' if action == 'ban_channel' else 'user'
            db.add_ban(target_id, user_type, action, user_id, "success")
            
            await query.edit_message_text(
                f"✅ **Действие выполнено успешно!**\n\n"
                f"Цель: `{target_id}`",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ В главное меню", callback_data="main_menu")
                ]]),
                parse_mode='Markdown'
            )
        else:
            # Ошибка - сохраняем в БД как неудачную попытку
            user_type = 'channel' if action == 'ban_channel' else 'user'
            db.add_ban(target_id, user_type, action, user_id, "failed")
            
            # Сообщение об ошибке уже отправлено в execute_action
    
    # Очищаем временные данные
    if user_id in target_storage:
        del target_storage[user_id]

# ========== ВЫПОЛНЕНИЕ ДЕЙСТВИЙ ==========
async def execute_action(query, context, action, target_id):
    """Выполнить выбранное действие"""
    
    try:
        if action == "ban_user":
            # Бан пользователя
            await context.bot.ban_chat_member(
                chat_id=CHANNEL_ID,
                user_id=target_id
            )
            logger.info(f"Забанен пользователь {target_id}")
            return True, None
            
        elif action == "ban_channel":
            # Бан канала
            await context.bot.ban_chat_sender_chat(
                chat_id=CHANNEL_ID,
                sender_chat_id=target_id
            )
            logger.info(f"Забанен канал {target_id}")
            return True, None
            
    except TelegramError as e:
        error_message = str(e)
        
        if "chat not found" in error_message:
            await query.edit_message_text(
                "❌ Канал не найден. Проверьте CHANNEL_ID в настройках бота.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад", callback_data="main_menu")
                ]])
            )
        elif "not enough rights" in error_message:
            await query.edit_message_text(
                "❌ У бота нет прав на бан. Сделайте бота администратором канала с правом 'Банить пользователей'.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад", callback_data="main_menu")
                ]])
            )
        elif "user is an administrator" in error_message:
            await query.edit_message_text(
                "❌ Нельзя забанить администратора канала.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад", callback_data="main_menu")
                ]])
            )
        elif "PEER_ID_INVALID" in error_message:
            await query.edit_message_text(
                "❌ Неверный ID. Проверьте правильность ID.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад", callback_data="main_menu")
                ]])
            )
        else:
            await query.edit_message_text(
                f"❌ Ошибка: {error_message}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад", callback_data="main_menu")
                ]])
            )
        
        logger.error(f"Ошибка при выполнении {action} для {target_id}: {error_message}")
        return False, error_message

# ========== КОМАНДА START ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    if update.effective_user.id in ADMIN_IDS:
        await show_main_menu(update, context)
    else:
        await update.message.reply_text("❌ У вас нет доступа к этому боту.")

# ========== ЗАПУСК БОТА ==========
def main():
    """Главная функция запуска бота"""
    
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(?!confirm_).*$"))
    app.add_handler(CallbackQueryHandler(confirm_handler, pattern="^confirm_.*$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_id_input))
    
    # Запускаем бота
    logger.info("🚀 Бот запускается...")
    print("✅ Бот с кнопками и БД успешно запущен!")
    print("📝 Напишите /start боту для открытия меню.")
    print("💾 База данных сохранена в файле bans.db")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)
