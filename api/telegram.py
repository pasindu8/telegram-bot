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
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Bot Configuration ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
SEND_MESSAGE_API_URL = os.environ.get('SEND_MESSAGE_API_URL', "https://typical-gracia-pdbot-aed22ab6.koyeb.app/send-message")

# --- Firebase Initialization ---
# NOTE: Vercel environment variables are strings. We need to load the JSON string.
firebase_service_account_key_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY')
db = None
if firebase_service_account_key_json:
    try:
        # Check if the app is already initialized to prevent errors on hot reloads
        if not firebase_admin._apps:
            # The service account key is a JSON string, so we load it with json.loads()
            cred_json = json.loads(firebase_service_account_key_json)
            cred = credentials.Certificate(cred_json)
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
    """Generates a random alphanumeric PIN."""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for i in range(length))

async def is_pin_unique(pin: str) -> bool:
    """Checks if a PIN is unique in the Firestore collection."""
    if not FILES_COLLECTION:
        return False
    try:
        # Run the synchronous Firestore query in a separate thread
        query = FILES_COLLECTION.where('pin', '==', pin).limit(1)
        docs = await asyncio.to_thread(query.get)
        return len(docs) == 0
    except Exception as e:
        logger.error(f"Error checking PIN uniqueness: {e}")
        return False

async def generate_unique_pin(length=6) -> str:
    """Generates a unique PIN, retrying a few times if necessary."""
    max_attempts = 10
    for _ in range(max_attempts):
        pin = generate_pin(length)
        if await is_pin_unique(pin):
            return pin
        await asyncio.sleep(0.1) # Small delay to avoid tight loop
    return generate_pin(length) # Fallback to a non-guaranteed unique PIN

# --- API Functions ---
async def send_message_via_api(number: str, message_text: str) -> bool:
    """Sends a message using an external API."""
    try:
        payload = {"number": number, "message": message_text}
        headers = {"Content-Type": "application/json"}
        
        # Use asyncio.to_thread to run the blocking requests.post call
        response = await asyncio.to_thread(
            requests.post, SEND_MESSAGE_API_URL, json=payload, headers=headers, timeout=20
        )
        response.raise_for_status() # Raise an exception for bad status codes
        api_response = response.json()
        
        return api_response.get("status") == "success"
    except Exception as e:
        logger.error(f"Message API call failed: {e}")
        return False

async def ask_gemini_ai(query: str) -> str:
    """Gets a response from the Gemini AI model."""
    if not gemini_model:
        return "AI සේවාව ලබා ගත නොහැක. කරුණාකර පසුව උත්සාහ කරන්න."
    
    try:
        # Run the synchronous SDK call in a separate thread
        response = await asyncio.to_thread(gemini_model.generate_content, query)
        return response.text
    except Exception as e:
        logger.error(f"Gemini AI error: {e}")
        return "AI ප්‍රතිචාරයක් ලබාගැනීමේ දෝෂයක් සිදුවිය. කරුණාකර පසුව උත්සාහ කරන්න."

# --- Bot Commands ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
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
    """Starts the send message conversation."""
    await update.message.reply_text('දුරකථන අංකය ඇතුළත් කරන්න (උදා: 94712345678):')
    return SENDMSG_ASK_NUMBER

async def get_sendmsg_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets the phone number for the message."""
    number = update.message.text
    if not number.isdigit() or len(number) < 10:
        await update.message.reply_text('වලංගු දුරකථන අංකයක් ඇතුළත් කරන්න.')
        return SENDMSG_ASK_NUMBER
    
    context.user_data['sendmsg_number'] = number
    await update.message.reply_text('Message එක ඇතුළත් කරන්න:')
    return SENDMSG_ASK_MESSAGE

async def get_sendmsg_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets the message text and sends it."""
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
    
    context.user_data.clear()
    return ConversationHandler.END

