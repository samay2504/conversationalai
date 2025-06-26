import logging
import traceback
import asyncio
from datetime import datetime, time
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from collections import defaultdict
import requests
from insta_api import send_reply, sync_local_from_cloud, backup_unique_users_to_cloud,send_reaction,send_offer_quick_replies
from pyngrok import ngrok
from contextlib import asynccontextmanager
from AI_Agent import AsyncAIBot
import uvicorn
from dotenv import load_dotenv
import os
import sys
from mongodb_handler import get_context_collection,get_collection
import schedule
import time
import subprocess
import signal
import time
import json
from datetime import datetime
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List, Dict, Optional
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn
from pyngrok import ngrok
from error_logger import log_to_openobserve
from fastapi.templating import Jinja2Templates
from redis.asyncio import Redis
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage, SystemMessage
from fastapi.responses import PlainTextResponse
import instaloader
from pymongo.collection import Collection
from gemini_live import upload_to_gcs_public
from insta_api import send_offer_image
# Define models
class ButtonState(BaseModel):
    enabled: bool

class LogEntry(BaseModel):
    timestamp: str
    ip_address: str
    action: str

class ServiceResponse(BaseModel):
    status: str
    message: Optional[str] = None


public_ngrok_url = None
services_active = False  # Track if services are active
# Redis setup
# Assuming Redis client is initialized globally
redis = Redis(host="localhost", port=6379, decode_responses=True)


load_dotenv()
# Ensure the logs directory exists
os.makedirs("logs", exist_ok=True)

# Configure logging with force=True to reconfigure even if already set up
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/app.log"),
        logging.StreamHandler()
    ],
    force=True
)
# Suppress pyngrok logs
logging.getLogger("pyngrok").setLevel(logging.ERROR)
public_ngrok_url=None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup code: offload synchronous operations via asyncio.to_thread()
    """
    Lifespan context manager for FastAPI.
    This context manager is used to define the startup and shutdown code
    for the FastAPI application. It is called automatically by FastAPI
    when the application is started or shut down.
    On startup, it initializes the Selenium WebDriver, logs into Zoho,
    and syncs users from the cloud to local. It then schedules a token
    refresh every 55 minutes and starts the scheduler in a separate task.
    On shutdown, it backs up unique users to the cloud, kills the ngrok
    tunnel, and quits the Selenium WebDriver.
    """
    global public_ngrok_url, driver, wait,operator_status
    try:
        http_tunnel = ngrok.connect(5000, proto="http", hostname="keen-fowl-gently.ngrok-free.app")  # Using port 5000 for the app
        public_ngrok_url = http_tunnel.public_url
        logging.info(f"Ngrok tunnel URL: {public_ngrok_url}")
    except Exception as e:
        await log_to_openobserve("zoho_app",traceback.format_exc(),level="critical")
        logging.info(f"Error connecting to ngrok: {e}")
        ngrok.kill()
        import sys
        sys.exit(1)
    global redis
    try:
        response = await redis.ping()
        if response:
            print("✅ Redis server is alive!")
        else:
            print("⚠️ Redis server did not respond!")
    except Exception as e:
        print(f"❌ Error pinging Redis: {e}")
    print("✅ Connected to Redis and MongoDB")
    logging.info("=== [STARTUP] Initializing Selenium WebDriver and logging into Zoho...")
    #remove previous_snapshot.json
    if os.path.exists("previous_snapshot.json"):
        os.remove("previous_snapshot.json")
        print("previous_snapshot.json removed")

    logging.info("Users synced from cloud. Starting the FastAPI app with lifespan.")
    
    yield
    
    
    logging.info("Shutting down server and ngrok tunnel...")
    backup_unique_users_to_cloud()
    logging.info("Lifespan shutdown: Backed up unique users.")
    ngrok.kill()
    
# Dictionary to store our subprocesses by name
processes = {}

# Global references to Selenium driver & WebDriverWait
driver = None
wait = None

#operator status
operator_status={}
#webhook status
webhook=False



app = FastAPI(lifespan=lifespan)
# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# Initialize the LangGraph workflow (your bot)
bot = AsyncAIBot()
# Dictionary to store operator set
operator_set = {}
# Dictionary to store queues per user
user_queues = defaultdict(asyncio.Queue)
# Dictionary to track active processing tasks per user
active_tasks = {}
# Dictionary to track sequence numbers per user
user_sequence = defaultdict(int)
# Dictionary to track next expected sequence number per user
user_next_seq = defaultdict(int)
# Dictionary to store response buffers per user
user_response_buffer = defaultdict(dict)
# Add a dictionary to track onboarding status
user_onboarding = {}  # Maps user_id -> boolean (True if onboarding is in progress)
# set to track AI status per user
user_ai_status = set()  # Maps user_id -> boolean (True if AI is enabled)
# Agent Status
agent_status=False
###############################################################################
# Async Queue Processing Function with Debounce and Ordered Replies
###############################################################################

