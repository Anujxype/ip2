import logging
import os
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode, ChatAction
import aiohttp
import asyncio
import json
import re
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
import pytz

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "8415869688:AAHSiFfKuAo4_75e_835hgebl2iKku3RJKg")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "osXspace")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/osXspace")
API_BASE_URL = os.getenv("API_BASE_URL", "https://paidf2.zioniiixx.workers.dev")

# Group configuration
GROUP_CHAT_ID = -1002414357299 # Your private group chat ID
GROUP_LINK = "https://t.me/+bVDSE8QxqJE1M2Nl"  # Your private group invite link

# Parse admin IDs from environment
admin_ids_str = os.getenv("ADMIN_IDS", "7167145056" , "6435989814")
ADMIN_IDS = [int(id.strip()) for id in admin_ids_str.split(",") if id.strip().isdigit()]

# MongoDB configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://osXspace:osXspace@cluster0.k3k6yzj.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = "osint_bot"
COLLECTION_NAME = "users"

# MongoDB connection options
MONGO_OPTIONS = {
    'tls': True,
    'tlsAllowInvalidCertificates': True,
    'tlsAllowInvalidHostnames': True,
    'serverSelectionTimeoutMS': 5000,
    'connectTimeoutMS': 10000,
    'socketTimeoutMS': 10000,
    'maxPoolSize': 10,
    'minPoolSize': 1
}

# Global variables
mongo_client = None
users_collection = None
db_connected = False  # Track connection status
user_last_request = {}
REQUEST_COOLDOWN = 5
USER_DATA_CACHE = {}

# Disclaimer text
DISCLAIMER_TEXT = """
🔍 <b>Search Bot - Terms of Use</b>

<b>Important Disclaimer:</b>

🔎 Search Bot – Explore public data for research & awareness. 
❌ No illegal use. 
👨‍💻 Respect Privacy • Use Responsibly

• <b>By using this bot, you agree to:</b>
  ✓ Use data responsibly and ethically
  ✓ Respect privacy of individuals
  ✓ NOT use for illegal activities
  ✓ NOT use for harassment or stalking
  ✓ Comply with all applicable laws

• <b>Prohibited Uses:</b>
  ✗ Any illegal activity
  ✗ Harassment or stalking
  ✗ Identity theft or fraud
  ✗ Violation of privacy rights

• The bot owners are NOT responsible for misuse of information.
• Data provided is from public sources and may not be accurate.

<b>By clicking "I Agree", you accept these terms and conditions.</b>
"""

async def init_mongodb():
    """Initialize MongoDB connection"""
    global mongo_client, users_collection, db_connected
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URI, **MONGO_OPTIONS)
        db = mongo_client[DB_NAME]
        users_collection = db[COLLECTION_NAME]
        
        # Test connection
        await mongo_client.admin.command('ping')
        db_connected = True
        logger.info("✅ MongoDB connected successfully")
        return True
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        db_connected = False
        return False

async def get_user_data(user_id: int):
    """Get user data from MongoDB or cache"""
    try:
        if db_connected and users_collection is not None:
            user = await users_collection.find_one({"user_id": user_id})
            if user:
                # Remove MongoDB _id field
                user.pop('_id', None)
                USER_DATA_CACHE[user_id] = user
                return user
    except Exception as e:
        logger.error(f"Error getting user data: {e}")
    
    return USER_DATA_CACHE.get(user_id, None)

async def save_user_data(user_data: dict):
    """Save or update user data"""
    user_id = user_data["user_id"]
    USER_DATA_CACHE[user_id] = user_data
    
    try:
        if db_connected and users_collection is not None:
            await users_collection.update_one(
                {"user_id": user_id},
                {"$set": user_data},
                upsert=True
            )
            return True
    except Exception as e:
        logger.error(f"Error saving user data: {e}")
    
    return True

async def get_all_users():
    """Get all users who agreed to terms"""
    users = []
    
    try:
        if db_connected and users_collection is not None:
            async for user in users_collection.find({"agreed_to_terms": True}):
                user.pop('_id', None)
                users.append(user)
            if users:
                return users
    except Exception as e:
        logger.error(f"Error getting all users: {e}")
    
    # Fallback to cache
    for user_data in USER_DATA_CACHE.values():
        if user_data.get("agreed_to_terms", False):
            users.append(user_data)
    
    return users

