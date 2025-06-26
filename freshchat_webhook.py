# nasher_miles_app.py
#
# Modified version with Freshchat-WhatsApp integration added at the bottom.
# All existing functionality (WhatsApp, Selenium, AI queues, etc.)
# remains unchanged. We have appended new code to handle Freshchat webhooks,
# signature verification, message parsing, logging, filtering, and sending replies.
#
# Ensure your .env file contains:
#   FRESHCHAT_PUBLIC_KEY  = your Freshchat RSA public key (exact PEM block)
#   FRESHCHAT_API_KEY     = your Freshchat Conversations API key (Bearer token)
#   FRESHCHAT_BASE_URL    = e.g. https://<your_subdomain>.freshchat.com/v2
#   (and all your existing env vars, e.g. for Redis, MongoDB, WhatsApp, etc.)
#
# Run with:
#   uvicorn nasher_miles_app:app --reload --port 8000
# Then point Freshchat's Webhook URL to:  http://<ngrok_tunnel>/webhook/freshchat
# and paste the exact RSA public key into Freshchat's "Authentication" box.

import logging
import traceback
import asyncio
from datetime import datetime, time
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from collections import defaultdict
import requests
from pyngrok import ngrok
from contextlib import asynccontextmanager
from AI_Agent import AsyncAIBot
import uvicorn
from dotenv import load_dotenv
import os
import sys
import uuid
import re
import json

# Import for Redis & MongoDB
from redis.asyncio import Redis
from mongodb_handler import get_collection

# Import for HTTP calls
import httpx

# Import for signature verification (for Freshchat)
import base64
import rsa
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature

# Import for rate-limiting or AI logic
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

# Import for logging to OpenObserve (error_logger)
from error_logger import log_to_openobserve

# Import WhatsApp API functions
from whatsapp_api import (
    sync_local_from_cloud,
    backup_unique_users_to_cloud,
    send_whatsapp_message,
    send_whatsapp_image,
    send_offer_quick_replies,
    add_user_to_set
)

# ------------------------------------------------------------------------------
# 1) GLOBALS AND ENV LOADING
# ------------------------------------------------------------------------------

# Create logs directory & configure logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/app.log"),
        logging.StreamHandler()
    ],
    force=True
)
logging.getLogger("pyngrok").setLevel(logging.ERROR)

# Load .env
load_dotenv()

# Redis setup (for caching/queues)
redis = Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", 6379)), decode_responses=True)

# MongoDB setup
# We assume environment variables for MongoDB URI/credentials are already set in .env
# For example: MONGODB_URI, etc.
daily_user_count_collection = get_collection("whatsapp_daily_user_count")
unique_users_collection    = get_collection("whatsapp_unique_users")

# Track global ngrok URL
public_ngrok_url = None

# Flag to know if services are active
services_active = False

# In-memory data structures for queue processing
user_queues        = defaultdict(asyncio.Queue)  # Stores a queue of (user_id, message_id) for each user
active_tasks       = {}                          # Maps user_id -> asyncio Task
user_onboarding    = {}                          # Maps user_id -> bool (True if onboarding in progress)
user_ai_status     = set()                       # Tracks users who have enabled AI