async def process_user_queue(sender_id,message_id=None):
    """
    Processes messages for a single user in batches after a debounce period.
    Includes an inactivity timeout to cancel idle tasks.
    """
    try:
        while True:
            try:
                # Wait for a message with an idle timeout
                first_message = await asyncio.wait_for(user_queues[sender_id].get(), timeout=150)
            except asyncio.TimeoutError:
                logging.info(f"User {sender_id} has been idle for too long, cancelling queue processing.")
                break

            user_queues[sender_id].task_done()
            messages = [first_message]

            # Collect additional messages with a debounce timeout
            try:
                while True:
                    message = await asyncio.wait_for(user_queues[sender_id].get(), timeout=3)
                    messages.append(message)
                    user_queues[sender_id].task_done()
            except asyncio.TimeoutError:
                # Debounce period ended, move on to processing the batch
                pass

            # Combine batched messages into one text
            bundled_message = " ".join(messages)
            logging.info(f"Processing batched messages for {sender_id}: {bundled_message}")

            # Assign a sequence number to this message batch
            user_sequence[sender_id] += 1
            current_seq = user_sequence[sender_id]

            # Process the batched message concurrently
            asyncio.create_task(handle_batch(sender_id, bundled_message, current_seq,message_id))

    except asyncio.CancelledError:
        logging.info(f"Processing task for user {sender_id} was cancelled.")
    except Exception as e:
        logging.error(f"Error processing user queue for {sender_id}: {e}", exc_info=True)
    finally:
        # Cleanup: remove the task from active_tasks
        active_tasks.pop(sender_id, None)
        logging.info(f"Cleaned up processing task for user {sender_id}.")

import re

async def replace_links_with_gcs(message: str) -> str:
    """
    Detects links in the message, uploads them to GCS, and replaces them with GCS URLs.
    
    Args:
        message (str): Original message text containing URLs.
    
    Returns:
        str: Message with URLs replaced by GCS public URLs.
    """
    # Regex to find all http(s) URLs
    url_pattern = re.compile(r'(https?://[^\s]+)')

    def replacer(match):
        original_url = match.group(0)
        try:
            new_url = upload_to_gcs_public(original_url)
            return new_url
        except Exception as e:
            print(f"Failed to upload {original_url}: {e}")
            return original_url  # Keep original if upload fails

    # Replace all matched URLs
    updated_message = url_pattern.sub(replacer, message)
    return updated_message

async def handle_batch(sender_id, bundled_message, seq_num,message_id=None):
    """
    Handles processing of a single batch and manages ordered response sending.
    """
    try:
        # Send the batched message to the bot
        print(f"Sending batched message to bot for user {sender_id}: {bundled_message}")
        try:
            bundled_message=await replace_links_with_gcs(bundled_message)
            response_text = await bot.run_agent(str(sender_id), bundled_message,"Tara")
        except Exception as e:
            await log_to_openobserve("gogizmo_bot",traceback.format_exc(),level="critical")
        # Save the response in the buffer
        user_response_buffer[sender_id][seq_num] = response_text
        # Attempt sending responses in order
        await send_ordered_responses(sender_id,message_id)
    except Exception as e:
        logging.error(f"Error handling batch for user {sender_id}, seq {seq_num}: {e}", exc_info=True)
        # In case of error, store an error message to maintain order
        user_response_buffer[sender_id][seq_num] = "Something went wrong on my end. It's not you, it's me. Please contact us at +919931080808 for any further details."
        await send_ordered_responses(sender_id)

