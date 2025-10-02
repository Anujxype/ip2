#!/usr/bin/env python
# -*- coding: utf-8 -*-

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
import html
from aiohttp import web

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration - Use environment variables for sensitive data
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8415869688:AAHSiFfKuAo4_75e_835hgebl2iKku3RJKg")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "osXspace")
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "https://t.me/osXspace")

# API configuration
API_BASE_URL = os.environ.get("API_BASE_URL", "https://osintx.info/API/krobetahack.php?key=SHAD0WINT3L&type=mobile&term=")
RASHAN_API = os.environ.get("RASHAN_API", "https://family-members-n5um.vercel.app/fetch?aadhaar={aadhaar}&key=paidchx")
UPI_API = os.environ.get("UPI_API", "https://upi-info.vercel.app/api/upi?upi_id={upi_id}&key=456")
ICMR_API = os.environ.get("ICMR_API", "https://raju09.serv00.net/ICMR/ICMR_api.php?phone={phone}")
VEHICLE_ADDRESS_API = os.environ.get("VEHICLE_ADDRESS_API", "https://caller.hackershub.shop/info.php?type=address&registration={registration}")
VEHICLE_CHALLAN_API = os.environ.get("VEHICLE_CHALLAN_API", "https://caller.hackershub.shop/info.php?type=challan&registration={registration}")

# Group configuration
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "-1002414357299"))
GROUP_LINK = os.environ.get("GROUP_LINK", "https://t.me/+bVDSE8QxqJE1M2Nl")

# Admin IDs
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "7167145056,6435989814").split(",")]

# MongoDB configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://osXspace:osXspace@cluster0.k3k6yzj.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = os.environ.get("DB_NAME", "osint_bot")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "users")

# Render web service port
PORT = int(os.environ.get("PORT", 10000))

# Global variables
mongo_client = None
users_collection = None
db_connected = False
user_last_request = {}
REQUEST_COOLDOWN = 5
USER_DATA_CACHE = {}
application = None
web_app = None
web_runner = None

# [Include all the previous bot functions here - same as before]
# ... (All the async functions from the previous code remain the same)

# Web server for Render health checks
async def health_check(request):
    """Health check endpoint for Render"""
    return web.Response(text="Bot is running!", status=200)

async def start_web_server():
    """Start web server for Render health checks"""
    global web_app, web_runner
    
    web_app = web.Application()
    web_app.router.add_get('/', health_check)
    web_app.router.add_get('/health', health_check)
    
    web_runner = web.AppRunner(web_app)
    await web_runner.setup()
    site = web.TCPSite(web_runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

async def stop_web_server():
    """Stop web server"""
    global web_runner
    if web_runner:
        await web_runner.cleanup()
        logger.info("Web server stopped")

async def post_init(application: Application) -> None:
    """Initialize resources on startup"""
    await init_mongodb()
    await start_web_server()
    logger.info("Bot initialization complete")

async def post_shutdown(application: Application) -> None:
    """Cleanup resources on shutdown"""
    global mongo_client
    
    await stop_web_server()
    
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB connection closed")

# [Include all the command handlers and other functions from previous code]
# ... (All handlers remain the same)

def main() -> None:
    """Start the bot"""
    global application
    
    print("=" * 50)
    print("🤖 Advanced OSINT Search Bot Starting...")
    print("=" * 50)
    print(f"📱 Bot Token: {BOT_TOKEN[:15]}...")
    print(f"📢 Channel: @{CHANNEL_USERNAME}")
    print(f"👥 Group ID: {GROUP_CHAT_ID}")
    print(f"👮 Admin IDs: {ADMIN_IDS}")
    print(f"🌐 Web Port: {PORT}")
    print("\n📡 Available APIs:")
    print("  ✅ Mobile Number Search")
    print("  ✅ Rashan Card Database")
    print("  ✅ UPI Information")
    print("  ✅ ICMR Database")
    print("  ✅ Vehicle Information")
    print("  ✅ Vehicle Challan")
    print("=" * 50)
    
    try:
        # Create application with proper configuration
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .post_init(post_init)
            .post_shutdown(post_shutdown)
            .build()
        )
        
        # Add command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("num", num_command))
        application.add_handler(CommandHandler("aadhaar", aadhaar_command))
        application.add_handler(CommandHandler("upi", upi_command))
        application.add_handler(CommandHandler("icmr", icmr_command))
        application.add_handler(CommandHandler("vehicle", vehicle_command))
        application.add_handler(CommandHandler("challan", challan_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("broadcast", broadcast_command))
        application.add_handler(CommandHandler("ban", ban_command))
        application.add_handler(CommandHandler("unban", unban_command))
        
        # Add callback and message handlers
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
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        print(f"❌ Failed to start bot: {e}")
        sys.exit(1)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹️ Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"❌ Fatal error: {e}")
        sys.exit(1)
