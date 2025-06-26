import os
import uuid
import requests
from urllib.parse import urlparse
from google.cloud import storage
from google.oauth2 import service_account
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv

load_dotenv()

# === CONFIG ===
GCP_SA_KEY  = "bucket_cred.json"
GCP_PROJECT = "fluent-oarlock-461903-h3"
BUCKET_NAME = "nashermiles2"

os.environ["GOOGLE_API_KEY"] = os.getenv("GOOGLE_API_KEY")
gemini = ChatGoogleGenerativeAI(
    model="models/gemini-2.0-flash",
    temperature=0.0,
    google_api_key=os.getenv("GOOGLE_API_KEY")
)

# === HELPER ===
def upload_to_gcs_public(url: str) -> str:
    """
    Download the given URL (image or PDF),
    upload to GCS under uploads/, and return its public URL.
    """
    # 1) fetch
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.content
    ctype = resp.headers.get("Content-Type", "").split(";")[0] or "application/octet-stream"

    # 2) choose extension
    ext_map = {
        "image/jpeg": ".jpg",
        "image/png":  ".png",
        "application/pdf": ".pdf",
    }
    ext = ext_map.get(ctype, os.path.splitext(urlparse(url).path)[-1] or "")

    # 3) upload
    creds  = service_account.Credentials.from_service_account_file(GCP_SA_KEY)
    client = storage.Client(project=GCP_PROJECT, credentials=creds)
    bucket = client.bucket(BUCKET_NAME)

    blob_name = f"uploads/{uuid.uuid4().hex}{ext}"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data, content_type=ctype)
    # no ACL needed if bucket is public via IAM
    return f"https://storage.googleapis.com/{BUCKET_NAME}/{blob_name}"

# === VALIDATOR ===
def validate_nashermiles_invoice(doc_url: str,try_count:int=0) -> str:
    """
    Validate an invoice (image or PDF) by uploading it to GCS,
    then feeding to Gemini as image_url or file_url.
    """
    public_url = doc_url
    prompt = (
        "You are validating an invoice document \n"
        "Respond with ONLY one word: VALID or INVALID.\n"
        "Look specifically for “NasherMiles” branding or invoice structure."
        "It should look like a valid invoice"
    )
    system = SystemMessage(content="You are a document validator. Reply VALID or INVALID.")

    # Detect based on extension
    if public_url.lower().endswith(".pdf"):
        part = {"type": "file",     "file_url": public_url}
    else:
        part = {"type": "image_url","image_url": public_url}

    user = HumanMessage(content=[
        {"type":"text", "text": prompt},
        part
    ])

    resp = gemini.invoke([system, user])
    return "valid" if resp.content.strip().upper().startswith("VALID") else "invalid"


def validate_admission_card(img_url: str,try_count:int=0) -> str:
    """
    Validate an admission card (image or PDF) by uploading it to GCS,
    then feeding to Gemini as image_url or file_url.
    """
    public_url = img_url
    prompt = (
        "You are validating an admission card document or admission letter\n"
        "It should be a valid admission card of some school or college or university or maybe valid school or college or university admission letter"
        "Respond with ONLY one word: VALID or INVALID.\n"
    )
    system = SystemMessage(content="You are a document validator. Reply VALID or INVALID.")
    # Detect based on extension
    if public_url.lower().endswith(".pdf"):
        part = {"type": "file",     "file_url": public_url}
    else:
        part = {"type": "image_url","image_url": public_url}

    user = HumanMessage(content=[
        {"type":"text", "text": prompt},
        part
    ])
    resp = gemini.invoke([system, user])
    return "valid" if resp.content.strip().upper().startswith("VALID") else "invalid"

# === CLI ENTRYPOINT ===
if __name__ == "__main__":
    url = input("Enter document URL (image or PDF): ").strip()
    result = validate_admission_card(url)
    print("Result:", result)