async def send_ordered_responses(sender_id,message_id=None):
    """
    Sends buffered responses in the correct order based on sequence numbers.
    """
    expected_seq = user_next_seq[sender_id] + 1

    while expected_seq in user_response_buffer[sender_id]:
        response = user_response_buffer[sender_id].pop(expected_seq)
        # Directly await the asynchronous send_reply function
        try:
            value = await redis.get(f"{sender_id}_discount")
            if value=="True":
                await send_offer_quick_replies(sender_id)
                await redis.delete(f"{sender_id}_discount")
            else:
                response=await remove_asterisks(response)
                await send_reply(sender_id, response)
            logging.info(f"Sent response to user {sender_id}, seq {expected_seq}.")
        except Exception as e:
            # user_onboarding[sender_id] = False
            logging.error(f"Error sending response to user {sender_id}, seq {expected_seq}: {e}", exc_info=True)
            # In case of error, store an error message to maintain order
            user_response_buffer[sender_id][expected_seq] = "Something went wrong on my end. It's not you, it's me. Please contact us at +919931080808 for any further details."
            await send_ordered_responses(sender_id)
            pass
        user_next_seq[sender_id] = expected_seq
        expected_seq += 1

async def remove_asterisks(message: str) -> str:
    """
    Removes all asterisk (*) characters from the input message.
    
    Args:
        message (str): The input text with potential asterisks.
    
    Returns:
        str: Cleaned message without asterisks.
    """
    return message.replace('*', '')

async def handle_message(sender_id: str, message_text: str,message_id: str=None):
    """
    Handles incoming messages by adding them to a user's queue and managing processing tasks.
    Respects onboarding status before starting message processing.
    """
    try:
        # Add message to the user's queue
        await user_queues[sender_id].put(message_text)
        
        # Only start processing if:
        # 1. No active task exists for this user AND
        # 2. Onboarding is not in progress (or onboarding status isn't tracked for this user)
        if sender_id not in active_tasks and not user_onboarding.get(sender_id, False):
            active_tasks[sender_id] = asyncio.create_task(process_user_queue(sender_id,message_id))
            logging.info(f"Started message processing for user {sender_id}")
        else:
            if user_onboarding.get(sender_id, False):
                logging.info(f"Message queued for user {sender_id} - onboarding in progress")
            else:
                logging.info(f"Message queued for user {sender_id} - processing already active")
    except Exception as e:
        logging.error(f"Error handling message for user {sender_id}: {e}", exc_info=True)
###############################################################################
# Helper Function for Processing Webhook Payload
###############################################################################
# async def delayed_reply_after_collecting_phone_number(user_id: str, message_text: str,message_id: str=None):
#     """
#     Handles the delayed reply after collecting the phone number.
#     """
#     global user_onboarding
#     try:

#         user_onboarding[user_id] = True
#         collection = get_context_collection()
#         existing_doc = await asyncio.to_thread(collection.find_one, {"_id": user_id})
#         if existing_doc:
#             context_data = existing_doc.get("context_data", {})
#             conversation_history = context_data.get("conversation_history", [])
#             await send_reply(user_id, message_text)
            
#             await asyncio.sleep(5)
#             content=f"""Would you like to come in today ? \nLet us know when you're planning to come as our team can help book an appointment for you!"""  
#             conversation_history.append({"role": "bot", "content": content})
#             await send_reply(user_id, content)
#             await asyncio.sleep(5)
#             content=f"""
#     You can visit our store to check out our stunning range of fine designer jewellery and for any further details!\nYou can also call us at +919931080808 for any more details!!
#     """         
#             conversation_history.append({"role": "bot", "content": content})
#             await send_reply(user_id, content)
#             # Update the document in MongoDB
#             context_data["conversation_history"] = conversation_history
#             await asyncio.to_thread(collection.update_one, {"_id": user_id}, {"$set": {"context_data": context_data}})
#             # Mark onboarding as completed
#             user_onboarding[user_id] = False
#             await redis.delete(f"{user_id}_phone")
#             # Check if there are pending messages in the queue
#             if not user_queues[user_id].empty() and user_id not in active_tasks:
#                 logging.info(f"Store visit is being offered for user {user_id}. Found pending messages, starting processing task.")
#                 active_tasks[user_id] = asyncio.create_task(process_user_queue(user_id))
#             else:
#                 logging.info(f"Store visit message sent {user_id}. No pending messages or processing already active.")

        
#     except Exception as e:
#         logging.error(f"Error in delayed reply for user {user_id}: {e}", exc_info=True)
#         log_to_openobserve("zoho_app", "Mongodb user not found in the database check the limits\n"+str(traceback.format_exc()), level="critical")
#         return
    


