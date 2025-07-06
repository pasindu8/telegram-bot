import json
import logging
import os
import tempfile
import random
import string
import asyncio
import shutil
from typing import Optional, Dict, Any

import requests
import yt_dlp
from google import genai
from google.genai import types
import firebase_admin
from firebase_admin import credentials, firestore

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
SEND_MESSAGE_API_URL = os.environ.get('SEND_MESSAGE_API_URL', "https://typical-gracia-pdbot-aed22ab6.koyeb.app/send-message")
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
FIREBASE_SERVICE_ACCOUNT_KEY = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY')

# --- Firebase Initialization ---
db = None
if FIREBASE_SERVICE_ACCOUNT_KEY:
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(json.loads(FIREBASE_SERVICE_ACCOUNT_KEY))
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Firebase initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Firebase: {e}")
else:
    logger.warning("FIREBASE_SERVICE_ACCOUNT_KEY not found. Firebase will not be initialized.")

# --- Gemini AI Initialization ---
gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Gemini AI client initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Gemini AI: {e}")
else:
    logger.warning("GEMINI_API_KEY not found. Gemini AI will not be initialized.")

APP_ID = "telegram_vercel_bot_app"
FILES_COLLECTION = db.collection(f"artifacts/{APP_ID}/public/data/files") if db else None

# --- User Session Management ---
user_sessions: Dict[int, Dict[str, Any]] = {}

# --- Helper Functions ---
def generate_pin(length=6):
    """Generate a random PIN"""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for i in range(length))

async def is_pin_unique(pin: str) -> bool:
    """Check if PIN is unique in Firestore"""
    if not FILES_COLLECTION:
        logger.error("Firestore not initialized, cannot check PIN uniqueness.")
        return False
    try:
        query = FILES_COLLECTION.where('pin', '==', pin).limit(1)
        docs = await asyncio.to_thread(query.get)
        return len(docs) == 0
    except Exception as e:
        logger.error(f"Error checking PIN uniqueness in Firestore: {e}")
        return False

async def generate_unique_pin(length=6) -> str:
    """Generate a unique PIN"""
    max_attempts = 10
    for _ in range(max_attempts):
        pin = generate_pin(length)
        if await is_pin_unique(pin):
            return pin
        await asyncio.sleep(0.1)
    logger.warning("Could not generate a unique PIN after multiple attempts.")
    return generate_pin(length)

