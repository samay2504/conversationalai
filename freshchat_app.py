import logging
import traceback
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv
import json
from typing import Optional

# Load environment variables
load_dotenv()

# Configure logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/freshchat_app.log"),
        logging.StreamHandler()
    ],
    force=True
)

app = FastAPI()

# --- FILTER OPTIONS ---
# Set to None to log all conversations, or set to a specific conversation_id to filter
FILTER_CONVERSATION_ID = None  # e.g., "e95dc313-16eb-4e61-8506-0518c8973095"
# Set to a list of message types to filter (e.g., ["text", "image", "video", "document/pdf"]), or None for all
FILTER_MESSAGE_TYPES = None  # e.g., ["image", "document"]

@app.post("/webhook/freshchat")
async def freshchat_webhook(request: Request):
    """
    Endpoint to receive Freshchat webhook events.
    Logs and prints all payloads. Allows filtering by conversation_id and message type.
    """
    try:
        payload = await request.json()
        # Debug log to see the actual payload structure
        logging.info("Debug - Full payload structure:")
        print("Debug - Full payload structure:")
        logging.info(json.dumps(payload, indent=2))
        print(json.dumps(payload, indent=2))
        
        # Extract conversation_id for filtering
        conversation_id = payload.get("data", {}).get("message", {}).get("conversation_id")
        message_parts = payload.get("data", {}).get("message", {}).get("message_parts", [])
        message_type = None
        media_urls = []
        # Extract platform information from freshchat_channel_id
        channel_id = payload.get("data", {}).get("message", {}).get("freshchat_channel_id")
        # Log channel ID for discovery
        logging.info(f"Channel ID: {channel_id}")
        print(f"Channel ID: {channel_id}")
        # Map channel IDs to platform names
        platform_map = {
            "107282": "WhatsApp",
            "86791": "WhatsApp1",
            "97619": "Instagram",
            "97921": "Email",
            # Add more channel IDs and their corresponding platforms as needed
        }
        platform = platform_map.get(channel_id, f"Channel {channel_id}")
        # Determine message type and collect media URLs if present
        for part in message_parts:
            if "text" in part:
                message_type = "text"
            elif "image" in part:
                message_type = "image"
                media_urls.append(part["image"].get("url"))
            elif "video" in part:
                message_type = "video"
                media_urls.append(part["video"].get("url"))
            elif "document" in part:
                message_type = "document"
                media_urls.append(part["document"].get("url"))
            # Add more types as needed

        # --- FILTERING LOGIC ---
        # Uncomment to filter by a specific conversation_id
        # if conversation_id != "e95dc313-16eb-4e61-8506-0518c8973095":
        #     return JSONResponse(content={"status": "ignored (filtered by conversation_id)"}, status_code=200)
        if FILTER_CONVERSATION_ID and conversation_id != FILTER_CONVERSATION_ID:
            return JSONResponse(content={"status": "ignored (filtered by conversation_id)"}, status_code=200)
        if FILTER_MESSAGE_TYPES and message_type not in FILTER_MESSAGE_TYPES:
            return JSONResponse(content={"status": "ignored (filtered by message_type)"}, status_code=200)

        # Log and print the full payload
        logging.info(f"Platform: {platform}")
        print(f"Platform: {platform}")
        logging.info(f"Received payload: {json.dumps(payload, indent=2)}")
        print(f"Received payload: {json.dumps(payload, indent=2)}")
        if message_type:
            logging.info(f"Message type: {message_type}")
            print(f"Message type: {message_type}")
        if media_urls:
            for url in media_urls:
                logging.info(f"Media URL: {url}")
                print(f"Media URL: {url}")
        # Add more detailed inspection as needed

        return JSONResponse(content={"status": "received"}, status_code=200)
    except Exception as e:
        logging.error(f"Error processing Freshchat webhook: {e}\n{traceback.format_exc()}")
        print(f"Error processing Freshchat webhook: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/healthz")
async def health_check():
    """
    Simple health check to verify the service is running.
    """
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    logging.info("Starting Freshchat FastAPI app on port 8000")
    print("Starting Freshchat FastAPI app on port 8000")
    print("If using ngrok, run: ngrok http --hostname=beagle-rich-closely.ngrok-free.app 8000")
    uvicorn.run("freshchat_app:app", host="0.0.0.0", port=8000, log_config=None) 
    # uvicorn freshchat_app:app --reload --host 0.0.0.0 --port 8000