# async def delayed_followup(user_id: str, delay: int, name: str=None,message_id: str=None):
#     """
#     Sends delayed onboarding messages to new users and triggers queue processing
#     after completion.
#     """
#     try:
#         # Mark onboarding as in progress
#         user_onboarding[user_id] = True
#         if message_id:
#             await send_reaction(user_id, message_id, "love")
#         await asyncio.sleep(2)
#         collection = get_context_collection()
#         existing_doc = await asyncio.to_thread(collection.find_one, {"_id": user_id})
#         if existing_doc:
#             context_data = existing_doc.get("context_data", {})
#             conversation_history = context_data.get("conversation_history", [])
#             if name==None:
#                 content=f"""Hey,\n\nWelcome to Ribbons Jewellery! 🎀 \n\n You can visit our boutique located at 57 Basant Lok, Vasant Vihar, New Delhi (Priya Complex) or you can also call us at +919931080808 for further details."""
#             else:
#                 content=f"""Hey {name},\n\nWelcome to Ribbons Jewellery! 🎀 \n\nYou can visit our boutique located at 57 Basant Lok, Vasant Vihar, New Delhi (Priya Complex) or you can also call us at +919931080808 for further details."""
#             conversation_history.append({"role": "bot", "content": content})
#         await send_reply(user_id, content)
#         await asyncio.sleep(delay)
#         await send_reply(user_id, "We only deal in 18 KT hallmark gold and certified syndicate natural diamonds polki jewellery!")
#         conversation_history.append({"role": "bot", "content": "We only deal in 18 KT hallmark gold and certified syndicate natural diamonds polki jewellery!"})
#         # Mark onboarding as completed
#         await asyncio.sleep(delay)
#         await send_reply(user_id, "Sure! What's a good number to reach you at ?\n\nPlease share your contact details so our team can reach out to you with all the details! Thank you!")
#         conversation_history.append({"role": "bot", "content": "Sure! What's a good number to reach you at ?\n\nPlease share your contact details so our team can reach out to you with all the details! Thank you!"})
#         context_data["conversation_history"] = conversation_history
#         await asyncio.to_thread(collection.update_one, {"_id": user_id}, {"$set": {"context_data": context_data}})
#         # Clear any messages that came during onboarding
#         while not user_queues[user_id].empty():
#             try:
#                 await user_queues[user_id].get()
#                 user_queues[user_id].task_done()
#             except Exception as e:
#                 logging.error(f"Error clearing queue for user {user_id}: {e}")
#                 break
                
#         user_onboarding[user_id] = False
        
#     except Exception as e:
#         # In case of error, still mark onboarding as completed
#         user_onboarding[user_id] = False
#         logging.error(f"Error in delayed followup for user {user_id}: {e}", exc_info=True)


# async def get_followers(username: str, do_login: bool = False) -> int:
#     """
#     Return the follower count for the given Instagram username.
#     If do_login=True, logs in with IG_USER/IG_PASS env vars to access private profiles you follow.
#     """
#     L = instaloader.Instaloader()
#     if do_login:
#         ig_user = "filtero.chat"
#         ig_pass = "Jha@333305"
#         if not ig_user or not ig_pass:
#             sys.exit("Error: set IG_USER and IG_PASS as environment variables")
#         L.login(ig_user, ig_pass)
#     profile = instaloader.Profile.from_username(L.context, username)
#     return profile.followers, profile.followees
async def check_user_status(data: dict) -> bool:
    """
    Checks whether the user exists in Redis cache first.
    If not found, checks MongoDB database and adds user to Redis.
    Returns True if the user exists or was successfully created, False otherwise.
    """
    try:
        # Extract user_id from webhook payload
        user_id = data.get("entry", [{}])[0].get("messaging", [{}])[0].get("sender", {}).get("id", "NA")
        message_id = data.get("entry", [{}])[0].get("messaging", [{}])[0].get("message", {}).get("mid", "NA")
        if not user_id or user_id == "NA":
            logging.warning("Webhook payload does not include a user id.")
            return False

        # Check Redis cache first
        cached = await redis.exists(user_id)
        if cached:
            logging.info(f"User {user_id} found in Redis cache.")
            if user_id not in user_onboarding:
                user_onboarding[user_id] = False  # Initialize in case it's missing
            return True

        # Not found in Redis, check MongoDB
        collection = get_context_collection()
        existing_doc = await asyncio.to_thread(collection.find_one, {"_id": user_id})

        if existing_doc:
            # Cache in Redis for future fast access
            await redis.set(user_id, 1, ex=60*60*24*30)  # 30 days expiry
            logging.info(f"User {user_id} found in MongoDB and cached in Redis.")
            if user_id not in user_onboarding:
                user_onboarding[user_id] = False  # Initialize in case it's missing
            return True
        
        response = requests.get(f"https://graph.instagram.com/v22.0/{user_id}?fields=name,username&access_token={os.getenv('IG_TOKEN')}").json()
        name = None
        if 'name' in response:
            name = response['name']
        username = response.get('username', '')
        follower="NA"
        followee="NA"    
        print(f"Follower count for {username}: {follower}")
        # New user, insert into MongoDB
        history = []
        new_doc = {
            "_id": user_id,
            "context_data": {
                "first_message": "",
                "name": name,
                "Phone_Number": "",
                "followers":follower,
                "following":followee,
                "channel": data['object'],
                "instagram_username": username,
                "Last_Response": "",
                "last_suggestion": "",
                "conversation_history": history
            }
        }
        await asyncio.to_thread(collection.insert_one, new_doc)
        logging.info(f"Inserted new user in DB: {user_id}")
        
        # Schedule follow-up tasks
        # from scheduled_jobs_redis import schedule_send_reply_jobs # Assuming test.py is accessible
        # await asyncio.to_thread(schedule_send_reply_jobs, user_id)
        logging.info(f"Scheduled follow-up jobs for new user {user_id}")
        
        # Cache in Redis
        await redis.set(user_id, 1, ex=60*60*24*30)
        
        # Initialize the onboarding status for this user
        # user_onboarding[user_id] = False
        
        # Start delayed followup task
        # asyncio.create_task(delayed_followup(user_id, 5, name,message_id))
        
        logging.info(f"User {user_id} cached in Redis after creation.")
        
        return True

    except Exception as e:
        await log_to_openobserve("ribbons_app", traceback.format_exc(), level="critical")
        logging.error("Error in check_user_status", exc_info=True)
        return False