async def update_user_activity(user_id: int, activity_type: str):
    """Update user's last activity"""
    try:
        timestamp = datetime.now(pytz.UTC).isoformat()
        
        # Update cache
        if user_id in USER_DATA_CACHE:
            USER_DATA_CACHE[user_id]["last_activity"] = timestamp
            USER_DATA_CACHE[user_id]["last_activity_type"] = activity_type
            if activity_type == "search":
                USER_DATA_CACHE[user_id]["total_searches"] = USER_DATA_CACHE[user_id].get("total_searches", 0) + 1
        
        # Update MongoDB
        if db_connected and users_collection is not None:
            update_data = {
                "$set": {
                    "last_activity": timestamp,
                    "last_activity_type": activity_type
                }
            }
            if activity_type == "search":
                update_data["$inc"] = {"total_searches": 1}
            
            await users_collection.update_one(
                {"user_id": user_id},
                update_data
            )
    except Exception as e:
        logger.error(f"Error updating user activity: {e}")

async def check_rate_limit(user_id: int) -> bool:
    """Check if user is rate limited"""
    current_time = asyncio.get_event_loop().time()
    
    if user_id in user_last_request:
        time_diff = current_time - user_last_request[user_id]
        if time_diff < REQUEST_COOLDOWN:
            return False
    
    user_last_request[user_id] = current_time
    return True

