import json
import logging
import os
import tempfile
import random
import string
import asyncio
from typing import Dict, Any

import telegram
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
import requests
import yt_dlp

# Firebase Admin SDK imports
import firebase_admin
from firebase_admin import credentials, firestore

# Google Gemini API import
import google.generativeai as genai

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Bot Configuration ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
SEND_MESSAGE_API_URL = os.environ.get('SEND_MESSAGE_API_URL', "https://typical-gracia-pdbot-aed22ab6.koyeb.app/send-message")

# --- Firebase Initialization ---
firebase_service_account_key_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY')
db = None
if firebase_service_account_key_json:
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(json.loads(firebase_service_account_key_json))
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Firebase initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Firebase: {e}")

# --- Gemini AI Initialization ---
gemini_api_key = os.environ.get('GEMINI_API_KEY')
gemini_model = None
if gemini_api_key:
    try:
        genai.configure(api_key=gemini_api_key)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')
        logger.info("Gemini AI model initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Gemini AI: {e}")

APP_ID = "telegram_vercel_bot_app"
FILES_COLLECTION = db.collection(f"artifacts/{APP_ID}/public/data/files") if db else None

# --- Conversation States ---
SENDMSG_ASK_NUMBER, SENDMSG_ASK_MESSAGE = range(2)
YT_ASK_URL = range(2, 3)
UPLOAD_WAIT_FILE = range(3, 4)
GETFILE_ASK_PIN = range(4, 5)
AI_ASK_QUERY = range(5, 6)
DOWNLOAD_ASK_URL = range(6, 7)

# --- Helper Functions ---
def generate_pin(length=6):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for i in range(length))

async def is_pin_unique(pin: str) -> bool:
    if not FILES_COLLECTION:
        return False
    try:
        query = FILES_COLLECTION.where('pin', '==', pin).limit(1)
        docs = await asyncio.to_thread(query.get)
        return len(docs) == 0
    except Exception:
        return False

async def generate_unique_pin(length=6) -> str:
    max_attempts = 10
    for _ in range(max_attempts):
        pin = generate_pin(length)
        if await is_pin_unique(pin):
            return pin
        await asyncio.sleep(0.1)
    return generate_pin(length)  # Fallback

# --- API Functions ---
async def send_message_via_api(number: str, message_text: str) -> bool:
    try:
        payload = {"number": number, "message": message_text}
        headers = {"Content-Type": "application/json"}
        
        response = requests.post(SEND_MESSAGE_API_URL, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        api_response = response.json()
        
        return api_response.get("status") == "success"
    except Exception as e:
        logger.error(f"Message API call failed: {e}")
        return False

async def ask_gemini_ai(query: str) -> str:
    if not gemini_model:
        return "AI සේවාව ලබා ගත නොහැක. කරුණාකර පසුව උත්සාහ කරන්න."
    
    try:
        response = await asyncio.to_thread(gemini_model.generate_content, query)
        return response.text
    except Exception as e:
        logger.error(f"Gemini AI error: {e}")
        return "AI ප්‍රතිචාරයක් ලබාගැනීමේ දෝෂයක් සිදුවිය. කරුණාකර පසුව උත්සාහ කරන්න."

# --- Bot Commands ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        'ආයුබෝවන්! මම ඔබට උදව් කරන bot කෙනෙක්.\n\n'
        'Commands:\n'
        '/sendmsg - Message යවන්න\n'
        '/yt_download - YouTube download\n'
        '/download_url - URL download\n'
        '/upload_file - File upload\n'
        '/get_file - File download\n'
        '/ask_ai - AI chat\n'
        '/cancel - Cancel'
    )

# --- Send Message Handlers ---
async def start_send_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('දුරකථන අංකය ඇතුළත් කරන්න (උදා: 94712345678):')
    return SENDMSG_ASK_NUMBER

async def get_sendmsg_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    number = update.message.text
    if not number.isdigit() or len(number) < 10:
        await update.message.reply_text('වලංගු දුරකථන අංකයක් ඇතුළත් කරන්න.')
        return SENDMSG_ASK_NUMBER
    
    context.user_data['sendmsg_number'] = number
    await update.message.reply_text('Message එක ඇතුළත් කරන්න:')
    return SENDMSG_ASK_MESSAGE

async def get_sendmsg_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message_text = update.message.text
    number = context.user_data.get('sendmsg_number')

    if number and message_text:
        await update.message.reply_text('Message යවමින්...')
        success = await send_message_via_api(number, message_text)
        
        if success:
            await update.message.reply_text('✅ Message සාර්ථකව යවන ලදී!')
        else:
            await update.message.reply_text('❌ Message යැවීම අසාර්ථක විය.')
    else:
        await update.message.reply_text('දෝෂයක් සිදුවිය. /sendmsg නැවත උත්සාහ කරන්න.')
    return ConversationHandler.END

# --- YouTube Download Handlers ---
async def start_yt_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('YouTube URL එක ඇතුළත් කරන්න:')
    return YT_ASK_URL