async def process_webhook_payload(data: dict, ignored_sender_ids: set):
    """
    Processes the webhook payload by iterating over entries and dispatching messages.
    Ignores messages coming from specified sender IDs.
    """
    entries = data.get("entry", [])
    message_id=message_id = data.get("entry", [{}])[0].get("messaging", [{}])[0].get("message", {}).get("mid", "NA")
    for entry in entries:
        messaging_events = entry.get("messaging", [])
        for event in messaging_events:
            sender_id = event.get("sender", {}).get("id")
            if sender_id and sender_id in ignored_sender_ids:
                continue  # Skip processing messages from our own business account
            message = event.get("message", {})
            message_text = message.get("text", "").strip()
            attachments = message.get("attachments", [])

            final_message = ""

            # Check for text
            if message_text:
                final_message += message_text

            # Check for attachment URLs (e.g., post/reel share)
            for att in attachments:
                payload = att.get("payload", {})
                url = payload.get("url") or payload.get("reel_url") or payload.get("share_url")
                if url:
                    if final_message:
                        final_message += f" | {url}"
                    else:
                        final_message = url

            if final_message:
                print("final message: ",final_message)
                logging.info(f"Received message from {sender_id}: {final_message}")
                asyncio.create_task(handle_message(sender_id, final_message, message_id))

    
###############################################################################
# Helper Functions for Managing Subprocesses
###############################################################################
def stop_with_sigint(proc, timeout=7):
    """Cleanly stop a subprocess using SIGINT to avoid zombies."""
    if proc and proc.poll() is None:
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