# --- YouTube Download Handlers ---
async def start_yt_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the YouTube download conversation."""
    await update.message.reply_text('YouTube URL එක ඇතුළත් කරන්න:')
    return YT_ASK_URL

async def get_yt_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets the YouTube URL and downloads the video."""
    url = update.message.text
    
    if "youtube.com/" not in url and "youtu.be/" not in url:
        await update.message.reply_text('වලංගු YouTube URL එකක් ඇතුළත් කරන්න.')
        return YT_ASK_URL

    await update.message.reply_text('Video download කරමින්...')
    
    temp_dir = tempfile.mkdtemp()
    try:
        ydl_opts = {
            'format': 'best[height<=720][ext=mp4]/best[ext=mp4]/best',
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'noplaylist': True,
            'max_filesize': 50 * 1024 * 1024, # 50MB limit for Telegram
            'quiet': True,
            'no_warnings': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Run the blocking download call in a separate thread
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
            await update.message.reply_text('❌ Download දෝෂයක් සිදුවිය. File එක සොයාගත නොහැක.')
            
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"YouTube download error (yt_dlp): {e}")
        await update.message.reply_text('❌ Download දෝෂයක් සිදුවිය. URL එක පරීක්ෂා කරන්න.')
    except Exception as e:
        logger.error(f"YouTube download error (general): {e}")
        await update.message.reply_text('❌ Download දෝෂයක් සිදුවිය.')
    finally:
        # Clean up the temporary directory
        import shutil
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
    
    return ConversationHandler.END