async def send_telegram_message(chat_id: int, text: str, parse_mode: str = None) -> bool:
    """Send message via Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return False

async def send_telegram_document(chat_id: int, document_data: bytes, filename: str, caption: str = None) -> bool:
    """Send document via Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    
    files = {
        'document': (filename, document_data, 'application/octet-stream')
    }
    data = {
        'chat_id': chat_id
    }
    if caption:
        data['caption'] = caption
    
    try:
        response = requests.post(url, files=files, data=data, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Error sending Telegram document: {e}")
        return False

async def send_message_via_api(number: str, message_text: str) -> bool:
    """Send message via external API"""
    logger.info(f"Attempting to send message to number: {number}")
    payload = {"number": number, "message": message_text}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(SEND_MESSAGE_API_URL, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        api_response = response.json()
        
        if api_response.get("status") == "success": 
            logger.info(f"Message sent successfully to {number}")
            return True
        else:
            logger.warning(f"Failed to send message to {number}. API Response: {api_response}")
            return False
    except Exception as e:
        logger.error(f"Message API call failed for number {number}: {e}")
        return False

async def ask_gemini_ai(query: str) -> str:
    """Ask Gemini AI a question"""
    if not gemini_client:
        return "AI සේවාව ලබා ගත නොහැක. කරුණාකර පසුව උත්සාහ කරන්න."
    
    logger.info(f"Asking AI: {query[:100]}...")
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=query
        )
        return response.text or "AI ප්‍රතිචාරයක් ලබාගත නොහැකි විය."
    except Exception as e:
        logger.error(f"Gemini AI error: {e}")
        return "AI ප්‍රතිචාරයක් ලබාගැනීමේ දෝෂයක් සිදුවිය. කරුණාකර පසුව උත්සාහ කරන්න."

async def download_youtube_video(url: str, chat_id: int) -> bool:
    """Download YouTube video and send to user"""
    await send_telegram_message(chat_id, 'Video එක download කරමින් සිටී. කරුණාකර මොහොතක් රැඳී සිටින්න...')
    
    temp_dir = tempfile.mkdtemp()
    try:
        ydl_opts = {
            'format': 'best[filesize<50M]/worst',
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'noplaylist': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if os.path.exists(filename):
                file_size = os.path.getsize(filename)
                if file_size > 50 * 1024 * 1024:  # 50MB limit
                    await send_telegram_message(chat_id, f'Video එක ({file_size / (1024*1024):.2f} MB) Telegram හරහා යැවීමට විශාල වැඩියි.')
                    return False
                
                with open(filename, 'rb') as f:
                    video_data = f.read()
                
                success = await send_telegram_document(
                    chat_id,
                    video_data,
                    os.path.basename(filename),
                    f"ඔබගේ YouTube video: {info.get('title', 'Video')}"
                )
                
                if success:
                    await send_telegram_message(chat_id, '✅ Video සාර්ථකව යවන ලදී!')
                    return True
                else:
                    await send_telegram_message(chat_id, '❌ Video යැවීමේ දෝෂයක් සිදුවිය.')
                    return False
            else:
                await send_telegram_message(chat_id, '❌ Video download කිරීමේ දෝෂයක් සිදුවිය.')
                return False
                
    except Exception as e:
        logger.error(f"YouTube download error: {e}")
        await send_telegram_message(chat_id, f'❌ YouTube video download කිරීමේ දෝෂයක් සිදුවිය: {str(e)}')
        return False
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

async def download_file_from_url(url: str, chat_id: int) -> bool:
    """Download file from URL and send to user"""
    await send_telegram_message(chat_id, 'File එක download කරමින් සිටී. කරුණාකර මොහොතක් රැඳී සිටින්න...')
    
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        filename = None
        if "Content-Disposition" in response.headers:
            filename = response.headers["Content-Disposition"].split("filename=")[-1].strip('"\'')
        if not filename:
            filename = os.path.basename(url.split('?')[0])
            if not filename:
                filename = "downloaded_file"
        
        total_size = int(response.headers.get('content-length', 0))
        if total_size > 50 * 1024 * 1024:  # 50MB limit
            await send_telegram_message(chat_id, f'File එක ({total_size / (1024*1024):.2f} MB) Telegram හරහා යැවීමට විශාල වැඩියි.')
            return False
        
        file_data = b''
        for chunk in response.iter_content(chunk_size=8192):
            file_data += chunk
        
        success = await send_telegram_document(
            chat_id,
            file_data,
            filename,
            f"ඔබගේ file එක: {filename}"
        )
        
        if success:
            await send_telegram_message(chat_id, '✅ File සාර්ථකව යවන ලදී!')
            return True
        else:
            await send_telegram_message(chat_id, '❌ File යැවීමේ දෝෂයක් සිදුවිය.')
            return False
            
    except Exception as e:
        logger.error(f"URL download error: {e}")
        await send_telegram_message(chat_id, f'❌ File download කිරීමේ දෝෂයක් සිදුවිය: {str(e)}')
        return False

async def store_file_with_pin(file_data: bytes, filename: str, chat_id: int) -> str:
    """Store file in Firestore with PIN"""
    if not FILES_COLLECTION:
        logger.error("Firestore not initialized")
        return None
    
    try:
        pin = await generate_unique_pin()
        
        # Store file data as base64 in Firestore
        import base64
        file_b64 = base64.b64encode(file_data).decode('utf-8')
        
        doc_data = {
            'pin': pin,
            'filename': filename,
            'file_data': file_b64,
            'chat_id': chat_id,
            'created_at': firestore.SERVER_TIMESTAMP
        }
        
        await asyncio.to_thread(FILES_COLLECTION.add, doc_data)
        logger.info(f"File stored with PIN: {pin}")
        return pin
        
    except Exception as e:
        logger.error(f"Error storing file with PIN: {e}")
        return None

async def get_file_by_pin(pin: str) -> Optional[tuple]:
    """Retrieve file by PIN from Firestore"""
    if not FILES_COLLECTION:
        logger.error("Firestore not initialized")
        return None
    
    try:
        query = FILES_COLLECTION.where('pin', '==', pin).limit(1)
        docs = await asyncio.to_thread(query.get)
        
        if docs:
            doc = docs[0]
            data = doc.to_dict()
            
            # Decode base64 file data
            import base64
            file_data = base64.b64decode(data['file_data'])
            filename = data['filename']
            
            return file_data, filename
        else:
            return None
            
    except Exception as e:
        logger.error(f"Error retrieving file by PIN: {e}")
        return None

# --- Message Handlers ---
async def handle_start(chat_id: int):
    """Handle /start command"""
    message = (
        'ආයුබෝවන්! මම ඔබට දුරකථන අංකයකට message යැවීමට, YouTube videos download කිරීමට, '
        'ඕනෑම URL එකකින් files download කිරීමට, AI සමඟ කතා කිරීමට, '
        'සහ files upload කර PIN එකකින් නැවත download කිරීමට උදව් කරන bot කෙනෙක්.\n\n'
        'Commands:\n'
        '/sendmsg - දුරකථන අංකයකට message එකක් යවන්න\n'
        '/yt_download - YouTube video එකක් download කරන්න\n'
        '/download_url - ඕනෑම URL එකකින් file එකක් download කරන්න\n'
        '/upload_file - File එකක් upload කර PIN එකක් ලබාගන්න\n'
        '/get_file - PIN එකක් දී file එකක් download කරන්න\n'
        '/ask_ai - AI සමඟ කතා කරන්න\n'
        '/cancel - ඕනෑම ක්‍රියාවලියක් අවලංගු කරන්න'
    )
    await send_telegram_message(chat_id, message)

async def handle_sendmsg(chat_id: int):
    """Handle /sendmsg command"""
    user_sessions[chat_id] = {"state": "waiting_phone", "command": "sendmsg"}
    await send_telegram_message(chat_id, 'කරුණාකර ඔබට message එක යැවීමට අවශ්‍ය **දුරකථන අංකය** ඇතුළත් කරන්න (රට කේතය සමඟ, උදා: 94712345678).')

async def handle_yt_download(chat_id: int):
    """Handle /yt_download command"""
    user_sessions[chat_id] = {"state": "waiting_youtube_url", "command": "yt_download"}
    await send_telegram_message(chat_id, 'කරුණාකර YouTube video එකේ URL එක ඇතුළත් කරන්න.')

async def handle_download_url(chat_id: int):
    """Handle /download_url command"""
    user_sessions[chat_id] = {"state": "waiting_download_url", "command": "download_url"}
    await send_telegram_message(chat_id, 'කරුණාකර download කිරීමට අවශ්‍ය file එකේ URL එක ඇතුළත් කරන්න.')

async def handle_upload_file(chat_id: int):
    """Handle /upload_file command"""
    user_sessions[chat_id] = {"state": "waiting_file_upload", "command": "upload_file"}
    await send_telegram_message(chat_id, 'කරුණාකර upload කිරීමට අවශ්‍ය file එක යවන්න.')

async def handle_get_file(chat_id: int):
    """Handle /get_file command"""
    user_sessions[chat_id] = {"state": "waiting_pin", "command": "get_file"}
    await send_telegram_message(chat_id, 'කරුණාකර file එක download කිරීමට PIN එක ඇතුළත් කරන්න.')

async def handle_ask_ai(chat_id: int):
    """Handle /ask_ai command"""
    user_sessions[chat_id] = {"state": "waiting_ai_query", "command": "ask_ai"}
    await send_telegram_message(chat_id, 'කරුණාකර AI එකෙන් අසන්න අවශ්‍ය ප්‍රශ්නය ඇතුළත් කරන්න.')

async def handle_cancel(chat_id: int):
    """Handle /cancel command"""
    if chat_id in user_sessions:
        del user_sessions[chat_id]
        await send_telegram_message(chat_id, 'ක්‍රියාවලිය අවලංගු කරන ලදී.')
    else:
        await send_telegram_message(chat_id, 'අවලංගු කිරීමට ක්‍රියාවලියක් නැත.')

async def handle_text_message(chat_id: int, text: str):
    """Handle text messages based on user state"""
    if chat_id not in user_sessions:
        await send_telegram_message(chat_id, 'කරුණාකර command එකක් භාවිතා කරන්න. /start කර වැඩි තොරතුරු ලබාගන්න.')
        return
    
    session = user_sessions[chat_id]
    state = session.get("state")
    command = session.get("command")
    
    if command == "sendmsg":
        if state == "waiting_phone":
            if not text.isdigit() or len(text) < 10:
                await send_telegram_message(chat_id, 'කරුණාකර වලංගු දුරකථන අංකයක් ඇතුළත් කරන්න. (උදා: 94712345678)')
                return
            session["phone"] = text
            session["state"] = "waiting_message"
            await send_telegram_message(chat_id, 'හොඳයි. දැන් කරුණාකර ඔබට යැවීමට අවශ්‍ය **message එක** ඇතුළත් කරන්න.')
        elif state == "waiting_message":
            phone = session.get("phone")
            await send_telegram_message(chat_id, 'ඔබගේ message එක යවමින් සිටී...')
            success = await send_message_via_api(phone, text)
            if success:
                await send_telegram_message(chat_id, '✅ Message සාර්ථකව යවන ලදී!')
            else:
                await send_telegram_message(chat_id, '❌ Message යැවීම අසාර්ථක විය. කරුණාකර නැවත උත්සාහ කරන්න.')
            del user_sessions[chat_id]
    
    elif command == "yt_download" and state == "waiting_youtube_url":
        if "youtube.com" in text or "youtu.be" in text:
            await download_youtube_video(text, chat_id)
        else:
            await send_telegram_message(chat_id, 'කරුණාකර වලංගු YouTube URL එකක් ඇතුළත් කරන්න.')
        del user_sessions[chat_id]
    
    elif command == "download_url" and state == "waiting_download_url":
        if text.startswith("http"):
            await download_file_from_url(text, chat_id)
        else:
            await send_telegram_message(chat_id, 'කරුණාකර වලංගු URL එකක් ඇතුළත් කරන්න.')
        del user_sessions[chat_id]
    
    elif command == "get_file" and state == "waiting_pin":
        result = await get_file_by_pin(text.upper())
        if result:
            file_data, filename = result
            success = await send_telegram_document(chat_id, file_data, filename, f"PIN: {text.upper()}")
            if success:
                await send_telegram_message(chat_id, '✅ File සාර්ථකව යවන ලදී!')
            else:
                await send_telegram_message(chat_id, '❌ File යැවීමේ දෝෂයක් සිදුවිය.')
        else:
            await send_telegram_message(chat_id, '❌ PIN එක සොයාගත නොහැක. කරුණාකර නිවැරදි PIN එක ඇතුළත් කරන්න.')
        del user_sessions[chat_id]
    
    elif command == "ask_ai" and state == "waiting_ai_query":
        response = await ask_gemini_ai(text)
        await send_telegram_message(chat_id, response)
        del user_sessions[chat_id]

async def handle_document(chat_id: int, file_id: str, filename: str):
    """Handle document upload"""
    if chat_id not in user_sessions or user_sessions[chat_id].get("state") != "waiting_file_upload":
        await send_telegram_message(chat_id, 'කරුණාකර /upload_file command එක භාවිතා කරන්න.')
        return
    
    try:
        # Get file info from Telegram
        file_info_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        response = requests.get(file_info_url)
        response.raise_for_status()
        file_info = response.json()
        
        if not file_info.get("ok"):
            await send_telegram_message(chat_id, '❌ File තොරතුරු ලබාගැනීමේ දෝෂයක් සිදුවිය.')
            return
        
        file_path = file_info["result"]["file_path"]
        
        # Download file from Telegram
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        file_response = requests.get(file_url)
        file_response.raise_for_status()
        
        file_data = file_response.content
        
        # Store file with PIN
        pin = await store_file_with_pin(file_data, filename, chat_id)
        
        if pin:
            await send_telegram_message(chat_id, f'✅ File සාර්ථකව upload කරන ලදී!\n\nඔබගේ PIN: **{pin}**\n\nමෙම PIN එක භාවිතා කර /get_file command එකෙන් file එක download කරගත හැක.')
        else:
            await send_telegram_message(chat_id, '❌ File upload කිරීමේ දෝෂයක් සිදුවිය.')
        
        del user_sessions[chat_id]
        
    except Exception as e:
        logger.error(f"Document handling error: {e}")
        await send_telegram_message(chat_id, '❌ File upload කිරීමේ දෝෂයක් සිදුවිය.')
        del user_sessions[chat_id]

async def process_update(update_data: dict):
    """Process incoming Telegram update"""
    try:
        if "message" in update_data:
            message = update_data["message"]
            chat_id = message["chat"]["id"]
            
            if "text" in message:
                text = message["text"]
                
                # Handle commands
                if text.startswith("/"):
                    command = text.split()[0]
                    
                    if command == "/start":
                        await handle_start(chat_id)
                    elif command == "/sendmsg":
                        await handle_sendmsg(chat_id)
                    elif command == "/yt_download":
                        await handle_yt_download(chat_id)
                    elif command == "/download_url":
                        await handle_download_url(chat_id)
                    elif command == "/upload_file":
                        await handle_upload_file(chat_id)
                    elif command == "/get_file":
                        await handle_get_file(chat_id)
                    elif command == "/ask_ai":
                        await handle_ask_ai(chat_id)
                    elif command == "/cancel":
                        await handle_cancel(chat_id)
                    else:
                        await send_telegram_message(chat_id, 'අදාළ command එක සොයාගත නොහැක. /start කර වැඩි තොරතුරු ලබාගන්න.')
                else:
                    # Handle text messages
                    await handle_text_message(chat_id, text)
            
            elif "document" in message:
                doc = message["document"]
                file_id = doc["file_id"]
                filename = doc.get("file_name", "document")
                await handle_document(chat_id, file_id, filename)
                
    except Exception as e:
        logger.error(f"Error processing update: {e}")

def handler(request):
    """Vercel serverless function handler"""
    try:
        # Handle webhook verification
        if request.method == 'GET':
            return {
                'statusCode': 200,
                'body': json.dumps({'status': 'Bot is running'})
            }
        
        # Handle POST requests (Telegram updates)
        if request.method == 'POST':
            try:
                # Parse request body
                if hasattr(request, 'get_json'):
                    update_data = request.get_json()
                else:
                    # Handle different request formats
                    body = request.body if hasattr(request, 'body') else request.data
                    if isinstance(body, bytes):
                        body = body.decode('utf-8')
                    update_data = json.loads(body)
                
                # Process update asynchronously
                asyncio.run(process_update(update_data))
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({'status': 'ok'})
                }
                
            except Exception as e:
                logger.error(f"Error processing webhook: {e}")
                return {
                    'statusCode': 500,
                    'body': json.dumps({'error': str(e)})
                }
        
        return {
            'statusCode': 405,
            'body': json.dumps({'error': 'Method not allowed'})
        }
        
    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

# For Vercel, we need to export the handler
def telegram_webhook(request):
    """Main webhook handler for Vercel"""
    return handler(request)