def log_action(ip_address: str, action: str):
    """Log actions with timestamp and IP address."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = {"timestamp": timestamp, "ip_address": ip_address, "action": action}
    with open("action_logs.json", "a") as f:
        f.write(json.dumps(log_entry) + "\n")

async def intiate_operator_status():
    global operator_status
    operator_list=eval(os.environ.get("OPERATOR_LIST"))
    for i in operator_list:
        # print(i)
        operator_status[i]=False

###############################################################################
# FastAPI routes
###############################################################################
# Serve index.html as the homepage
@app.get("/", response_class=HTMLResponse)
async def get_frontend():
    with open("static/index.html", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content)

@app.post("/sheet_change")
async def sheet_change(request: Request):
    data = await request.json()
    print(data)
    # Call the new handler function
    await handle_sheet_change(data)
    return {"message": "Data received successfully"}
@app.get("/ai_enable_disable")
async def ai_enable_disable(request: Request):
    global user_ai_status
    data= await request.json()
    id=data.get("id")
    status=data.get("status")
    if status=="enable" or status=="Enable" or status=="ENABLE":
        #remove the user from the user_ai_status dictionary
        user_ai_status.discard(id)
    else: 
        user_ai_status.add(id)
    return {"message": "Data received successfully"}
    
    

@app.get("/status")
def status():
    """Return the current status of the services."""
    return {"status": "active" if services_active else "inactive"}

@app.post("/activate")
async def activate_services(request: Request):
    """
    1. Start scheduler.py if they are not running.
    2. Use Selenium to navigate to SalesIQ, enable SalesIQ, and ensure Desk Tickets are offline.
    """
    data= await request.json()
    print(data)
    if(data.get("password")!=os.environ.get("ADMIN_PASSWORD")):
        return JSONResponse(status_code=401, content={"message": "Wrong Password"})
    global processes, services_active
    client_ip = request.client.host
    log_action(client_ip, "AI Services Started")
    sync_local_from_cloud()
    if processes:
        return {"message": "AI Services are already active."}

    # Start external processes
    p2 = subprocess.Popen(["python3", "scheduler.py"], stdin=subprocess.PIPE)
    processes = {"scheduler": p2}


    services_active = True
    return {
        "message": "✨ AI System Successfully Activated.  ✨",
        "details": {
            "AI Agent": "Running",
            "Scheduler Services": "Started",
            "Zoho Operator": "Enabled",
            "SalesIQ": "Online",
            "Desk Tickets": "Offline",
            "Note": "To deactivate all services, please call /deactivate endpoint"
        },
        "status": "success"
    }

@app.get("/deactivate")
async def deactivate_services(request: Request):
    """
    Deactivates the running services and updates their status.

    This endpoint performs the following actions:
    1. Logs the deactivation attempt and backs up unique users to the cloud.
    2. Checks if services are already inactive, returning a message if true.
    3. Stops the scheduler process using SIGINT.
    4. Resets the operator set and button states.
    5. Uses Selenium to navigate to SalesIQ and turn off SalesIQ services.
    6. Updates the webhook to disable mode.
    7. Sets the services_active flag to False.

    Returns a success message with details of the deactivated services.
    """

    global processes, services_active
   
    client_ip = request.client.host
    log_action(client_ip, "AI Services Stopped ")
    backup_unique_users_to_cloud()
    if not processes:
        return {"message": "AI Services are already inactive."}

    stop_with_sigint(processes.get("scheduler"))
    processes = {}
    services_active = False
    return {
        "message": "✨ AI System Successfully stopped ✨",
        "details": {
            "AI Agent": "Stopped",
            "Scheduler Services": "Stopped",
            "Zoho Operator": "Disabled",
            "Note": "To reactivate all services, please call /activate endpoint"
        },
        "status": "success"
    }



@app.get("/logs")
async def get_logs():
    """Return the last 50 log entries."""
    try:
        logs = []
        with open("action_logs.json", "r") as f:
            for line in f:
                logs.append(json.loads(line.strip()))
        return {"logs": logs[-50:]}
    except FileNotFoundError:
        return {"logs": []}
    
# Get all button statuses
@app.get("/status/buttons")
async def get_all_button_status():
    """
    Return the current status of all buttons as a dictionary.

    Returns:
        dict: with button IDs as keys and their current status as values
    """
    return {"buttons": operator_status}

# Get status of a specific button
@app.get("/status/button/{button_id}")
async def get_button_status(button_id: str):
    """
    Get the status of a specific button.

    Args:
        button_id (str): ID of the button to query.

    Returns:
        A JSON object with the following keys:
            * button_id (str): The ID of the button.
            * enabled (bool): Whether the button is enabled.

    Raises:
        HTTPException: If the button ID is not found.
    """
    if button_id not in operator_status:
        raise HTTPException(status_code=404, detail=f"Button {button_id} not found")
    # print("Debugging")
    # print(operator_status)
    return {
        "button_id": button_id,
        "enabled": operator_status[button_id]
    }

# Update button status
@app.post("/status/button/{button_id}")
async def update_button_status(button_id: str, state: ButtonState, request: Request):
    """
    Update the status of a button and manage webhook status based on overall operator availability.
    """
    global webhook
    if button_id not in operator_status:
        raise HTTPException(status_code=404, detail=f"Button {button_id} not found")
    
    if not services_active:
        raise HTTPException(status_code=400, detail="Cannot update button when service is inactive")
    
    operator_status[button_id] = state.enabled
    
    # Check if any operator is enabled
    any_operator_enabled = any(operator_status.values())
    
    # Update webhook based on operator availability
    if any_operator_enabled:
        # await update_webhook("enable", f"{public_ngrok_url}/webhook")
        print("Webhook enabled with ngrok")
    else:
        # await update_webhook("disable")
        webhook=False
        print("Webhook disabled")
    
    # Log the action
    if state.enabled:
        log_action(request.client.host, f"AI Services for operator: {button_id} enabled")
    else:
        log_action(request.client.host, f"AI Services for operator: {button_id} disabled")
    
    return {
        "button_id": button_id,
        "enabled": state.enabled,
        "status": "success"
    }

# Function to shorten URL using TinyURL API
def shorten_url(long_url):
    """
    Shortens a URL using the TinyURL API.
    Returns the shortened URL or the original URL if shortening fails.
    """
    try:
        api_url = f"https://tinyurl.com/api-create.php?url={long_url}"
        response = requests.get(api_url, timeout=5)
        if response.status_code == 200:
            return response.text
        return long_url
    except Exception as e:
        print(f"Error shortening URL: {e}")
        return long_url


##########################################################################
#  Webhook routes
##########################################################################
from fastapi.responses import StreamingResponse
import httpx

async def forward_webhook_data(data: dict):
    """
    Forwards the received webhook data to another endpoint with a query param.
    Times out if the response takes more than 5 seconds.
    """
    forward_url = "https://sparkle.powersmy.biz/webhook"
    params = {"page_id": "ribbons"}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(forward_url, params=params, json=data, timeout=5.0)
            print("Forwarded webhook response:", response.status_code, response.text)
            return response
        except httpx.RequestError as exc:
            print(f"Request failed: {exc}")
            return None

@app.get("/media/")
async def media_proxy(url: str):
    """
    Fetches the given URL server‐side and streams it back,
    avoiding client‐side blocks & CORS issues.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10.0)
        resp.raise_for_status()
        return StreamingResponse(
            resp.aiter_bytes(),
            media_type=resp.headers.get("content-type", "application/octet-stream")
        )