# --- Other handlers (simplified for space, can be expanded) ---
async def start_download_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the direct URL download conversation."""
    await update.message.reply_text('Download URL එක ඇතුළත් කරන්න:')
    return DOWNLOAD_ASK_URL

async def get_download_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the direct URL download logic (placeholder)."""
    url = update.message.text
    if not (url.startswith("http://") or (url.startswith("https://")):
        await update.message.reply_text('වලංගු URL එකක් ඇතුළත් කරන්න.')
        return DOWNLOAD_ASK_URL
    
    await update.message.reply_text('File download කරමින්... (මෙම ක්‍රියාවලිය තවම සකසා නැත)')
    # TODO: Add actual download logic here, similar to the YouTube handler
    return ConversationHandler.END

async def start_upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the file upload conversation."""
    if not FILES_COLLECTION:
        await update.message.reply_text("File upload සේවාව මේ මොහොතේ ලබා ගත නොහැක.")
        return ConversationHandler.END
    await update.message.reply_text('File එක එවන්න:')
    return UPLOAD_WAIT_FILE

async def handle_uploaded_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the uploaded file logic (placeholder)."""
    # Simplified file upload handler
    await update.message.reply_text('File upload කරමින්... (මෙම ක්‍රියාවලිය තවම සකසා නැත)')
    # TODO: Implement file upload to a storage and save metadata to Firestore
    return ConversationHandler.END

async def start_get_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the file retrieval conversation."""
    if not FILES_COLLECTION:
        await update.message.reply_text("File download සේවාව මේ මොහොතේ ලබා ගත නොහැක.")
        return ConversationHandler.END
    await update.message.reply_text('PIN එක ඇතුළත් කරන්න:')
    return GETFILE_ASK_PIN

async def get_file_by_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Retrieves a file from storage using a PIN (placeholder)."""
    pin = update.message.text.strip().upper()
    await update.message.reply_text(f'PIN {pin} සමඟ file සොයමින්... (මෙම ක්‍රියාවලිය තවම සකසා නැත)')
    # TODO: Implement logic to find file metadata in Firestore by PIN and send it
    return ConversationHandler.END

async def start_ask_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the AI conversation."""
    if not gemini_model:
        await update.message.reply_text("AI සේවාව මේ මොහොතේ ලබා ගත නොහැක.")
        return ConversationHandler.END
    await update.message.reply_text('AI වෙතින් දැනගැනීමට අවශ්‍ය දේ ඇතුළත් කරන්න:')
    return AI_ASK_QUERY

async def get_ai_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets the user's query and asks the AI."""
    query = update.message.text
    if not query:
        await update.message.reply_text('කරුණාකර වලංගු ප්‍රශ්නයක් ඇතුළත් කරන්න.')
        return AI_ASK_QUERY
    
    await update.message.reply_text('AI ප්‍රතිචාරය සකස් කරමින්...')
    response = await ask_gemini_ai(query)
    await update.message.reply_text(response)
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current conversation."""
    await update.message.reply_text('ක්‍රියාවලිය අවලංගු කරන ලදී.')
    context.user_data.clear()
    return ConversationHandler.END

async def unhandled_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles any message that isn't part of a command or conversation."""
    await update.message.reply_text("මට ඔබව තේරුම් ගත නොහැක. පවතින විධාන බැලීමට /start භාවිතා කරන්න.")

# --- Application Setup ---
def create_application():
    """Creates and configures the Telegram Bot Application."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable not found!")
        return None
    
    app_builder = Application.builder().token(BOT_TOKEN)
    app = app_builder.build()
    
    # --- Conversation Handlers ---
    conv_handlers = {
        "sendmsg": ConversationHandler(
            entry_points=[CommandHandler("sendmsg", start_send_message)],
            states={
                SENDMSG_ASK_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_sendmsg_number)],
                SENDMSG_ASK_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_sendmsg_message)],
            },
            fallbacks=[CommandHandler("cancel", cancel_conversation)],
        ),
        "yt_download": ConversationHandler(
            entry_points=[CommandHandler("yt_download", start_yt_download)],
            states={YT_ASK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_yt_url)]},
            fallbacks=[CommandHandler("cancel", cancel_conversation)],
        ),
        "download_url": ConversationHandler(
            entry_points=[CommandHandler("download_url", start_download_url)],
            states={DOWNLOAD_ASK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_download_url)]},
            fallbacks=[CommandHandler("cancel", cancel_conversation)],
        ),
        "upload_file": ConversationHandler(
            entry_points=[CommandHandler("upload_file", start_upload_file)],
            states={UPLOAD_WAIT_FILE: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_uploaded_file)]},
            fallbacks=[CommandHandler("cancel", cancel_conversation)],
        ),
        "get_file": ConversationHandler(
            entry_points=[CommandHandler("get_file", start_get_file)],
            states={GETFILE_ASK_PIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_file_by_pin)]},
            fallbacks=[CommandHandler("cancel", cancel_conversation)],
        ),
        "ask_ai": ConversationHandler(
            entry_points=[CommandHandler("ask_ai", start_ask_ai)],
            states={AI_ASK_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ai_query)]},
            fallbacks=[CommandHandler("cancel", cancel_conversation)],
        )
    }

    # Register all handlers
    app.add_handler(CommandHandler("start", start_command))
    for name, handler_instance in conv_handlers.items():
        app.add_handler(handler_instance)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unhandled_message))
    
    return app

# Global application instance, created once
application = create_application()

# --- Vercel Handler ---
async def main(request_body: Dict[str, Any]):
    """Main async function to process a single update."""
    if not application:
        logger.error("Application not initialized.")
        return
    
    update = Update.de_json(request_body, application.bot)
    await application.process_update(update)

def handler(request):
    """
    Main Vercel serverless function handler.
    This function is the entry point for all requests from Vercel.
    """
    try:
        # Health check for GET requests
        if request.method == 'GET':
            return {
                'statusCode': 200,
                'body': json.dumps({'status': 'Bot is running and healthy!'})
            }

        # Process Telegram webhook for POST requests
        if request.method == 'POST':
            if not application:
                return {'statusCode': 500, 'body': json.dumps({'error': 'Bot application not initialized'})}
            
            # Run the async main function to handle the update
            asyncio.run(main(json.loads(request.body.decode('utf-8'))))
            
            return {'statusCode': 200, 'body': json.dumps({'status': 'ok'})}

        # Disallow other methods
        return {
            'statusCode': 405,
            'body': json.dumps({'error': 'Method Not Allowed'})
        }
    
    except Exception as e:
        logger.error(f"Error in Vercel handler: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
