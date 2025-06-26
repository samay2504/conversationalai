import os
import logging
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from tenacity import retry, wait_exponential, stop_after_attempt
from dotenv import load_dotenv
import asyncio
from error_logger import log_to_openobserve
import traceback
import requests
from pymongo import MongoClient
import base64
from datetime import datetime, timedelta
import hashlib
from bson.objectid import ObjectId

load_dotenv()
logging.basicConfig(filename='mongodb_handler_errors.log',
                    level=logging.ERROR,
                    format='%(asctime)s %(levelname)s: %(message)s')

# Retrieve MongoDB connection string from environment or default to the Atlas URI
MONGODB_CONNECTION_STRING = os.environ.get(
    "MONGODB_CONNECTION_STRING",
    "mongodb+srv://gogizmo:root@cluster.akp9e.mongodb.net/?retryWrites=true&w=majority&appName=Cluster"
)

try:
    # Create a MongoDB client using the connection string and the latest stable server API version
    client = MongoClient(MONGODB_CONNECTION_STRING, server_api=ServerApi("1"))
    
    # Access the database used by your bot
    db = client.get_database("nashermiles_db")
    # Initialize GridFS using the database's fs bucket
    fs = db.fs
    print(db)
    # Verify connection by sending a ping command
    client.admin.command("ping")
    print("Successfully connected to MongoDB Atlas!",MONGODB_CONNECTION_STRING)
    logging.info("Successfully connected to MongoDB Atlas!")
except Exception as e:
    asyncio.run(log_to_openobserve("mongodb_handler",traceback.format_exc,level="critical"))
    logging.error("Failed to connect to MongoDB Atlas", exc_info=True)
    raise


def get_db():
    """
    Returns the MongoDB database instance.
    """
    logging.debug("Retrieving MongoDB database instance.")
    return db


def get_collection(collection_name: str):
    """
    Returns a specific collection from the MongoDB database.
    
    Args:
        collection_name (str): Name of the collection to retrieve.
    
    Returns:
        pymongo.collection.Collection: The MongoDB collection object.
    """
    logging.debug(f"Retrieving collection: {collection_name}")
    return db[collection_name]


def get_visits_collection():
    """
    Returns the 'user_visits' collection.
    """
    logging.debug("Retrieving 'user_visits' collection.")
    return db["user_visits"]


def get_context_collection():
    """
    Returns the 'user_contexts' collection.
    """
    logging.debug("Retrieving 'user_contexts' collection.")
    return db["user_contexts"]


def get_daily_user_count_collection():
    """
    Returns the 'daily_user_count' collection.
    """
    logging.debug("Retrieving 'daily_user_count' collection.")
    return db["daily_user_count"]


def get_unique_users_collection():
    """
    Returns the 'unique_users' collection.
    """
    logging.debug("Retrieving 'unique_users' collection.")
    return db["unique_users"]

def get_metrics_collection():
    """
    Returns the 'metric' collection.
    """
    logging.debug("Retrieving 'metric' collection.")
    return db["metric"]

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3), reraise=True)
def update_document(collection_name: str, filter_query: dict, update_values: dict, upsert: bool = False):
    """
    Updates a single document in the specified collection, retrying on failure.

    Args:
        collection_name (str): The name of the collection.
        filter_query (dict): The filter to find the document.
        update_values (dict): The updates to apply (e.g., {"$set": {"field": value}}).
        upsert (bool): If True, insert the document if it does not exist.

    Returns:
        pymongo.results.UpdateResult: The result from the update_one operation.
    """
    logging.debug(
        f"Attempting to update a document in '{collection_name}' with filter: {filter_query} and update: {update_values}"
    )
    collection = get_collection(collection_name)
    result = collection.update_one(filter_query, update_values, upsert=upsert)
    logging.info(f"update_document in '{collection_name}' modified_count: {result.modified_count}")
    return result


@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3), reraise=True)
def update_documents(collection_name: str, filter_query: dict, update_values: dict, upsert: bool = False):
    """
    Updates multiple documents in the specified collection, retrying on failure.

    Args:
        collection_name (str): The name of the collection.
        filter_query (dict): The filter to find the documents.
        update_values (dict): The updates to apply.
        upsert (bool): If True, insert the document if it does not exist.

    Returns:
        pymongo.results.UpdateResult: The result from the update_many operation.
    """
    logging.debug(
        f"Attempting to update multiple documents in '{collection_name}' with filter: {filter_query} and update: {update_values}"
    )
    collection = get_collection(collection_name)
    result = collection.update_many(filter_query, update_values, upsert=upsert)
    logging.info(f"update_documents in '{collection_name}' modified_count: {result.modified_count}")
    return result

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3), reraise=True)
def update_metrics(metrics: dict):
    """
    Updates the metrics collection to store lead type counts.

    Args:
        metrics (dict): The dictionary of lead type counts to update.
    """
    collection = get_metrics_collection()
    filter_query = {"_id": "lead_counts"}
    update_values = {"$set": {"counts": metrics}}
    result = collection.update_one(filter_query, update_values, upsert=True)
    logging.info(f"update_metrics modified_count: {result.modified_count}")

def store_image_from_url(url: str) -> str:
    """
    Downloads an image from a URL and stores it in MongoDB's GridFS.
    Returns the file ID as a string.
    """
    try:
        # Generate a unique filename based on URL and timestamp
        filename = hashlib.md5(f"{url}{datetime.now().timestamp()}".encode()).hexdigest()
        
        # Download the image with proper headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        
        # Store in GridFS
        file_id = fs.files.insert_one({
            'filename': filename,
            'contentType': response.headers.get('Content-Type', 'application/octet-stream'),
            'uploadDate': datetime.now(),
            'source_url': url
        }).inserted_id
        
        # Store the actual file data
        fs.chunks.insert_one({
            'files_id': file_id,
            'n': 0,
            'data': response.content
        })
        
        return str(file_id)
    except Exception as e:
        logging.error(f"Error storing image from URL {url}: {str(e)}")
        raise

def get_image_by_id(file_id: str) -> bytes:
    """
    Retrieves an image from GridFS by its ID.
    """
    try:
        if isinstance(file_id, str):
            file_id = ObjectId(file_id)
            
        # Get the file metadata
        file_data = fs.files.find_one({'_id': file_id})
        if not file_data:
            raise FileNotFoundError(f"File with ID {file_id} not found")
            
        # Get the file chunks
        chunk = fs.chunks.find_one({'files_id': file_id})
        if not chunk:
            raise FileNotFoundError(f"File chunks not found for ID {file_id}")
            
        return chunk['data']
    except Exception as e:
        logging.error(f"Error retrieving image with ID {file_id}: {str(e)}")
        raise

def delete_image(file_id: str) -> bool:
    """
    Deletes an image from GridFS.
    """
    try:
        if isinstance(file_id, str):
            file_id = ObjectId(file_id)
            
        # Delete file metadata and chunks
        fs.files.delete_one({'_id': file_id})
        fs.chunks.delete_many({'files_id': file_id})
        return True
    except Exception as e:
        logging.error(f"Error deleting image with ID {file_id}: {str(e)}")
        return False

def cleanup_old_images(hours: int = 24):
    """
    Cleans up images older than the specified number of hours.
    """
    try:
        cutoff_time = datetime.now() - timedelta(hours=hours)
        old_files = fs.files.find({'uploadDate': {'$lt': cutoff_time}})
        for file in old_files:
            delete_image(file['_id'])
    except Exception as e:
        logging.error(f"Error during cleanup of old images: {str(e)}")