async def get_yt_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text
    
    if "youtube.com/" not in url and "youtu.be/" not in url:
        await update.message.reply_text('වලංගු YouTube URL එකක් ඇතුළත් කරන්න.')
        return YT_ASK_URL

    await update.message.reply_text('Video download කරමින්...')
    
    try:
        temp_dir = tempfile.mkdtemp()
        ydl_opts = {
            'format': 'best[height<=720][ext=mp4]/best[ext=mp4]/best',
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'noplaylist': True,
            'max_filesize': 50 * 1024 * 1024,
            'quiet': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            file_path = ydl.prepare_filename(info)

        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > 50 * 1024 * 1024:
                await update.message.reply_text('File විශාල වැඩියි. 50MB ට අඩු files පමණි.')
            else:
                with open(file_path, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.message.chat_id,
                        document=f,
                        caption=f"Video: {info.get('title', 'YouTube Video')}"
                    )
                await update.message.reply_text('✅ Video සාර්ථකව යවන ලදී!')
        else:
            await update.message.reply_text('❌ Download දෝෂයක් සිදුවිය.')
            
    except Exception as e:
        logger.error(f"YouTube download error: {e}")
        await update.message.reply_text('❌ Download දෝෂයක් සිදුවිය.')
    finally:
        if 'temp_dir' in locals() and os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir)
    
    return ConversationHandler.END

# --- Other handlers (simplified for space) ---
async def start_download_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Download URL එක ඇතුළත් කරන්න:')
    return DOWNLOAD_ASK_URL

async def get_download_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text
    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text('වලංගු URL එකක් ඇතුළත් කරන්න.')
        return DOWNLOAD_ASK_URL
    
    await update.message.reply_text('File download කරමින්...')
    # Add download logic here
    return ConversationHandler.END

async def start_upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not FILES_COLLECTION:
        await update.message.reply_text("File upload සේවාව ලබා ගත නොහැක.")
        return ConversationHandler.END
    await update.message.reply_text('File එක එවන්න:')
    return UPLOAD_WAIT_FILE

async def handle_uploaded_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Simplified file upload handler
    await update.message.reply_text('File upload කරමින්...')
    return ConversationHandler.END

async def start_get_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not FILES_COLLECTION:
        await update.message.reply_text("File download සේවාව ලබා ගත නොහැක.")
        return ConversationHandler.END
    await update.message.reply_text('PIN එක ඇතුළත් කරන්න:')
    return GETFILE_ASK_PIN

async def get_file_by_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pin = update.message.text.strip().upper()
    await update.message.reply_text(f'PIN {pin} සමඟ file සොයමින්...')
    return ConversationHandler.END

async def start_ask_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not gemini_model:
        await update.message.reply_text("AI සේවාව ලබා ගත නොහැක.")
        return ConversationHandler.END
    await update.message.reply_text('AI ප්‍රශ්නය ඇතුළත් කරන්න:')
    return AI_ASK_QUERY

async def get_ai_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.message.text
    if not query:
        await update.message.reply_text('වලංගු ප්‍රශ්නයක් ඇතුළත් කරන්න.')
        return AI_ASK_QUERY
    
    await update.message.reply_text('AI ප්‍රතිචාරය සකස් කරමින්...')
    response = await ask_gemini_ai(query)
    await update.message.reply_text(response)
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('අවලංගු කරන ලදී.')
    return ConversationHandler.END

async def unhandled_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("මට තේරෙන්නේ නැහැ. /start භාවිතා කරන්න.")

# --- Application Setup ---
def create_application():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not found!")
        return None
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    
    # Send message conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("sendmsg", start_send_message)],
        states={
            SENDMSG_ASK_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_sendmsg_number)],
            SENDMSG_ASK_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_sendmsg_message)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    ))
    
    # YouTube download conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("yt_download", start_yt_download)],
        states={
            YT_ASK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_yt_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    ))
    
    # URL download conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("download_url", start_download_url)],
        states={
            DOWNLOAD_ASK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_download_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    ))
    
    # File upload conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("upload_file", start_upload_file)],
        states={
            UPLOAD_WAIT_FILE: [MessageHandler(filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO, handle_uploaded_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    ))
    
    # File download conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("get_file", start_get_file)],
        states={
            GETFILE_ASK_PIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_file_by_pin)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    ))
    
    # AI conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("ask_ai", start_ask_ai)],
        states={
            AI_ASK_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ai_query)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    ))
    
    # Unhandled messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unhandled_message))
    
    return app

# Global application instance
application = create_application()

# --- Vercel Handler ---
def handler(request):
    """Main Vercel serverless function handler"""
    try:
        if not application:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Bot not initialized'})
            }
        
        if request.method == 'POST':
            # Handle Telegram webhook
            try:
                update_data = json.loads(request.body.decode('utf-8'))
                update = Update.de_json(update_data, application.bot)
                
                # Process update in async context
                asyncio.run(application.process_update(update))
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({'status': 'ok'})
                }
            except Exception as e:
                logger.error(f"Error processing update: {e}")
                return {
                    'statusCode': 500,
                    'body': json.dumps({'error': str(e)})
                }
        
        elif request.method == 'GET':
            # Health check
            return {
                'statusCode': 200,
                'body': json.dumps({'status': 'Bot is running!'})
            }
        
        else:
            return {
                'statusCode': 405,
                'body': json.dumps({'error': 'Method Not Allowed'})
            }
    
    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

# Export for Vercel
app = handler