@app.get("/webhook")
async def verify(request: Request):
    """Verification endpoint for Facebook/Instagram webhook setup."""
    try:

        hub_mode = request.query_params.get('hub.mode')
        verify_token = request.query_params.get('hub.verify_token')
        challenge = request.query_params.get('hub.challenge')

        if hub_mode == 'subscribe' and verify_token == os.getenv("VERIFY_TOKEN"):
            logging.info("Webhook verification successful")
            return int(challenge)
        
        logging.warning("Webhook verification failed")
        raise HTTPException(status_code=403, detail="Verification failed")
    
    except Exception as e:
        logging.error(f"Error during verification: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")
    
# async def process_all_messages(data: dict):
#     """
#     Parse the incoming webhook payload and save each message
#     (text, post share, or reel) into MongoDB.
#     """
#     entry = data["entry"][0]["messaging"][0]
#     sender_id = entry["sender"]["id"]
#     recipient_id = entry["recipient"]["id"]
#     # figure out which side is the *user* (we always key off user_id)
#     from_business = os.getenv("IG_USER_ID")
#     if sender_id != from_business:
#         user_id = sender_id
#     else:
#         user_id = recipient_id

#     msg = entry.get("message", {})
#     ts = entry.get("timestamp", 0)

#     # text message?
#     text = msg.get("text")
#     if text:
#         await asyncio.to_thread(
#             insert_message,
#             user_id, sender_id, recipient_id,
#             text, ts,
#             "text"
#         )
#         return

#     # attachments?
#     for att in msg.get("attachments", []):
#         typ = att.get("type")
#         payload = att.get("payload", {})
#         url = payload.get("url") or payload.get("reel_url") or payload.get("share_url")
#         # fall back to payload['url'] for both share and reel
#         url = url or payload.get("url")
#         title=None
#         # classify
#         if typ == "ig_reel":
#             msg_type = "reel"
#             title   = payload.get("title", "")
#         elif typ == "share":
#             msg_type = "post"
#         else:
#             msg_type = typ or "attachment"

#         if url:
#             await asyncio.to_thread(
#                 insert_message,
#                 user_id, sender_id, recipient_id,
#                 url, ts,
#                 msg_type,title
#             )


@app.get("/chat/{user_id}", response_class=HTMLResponse)
async def chat_page(request: Request, user_id: str):
    # Render only the static page; data is fetched client-side
    return templates.TemplateResponse(
        "chat.html",
        {"request": request, "user_id": user_id}
    )

@app.get("/api/chat/{user_id}")
async def chat_history(user_id: str, since: Optional[int] = None):
    # Return conversation JSON, optionally only new messages
    coll: Collection = get_collection("ribbons_chat")
    user_doc = await asyncio.to_thread(coll.find_one, {"_id": user_id})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")
    history = user_doc.get("context_data", {}).get("conversation_history", [])
    # Sort by timestamp ascending
    history.sort(key=lambda m: m.get('timestamp', 0))
    if since is not None:
        # filter only messages newer than `since`
        history = [m for m in history if m.get('timestamp', 0) > since]
    return JSONResponse(history)
    