async def check_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is member of required channel"""
    try:
        user_id = update.effective_user.id
        chat_member = await context.bot.get_chat_member(
            chat_id=f"@{CHANNEL_USERNAME}",
            user_id=user_id
        )
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.warning(f"Channel check failed: {e}")
        return False

async def check_group_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is member of the private group"""
    try:
        chat_member = await context.bot.get_chat_member(
            chat_id=GROUP_CHAT_ID,
            user_id=user_id
        )
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.warning(f"Group check failed for user {user_id}: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command"""
    user = update.effective_user
    user_id = user.id
    chat_type = update.effective_chat.type
    
    # Only respond to /start in private chat
    if chat_type != 'private':
        return
    
    # Send typing action
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, 
        action=ChatAction.TYPING
    )
    
    # Get user data
    user_data = await get_user_data(user_id)
    
    # New user or hasn't agreed to terms
    if not user_data or not user_data.get("agreed_to_terms", False):
        # Create new user record
        new_user_data = {
            "user_id": user_id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "joined_date": datetime.now(pytz.UTC).isoformat(),
            "agreed_to_terms": False,
            "channel_joined": False,
            "total_searches": 0,
            "is_banned": False
        }
        
        await save_user_data(new_user_data)
        
        # Show disclaimer
        keyboard = [
            [
                InlineKeyboardButton("✅ I Agree", callback_data="agree_terms"),
                InlineKeyboardButton("❌ I Don't Agree", callback_data="disagree_terms")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            DISCLAIMER_TEXT,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        return
    
    # Check if banned
    if user_data.get("is_banned", False):
        await update.message.reply_text(
            "❌ <b>Access Denied</b>\n\n"
            "Your account has been banned from using this bot.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Check channel membership
    is_member = await check_channel_membership(update, context)
    
    if not is_member:
        keyboard = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ <b>Access Denied</b>\n\n"
            "To use this bot, join our channel first.",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        return
    
    # Update channel status
    user_data["channel_joined"] = True
    await save_user_data(user_data)
    
    # Check if user is in the group
    is_in_group = await check_group_membership(user_id, context)
    
    # Show welcome message with group info
    welcome_message = (
        "🔍 <b>Search Bot</b>\n\n"
        "🔎 <i>Explore public data for research & awareness.</i>\n"
        "❌ <b>No illegal use.</b>\n"
        "👨‍💻\n\n"
        "⚡ <b>Powered by:</b> meowmeow ⚡\n"
        "🌐 Stay Safe • Respect Privacy • Use Responsibly 🚀\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "📌 <b>⚡ Available Commands ⚡</b> 📌\n\n"
    )
    
    if is_in_group:
        welcome_message += (
            "📱 /num — 🔎 Find details from a 10-digit mobile number\n"
            "⚠️ <b><u>NOTE: /num command ONLY works in our private group!</u></b>\n"
            "━━━━━━━━━━━━━━━━"
        )
    else:
        welcome_message += (
            "⚠️ <b>Important Notice:</b>\n"
            "The /num command is <b><u>ONLY available in our private group</u></b>.\n\n"
            "👇 <b>Join our group to use /num command:</b>"
        )
        
        keyboard = [[InlineKeyboardButton("🔓 Join Private Group", url=GROUP_LINK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        return
    
    # Add admin commands if admin
    if user_id in ADMIN_IDS:
        welcome_message += (
            "\n\n<b>🔐 Admin Commands:</b>\n"
            "📊 /stats — View bot statistics\n"
            "📢 /broadcast <code>[message]</code> — Send to all users\n"
            "🚫 /ban <code>[user_id]</code> — Ban user\n"
            "✅ /unban <code>[user_id]</code> — Unban user"
        )
    
    await update.message.reply_text(
        welcome_message,
        parse_mode=ParseMode.HTML
    )

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from inline buttons"""
    query = update.callback_query
    user_id = query.from_user.id
    
    await query.answer()
    
    if query.data == "agree_terms":
        # Get or create user data
        user_data = await get_user_data(user_id) or {"user_id": user_id}
        user_data.update({
            "agreed_to_terms": True,
            "terms_agreed_date": datetime.now(pytz.UTC).isoformat(),
            "username": query.from_user.username,
            "first_name": query.from_user.first_name
        })
        await save_user_data(user_data)
        
        # Check channel membership
        is_member = await check_channel_membership(update, context)
        
        if not is_member:
            keyboard = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "✅ <b>Terms Accepted!</b>\n\n"
                "Now, please join our channel to continue:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            # Check if user is in the group
            is_in_group = await check_group_membership(user_id, context)
            
            message_text = (
                "✅ <b>Terms Accepted!</b>\n\n"
                "⚡ <b>Powered by:</b> meowmeow ⚡\n"
                "🌐 Stay Safe • Respect Privacy • Use Responsibly 🚀\n\n"
                "━━━━━━━━━━━━━━━━\n"
            )
            
            if is_in_group:
                message_text += (
                    "📌 <b>⚡ Available Commands ⚡</b> 📌\n\n"
                    "📱 /num — 🔎 Find details from a 10-digit mobile number\n"
                    "⚠️ <b><u>NOTE: /num command ONLY works in our private group!</u></b>\n"
                    "━━━━━━━━━━━━━━━━"
                )
                await query.edit_message_text(
                    message_text,
                    parse_mode=ParseMode.HTML
                )
            else:
                message_text += (
                    "⚠️ <b>Important Notice:</b>\n"
                    "The /num command is <b><u>ONLY available in our private group</u></b>.\n\n"
                    "👇 <b>Join our group to use /num command:</b>"
                )
                keyboard = [[InlineKeyboardButton("🔓 Join Private Group", url=GROUP_LINK)]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    message_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
    
    elif query.data == "disagree_terms":
        await query.edit_message_text(
            "❌ <b>Terms Not Accepted</b>\n\n"
            "You must agree to the terms of use to access this bot.\n"
            "Use /start to review the terms again.",
            parse_mode=ParseMode.HTML
        )

def clean_json_response(text: str) -> str:
    """Clean and extract valid JSON from response"""
    try:
        # Try to find JSON array
        json_match = re.search(r'\[[\s\S]*?\]', text)
        if json_match:
            return json_match.group()
        
        # Try to find JSON object
        json_match = re.search(r'\{[\s\S]*?\}', text)
        if json_match:
            return "[" + json_match.group() + "]"
        
        return text
    except Exception:
        return text

async def fetch_number_data(phone_number: str) -> dict:
    """Fetch data from API"""
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = f"{API_BASE_URL}/{phone_number}?paid=true"
            
            async with session.get(url) as response:
                if response.status == 200:
                    text_response = await response.text()
                    cleaned_response = clean_json_response(text_response)
                    
                    try:
                        data = json.loads(cleaned_response)
                        
                        if not isinstance(data, list):
                            data = [data]
                        
                        return {"success": True, "data": data}
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}")
                        
                        # Try to extract data with regex
                        patterns = {
                            "mobile": r'"mobile"\s*:\s*"([^"]*)"',
                            "name": r'"name"\s*:\s*"([^"]*)"',
                            "father_name": r'"father_name"\s*:\s*"([^"]*)"',
                            "address": r'"address"\s*:\s*"([^"]*)"',
                            "alt_mobile": r'"alt_mobile"\s*:\s*"([^"]*)"',
                            "circle": r'"circle"\s*:\s*"([^"]*)"',
                            "id_number": r'"id_number"\s*:\s*"([^"]*)"',
                            "email": r'"email"\s*:\s*"([^"]*)"'
                        }
                        
                        extracted_data = {}
                        for key, pattern in patterns.items():
                            match = re.search(pattern, text_response)
                            if match:
                                extracted_data[key] = match.group(1)
                        
                        if extracted_data:
                            return {"success": True, "data": [extracted_data]}
                        
                        return {"success": False, "error": "Invalid response format"}
                else:
                    return {"success": False, "error": f"API error: Status {response.status}"}
                    
    except asyncio.TimeoutError:
        return {"success": False, "error": "Request timeout - Please try again"}
    except Exception as e:
        logger.error(f"API request error: {e}")
        return {"success": False, "error": "Connection error"}

async def num_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /num command to search phone numbers"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    
    # Check if command is in private chat
    if chat_type == 'private':
        # In private chat, inform user to use command in group
        keyboard = [[InlineKeyboardButton("🔓 Join Private Group", url=GROUP_LINK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "⚠️ <b>Command Not Available Here!</b>\n\n"
            "The /num command is <b><u>ONLY available in our private group</u></b>.\n\n"
            "This restriction is for security and privacy reasons.\n\n"
            "👇 <b>Please join our group to use /num command:</b>",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        return
    
    # Check if command is in the authorized group
    if chat_id != GROUP_CHAT_ID:
        await update.message.reply_text(
            "⚠️ <b>Unauthorized Group!</b>\n\n"
            "This command only works in the official group.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Now we're in the correct group, proceed with checks
    
    # Check user data
    user_data = await get_user_data(user_id)
    
    if not user_data or not user_data.get("agreed_to_terms", False):
        await update.message.reply_text(
            "❌ <b>Access Denied</b>\n\n"
            "Please start the bot in private chat first: @YourBotUsername\n"
            "Accept the terms of use to continue.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Check if banned
    if user_data.get("is_banned", False):
        await update.message.reply_text(
            "❌ <b>Access Denied</b>\n\n"
            "Your account has been banned from using this bot.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Check rate limit
    if not await check_rate_limit(user_id):
        await update.message.reply_text(
            "⏱️ <b>Please wait</b>\n\n"
            f"You can only make one request every {REQUEST_COOLDOWN} seconds.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Check channel membership
    is_member = await check_channel_membership(update, context)
    
    if not is_member:
        keyboard = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ To use this bot, join our channel first.",
            reply_markup=reply_markup
        )
        return
    
    # Get phone number
    if not context.args:
        await update.message.reply_text(
            "❌ <b>Invalid Format</b>\n\n"
            "Please provide a 10-digit mobile number.\n"
            "Example: <code>/num 9876543210</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    phone_number = re.sub(r'\D', '', context.args[0].strip())
    
    # Validate phone number
    if len(phone_number) != 10:
        await update.message.reply_text(
            "❌ <b>Invalid Number</b>\n\n"
            "Please provide a valid 10-digit mobile number.\n"
            "Example: <code>/num 9876543210</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Send searching animation
    searching_msg = await update.message.reply_text(
        "🔍 <b>Searching...</b>",
        parse_mode=ParseMode.HTML
    )
    
    # Animate search
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )
    
    # Animation frames
    for i in range(4):
        dots = "." * (i % 4)
        await searching_msg.edit_text(
            f"🔍 <b>Searching{dots}</b>",
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(0.3)
    
    await searching_msg.edit_text(
        "📡 <b>Fetching data...</b>",
        parse_mode=ParseMode.HTML
    )
    
    # Fetch data
    api_result = await fetch_number_data(phone_number)
    
    if api_result["success"] and api_result["data"]:
        # Process result
        result = api_result["data"][0]
        
        # Remove API owner fields
        fields_to_remove = ["Api_owner", "api_owner", "API_owner"]
        for field in fields_to_remove:
            result.pop(field, None)
        
        # Format result
        formatted_result = json.dumps(result, indent=2, ensure_ascii=False)
        
        # Delete searching message
        await searching_msg.delete()
        
        # Send result
        result_message = (
            f"✅ <b>Search Results for:</b> <code>{phone_number}</code>\n\n"
            f"<pre>{formatted_result}</pre>"
        )
        
        await update.message.reply_text(
            result_message,
            parse_mode=ParseMode.HTML
        )
        
        # Update activity
        await update_user_activity(user_id, "search")
        
    else:
        error_msg = api_result.get("error", "No data found")
        await searching_msg.edit_text(
            f"❌ <b>Search Failed</b>\n\n"
            f"Number: <code>{phone_number}</code>\n"
            f"Reason: {error_msg}",
            parse_mode=ParseMode.HTML
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot statistics (admin only)"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "❌ This command is for admins only.",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        # MongoDB stats
        db_status = "✅ Connected" if db_connected else "❌ Offline"
        
        total_users = 0
        agreed_users = 0
        channel_joined = 0
        banned_users = 0
        total_searches = 0
        
        if db_connected and users_collection is not None:
            try:
                total_users = await users_collection.count_documents({})
                agreed_users = await users_collection.count_documents({"agreed_to_terms": True})
                channel_joined = await users_collection.count_documents({"channel_joined": True})
                banned_users = await users_collection.count_documents({"is_banned": True})
                
                # Get total searches
                pipeline = [
                    {"$group": {"_id": None, "total": {"$sum": "$total_searches"}}}
                ]
                search_result = await users_collection.aggregate(pipeline).to_list(1)
                total_searches = search_result[0]["total"] if search_result else 0
            except Exception as e:
                logger.error(f"Error getting MongoDB stats: {e}")
                # Fallback to cache
                total_users = len(USER_DATA_CACHE)
                agreed_users = sum(1 for u in USER_DATA_CACHE.values() if u.get("agreed_to_terms", False))
                channel_joined = sum(1 for u in USER_DATA_CACHE.values() if u.get("channel_joined", False))
                banned_users = sum(1 for u in USER_DATA_CACHE.values() if u.get("is_banned", False))
                total_searches = sum(u.get("total_searches", 0) for u in USER_DATA_CACHE.values())
        else:
            # Cache stats only
            total_users = len(USER_DATA_CACHE)
            agreed_users = sum(1 for u in USER_DATA_CACHE.values() if u.get("agreed_to_terms", False))
            channel_joined = sum(1 for u in USER_DATA_CACHE.values() if u.get("channel_joined", False))
            banned_users = sum(1 for u in USER_DATA_CACHE.values() if u.get("is_banned", False))
            total_searches = sum(u.get("total_searches", 0) for u in USER_DATA_CACHE.values())
        
        stats_message = (
            "📊 <b>Bot Statistics</b>\n\n"
            f"🗄️ Database: {db_status}\n"
            f"📡 API: ✅ Online\n\n"
            f"👥 Total Users: <code>{total_users}</code>\n"
            f"✅ Agreed to Terms: <code>{agreed_users}</code>\n"
            f"📢 Channel Joined: <code>{channel_joined}</code>\n"
            f"🚫 Banned Users: <code>{banned_users}</code>\n"
            f"🔍 Total Searches: <code>{total_searches}</code>\n\n"
            f"💾 Cache Size: <code>{len(USER_DATA_CACHE)}</code> users"
        )
        
        await update.message.reply_text(
            stats_message,
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Error in stats_command: {e}")
        await update.message.reply_text(
            f"❌ Error fetching statistics: {str(e)}",
            parse_mode=ParseMode.HTML
        )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast message to all users (admin only)"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "❌ This command is for admins only.",
            parse_mode=ParseMode.HTML
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ <b>Invalid Format</b>\n\n"
            "Usage: <code>/broadcast [message]</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    broadcast_message = " ".join(context.args)
    
    # Send status
    status_msg = await update.message.reply_text(
        "📢 <b>Broadcasting message...</b>",
        parse_mode=ParseMode.HTML
    )
    
    try:
        users = await get_all_users()
        success_count = 0
        failed_count = 0
        
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=f"📢 <b>Announcement</b>\n\n{broadcast_message}",
                    parse_mode=ParseMode.HTML
                )
                success_count += 1
                await asyncio.sleep(0.05)  # Avoid rate limits
            except Exception as e:
                logger.error(f"Failed to send to {user['user_id']}: {e}")
                failed_count += 1
        
        await status_msg.edit_text(
            f"✅ <b>Broadcast Complete</b>\n\n"
            f"Success: <code>{success_count}</code>\n"
            f"Failed: <code>{failed_count}</code>",
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Error in broadcast_command: {e}")
        await status_msg.edit_text(
            "❌ Error during broadcast.",
            parse_mode=ParseMode.HTML
        )

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ban a user (admin only)"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ <b>Invalid Format</b>\n\n"
            "Usage: <code>/ban [user_id]</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Update cache
        if target_user_id in USER_DATA_CACHE:
            USER_DATA_CACHE[target_user_id]["is_banned"] = True
            USER_DATA_CACHE[target_user_id]["banned_date"] = datetime.now(pytz.UTC).isoformat()
        
        # Update MongoDB
        if db_connected and users_collection is not None:
            await users_collection.update_one(
                {"user_id": target_user_id},
                {"$set": {
                    "is_banned": True,
                    "banned_date": datetime.now(pytz.UTC).isoformat()
                }}
            )
        
        await update.message.reply_text(
            f"✅ User <code>{target_user_id}</code> has been banned.",
            parse_mode=ParseMode.HTML
        )
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid user ID. Please provide a numeric ID.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in ban_command: {e}")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unban a user (admin only)"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ <b>Invalid Format</b>\n\n"
            "Usage: <code>/unban [user_id]</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Update cache
        if target_user_id in USER_DATA_CACHE:
            USER_DATA_CACHE[target_user_id]["is_banned"] = False
            USER_DATA_CACHE[target_user_id].pop("banned_date", None)
        
        # Update MongoDB
        if db_connected and users_collection is not None:
            await users_collection.update_one(
                {"user_id": target_user_id},
                {
                    "$set": {"is_banned": False},
                    "$unset": {"banned_date": ""}
                }
            )
        
        await update.message.reply_text(
            f"✅ User <code>{target_user_id}</code> has been unbanned.",
            parse_mode=ParseMode.HTML
        )
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid user ID. Please provide a numeric ID.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in unban_command: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular text messages"""
    chat_type = update.effective_chat.type
    text = update.message.text.strip()
    
    # Extract numbers
    clean_text = re.sub(r'\D', '', text)
    
    # Check if it's a 10-digit number
    if clean_text and len(clean_text) == 10:
        # If in private chat, inform about group requirement
        if chat_type == 'private':
            keyboard = [[InlineKeyboardButton("🔓 Join Private Group", url=GROUP_LINK)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "⚠️ <b>Number Search Not Available Here!</b>\n\n"
                "Number searches are <b><u>ONLY available in our private group</u></b>.\n\n"
                "👇 <b>Please join our group to search numbers:</b>",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            # In group, process as /num command
            context.args = [clean_text]
            await num_command(update, context)
    else:
        # Only respond with help in private chat
        if chat_type == 'private':
            await update.message.reply_text(
                "❓ <b>Need help?</b>\n\n"
                "Use /start to see available commands.\n\n"
                "Note: Number searches only work in our private group!",
                parse_mode=ParseMode.HTML
            )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors"""
    logger.error(f"Exception while handling an update: {context.error}")
    
    try:
        if update and hasattr(update, 'effective_message'):
            await update.effective_message.reply_text(
                "❌ An unexpected error occurred. Please try again.",
                parse_mode=ParseMode.HTML
            )
    except:
        pass

async def post_init(application: Application) -> None:
    """Initialize application after start"""
    await init_mongodb()

async def post_shutdown(application: Application) -> None:
    """Cleanup on shutdown"""
    global mongo_client
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB connection closed")

def main() -> None:
    """Start the bot"""
    print("=" * 50)
    print("🤖 OSINT Search Bot Starting...")
    print("=" * 50)
    print(f"📱 Bot Token: {BOT_TOKEN[:15]}...")
    print(f"📢 Channel: @{CHANNEL_USERNAME}")
    print(f"👥 Group ID: {GROUP_CHAT_ID}")
    print(f"👮 Admin IDs: {ADMIN_IDS}")
    print("=" * 50)
    
    # Create application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("num", num_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start polling
    print("✅ Bot is running! Press Ctrl+C to stop.")
    print("=" * 50)
    
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹️ Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
