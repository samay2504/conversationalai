from huey import RedisHuey
import redis
from datetime import datetime
from leads_gsheet_without_number import add_lead_without_phone_number
from mongodb_handler import get_context_collection # Assuming this can be initialized in Huey worker
from insta_api import send_reply as send_insta_reply
import random
import asyncio # For asyncio.run
import traceback

import re
huey = RedisHuey('my_app', host='localhost', port=6379, db=0)
track = redis.Redis(host='localhost', port=6379, decode_responses=True)

@huey.signal()
def on_success(signal, task, **kwargs):
    if task.args:
        user_id = task.args[0]
        tag = None
        if task.name == 'send_reply':
            tag = task.args[1]
            print(f"sendTask {tag} for user {user_id} completed")
        elif task.name == 'job_10min':
            tag = '10min'
            print(f"Task {tag} for user {user_id} completed")
        if tag:
            track.hdel(f"user:{user_id}:tasks", tag)
        

PHONE_REQUEST_MESSAGES = [
    "Thanks for your interest! To assist you better, could you please provide your phone number?",
    "We're excited to connect! What's the best phone number to reach you for a quick chat?",
    "To offer you our best service, may we have your phone number?",
    "Hello! Could you share your phone number so we can follow up with more details?",
]
SHEET_ID_FOR_LEADS = "1PqE7K-L9Yj2pASwYQOM3pPwtXIl_vf4BpQaWfNIhTws"

@huey.task()
def job_10min(user_id):
    print("--------------------------------\n")
    print(datetime.now())
    print(f"[10min] Special task for user {user_id}")

    try:
        # 1. Get user details from MongoDB
        collection = get_context_collection()
        user_doc = collection.find_one({"_id": user_id})

        if not user_doc:
            print(f"[10min] User {user_id} not found in MongoDB.")
            return

        context_data = user_doc.get("context_data", {})
        name = context_data.get("name", "")
        insta_handle = context_data.get("instagram_username", "")
        followers = context_data.get("followers", None)
        following = context_data.get("following", None)
        conversation_history = context_data.get("conversation_history", [])
        #get query from conversation history by iterating through the conversation history and getting the last message
        user_query = ""
        for entry in conversation_history:
            if entry['role'] == 'user':
                message_content = entry['content']
                # This regex removes patterns like "[timestamp] ", "(timestamp) ", or "DD-MM-YYYY HH:MM:SS : " from the start.
                cleaned_content = re.sub(r"^(?:(\[.*?\]|\(.*?\))|\d{2}-\d{2}-\d{4}\s\d{2}:\d{2}:\d{2}\s*:)\s*", "", message_content)
                user_query += f"{cleaned_content},"
            elif entry['role'] == 'bot':
                continue

        # 2. Add lead to Google Sheet

        print(f"[10min] Adding lead for user {user_id} to Google Sheet ID: {SHEET_ID_FOR_LEADS}.")
        add_lead_without_phone_number(
            id=user_id,
            name=name,
            insta_handle=insta_handle,
            followers=followers,
            following=following,
            query=user_query,
            lead_status="Open",
            sheet_id="1PqE7K-L9Yj2pASwYQOM3pPwtXIl_vf4BpQaWfNIhTws"
        )
        print(f"[10min] Lead for user {user_id} added to sheet.")

        

    except Exception as e:
        print(f"[10min] Error processing task for user {user_id}: {e}")
        traceback.print_exc()

@huey.task()
def send_reply(user_id, tag):

    # 3. Send message to user asking for phone number
    selected_message = random.choice(PHONE_REQUEST_MESSAGES)
    print(f"[10min] Sending phone request to {user_id}: {selected_message}")
    asyncio.run(send_insta_reply(user_id, selected_message))
    print(f"[10min] Phone request message sent to {user_id}.")