@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Main webhook endpoint that receives messages.
    """

    global services_active
    try:
        data = await request.json()
        print(data,"\n")
        # Forward the webhook data
        # asyncio.create_task(forward_webhook_data(data))
        # if not services_active:
        #     return JSONResponse(content={"status": "Event Received"}, status_code=200)
        # Decide which business account to ignore based on the USER variable.
        ignored_sender_ids = {str(os.getenv("IG_USER_ID"))}
        sender_ids = data.get("entry", [{}])[0].get("messaging", [{}])[0].get("sender", {}).get("id", "NA")
        # if sender_ids in user_ai_status:
        #     return JSONResponse(content={"status": "Event Received"}, status_code=200)
        # print("Sender ids: ",sender_ids,"\n")
        # print("Ignored sender ids: ",ignored_sender_ids,"\n")
        if sender_ids == "NA":
            return JSONResponse(content={"status": "Event Received"}, status_code=200)
        if sender_ids in ignored_sender_ids:
            return JSONResponse(content={"status": "Event Received"}, status_code=200)
        quick_reply = data.get("entry", [{}])[0] \
                  .get("messaging", [{}])[0] \
                  .get("message", {}) \
                  .get("quick_reply", {}) \
                  .get("payload", None)

        print("quick_reply",quick_reply,"\n")
        if quick_reply:
            content=""
            if quick_reply=="NEWLOGINOFFER":
                content="New Login Offer\nLogin & Get Flat 200 Off\nDiscount Code: NMFAM200\nMinimum Cart Value Rs. 1,999/-\nPrepaid Discount of 5% (Upto 200/-) auto-applied at the checkout page."
            elif quick_reply=="WELCOME5":
                content="Welcome Offer\n5% Off for 1st time buyer\nDiscount Code: Welcome-5\nMinimum Cart Value Rs. 2,499/-\n(Maximum Discount Rs. 500/-)\n Prepaid Discount of 5%(Upto 200/-) auto-applied at the checkout page."
            elif quick_reply=="RISHABHPANT":
                content="Rishabh Pant\n7% Off on Rishabh Pant Collection \nDiscount Code: Welcome-5\nMinimum Cart Value Rs. 7,999/-\n(Maximum Discount Rs. 1,000/-)\n Prepaid Discount of 5% (Upto 200/-) auto-applied at the checkout page."
            elif quick_reply=="STUDENTDISCOUNT":
                content="Student Discount\nGet 15% discount exclusively for students.\nSubmit your Admission Letter copy or Identity Card.\nOur team will issue a Special Discount code to you."
            elif quick_reply=="NMSCAPIA":
                content="Bank Offer\n15% Off for Scapia Card Holders\nDiscount Code: NMSCAPIA\nMinimum Cart Value Rs. 2,499/-\nNote: Offer Valid till 30.06.2025"
            context_collection=get_context_collection()
            existing_doc= context_collection.find_one({"_id":sender_ids})
            context_data=existing_doc.get("context_data",{})
            conversation_history=context_data.get("conversation_history",[])
            conversation_history.append({"role":"user","content":data.get("message",{}).get("text",None)})
            conversation_history.append({"role":"bot","content":content})
            context_data["conversation_history"]=conversation_history
            context_collection.update_one({"_id":sender_ids},{"$set":{"context_data":context_data}})
            print(content)
            if quick_reply=="WELCOME5":
                image_url="https://storage.googleapis.com/nashermiles/uploads/WhatsApp%20Image%202025-05-27%20at%2012.51.28_5b690140.jpg"
                await send_offer_image(sender_ids,image_url)
            await send_reply(sender_ids,content)

        else:
            if await check_user_status(data):
                await process_webhook_payload(data, ignored_sender_ids)
        
            
        
        return JSONResponse(content={"status": "Event Received"}, status_code=200)
        # return JSONResponse(content={"status": "Event Received","reply":reply}, status_code=200)

    except Exception as e:
        logging.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error processing webhook")

###############################################################################
# Start the FastAPI app
###############################################################################
if __name__ == "__main__":
    logging.info("Starting FastAPI app")
    uvicorn.run("nasher_miles_app:app", host="0.0.0.0", port=5000, log_config=None, reload=True)