import os
import json
import logging
import aiohttp  # Use aiohttp for non-blocking HTTP requests
from datetime import datetime, timedelta
from mongodb_handler import get_collection  # Using the migrated handler
import traceback
from dotenv import load_dotenv
import asyncio
load_dotenv()
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

logger = logging.getLogger(__name__)
handler = logging.FileHandler('insta_api_errors.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.ERROR)

ACCESS_TOKEN = os.getenv("IG_TOKEN")
BUSINESS_ACCOUNT_ID = os.getenv("IG_USER_ID")

# Path to the .env file
ENV_FILE_PATH = ".env"


# Local JSON file to temporarily store unique user IDs
UNIQUE_USERS_JSON = "unique_users.json"

# Retrieve collections from MongoDB Atlas
daily_user_count_collection = get_collection("daily_user_count")
unique_users_collection = get_collection("unique_users")

def sync_local_from_cloud():
    """
    On startup, clear the local UNIQUE_USERS_JSON file and pull unique users
    from the cloud backup ('unique_users' collection). This ensures that any previously 
    stored cloud data replaces local data before processing.
    """
    cloud_doc = unique_users_collection.find_one({"_id": "unique_users"})
    if cloud_doc and "users" in cloud_doc:
        unique_users = cloud_doc["users"]
    else:
        unique_users = []
    with open(UNIQUE_USERS_JSON, "w") as f:
        json.dump({"users": unique_users}, f)
    logger.info("Local unique_users.json updated with cloud backup; previous local data cleared.")

def add_user_to_local_file(user_id: str) -> bool:
    """
    Add a unique user_id to the local JSON file.
    Returns True if a new user was added, False if the user_id already exists.
    """
    if os.path.exists(UNIQUE_USERS_JSON):
        with open(UNIQUE_USERS_JSON, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {"users": []}
    else:
        data = {"users": []}
    
    if user_id not in data.get("users", []):
        data["users"].append(user_id)
        with open(UNIQUE_USERS_JSON, "w") as f:
            json.dump(data, f)
        logger.info(f"User {user_id} added locally.")
        return True
    else:
        logger.info(f"User {user_id} already exists locally.")
    return False

def add_user_to_set(user_id: str):
    """
    Instead of directly updating the cloud, add the unique user to a local JSON file.
    Later, call backup_unique_users_to_cloud() to sync these local entries to MongoDB Atlas.
    """
    add_user_to_local_file(user_id)

def backup_unique_users_to_cloud():
    """
    Backup all unique user IDs stored in the local JSON file to the MongoDB Atlas backup.
    This will update the 'unique_users' collection with the list of users from the local JSON file.
    """
    if os.path.exists(UNIQUE_USERS_JSON):
        with open(UNIQUE_USERS_JSON, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                logger.error("Local unique users JSON file is corrupted.")
                return
        unique_users = data.get("users", [])
        unique_users_collection.update_one(
            {"_id": "unique_users"},
            {"$set": {"users": unique_users}},
            upsert=True
        )
        logger.info(f"Backed up {len(unique_users)} unique users to MongoDB Atlas.")
    else:
        logger.info("No local unique users to backup.")

def reset_and_store_user_count():
    """
    Reset the daily unique user count by:
      1. Loading the local unique user list from the JSON file.
      2. Counting the unique users and storing that count in the cloud (daily_user_count collection).
      3. Cleaning the cloud backup (emptying the 'unique_users' collection) and
         clearing the local JSON file.
    """
    local_users = []
    if os.path.exists(UNIQUE_USERS_JSON):
        with open(UNIQUE_USERS_JSON, "r") as f:
            try:
                data = json.load(f)
                local_users = data.get("users", [])
            except json.JSONDecodeError:
                local_users = []
    count = len(local_users)
    yesterday = datetime.now() - timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d")
    daily_user_count_collection.insert_one({
        "date": date_str,
        "unique_user_count": count
    })
    logger.info(f"Stored count for {date_str}: {count} unique users (from local file).")
    
    unique_users_collection.update_one(
        {"_id": "unique_users"},
        {"$set": {"users": []}},
        upsert=True
    )
    logger.info("Cleaned cloud backup unique users.")
    
    with open(UNIQUE_USERS_JSON, "w") as f:
        json.dump({"users": []}, f)
    logger.info("Cleared local unique users JSON file.")

async def refresh_access_token() -> str:
    """
    Refresh the Instagram access token asynchronously and update the .env file.
    """
    url = "https://graph.instagram.com/refresh_access_token"
    params = {
        "grant_type": "ig_refresh_token",
        "access_token": ACCESS_TOKEN
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            response_data = await response.json()
            logger.info(f"Refresh token response: {response_data}")
            if "access_token" in response_data:
                new_access_token = response_data["access_token"]
                print("Access token refreshed:", new_access_token)
                
                # ✏️ Update .env file
                await update_env_file("IG_TOKEN", new_access_token)

                return new_access_token
            else:
                raise Exception(f"Error refreshing token: {response_data}")

async def update_env_file(key: str, value: str):
    """
    Update a key-value pair in the .env file.
    If the key doesn't exist, add it.
    """
    try:
        lines = []
        # Read current .env contents
        if os.path.exists(ENV_FILE_PATH):
            with open(ENV_FILE_PATH, "r") as f:
                lines = f.readlines()
        
        key_found = False
        for idx, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[idx] = f"{key}={value}\n"
                key_found = True
                break
        
        if not key_found:
            lines.append(f"{key}={value}\n")
        
        # Write back updated lines
        with open(ENV_FILE_PATH, "w") as f:
            f.writelines(lines)

        logger.info(f"✅ .env file updated with new {key}")
    except Exception as e:
        logger.error(f"❌ Failed to update .env file: {e}")
async def utf8len(s):
    if len(s.encode('utf-8'))>1000:
        llm=ChatGoogleGenerativeAI(model="gemini-1.5-flash-8b-latest", temperature=0.4)
        response=llm.invoke([HumanMessage(content=f"Please compress the following text to 1000 characters or less don't change them semantically and don't remove any important information or link: {s}")])
        return response.content
        
    else:
        return s
async def send_reply(sender_id: str, message_text: str) -> dict:
    """
    Send a reply to an Instagram user asynchronously and track unique users (locally first).
    In case the message is not sent successfully, an error is logged to the log file.
    """
    
    try:
        message_text=await utf8len(message_text)
        add_user_to_set(sender_id)
        url = f"https://graph.instagram.com/v22.0/{BUSINESS_ACCOUNT_ID}/messages"
        headers = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        data = {
            "recipient": {"id": sender_id},
            "message": {"text": message_text}
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                result = await response.json()
                if response.status != 200:
                    logger.error(f"Error sending reply to user {sender_id}: {result} (Status Code: {response.status})")
                    logger.error(traceback.format_exc())
                return result
    except Exception as e:
        logger.error(f"Exception sending reply to user {sender_id}: {str(e)}")
        logger.error(traceback.format_exc())
        raise

async def get_conversations() -> dict:
    """Retrieve all conversations asynchronously."""
    url = f"https://graph.instagram.com/v22.0/{BUSINESS_ACCOUNT_ID}/conversations"
    params = {
        "platform": "instagram",
        "access_token": ACCESS_TOKEN
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            return await response.json()

async def get_messages(conversation_id: str) -> dict:
    """Retrieve messages for a specific conversation asynchronously."""
    url = f"https://graph.instagram.com/v22.0/{conversation_id}"
    params = {
        "fields": "messages{id,created_time,from,to,message}",
        "access_token": ACCESS_TOKEN
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            return await response.json()

async def send_reaction(sender_id: str, message_id: str, reaction_type: str = "love") -> dict:
    """
    Send a reaction to an Instagram message asynchronously.
    
    Args:
        sender_id (str): The Instagram user ID to send the reaction to
        message_id (str): The ID of the message to react to
        reaction_type (str): The type of reaction (default: "love")
    
    Returns:
        dict: The API response
    """
    try:
        url = f"https://graph.instagram.com/v22.0/{BUSINESS_ACCOUNT_ID}/messages"
        headers = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        data = {
            "recipient": {"id": sender_id},
            "sender_action": "react",
            "payload": {
                "message_id": message_id,
                "reaction": reaction_type
            }
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                result = await response.json()
                if response.status != 200:
                    logger.error(f"Error sending reaction to message {message_id}: {result} (Status Code: {response.status})")
                    logger.error(traceback.format_exc())
                return result
    except Exception as e:
        logger.error(f"Exception sending reaction to message {message_id}: {str(e)}")
        logger.error(traceback.format_exc())
        raise

async def send_offer_quick_replies(sender_id: str) -> dict:
    """
    Sends 5 offer options as quick replies to the Instagram user.
    """
    try:
        url = f"https://graph.instagram.com/v22.0/{BUSINESS_ACCOUNT_ID}/messages"
        headers = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }

        data = {
            "recipient": {
                "id": sender_id
            },
            "message": {
                "text": "Pick an offer to learn more 👇",
                "quick_replies": [
                    {"content_type": "text", "title": "New Login Offer", "payload": "NEWLOGINOFFER"},
                    {"content_type": "text", "title": "Welcome Offer", "payload": "WELCOME5"},
                    {"content_type": "text", "title": "Rishabh Pant Offer", "payload": "RISHABHPANT"},
                    {"content_type": "text", "title": "Student Discount", "payload": "STUDENTDISCOUNT"},
                    {"content_type": "text", "title": "Bank Offer", "payload": "NMSCAPIA"}
                ]
            }
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                result = await response.json()
                if response.status != 200:
                    logger.error(f"Error sending quick replies to user {sender_id}: {result} (Status Code: {response.status})")
                    logger.error(traceback.format_exc())
                return result
    except Exception as e:
        logger.error(f"Exception sending quick replies to user {sender_id}: {str(e)}")
        logger.error(traceback.format_exc())
        raise

async def send_offer_image(sender_id: str, image_url: str) -> dict:
    """
    Sends an image to the Instagram user before text reply.
    """
    url = f"https://graph.instagram.com/v22.0/{BUSINESS_ACCOUNT_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    image_payload = {
        "recipient": {"id": sender_id},
        "message": {
            "attachment": {
                "type": "image",
                "payload": {
                    "url": image_url,
                    "is_reusable": True
                }
            }
        }
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=image_payload) as resp:
            return await resp.json()
        
# asyncio.run(send_offer_image("534460082834879", "https://storage.googleapis.com/nashermiles/uploads/WhatsApp%20Image%202025-05-27%20at%2012.51.28_5b690140.jpg"))