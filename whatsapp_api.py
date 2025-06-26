import os
import json
import logging
import aiohttp
from datetime import datetime, timedelta

import requests
from mongodb_handler import get_collection
import traceback
from dotenv import load_dotenv
import asyncio
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from error_logger import log_to_openobserve
import uuid
from google.cloud import storage
from google.oauth2 import service_account
load_dotenv()

logger = logging.getLogger(__name__)
handler = logging.FileHandler('whatsapp_api_errors.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.ERROR)

# WhatsApp API Configuration
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_NUMBER_ID = os.getenv("WHATSAPP_NUMBER_ID")

# Path to the .env file
ENV_FILE_PATH = ".env"

# Local JSON file to temporarily store unique user IDs
UNIQUE_USERS_JSON = "whatsapp_unique_users.json"

# Retrieve collections from MongoDB Atlas
daily_user_count_collection = get_collection("whatsapp_daily_user_count")
unique_users_collection = get_collection("whatsapp_unique_users")

def sync_local_from_cloud():
    """
    On startup, clear the local UNIQUE_USERS_JSON file and pull unique users
    from the cloud backup ('whatsapp_unique_users' collection).
    """
    cloud_doc = unique_users_collection.find_one({"_id": "unique_users"})
    if cloud_doc and "users" in cloud_doc:
        unique_users = cloud_doc["users"]
    else:
        unique_users = []
    with open(UNIQUE_USERS_JSON, "w") as f:
        json.dump({"users": unique_users}, f)
    logger.info("Local whatsapp_unique_users.json updated with cloud backup; previous local data cleared.")

def add_user_to_local_file(phone_number: str) -> bool:
    """
    Add a unique phone_number to the local JSON file.
    Returns True if a new user was added, False if the phone_number already exists.
    """
    if os.path.exists(UNIQUE_USERS_JSON):
        with open(UNIQUE_USERS_JSON, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {"users": []}
    else:
        data = {"users": []}
    
    if phone_number not in data.get("users", []):
        data["users"].append(phone_number)
        with open(UNIQUE_USERS_JSON, "w") as f:
            json.dump(data, f)
        logger.info(f"User {phone_number} added locally.")
        return True
    else:
        logger.info(f"User {phone_number} already exists locally.")
    return False

def add_user_to_set(phone_number: str):
    """
    Add the unique user to a local JSON file.
    Later, call backup_unique_users_to_cloud() to sync these local entries to MongoDB Atlas.
    """
    add_user_to_local_file(phone_number)

def backup_unique_users_to_cloud():
    """
    Backup all unique user IDs stored in the local JSON file to the MongoDB Atlas backup.
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
    Reset the daily unique user count and store it in the cloud.
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

async def utf8len(s):
    """
    Compress text if it exceeds 1000 characters.
    """
    if len(s.encode('utf-8')) > 1000:
        llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash-8b-latest", temperature=0.4)
        response = llm.invoke([HumanMessage(content=f"Please compress the following text to 1000 characters or less don't change them semantically and don't remove any important information or link: {s}")])
        return response.content
    else:
        return s

async def send_whatsapp_message(phone_number: str, message_text: str) -> dict:
    """
    Send a WhatsApp message using the WhatsApp Business API.
    """
    try:
        add_user_to_set(phone_number)
        
        url = f"https://graph.facebook.com/v22.0/{WHATSAPP_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": message_text}
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                result = await response.json()
                if response.status != 200:
                    logger.error(f"Error sending message to user {phone_number}: {result} (Status Code: {response.status})")
                    logger.error(traceback.format_exc())
                return result
    except Exception as e:
        logger.error(f"Exception sending message to user {phone_number}: {str(e)}")
        logger.error(traceback.format_exc())
        raise

async def send_whatsapp_image(phone_number: str, image_url: str, caption: str = None) -> dict:
    """
    Send an image via WhatsApp with optional caption.
    """
    try:
        url = f"https://graph.facebook.com/v22.0/{WHATSAPP_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "image",
            "image": {
                "link": image_url
            }
        }
        
        if caption:
            payload["image"]["caption"] = caption

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                result = await response.json()
                if response.status != 200:
                    logger.error(f"Error sending image to user {phone_number}: {result} (Status Code: {response.status})")
                    logger.error(traceback.format_exc())
                return result
    except Exception as e:
        logger.error(f"Exception sending image to user {phone_number}: {str(e)}")
        logger.error(traceback.format_exc())
        raise



async def mark_message_as_read(message_id: str) -> dict:
    """
    Mark a message as read.
    """
    try:
        url = f"https://graph.facebook.com/v22.0/{WHATSAPP_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                result = await response.json()
                if response.status != 200:
                    logger.error(f"Error marking message as read: {result} (Status Code: {response.status})")
                    logger.error(traceback.format_exc())
                return result
    except Exception as e:
        logger.error(f"Exception marking message as read: {str(e)}")
        logger.error(traceback.format_exc())
        raise

async def send_offer_quick_replies(phone_number: str) -> dict:
    """
    Sends 5 offer options as a list message to the WhatsApp user.
    """
    try:
        url = f"https://graph.facebook.com/v22.0/{WHATSAPP_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": "🎁 Available Discounts"
                },
                "body": {
                    "text": "✨ Please select a discount from the list below:"
                },
                "footer": {
                    "text": "👆 Tap to choose a discount"
                },
                "action": {
                    "button": "🔍 View Offers",
                    "sections": [
                        {
                            "title": "🏷️ Offers",
                            "rows": [
                                {
                                    "id": "NEWLOGINOFFER",
                                    "title": "🆕 New Login Offer"
                                },
                                {
                                    "id": "WELCOME5",
                                    "title": "👋 Welcome Offer"
                                },
                                {
                                    "id": "RISHABHPANT",
                                    "title": "🏏 Rishabh Pant Offer"
                                },
                                {
                                    "id": "STUDENTDISCOUNT",
                                    "title": "📚 Student Discount"
                                },
                                {
                                    "id": "NMSCAPIA",
                                    "title": "🏦 Bank Offer"
                                }
                            ]
                        }
                    ]
                }
            }
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                result = await response.json()
                if response.status != 200:
                    logger.error(f"Error sending list message to user {phone_number}: {result} (Status Code: {response.status})")
                    logger.error(traceback.format_exc())
                return result
    except Exception as e:
        logger.error(f"Exception sending list message to user {phone_number}: {str(e)}")
        logger.error(traceback.format_exc())
        raise


async def remove_asterisks(message: str) -> str:
    """
    Removes all asterisk (*) characters from the input message.
    """
    return message.replace('*', '') 


MAX_WA_FILESIZE = 10 * 1024 * 1024  # 10 MB in bytes

async def download_whatsapp_media(media_id: str):
    """
    1) Fetches the temporary URL for the media item (image or video).
    2) Does a HEAD request to check Content-Length; if >10 MB, raises an exception.
    3) Downloads the binary via aiohttp using that URL.
    Returns (data_bytes, content_type).
    """
    # ── Step 1: Get the media URL ─────────────────────────────────────────────
    url_endpoint = f"https://graph.facebook.com/v22.0/{media_id}?fields=url"
    resp = requests.get(
        url_endpoint,
        headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    )
    resp.raise_for_status()
    media_url = resp.json().get("url")
    if not media_url:
        raise ValueError("Could not retrieve media URL from WhatsApp API.")

    # ── Step 2: HEAD request to check size ────────────────────────────────────
    head_resp = requests.head(media_url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"})
    head_resp.raise_for_status()
    content_length = head_resp.headers.get("Content-Length")
    if content_length is not None:
        size_bytes = int(content_length)
        if size_bytes > MAX_WA_FILESIZE:
            return f"❌ The file size exceeds the maximum limit of 10 MB. Please try again with a smaller file. 📷","None"

    # If Content-Length header is missing, fall back to streaming but still guard during download.
    # ── Step 3: Download the actual binary ───────────────────────────────────
    async with aiohttp.ClientSession() as session:
        async with session.get(media_url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}) as download:
            if download.status != 200:
                text = await download.text()
                raise Exception(f"Failed to download media: {download.status} {text}")

            # If header was absent, we still need to ensure we don’t exceed 10 MB while reading.
            data = bytearray()
            total_read = 0
            async for chunk in download.content.iter_chunked(1024 * 256):
                total_read += len(chunk)
                if total_read > MAX_WA_FILESIZE:
                    raise ValueError(f"Downloaded data exceeds 10 MB limit (read {total_read} bytes).")
                data.extend(chunk)
            content_type = download.headers.get("Content-Type", "application/octet-stream")
            return bytes(data), content_type
GCP_SA_KEY = "bucket_cred.json"
GCP_PROJECT = "fluent-oarlock-461903-h3"
BUCKET_NAME = "nashermiles2"

def upload_to_gcs_from_bytes(data: bytes, content_type: str) -> str:
    """
    Uploads the given bytes to GCS and returns the public URL.
    """
    ext_map = {
        "image/jpeg": ".jpg",
        "image/png":  ".png",
        "video/mp4":  ".mp4",
        "application/pdf": ".pdf",
    }
    ext = ext_map.get(content_type, "")

    creds  = service_account.Credentials.from_service_account_file(GCP_SA_KEY)
    client = storage.Client(project=GCP_PROJECT, credentials=creds)
    bucket = client.bucket(BUCKET_NAME)

    blob_name = f"uploads/{uuid.uuid4().hex}{ext}"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data, content_type=content_type)
    print(f"Uploaded media to GCS: https://storage.googleapis.com/{BUCKET_NAME}/{blob_name}")
    return f"https://storage.googleapis.com/{BUCKET_NAME}/{blob_name}"


async def process_whatsapp_media_payload(payload: dict) -> str:
    """
    Given the full webhook payload dict, extracts the media ID (image or video),
    downloads it, uploads to GCS, and returns the public GCS URL.
    """
    # Drill into the payload to find the message and media ID
    message = (
        payload.get("entry", [{}])[0]
               .get("changes", [{}])[0]
               .get("value", {})
               .get("messages", [{}])[0]
    )
    media_id = None
    msg_type = message.get("type")
    if msg_type == "image":
        media_id = message.get("image", {}).get("id")
    elif msg_type == "video":
        media_id = message.get("video", {}).get("id")
    elif msg_type == "document":
        media_id = message.get("document", {}).get("id")
    else:
        raise ValueError(f"Unsupported media type: {msg_type}")

    if not media_id:
        raise ValueError("No media ID found in the payload.")

    # Download & upload
    data, content_type = await download_whatsapp_media(media_id)
    if data == "❌ The file size exceeds the maximum limit of 10 MB. Please try again with a smaller file. 📷":
        return "❌ The file size exceeds the maximum limit of 10 MB. Please try again with a smaller file. 📷"
    public_url = upload_to_gcs_from_bytes(data, content_type)
    return public_url
