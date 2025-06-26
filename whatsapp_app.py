import logging
import traceback
import asyncio
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from collections import defaultdict
import requests
from contextlib import asynccontextmanager
from AI_Agent import AsyncAIBot
import uvicorn
from dotenv import load_dotenv
import os
from mongodb_handler import get_context_collection, get_collection
from redis.asyncio import Redis
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage, SystemMessage
from error_logger import log_to_openobserve
import json
from pydantic import BaseModel
from typing import Optional, Dict, List
from pyngrok import ngrok
import sys
from whatsapp_api import *

# Load environment variables
load_dotenv()

# Configure logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/whatsapp_app.log"),
        logging.StreamHandler()
    ],
    force=True
)

# Suppress pyngrok logs
logging.getLogger("pyngrok").setLevel(logging.ERROR)
redis_host = os.getenv("REDIS_HOST", "localhost")
# Initialize Redis
redis = Redis(host=redis_host, port=6379, decode_responses=True)

# Global variables
public_ngrok_url = None
services_active = False
user_queues = defaultdict(asyncio.Queue)
active_tasks = {}
user_sequence = defaultdict(int)
user_next_seq = defaultdict(int)
user_response_buffer = defaultdict(dict)
user_onboarding = {}
user_ai_status = set()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI.
    Handles startup and shutdown of the application, including ngrok tunnel setup.
    """
    global public_ngrok_url, services_active
    try:
        # Start ngrok tunnel
        http_tunnel = ngrok.connect(5001, proto="http", hostname="keen-fowl-gently.ngrok-free.app")
        public_ngrok_url = http_tunnel.public_url
        logging.info(f"Ngrok tunnel URL: {public_ngrok_url}")
        
        # Test Redis connection
        try:
            response = await redis.ping()
            if response:
                print("✅ Redis server is alive!")
            else:
                print("⚠️ Redis server did not respond!")
        except Exception as e:
            print(f"❌ Error pinging Redis: {e}")
            sys.exit(1)
            
        print("✅ Connected to Redis and MongoDB")
        logging.info("Starting the FastAPI app with lifespan.")
        
        yield
        
        # Cleanup on shutdown
        logging.info("Shutting down server and ngrok tunnel...")
        backup_unique_users_to_cloud()
        logging.info("Lifespan shutdown: Backed up unique users.")
        ngrok.kill()
        logging.info("Lifespan shutdown: Cleaned up resources.")
        
    except Exception as e:
        await log_to_openobserve("whatsapp_app", traceback.format_exc(), level="critical")
        logging.error(f"Error in lifespan: {e}", exc_info=True)
        ngrok.kill()
        sys.exit(1)

# Initialize FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# Initialize the AI bot
bot = AsyncAIBot()

class ButtonState(BaseModel):
    enabled: bool

class LogEntry(BaseModel):
    timestamp: str
    ip_address: str
    action: str

class ServiceResponse(BaseModel):
    status: str
    message: Optional[str] = None

async def process_user_queue(phone_number: str, message_id: str = None):
    """
    Process messages for a single user in batches after a debounce period.
    """
    try:
        while True:
            try:
                first_message = await asyncio.wait_for(user_queues[phone_number].get(), timeout=150)
            except asyncio.TimeoutError:
                logging.info(f"User {phone_number} has been idle for too long, cancelling queue processing.")
                break

            user_queues[phone_number].task_done()
            messages = [first_message]

            try:
                while True:
                    message = await asyncio.wait_for(user_queues[phone_number].get(), timeout=3)
                    messages.append(message)
                    user_queues[phone_number].task_done()
            except asyncio.TimeoutError:
                pass

            bundled_message = " ".join(messages)
            logging.info(f"Processing batched messages for {phone_number}: {bundled_message}")

            user_sequence[phone_number] += 1
            current_seq = user_sequence[phone_number]

            asyncio.create_task(handle_batch(phone_number, bundled_message, current_seq, message_id))

    except asyncio.CancelledError:
        logging.info(f"Processing task for user {phone_number} was cancelled.")
    except Exception as e:
        logging.error(f"Error processing user queue for {phone_number}: {e}", exc_info=True)
    finally:
        active_tasks.pop(phone_number, None)
        logging.info(f"Cleaned up processing task for user {phone_number}.")

async def handle_batch(phone_number: str, bundled_message: str, seq_num: int, message_id: str = None):
    """
    Handle processing of a single batch and manage ordered response sending.
    """
    try:
        print(f"Sending batched message to bot for user {phone_number}: {bundled_message}")
        try:
            response_text = await bot.run_agent(str(phone_number), bundled_message, "Tara")
        except Exception as e:
            await log_to_openobserve("whatsapp_bot", traceback.format_exc(), level="critical")
            response_text = "I apologize, but I'm having trouble processing your request right now. Please try again later."

        user_response_buffer[phone_number][seq_num] = response_text
        await send_ordered_responses(phone_number, message_id)
    except Exception as e:
        logging.error(f"Error handling batch for user {phone_number}, seq {seq_num}: {e}", exc_info=True)
        user_response_buffer[phone_number][seq_num] = "Something went wrong on my end. Internet Connection Issue. Please try again later or contact support."
        await send_ordered_responses(phone_number)

async def send_ordered_responses(phone_number: str, message_id: str = None):
    """
    Send buffered responses in the correct order based on sequence numbers.
    """
    expected_seq = user_next_seq[phone_number] + 1

    while expected_seq in user_response_buffer[phone_number]:
        response = user_response_buffer[phone_number].pop(expected_seq)
        try:
            value = await redis.get(f"{phone_number}_discount")
            if value == "True":
                await send_offer_quick_replies(phone_number)
                await redis.delete(f"{phone_number}_discount")
            else:
                # response = await remove_asterisks(response)
                await send_whatsapp_message(phone_number, response)
            logging.info(f"Sent response to user {phone_number}, seq {expected_seq}.")
        except Exception as e:
            logging.error(f"Error sending response to user {phone_number}, seq {expected_seq}: {e}", exc_info=True)
            user_response_buffer[phone_number][expected_seq] = "Something went wrong on my end. It's not you, it's me. Please contact us at +919931080808 for any further details."
            await send_ordered_responses(phone_number)
        user_next_seq[phone_number] = expected_seq
        expected_seq += 1

async def handle_message(phone_number: str, message_text: str, message_id: str = None):
    """
    Handle incoming messages by adding them to a user's queue and managing processing tasks.
    """
    try:
        await user_queues[phone_number].put(message_text)
        
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

async def check_user_status(data: dict) -> bool:
    """
    Check if user exists in Redis cache or MongoDB database.
    """
    try:
        phone_number = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("contacts", [{}])[0].get("wa_id")
        if not phone_number:
            logging.warning("Webhook payload does not include a phone number.")
            return False

        cached = await redis.exists(phone_number)
        if cached:
            logging.info(f"User {phone_number} found in Redis cache.")
            if phone_number not in user_onboarding:
                user_onboarding[phone_number] = False
            return True

        collection = get_context_collection()
        existing_doc = await asyncio.to_thread(collection.find_one, {"_id": phone_number})

        if existing_doc:
            await redis.set(phone_number, 1, ex=60*60*24*30)  # 30 days expiry
            logging.info(f"User {phone_number} found in MongoDB and cached in Redis.")
            if phone_number not in user_onboarding:
                user_onboarding[phone_number] = False
            return True

        # New user, create in MongoDB
        history = []
        new_doc = {
            "_id": phone_number,
            "context_data": {
                "first_message": "",
                "name": data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("contacts", [{}])[0].get("profile", {}).get("name", ""),
                "Phone_Number": phone_number,
                "email": "",
                "channel": "whatsapp",
                "Last_Response": "",
                "last_suggestion": "",
                "conversation_history": history
            }
        }
        await asyncio.to_thread(collection.insert_one, new_doc)
        logging.info(f"Inserted new user in DB: {phone_number}")
        
        await redis.set(phone_number, 1, ex=60*60*24*30)
        return True

    except Exception as e:
        await log_to_openobserve("whatsapp_app", traceback.format_exc(), level="critical")
        logging.error("Error in check_user_status", exc_info=True)
        return False

async def process_webhook_payload(data: dict):
    """
    Process the webhook payload from WhatsApp.
    """
    try:
        entry = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        if not entry:
            return

        messages = entry.get("messages", [])
        if not messages:
            return

        for message in messages:
            phone_number = message.get("from")
            message_id = message.get("id")
            
            if not phone_number:
                continue

            message_type = message.get("type")
            if message_type == "text":
                message_text = message.get("text", {}).get("body", "").strip()
                if message_text:
                    logging.info(f"Received message from {phone_number}: {message_text}")
                    asyncio.create_task(handle_message(phone_number, message_text, message_id))
            elif message_type == "interactive":
                # Handle interactive responses (button clicks)
                interactive = message.get("interactive", {})
                if interactive.get("type") == "list_reply":
                    button_id = interactive.get("list_reply", {}).get("id")
                    message_text = interactive.get("list_reply", {}).get("title")
                    if button_id:
                        content = ""
                        if button_id == "NEWLOGINOFFER":
                            content = "New Login Offer\nLogin & Get Flat 200 Off\nDiscount Code: NMFAM200\nMinimum Cart Value Rs. 1,999/-\nPrepaid Discount of 5% (Upto 200/-) auto-applied at the checkout page."
                        elif button_id == "WELCOME5":
                            content = "We welcome first-time buyers with an additional 5% off! Use code `WELCOME-5` .\n\n*Not applicable to products already on sale. \n\nPlus, enjoy an additional *5% off (Capped at 200 Maximum) on prepaid orders (auto-applied).\n\nShop now and save more!\n\nGet it now, book that trip, and `#UnpackYou` . \n\nFor bulk enquiry, you can mail us at `customercare@nashermiles.com`."
                        elif button_id == "RISHABHPANT":
                            content = "Rishabh Pant\n7% Off on Rishabh Pant Collection \nDiscount Code: Welcome-5\nMinimum Cart Value Rs. 7,999/-\n(Maximum Discount Rs. 1,000/-)\n Prepaid Discount of 5% (Upto 200/-) auto-applied at the checkout page."
                        elif button_id == "STUDENTDISCOUNT":
                            content = "Student Discount\nGet 15% discount exclusively for students.\nSubmit your Admission Letter copy or Identity Card.\nOur team will issue a Special Discount code to you."
                        elif button_id == "NMSCAPIA":
                            content = "Bank Offer\n15% Off for Scapia Card Holders\nDiscount Code: NMSCAPIA\nMinimum Cart Value Rs. 2,499/-\nNote: Offer Valid till 30.06.2025"
                        
                        if content:
                            context_collection = get_context_collection()
                            existing_doc = await asyncio.to_thread(context_collection.find_one, {"_id": phone_number})
                            if existing_doc:
                                context_data = existing_doc.get("context_data", {})
                                conversation_history = context_data.get("conversation_history", [])
                                conversation_history.append({"role": "user", "content": message_text})
                                conversation_history.append({"role": "bot", "content": content})
                                context_data["conversation_history"] = conversation_history
                                await asyncio.to_thread(context_collection.update_one, 
                                                     {"_id": phone_number},
                                                     {"$set": {"context_data": context_data}})
                            
                            # await send_whatsapp_message(phone_number, content)
                            
                            if button_id == "WELCOME5":
                                image_url = "https://storage.googleapis.com/nashermiles/uploads/WhatsApp%20Image%202025-05-27%20at%2012.51.28_5b690140.jpg"
                                await send_whatsapp_image(phone_number, image_url,content)
                                return
                            
                            await send_whatsapp_message(phone_number, content)

            elif message_type in ["image", "document"]:
                # Handle media messages if needed
                media_id = message.get(message_type, {}).get("id")
                if media_id:
                    logging.info(f"Received {message_type} from {phone_number}")
                    gcs_url = await process_whatsapp_media_payload(data)
                    if gcs_url == "❌ The file size exceeds the maximum limit of 10 MB. Please try again with a smaller file. ":
                        await send_whatsapp_message(phone_number, gcs_url)
                        return
                    asyncio.create_task(handle_message(phone_number, gcs_url, message_id))
                    print(gcs_url, "\n")
                    logging.info(f"Uploaded media to GCS: {gcs_url}")
            elif message_type == "audio":
                await send_whatsapp_message(
                    phone_number,
                    "Right now, I can only process text messages. Could you please type out your message?"
                )
            elif message_type == "video":
                await send_whatsapp_message(
                    phone_number,
                    "At the moment, I can only accept images (jpg or png) or PDF files. Could you send one of those instead?"
                )
            else:
                await send_whatsapp_message(
                    phone_number,
                    "I'm sorry, I can only process text messages at the moment. Could you please type out your message?"
                )

    except Exception as e:
        logging.error(f"Error processing webhook payload: {e}", exc_info=True)
        raise

@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Verify webhook endpoint for WhatsApp setup.
    """
    try:
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        if mode == "subscribe" and token == os.getenv("WHATSAPP_VERIFY_TOKEN"):
            logging.info("Webhook verification successful")
            return int(challenge)
        
        logging.warning("Webhook verification failed")
        raise HTTPException(status_code=403, detail="Verification failed")
    
    except Exception as e:
        logging.error(f"Error during verification: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Main webhook endpoint that receives WhatsApp messages.
    """
    global services_active
    try:
        data = await request.json()
        print(data, "\n")

        if await check_user_status(data):
            await process_webhook_payload(data)
        
        return JSONResponse(content={"status": "Event Received"}, status_code=200)

    except Exception as e:
        logging.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error processing webhook")

@app.get("/status")
def status():
    """Return the current status of the services."""
    return {"status": "active" if services_active else "inactive"}

@app.post("/activate")
async def activate_services(request: Request):
    """Activate WhatsApp services."""
    data = await request.json()
    if data.get("password") != os.getenv("ADMIN_PASSWORD"):
        return JSONResponse(status_code=401, content={"message": "Wrong Password"})
    
    global services_active, public_ngrok_url
    client_ip = request.client.host
    log_action(client_ip, "WhatsApp Services Started")
    
    sync_local_from_cloud()
    services_active = True
    
    return {
        "message": "✨ WhatsApp System Successfully Activated ✨",
        "details": {
            "AI Agent": "Running",
            "WhatsApp Services": "Started",
            "Ngrok URL": public_ngrok_url,
            "Note": "To deactivate all services, please call /deactivate endpoint"
        },
        "status": "success"
    }

@app.get("/deactivate")
async def deactivate_services(request: Request):
    """Deactivate WhatsApp services."""
    global services_active
    client_ip = request.client.host
    log_action(client_ip, "WhatsApp Services Stopped")
    
    backup_unique_users_to_cloud()
    services_active = False
    
    return {
        "message": "✨ WhatsApp System Successfully stopped ✨",
        "details": {
            "AI Agent": "Stopped",
            "WhatsApp Services": "Stopped",
            "Note": "To reactivate all services, please call /activate endpoint"
        },
        "status": "success"
    }

def log_action(ip_address: str, action: str):
    """Log actions with timestamp and IP address."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = {"timestamp": timestamp, "ip_address": ip_address, "action": action}
    with open("whatsapp_action_logs.json", "a") as f:
        f.write(json.dumps(log_entry) + "\n")

if __name__ == "__main__":
    logging.info("Starting WhatsApp FastAPI app")
    uvicorn.run("whatsapp_app:app", host="0.0.0.0", port=5001, log_config=None) 