# ------------------------------------------------------------------------------
# 2) FASTAPI APP + LIFESPAN (Start ngrok, Ping Redis)
# ------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    On startup:
      • Start ngrok tunnel on port 8000
      • Ping Redis to ensure it's up
      • Sync unique users from cloud
    On shutdown:
      • Backup unique users
      • Kill ngrok
    """
    global public_ngrok_url

    # Start ngrok tunnel
    http_tunnel = ngrok.connect(8000, proto="http", hostname="beagle-rich-closely.ngrok-free.app")
    public_ngrok_url = http_tunnel.public_url
    logging.info(f"ngrok tunnel opened at {public_ngrok_url}")

    # Check Redis connectivity
    try:
        response = await redis.ping()
        if response:
            logging.info("✅ Redis server is alive!")
        else:
            logging.warning("⚠️ Redis server did not respond!")
    except Exception as e:
        logging.error(f"❌ Error pinging Redis: {e}")

    # Sync unique WhatsApp users from MongoDB
    try:
        sync_local_from_cloud()
        logging.info("✅ Synced unique WhatsApp users from cloud to local JSON.")
    except Exception as e:
        logging.error(f"❌ Error syncing unique users: {e}", exc_info=True)

    yield

    # On shutdown:
    logging.info("Shutting down server and ngrok tunnel...")
    try:
        backup_unique_users_to_cloud()
        logging.info("✅ Backed up unique users to cloud.")
    except Exception as e:
        logging.error(f"❌ Error backing up unique users: {e}", exc_info=True)

    ngrok.kill()
    logging.info("ngrok tunnel closed.")
    # If desired, close Redis (though async client can be closed automatically)
    await redis.close()

app = FastAPI(lifespan=lifespan)

# ------------------------------------------------------------------------------
# 3) HEALTH CHECK ENDPOINT
# ------------------------------------------------------------------------------

@app.get("/healthz")
async def health_check():
    """
    Health check to verify the server and ngrok are running.
    """
    return {"status": "ok", "ngrok_url": public_ngrok_url}

# ------------------------------------------------------------------------------
# 4) AI BOT INITIALIZATION
# ------------------------------------------------------------------------------

# Instantiate your AI bot once
# AsyncAIBot is assumed to have a method: async def run_agent(user_id, user_text, session_name) -> str
ai_bot = AsyncAIBot()

# ------------------------------------------------------------------------------
# 5) BACKGROUND PROCESSING: Process User Queues (Debounce + Ordered Replies)
# ------------------------------------------------------------------------------

async def process_user_queue(sender_id, message_id=None):
    """
    Processes messages for a single user in batches after a debounce period.
    Ensures ordered replies and cancels if user is idle for too long.
    Also mirrors messages to Freshchat.
    """
    try:
        while True:
            try:
                # Wait for first message or timeout
                first_message = await asyncio.wait_for(user_queues[sender_id].get(), timeout=150)
            except asyncio.TimeoutError:
                logging.info(f"User {sender_id} idle for too long, ending processing task.")
                break

            user_queues[sender_id].task_done()
            messages = [first_message]

            # Debounce: gather any new messages for 1 second
            while True:
                try:
                    msg = await asyncio.wait_for(user_queues[sender_id].get(), timeout=1.0)
                    user_queues[sender_id].task_done()
                    messages.append(msg)
                except asyncio.TimeoutError:
                    break

            # Now process the batch of messages in order
            for (mid, text, attachments) in messages:
                # Step 1: Log the incoming WhatsApp message
                logging.info(f"[WhatsApp] Processing user={sender_id}, message_id={mid}, text={text!r}, attachments={attachments}")

                # Step 2: Handle attachments if any
                if attachments:
                    for att in attachments:
                        logging.info(f"[WhatsApp] Attachment URL/filename for user {sender_id}: {att}")

                # Step 3: Call AI to generate a reply
                try:
                    if sender_id in user_ai_status:
                        # AI enabled for this user
                        ai_response = await ai_bot.run_agent(sender_id, text, session_name="whatsapp-session")
                    else:
                        # AI disabled: send info or skip
                        ai_response = "AI is currently disabled for you. Send 'AI ON' to enable."
                except Exception as e:
                    logging.error(f"[WhatsApp] AI_Agent error for user {sender_id}: {e}", exc_info=True)
                    ai_response = "Sorry, something went wrong. Please try again later."

                # Step 4: Send the reply via WhatsApp API
                try:
                    await send_whatsapp_message(sender_id, ai_response)
                    
                    # Step 5: Mirror the conversation to Freshchat
                    try:
                        # Get or create Freshchat conversation ID for this user
                        conversation_id = await get_or_create_freshchat_conversation(sender_id)
                        if conversation_id:
                            # Send both the user's message and the bot's response to Freshchat
                            await send_freshchat_reply(conversation_id, "user", text)  # User's message
                            await send_freshchat_reply(conversation_id, "bot", ai_response)  # Bot's response
                    except Exception as e:
                        logging.error(f"Failed to mirror conversation to Freshchat: {e}", exc_info=True)
                        
                except Exception as e:
                    logging.error(f"[WhatsApp] Failed to send reply to user {sender_id}: {e}", exc_info=True)

            # If we finish processing all messages, loop again to wait for new ones
    except asyncio.CancelledError:
        logging.info(f"Processing task for user {sender_id} was cancelled.")
    except Exception as e:
        logging.error(f"Error in process_user_queue for user {sender_id}: {e}", exc_info=True)
    finally:
        # Clean up
        if sender_id in active_tasks:
            active_tasks.pop(sender_id, None)
        logging.info(f"Cleaned up processing task for user {sender_id}.")

# ------------------------------------------------------------------------------
# 6) FRESHCHAT WHATSAPP INTEGRATION CODE
# ------------------------------------------------------------------------------

# 6.1) Load Freshchat environment variables & verify we have them
FRESHCHAT_PUBLIC_KEY_PEM = os.getenv("FRESHCHAT_PUBLIC_KEY", "").strip()
FRESHCHAT_API_KEY       = os.getenv("FRESHCHAT_API_KEY", "").strip()
FRESHCHAT_BASE_URL      = os.getenv("FRESHCHAT_BASE_URL", "").rstrip("/")

if not FRESHCHAT_PUBLIC_KEY_PEM:
    raise RuntimeError("Set FRESHCHAT_PUBLIC_KEY in your .env (RSA public key).")

if not FRESHCHAT_API_KEY:
    raise RuntimeError("Set FRESHCHAT_API_KEY in your .env (Conversations API key).")

if not FRESHCHAT_BASE_URL:
    raise RuntimeError("Set FRESHCHAT_BASE_URL in your .env (e.g. https://<subdomain>.freshchat.com/v2).")

# 6.2) Signature verification for Freshchat
def _load_rsa_public_key(pem_str: str) -> rsa.PublicKey:
    """
    Load RSA public key from various formats using rsa library.
    Handles:
    1. PEM format (with or without headers)
    2. Base64 encoded key
    3. Raw key bytes
    """
    pem_str = pem_str.strip()
    
    # Helper function to try loading key
    def try_load_key(key_data: bytes) -> rsa.PublicKey:
        try:
            return rsa.PublicKey.load_pkcs1(key_data)
        except:
            try:
                return rsa.PublicKey.load_pkcs1_openssl_pem(key_data)
            except:
                return None

    # Strategy 1: Try as PEM format
    if pem_str.startswith('-----BEGIN'):
        try:
            return rsa.PublicKey.load_pkcs1_openssl_pem(pem_str.encode('utf-8'))
        except Exception as e:
            logging.warning(f"Failed to load as PEM: {e}")
    else:
        # Strategy 2: Try as base64 encoded key
        try:
            # Try with PKCS#1 header
            pkcs1_key = f"-----BEGIN RSA PUBLIC KEY-----\n{pem_str}\n-----END RSA PUBLIC KEY-----"
            key = try_load_key(pkcs1_key.encode('utf-8'))
            if key:
                return key
                
            # Try with standard header
            standard_key = f"-----BEGIN PUBLIC KEY-----\n{pem_str}\n-----END PUBLIC KEY-----"
            key = try_load_key(standard_key.encode('utf-8'))
            if key:
                return key
                
            # Try raw base64 decode
            key_bytes = base64.b64decode(pem_str)
            key = try_load_key(key_bytes)
            if key:
                return key
                
        except Exception as e:
            logging.warning(f"Failed to load as base64: {e}")
    
    # If all attempts fail, raise error
    raise ValueError("Could not load RSA public key in any supported format")

def verify_freshchat_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verify X-Freshchat-Signature header using rsa library.
    """
    if not signature_header:
        logging.warning("Missing X-Freshchat-Signature header.")
        return False
        
    try:
        # Decode the base64 signature
        signature = base64.b64decode(signature_header.encode("utf-8"))
        
        # Verify the signature using rsa library
        try:
            rsa.verify(raw_body, signature, RSA_PUBLIC_KEY)
            return True
        except rsa.VerificationError:
            logging.error("Invalid signature")
            return False
            
    except Exception as e:
        logging.error(f"Signature verification failed: {e}")
        return False

