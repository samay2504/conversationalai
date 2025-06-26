import logging
import traceback
import asyncio
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from collections import defaultdict
import os
import sys
from contextlib import asynccontextmanager
import rsa
import base64
from dotenv import load_dotenv
from redis.asyncio import Redis
from pyngrok import ngrok

load_dotenv()

FRESHCHAT_PUBLIC_KEY = (
    "-----BEGIN RSA PUBLIC KEY-----"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqpnuxTRHwDiEs86NHbxD"
    "BxKT3kTHE1jJXBf8PIJMsL9fLTUHm8YlpW3sJ1moQREnr5uS2Wn3ndYbwTNJWqry"
    "cGdd+VxCnvl8XK/xnqr3G2zAdlyOEXHz9uoq4j9/dtjWGLBk0qFmP51VhEePbnxX"
    "2WofHLXgJi/s+Gq3qR87ngKHfufh1urQ6c4cXbSn3dm/UpiozOv4peG4fxodggRi"
    "NC8sPuZI5iJ5ML3vOu+fCVpbTGiUCIcuK23tRPfdLeQMM4rAGJEwcuej5tZW28Iy"
    "RKd8QPRRkSVICrOv5H2kiEPvqsrMv7BwSkAfFxdtaXxxVdaBKC49vFwQmChfq/aT"
    "4wIDAQAB"
    "-----END RSA PUBLIC KEY-----"
).replace("\n", "")

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/freshchat_webhook.log"),
        logging.StreamHandler()
    ],
    force=True
)
logging.getLogger("pyngrok").setLevel(logging.ERROR)

redis = Redis(host="localhost", port=6379, decode_responses=True)
public_ngrok_url = None

def load_rsa_public_key(pem_str: str) -> rsa.PublicKey:
    """
    Try to load a PEMencoded RSA public key. First attempt PKCS#1 (-----BEGIN RSA PUBLIC KEY-----),
    then fall back to OpenSSL PEM (-----BEGIN PUBLIC KEY-----). If both fail, raises ValueError.
    """
    pem_bytes = pem_str.encode()
    try:
        # Try PKCS#1 format first:
        return rsa.PublicKey.load_pkcs1(pem_bytes)
    except Exception as e1:
        try:
            # Try OpenSSL PEM “PUBLIC KEY” format:
            return rsa.PublicKey.load_pkcs1_openssl_pem(pem_bytes)
        except Exception as e2:
            raise ValueError(
                f"Failed to load RSA key as PKCS#1 or OpenSSL PEM.\n"
                f"PKCS#1 error: {e1}\n"
                f"OpenSSL PEM error: {e2}"
            )

@asynccontextmanager
async def lifespan(app: FastAPI):
    global public_ngrok_url
    print("Starting lifespan context...")
    yield  # This is where the app starts
    print("Ending lifespan context...")
    # Shutdown logic here

app = FastAPI()

@app.post("/webhook/freshchat")
async def freshchat_webhook(
    request: Request,
    x_freshchat_signature: str = Header(None),
    x_freshchat_payload_version: str = Header(None),
    x_retry_count: str = Header("0")
):
    """
    Endpoint to receive Freshchat webhook events.
    - If a valid RSA_PUBLIC_KEY_OBJ was loaded, verifies X-Freshchat-Signature.
    - Otherwise, logs a warning and skips verification.
    """
    payload = await request.json()
    conversation_id = payload.get("data", {}).get("message", {}).get("conversation_id", None)
    if conversation_id== "e95dc313-16eb-4e61-8506-0518c8973095":
        print(f"Received payload: {payload}")  # Debugging line
    
    # 6. Respond quickly with 200 OK
    return JSONResponse(content={"status": "received"}, status_code=200)

@app.get("/healthz")
async def health_check():
    """
    Simple health check to verify the service is running.
    """
    try:
        pong = await redis.ping()
        redis_status = "alive" if pong else "no response"
    except:
        redis_status = "error"
    return {
        "status": "ok",
        "redis": redis_status,
        "ngrok_url": public_ngrok_url or "not_started"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="localhost", port=8000, reload=True)