# Load the public key with detailed error handling
try:
    logging.info("Attempting to load RSA public key...")
    RSA_PUBLIC_KEY = _load_rsa_public_key(FRESHCHAT_PUBLIC_KEY_PEM)
    logging.info("Successfully loaded RSA public key")
except Exception as e:
    logging.error(f"Failed to load RSA public key: {e}")
    raise

# 6.3) Helper to send a reply into Freshchat
async def send_freshchat_reply(conversation_id: str, author_id: str, content: str):
    """
    Send a text message into a Freshchat conversation (WhatsApp channel).
      • conversation_id: Freshchat's conversation ID.
      • author_id: the Freshchat user/agent (Bot) ID.
      • content: the text to send.
    """
    send_url = f"{FRESHCHAT_BASE_URL}/conversations/{conversation_id}/messages"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {FRESHCHAT_API_KEY}"
    }
    payload = {
        "message_parts": [
            {"text": {"content": content}}
        ],
        "author_id": author_id
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(send_url, json=payload, headers=headers, timeout=10.0)
        try:
            resp.raise_for_status()
            logging.info(f"[Freshchat] Sent reply to convo {conversation_id}: {content!r}")
        except httpx.HTTPStatusError as e:
            logging.error(f"[Freshchat] Failed to send reply ({resp.status_code}): {resp.text}", exc_info=True)
            raise

# 6.4) WhatsApp Webhook Handler
@app.post("/webhook/whatsapp")
async def handle_whatsapp_webhook(request: Request):
    """
    Handle incoming WhatsApp messages from Meta's API and mirror them to Freshchat.
    """

    # Parse the incoming webhook payload
    payload = await request.json()
    print(payload)
        # Extract the message details
    """   entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [{}])[0]
        
        # Get the message details
        phone_number = messages.get("from")
        message_text = messages.get("text", {}).get("body", "")
        message_id = messages.get("id")
        
        if not phone_number or not message_text:
            return JSONResponse({"status": "ignored", "reason": "missing required fields"}, status_code=200)
            
        # Log the incoming WhatsApp message
        logging.info(f"[WhatsApp] Received message from {phone_number}: {message_text!r}")
        
        # Add user to tracking
        add_user_to_set(phone_number)
        
        # Add message to user's queue for AI processing
        try:
            await user_queues[phone_number].put((message_id, message_text, []))
            
            if phone_number not in active_tasks and not user_onboarding.get(phone_number, False):
                active_tasks[phone_number] = asyncio.create_task(process_user_queue(phone_number, message_id))
                logging.info(f"Started message processing for user {phone_number}")
            else:
                if user_onboarding.get(phone_number, False):
                    logging.info(f"Message queued for user {phone_number} - onboarding in progress")
                else:
                    logging.info(f"Message queued for user {phone_number} - processing already active")
        except Exception as e:
            logging.error(f"Error handling message for user {phone_number}: {e}", exc_info=True)
            return JSONResponse({"status": "error_processing_message"}, status_code=200)   """
            
    return JSONResponse({"status": "ok"}, status_code=200)
        
# 6.5) Helper to get or create Freshchat conversation
async def get_or_create_freshchat_conversation(phone_number: str) -> str:
    """
    Get existing Freshchat conversation ID for a phone number or create a new one.
    Returns the conversation ID or None if failed.
    """
    try:
        # First try to find existing conversation
        search_url = f"{FRESHCHAT_BASE_URL}/conversations"
        headers = {
            "Authorization": f"Bearer {FRESHCHAT_API_KEY}",
            "Content-Type": "application/json"
        }
        params = {
            "filter": f"channel_type:whatsapp AND channel_id:{phone_number}"
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(search_url, headers=headers, params=params)
            resp.raise_for_status()
            conversations = resp.json().get("conversations", [])
            
            if conversations:
                return conversations[0].get("id")
                
            # If no conversation exists, create a new one
            create_url = f"{FRESHCHAT_BASE_URL}/conversations"
            payload = {
                "channel_type": "whatsapp",
                "channel_id": phone_number,
                "status": "new"
            }
            
            resp = await client.post(create_url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json().get("id")
            
    except Exception as e:
        logging.error(f"Failed to get/create Freshchat conversation: {e}", exc_info=True)
        return None

# ------------------------------------------------------------------------------
# 7) MAIN ENTRYPOINT
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Run with:  python freshchat_app.py
    or:        uvicorn freshchat_app:app --reload --port 8000
    """
    logging.info("Starting freshchat_app with Freshchat integration on port 8000...")
    uvicorn.run("freshchat_app:app", host="0.0.0.0", port=8000, reload=True)