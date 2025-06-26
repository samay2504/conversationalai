import os,logging
import traceback
import uuid
import json
from typing import Dict, List, Literal, TypedDict, Annotated, Optional
from langgraph.graph import END, START, StateGraph, MessagesState
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from rapidfuzz import process,fuzz
import re
from error_logger import log_to_openobserve
import asyncio
from spreadsheet import get_sheet_values,get_sheet_as_dataframe
from langsmith import traceable
from dotenv import load_dotenv
import datetime
from mongodb_handler import get_collection, update_document,get_context_collection
import random
from redis.asyncio import Redis
from typing import Optional, Dict
import requests
import logging
import pandas as pd
from math import radians, sin, cos, sqrt, atan2
from geopy.geocoders import Nominatim
from urllib.parse import quote_plus
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain.prompts import PromptTemplate
from langchain.chains import RetrievalQA
from spreadsheet import get_sheet_values
from gemini_live import validate_nashermiles_invoice
from redis import Redis
import os
from typing import Optional, Dict, Any, List
from langchain_core.tools import tool
from dotenv import load_dotenv
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from datetime import datetime
import urllib.parse
from datetime import datetime, timezone

load_dotenv()

# Constants (placeholders)
SHOP_NAME = os.getenv("SHOP_NAME")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOP_BASE_URL = f"https://{SHOP_NAME}.myshopify.com/admin/api/2025-04"
CLICKPOST_API_KEY = os.getenv("CLICKPOST_API_KEY", "ac299bb3-f8af-472d-8068-a0823e2d2ff4")
CLICKPOST_USERNAME = os.getenv("CLICKPOST_USERNAME", "nashermiles")
CLICKPOST_TRACK_API_URL = "https://api.clickpost.in/api/v2/track-order/"
FRESHDESK_API_KEY = os.getenv("FRESHDESK_API_KEY")
FRESHDESK_DOMAIN = os.getenv("FRESHDESK_DOMAIN")
FRESHDESK_BASE_URL = f"https://{FRESHDESK_DOMAIN}/api/v2"
# from scheduled_jobs_redis import cancel_all_jobs_for_user
# import shopify
redis_host = os.getenv("REDIS_HOST", "localhost")
redis = Redis(host=redis_host, port=6379, decode_responses=True)

# ──────────── HTTP session with retries ────────────────────────────────────────
def _init_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

_session = _init_session()
def update_email_in_context(user_id:str,email:str):
    context_collection=get_context_collection()
    context_collection.update_one({"_id":user_id},{"$set":{"context_data.email":email}})

def email_id_validation(email:str):
    if re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return True
    else:
        return f"❌ Invalid email address {email}. Please provide a valid email address. 📧"

def get_sheet_as_text(sheet_name: str, sheet_id: str = None, exclude_columns: list = None) -> list[str]:
    """
    Fetches data from Google Sheets and converts each row to a text string.
    
    Args:
        sheet_name (str): The name of the Google Sheet to fetch data from.
        sheet_id (str, optional): The ID of the Google Sheet. If not provided, uses the default ID.
        exclude_columns (list, optional): List of column names to exclude from the output.
        
    Returns:
        list[str]: A list where each element is a string representation of a row.
    """
    try:
        # Get sheet values
        values = get_sheet_values(sheet_name, sheet_id) if sheet_id else get_sheet_values(sheet_name)
        
        if not values or len(values) < 2:
            logging.warning("No data found or the sheet is empty.")
            return []
            
        headers = values[0]
        rows = values[1:]
        
        # Convert exclude_columns to indices
        exclude_indices = []
        if exclude_columns:
            exclude_indices = [i for i, h in enumerate(headers) if h in exclude_columns]
        
        # Filter out excluded columns
        filtered_headers = [h for i, h in enumerate(headers) if i not in exclude_indices]
        
        result = []
        for row in rows:
            # Create a dictionary for the row, excluding specified columns
            row_dict = {
                headers[i]: str(cell) 
                for i, cell in enumerate(row) 
                if i not in exclude_indices
            }
            # Convert to string in format "key: value" pairs
            row_text = " ".join([f"{k}: {v}" for k, v in row_dict.items()])
            result.append(row_text)
            
        return result
        
    except Exception as e:
        logging.error(f"Error processing sheet {sheet_name}: {str(e)}")
        return []
@tool
def get_detail_about_particular_discount(query: str) -> str:
    """
    Get the information about a particular discount running in Nasher Miles example:
    New Login Offer,Welcome Offer,Rishabh Pant,Student Discount,Bank Offer
    Args:
        query (str): The user's query about discounts
    Returns:
        str: A concise response about the current discounts
    """
    try:
       
            
        llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash-8b-latest", temperature=0.4)
        
        # Create a concise prompt
        prompt = f"""
        You are Nasher Miles' discount assistant. 
        New Login Offer
        Login & Get Flat 200 Off
        Discount Code: NMFAM200
        Minimum Cart Value Rs. 1,999/-
        Prepaid Discount of 5% (Upto 200/-) auto-applied at the checkout page.


        Welcome Offer
        Query related to Welcome Offer
        Answer should be like this:
        5% Off for 1st time buyer
        Discount Code: Welcome-5
        Minimum Cart Value Rs. 2,499/-
        (Maximum Discount Rs. 500/-)
        Prepaid Discount of 5%(Upto 200/-) auto-applied at the checkout page.


        Query related to Rishabh Pant
        Answer should be like this:
        7% Off on Rishabh Pant Collection 
        Discount Code: Welcome-5
        Minimum Cart Value Rs. 7,999/-
        (Maximum Discount Rs. 1,000/-)
        Prepaid Discount of 5% (Upto 200/-) auto-applied at the checkout page.

        Query related to Student Discount
        Answer should be like this:
        Get 15% discount exclusively for students.
        Submit your Admission Letter copy or Identity Card.
        Our team will issue a Special Discount code to you.

        Query related to Bank Offer
        Answer should be like this:
        15% Off for Scapia Card Holders
        Discount Code: NMSCAPIA
        Minimum Cart Value Rs. 2,499/-
        Note: Offer Valid till 30.06.2025
        
        Query: {query}
        
        Respond with exact answer given in the context. Use emoji in the response.
        """
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content
    except Exception as e:
        log_to_openobserve(f"Error in get_discount: {str(e)}")
        return "Error retrieving discount information. Please try again later."

@tool
def get_all_discount(user_id: str,query: str) -> str:
    """
    Get the information about all the current discounts. When the customer is asking about all the discounts not about a particular discount use this tool.
    Example:
    "What are the discount","Any discount","Any Promo Code","Current Discount", "Current offers
    
    Args:
        user_id (str): The user's ID(do not ask for it automatically injected never ask for it)
        query (str): The user's query about discounts
    
    Returns:
        str: A response to ask the user to select the discount they want to know about
    """
    try:
        redis.set(f"{user_id}_discount","True")
        return "Please select the discount you want to know about"
    except Exception as e:
        log_to_openobserve(f"Error in get_discount: {str(e)}")
        return "Error retrieving discount information. Please try again later."


@tool
def influencer_collaboration() -> str:
    """
    Handle influencer collaboration inquiries related to Nasher Mile    
    Returns:
        str: Response for influencer collaborations
    """
    return "Please email falak@nashermiles.com for influencer collaborations."

# -------------------------------------------------
#  Helper: Shorten a URL using a URL shortening service
# -------------------------------------------------
def shorten_url(url:str) -> str:
    """
    Shorten a URL using a URL shortening service
    """
    try:
        # Use a URL shortening service API
        response = requests.get(f"https://tinyurl.com/api-create.php?url={url}")
        response.raise_for_status()
        return response.text
    except Exception as e:
        logging.error(f"Error shortening URL: {str(e)}")
        return "Error shortening URL. Please try again later."
    
# -------------------------------------------------
#  Helper: Fetch latest status + EDD from ClickPost
# -------------------------------------------------
def get_clickpost_tracking_summary(
    tracking_number: str,
    clickpost_user: str,
    clickpost_key: str,
    carrier_partner_id: Optional[str] = None
) -> str:
    """
    Returns a human-friendly, multi-sentence summary including:
      - AWB number
      - Current checkpoint (status, location, timestamp)
      - Any remark
      - Estimated Delivery Date (EDD) if available
      - Special note if the package is already delivered
    """
    if not tracking_number:
        return "ClickPost: No tracking number provided."

    # Determine which cp_id to send (either explicitly passed or fall back to username)
    cp_id_to_use = carrier_partner_id if carrier_partner_id else clickpost_user

    params = {
        "key": clickpost_key,
        "username": clickpost_user,
        "waybill": tracking_number,
        "cp_id": cp_id_to_use
    }
    logging.info(f"ClickPost GET → AWB={tracking_number}, cp_id={params['cp_id']}")

    try:
        resp = requests.get(CLICKPOST_TRACK_API_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        meta = data.get("meta", {})
        if not meta.get("success", False):
            err = meta.get("message", "Unknown error from ClickPost")
            logging.warning(f"ClickPost API returned error for {tracking_number}: {err}")
            return f"ClickPost: Error fetching details – {err}"

        # The "result" object is keyed by the AWB number(s) we passed
        result_data = data.get("result", {})
        shipment_info = result_data.get(tracking_number, {})

        # 1️⃣ Extract the latest checkpoint
        latest = shipment_info.get("latest_status", {})
        if not latest:
            return f"ClickPost (AWB: {tracking_number}): No tracking information is available yet."

        status_raw = latest.get("status", "").lower()
        status_desc = latest.get("clickpost_status_description", status_raw or "Status unknown")
        location = latest.get("location", "Location unknown")
        timestamp = latest.get("timestamp", "Timestamp unknown")
        remark = latest.get("remark", "").strip()

        # Build a base sentence about current checkpoint
        checkpoint_sentence = (
            f"Shipment (AWB: {tracking_number}) is currently {status_desc.title()} "
            f"at {location} as of {timestamp}."
        )
        if remark:
            checkpoint_sentence += f" Remark: {remark}."

        # 2️⃣ Handle "Delivered" as a special case
        if status_raw in ["delivered", "delivered to recipient"]:
            return f"{checkpoint_sentence} This package has already been delivered."

        # 3️⃣ Extract the EDD (if provided under "additional" → "courier_partner_edd")
        additional = shipment_info.get("additional", {})
        edd = additional.get("courier_partner_edd")
        if edd:
            return f"{checkpoint_sentence} Estimated Delivery: {edd}."
        else:
            return f"{checkpoint_sentence} Estimated Delivery: Not available."

    except requests.exceptions.HTTPError as e:
        # Attempt to get a succinct error message from the response body
        err_text = "Unknown"
        if e.response is not None:
            try:
                err_text = e.response.json().get("meta", {}).get("message", e.response.text)
            except ValueError:
                err_text = e.response.text
        logging.error(f"ClickPost HTTPError for {tracking_number} (cp_id={cp_id_to_use}): {err_text}")
        return (
            f"ClickPost: Could not connect (HTTP {e.response.status_code if e.response else 'Unknown'}). "
            f"Details: {err_text}"
        )
    except requests.exceptions.RequestException as e:
        logging.error(f"ClickPost network error for {tracking_number}: {e}")
        return "ClickPost: Network error while contacting service."
    except Exception as e:
        logging.exception(f"Unexpected error in get_clickpost_tracking_summary for {tracking_number}: {e}")
        return "ClickPost: Unexpected error processing tracking data."


# ---------------------------------------------------
#  Helper: Extract cp_id from a ClickPost tracking URL
# ---------------------------------------------------
def get_parsed_cp_id_from_url(tracking_url: Optional[str]) -> Optional[str]:
    if not tracking_url:
        return None
    try:
        parsed = urllib.parse.urlparse(tracking_url)
        qs = urllib.parse.parse_qs(parsed.query)
        cp_list = qs.get("cp_id")
        if cp_list and cp_list[0]:
            logging.info(f"Extracted cp_id={cp_list[0]} from URL.")
            return cp_list[0]
    except Exception as e:
        logging.warning(f"Failed to parse cp_id from '{tracking_url}': {e}")
    return None


# ---------------------------------------------------------------------
#  Formats a single-line status message for one fulfillment (if shipped)
# ---------------------------------------------------------------------
def format_fulfillment_status(
    fulfillment: Dict[str, Any],
    clickpost_user_param: str,
    clickpost_api_key_param: str
) -> str:
    """
    If this fulfillment has a tracking number, calls get_clickpost_tracking_summary.
    Otherwise, returns an empty string.
    """
    tn = fulfillment.get("tracking_number")
    if not tn:
        return ""

    # Try to extract cp_id from tracking_urls or tracking_url
    extracted_cp_id = None
    t_urls = fulfillment.get("tracking_urls", []) or []
    if t_urls:
        extracted_cp_id = get_parsed_cp_id_from_url(t_urls[0])
    elif fulfillment.get("tracking_url"):
        extracted_cp_id = get_parsed_cp_id_from_url(fulfillment.get("tracking_url"))
    if t_urls or fulfillment.get("tracking_url"):
        tracking_link_short=shorten_url(t_urls[0])
        status_order=get_clickpost_tracking_summary(
            tn,
            clickpost_user_param,
            clickpost_api_key_param,
            extracted_cp_id
        )
        llm=ChatGoogleGenerativeAI(model="gemini-1.5-flash-8b-latest", temperature=0.1)
        prompt=f"""
        Rewrite the order status in a more friendly way, Keep it sementically same to original. Don't change the meaning of the status. Use emoji
        Order Status: {status_order}
        """
        response=llm.invoke([HumanMessage(content=prompt)])
        status_order=response.content
        return status_order+f"\n\nYou can track your order here: {tracking_link_short}"
    else:
        return ""

# -------------------------------------------------------------
#  Main: Return only a single {"message": "..."} describing status
# -------------------------------------------------------------
@tool
def where_is_my_order(
    user_id: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    order_number: Optional[str] = None
) -> Dict[str, str]:
    """
    Use this tool to check the status or tracking details of your order.
    It can be used to check the status of the order or track the order by providing either order number or email or phone number.
    Args:
        user_id (str): The user ID(available in the context)
        email (str): The customer's email.
        phone (str): The customer's phone number.
        order_number (str): The order number starting with "NM"
    Returns:
        str: A response with the status or tracking details of the order does not offer to reschedule the order.
    """
    if email:
        update_email_in_context(user_id,email)
    if not SHOP_NAME or not ACCESS_TOKEN:
        logging.error("SHOP_NAME or SHOPIFY_ACCESS_TOKEN is not set.")
        return "Shopify integration is not configured. Please contact support."


    base_url = f"https://{SHOP_NAME}.myshopify.com/admin/api/2025-04"
    headers = {"X-Shopify-Access-Token": ACCESS_TOKEN}

    try:
        # 1. Identify which order to fetch
        current_order = None
        searched_by_contact = False

        if order_number:
            # Fetch by exact order name (e.g., "NM1234")
            params = {"name": order_number, "status": "any"}
            resp = requests.get(f"{base_url}/orders.json", headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            orders_data = resp.json().get("orders", [])
            if not orders_data:
                return f"No order found with ID '{order_number}'. Please double-check."
            current_order = orders_data[0]

        elif email or phone:
            searched_by_contact = True
            params: Dict[str, Any] = {"status": "any"}
            desc_parts: List[str] = []

            if email:
                params["email"] = email
                desc_parts.append(f"email: {email}")
            if phone:
                desc_parts.append(f"phone: {phone}")
                # Shopify may return many; filter locally
            resp = requests.get(f"{base_url}/orders.json", headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            orders = resp.json().get("orders", [])

            if phone and orders:
                # Normalize and filter by any matching phone in customer/billing/shipping
                normalized_query = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
                filtered = []
                for o in orders:
                    for field_path in ["customer.phone", "billing_address.phone", "shipping_address.phone"]:
                        val = o
                        for key in field_path.split("."):
                            val = val.get(key) if isinstance(val, dict) else None
                        if val and normalized_query in val.replace(" ", "").replace("-", "").replace("(", "").replace(")", ""):
                            filtered.append(o)
                            break
                orders = filtered

            if not orders:
                crit = " and ".join(desc_parts) or "provided criteria"
                return f"No orders found with {crit}."

            # Pick the most recently created order
            orders.sort(key=lambda o: o.get("created_at", ""), reverse=True)
            current_order = orders[0]

        else:
            return "Please provide an order ID (e.g., NM1234), or an email, or a phone number."

        # 2. Build the status message
        fulfillment_status = (current_order.get("fulfillment_status") or "unfulfilled").lower()
        message = ""
        if current_order.get("financial_status") == "refunded"  :
            message = f"💸 This order {current_order.get('name')} has been refunded. 💳 Please check your payment method for the refund amount. 💰"
            return message
        elif current_order.get("cancelled_at"):
            message = f"🚫 This order {current_order.get('name')} has been cancelled at {current_order.get('cancelled_at')} . 🚫 Please check your order details for more information. 📄"
            return message
        elif fulfillment_status in ["fulfilled", "partial"]:
            # Find the first fulfillment with a tracking number
            fulfillments = current_order.get("fulfillments", [])
            status_line = ""
            for f in fulfillments:
                cs = format_fulfillment_status(f, CLICKPOST_USERNAME, CLICKPOST_API_KEY)
                if cs:
                    status_line = cs
                    
                    break

            if not status_line:
                # Shipped but no tracking yet
                message = "🚚 Your order has been shipped. Tracking details are not available yet. You will be receiving the tracking details shortly 📦"
            else:
                message = status_line

        else:
            message = "⚙️ Our team is working on your order. 📫 We'll notify you as soon as it's packed and ready for shipping! 🚚"

        # 3. If lookup was by email/phone, append a footer
        if searched_by_contact:
            message += " If you're looking for a different order, please mention the order ID starting with 'NM' 🚚."

        return message

    except requests.exceptions.HTTPError as e:
        err_msg = f"HTTP error ({e.response.status_code}) fetching order."
        logging.error(err_msg)
        return err_msg
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error: {e}")
        return "Network error while retrieving order. Please try again later."
    except Exception as e:
        logging.exception(f"Unexpected error in shopify_order_status: {e}")
        return "An unexpected error occurred. Our team is looking into it."

@tool
def do_it_yourself(query: str) -> str:
    """
    When the customer is interested to know a solution for their product which have some issue or wants to fix it themselves and shows the intent to do it themselves use this tool.
    Args:
        query (str): The customer's query.

    Returns:
        str: A response with a matching DIY video and step-by-step solution (if available),
    """
    try:
        llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash-8b-latest", temperature=0.4)
        text_diy=get_sheet_as_text("DIY")
        # Create a concise prompt
        prompt = f"""
        You are the DIY Support Assistant for **Nasher Miles**, here to help customers resolve product issues using our video guides.
        - Apologize for the inconvenience and ask the customer to watch the video and small steps to fix the issue.
        - Provide a clear solution based on the context.
        - Include relevant DIY video links.
        - End with a kind follow-up asking if the issue is now resolved.
        - Ensure each video link appears only once — do not include duplicate links.
        - Do no say that you are support assistant do not disclose any information about your identity.
        
        📝 Customer Query: {query}  
        📚 DIY Video Context: {text_diy}

        
        """

        
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content
    except Exception as e:
        log_to_openobserve(f"Error in do_it_yourself: {str(e)}")
        return "Error in do_it_yourself. Please try again later."

@tool
def product_issue_or_complain_registration_invoice_validation(url_of_invoice:str,try_count:int=1) -> str:
    """
    Validate the invoice and proceed to product issue or complain registration 
    Args:
        url_of_invoice (str): Invoice to be uploaded by customer(to be extracted out from the conversation history)
        try_count (int): Number of times the tool has been called. (increases by 1 after each call first time it's value is 1)
    Returns:
        str: A response related to product issue or complain registration
    """
    try:
        if validate_nashermiles_invoice(url_of_invoice) == "valid":
            
            return "Please upload the photo of the damaged product."
        else:
            if try_count == 4:
                return "Please upload the photo of the damaged product."
            return "❌ Invoice is invalid. Please upload a valid invoice. 📄 Please download the valid invoice from where you made the purchase and upload it again. 🔄"
    except Exception as e:
        log_to_openobserve(f"Error in product_issue_or_complain_registration_invoice_validation: {str(e)}")
        return "Error in product_issue_or_complain_registration_invoice_validation. Please try again later."

@tool
def product_issue_or_complain_registration(user_id:str,email_id:str,size_of_bag:str,url_of_invoice:str,url_of_photo_of_product:str,product_issue_or_complain_type:str) -> str:
    """
    When the customer is showing intent to register a complaint for a product use this tool.
    Example:
    "My wheel got damaged it's in warranty period"
    "My telescopic handle is not working"
    "My lock is not working I want to raise a complaint"
    Args:
        user_id (str): User's Unique Id(available in the context)
        email_id (str): Email ID of the customer used to place the order
        size_of_bag (str): (Small / Medium / Large)(to be asked from the customer)
        url_of_invoice(str): Invoice to be uploaded by customer(to be extracted out from the conversation history)(ask for it first)  
        url_of_photo_of_product(str): Photo of product to be uploaded by customer(to be extracted out from the conversation history)(ask for it after invoice)
        product_issue_or_complain_type (str): Wheel Issue/Telescopic Handle Issue/Lock Issue/Pick up Handle Issue/Zipper Issue/Stitching Issue/Logo Issue/Defective Product/Damaged Product/Missing Item Complaint/Wrong Product Complaint (Auto detect it based on the conversation history)
    Returns:
        str: A response related to product issue or complain registration
    """
    if email_id:
        update_email_in_context(user_id,email_id)
    try:
        from freshdesk_helper import create_freshdesk_ticket
        requirements=""
        requirements+=f"Email ID: {email_id}\n"
        requirements+=f"Size of Bag: {size_of_bag}\n"
        requirements+=f"Product Issue or Complain Type: {product_issue_or_complain_type}\n"
        requirements+=f"Invoice: {url_of_invoice}\n"
        requirements+=f"Photo of Product: {url_of_photo_of_product}\n"
        message=create_freshdesk_ticket("Product Issue or Complain",requirements,ticket_type=product_issue_or_complain_type,email=email_id)
        return "😔 We sincerely apologize for any inconvenience you're experiencing with your product. We understand how frustrating it can be when a product doesn't meet your expectations. "+message
    except Exception as e:
        print(traceback.format_exc())
        return "Error in product_issue_or_complain_registration. Please try again later."

@tool
def return_exchange_request(type:str):
    """
    Handle return and exchange requests
    Args:
        type (str): The type of request (return or exchange)
    
    Returns:
        str: A response related to return and exchange requests to visit the link to initiate the return or exchange
    """
    try:
        clickpost_link=f"https://nashermiles.clickpost.ai/returns"
        return f"🔄 Please visit the link below to {type} your order:\n\n🔗 {clickpost_link}"
    except Exception as e:
        log_to_openobserve(f"Error in return_exchange_request: {str(e)}")
        return "Error in return_exchange_request. Please try again later."
   
@tool
def bulk_order(name:str,company_name:str,minimum_quantity:int,budget:int,contact_number:str) -> str:
    """
    Handle bulk order inquiries
    Args:
        name (str): The Customer's name
        company_name (str): The company name
        minimum_quantity (int): The minimum quantity required
        budget (int): The Customer's budget
        contact_number (str): The Customer's contact number
    
    Returns:
        str: A response related to bulk orders
    """
    try:
        requirements=""
        requirements+="Bulk Order Inquiry Received.Check details and assign a relevant team member\n"
        requirements+=f"Name: {name}\n"
        requirements+=f"Company Name: {company_name}\n"
        requirements+=f"Minimum Quantity: {minimum_quantity}\n"
        requirements+=f"Budget: {budget}\n"
        requirements+=f"Contact Number: {contact_number}\n"
        from freshdesk_helper import create_freshdesk_ticket
        create_freshdesk_ticket("Bulk Order",requirements,"Bulk Order Inquiry",contact_number=contact_number,name=name,priority=3)
        return "🛄 Thank you for your interest in placing a bulk order with `Nasher Miles`! We appreciate you considering us for your business needs. Our dedicated team will carefully review your requirements and get in touch with you shortly to discuss pricing, customization options, and delivery timelines. We look forward to serving you! 🤝 📦"
    except Exception as e:
        log_to_openobserve(f"Error in bulk_order: {str(e)}")
        return "Error in bulk_order. Please try again later."
@tool
def discount_for_students(user_id:str,email_id:str,url_of_admission_card_or_school_id:str,try_count:int) -> str:
    """  
    Trigger this tool onlywhen a student (from a school, college, or university) expresses interest in availing a student discount.
    Typical usage scenarios include messages like:  
    - "I am a student and want to avail the student discount"  
    - "I want to avail the student discount"  
    - "Mujhe student discount code chahiye"  
    - "I am a college student and want to get the discount"

    Arguments:
        user_id (str): User's Unique Id(available in the context)
        email_id (str): Student's email address  
        url_of_admission_card_or_school_id (str): URL of the uploaded admission card or school ID (extracted from conversation history)
        try_count (int): Number of times the tool has been called. (increases by 1 after each call first time it's value is 1)
    """
    if email_id:
        update_email_in_context(user_id,email_id)
    try:
        from gemini_live import validate_admission_card
        if validate_admission_card(url_of_admission_card_or_school_id) == "valid":
            from freshdesk_helper import create_freshdesk_ticket
            requirements=""
            requirements+=f"Email ID: {email_id}\n"
            requirements+=f"URL of Admission Card or School ID: {url_of_admission_card_or_school_id}\n"
            create_freshdesk_ticket("Discount for Students",requirements,"Student Discount Offer",email=email_id,priority=3)
            return "🎓 Thanks! Please check your email shortly to receive your student discount code! 🎉 Happy shopping! 🛍️"
        else:
            if try_count == 4:
                from freshdesk_helper import create_freshdesk_ticket
                requirements=""
                requirements+=f"Email ID: {email_id}\n"
                requirements+=f"URL of Admission Card or School ID: {url_of_admission_card_or_school_id}\n"
                create_freshdesk_ticket("Discount for Students",requirements,"Student Discount Offer",email=email_id,priority=3)
                return "🎓 We have received your request. Our team will get back to you shortly!"
            return "❌ Admission card or school ID is invalid. Please upload a valid admission card or school ID 🎓 📄"
    except Exception as e:
        log_to_openobserve(f"Error in discount_for_students: {str(e)}")
        return "Error in discount_for_students. Please try again later."



def warranty_extension(user_id:str,email_id:str,url_of_invoice:str,try_count:int):
    """
    Handle warranty extension inquiries or user mentions 6MonthsMore. Validate the invoice and process warranty extension if valid
    Args:
        user_id (str): User's Unique Id(available in the context)
        email_id (str): The user's email which was used to buy the product()
        url_of_invoice (str): Invoice to be uploaded by customer(to be extracted out from the conversation history latest uploaded invoice)
        try_count (int): Number of times the tool has been called. (increases by 1 after each call first time it's value is 1)
    Returns:
        str: A response related to warranty extension
    """
    if email_id:
        update_email_in_context(user_id,email_id)
    try:
        response=validate_nashermiles_invoice(url_of_invoice)
        print(response)
        # return response
        if response == "valid":
            try:
                print("I am in warranty extension")
                requirements=""
                requirements+="Warranty Extension Inquiry Received.Check details and assign a relevant team member\n"
                requirements+=f"Email ID: {email_id}\n"
                requirements+=f"URL of Invoice: {url_of_invoice}\n"
                from freshdesk_helper import create_freshdesk_ticket
                create_freshdesk_ticket("Warranty Extension",requirements,"6Monthsmore",email=email_id,priority=3)
                return "🙏 Thank you for trusting Nasher Miles! We have received your warranty extension request and our team will contact you shortly! ✨"
        
            except Exception as e:
                print(traceback.format_exc())
                log_to_openobserve(f"Error in warranty_extension: {str(e)}")
                return "Error in warranty_extension. Please try again later."
        else:
            if try_count == 4:
                print("I am in warranty extension")
                print(try_count)
                requirements=""
                requirements+="Warranty Extension Inquiry Received.Check details and assign a relevant team member\n"
                requirements+=f"Email ID: {email_id}\n"
                requirements+=f"URL of Invoice: {url_of_invoice}\n"
                from freshdesk_helper import create_freshdesk_ticket
                create_freshdesk_ticket("Warranty Extension",requirements,"6Monthsmore",email=email_id,priority=3)
                return "I have forwarded your request to the concerned team and they will get back to you shortly! 🤝"
        
            else:
                return "❌ Invoice is invalid. Please upload a valid invoice. 📄 Please download the valid invoice from where you made the purchase and upload it again. 🔄"
    except Exception as e:
        print(traceback.format_exc())
        log_to_openobserve(f"Error in warranty_extension: {str(e)}")
        return "Error in warranty_extension_validation. Please try again later."
    
# test=warranty_extension(user_id="test@test.com",email_id="test@test.com",url_of_invoice="https://storage.googleapis.com/nashermiles/uploads/fb0d5c24f6cc4ac4aaad38bf95a78dca.jpg")
# print(test)

@tool
def faqs(query:str,category:str):
    """
    Use this tool to answer Frequently asked queries related to [Shipping, Returns& Cancellation],[Warranty, Service & Support],[Security & Locks],[Luggage Care & Usage],[Product Information]
    This tool can handle questions in the following areas:
    1. Shipping, Returns & Cancellation
    - What is your shipping policy?
    - What is your return policy for products purchased from the website?
    - Are there any shipping charges?
    - How do I make a return?
    - How long will it take for my refund to be processed?
    - What should I do if I receive an order that is damaged, incorrect, or incomplete?
    - What should I do if my order shows as delivered, but I haven't received it?
    - What should I do if I want to cancel my order?
    - What should I do if I ordered multiple products but only received one?
    - Why is my order for a luggage set shipped in a single box?
    - What should I do if I need to update my PIN code, address, or contact information?
    - What should I do if my pin code isn't serviceable and I need to get my Nasher Miles returned?
    - What should I consider when returning my Nasher Miles order?
    - How long will it take to receive my order from Nasher Miles?
    - How can I place a bulk or corporate order?
    - Do you have a retail store that I can visit?
    - Can I collect my online order from a nearby retail store?

    2. Warranty, Service & Support
    - How can I avail warranty on Nasher Miles products?
    - What is the warranty on Nasher Miles products? Do I get a warranty card?
    - Does Nasher Miles offer an international warranty?
    - How many service centers do you have? Where are they located?
    - Do you offer a paid repair service?
    - How can I order spare parts from Nasher Miles?
    - How long does the luggage or backpack repair process take?

    3. Security & Locks
    - What is a normal lock and how to use it?
    - What is TSA lock and how to use it?
    - What if I forget the lock combination and am unable to open the bag?

    4. Luggage Care & Usage
    - Is the luggage waterproof?
    - How should the luggage be cleaned?
    - How should the luggage be stored?

    5. Product Information
    - Why does Nasher Miles cost less than other luggage?
    - Which is the best choice for international travels?
    - What is cabin luggage and what is check-in luggage?
    - Do you have 100% polypropylene luggage?
    - What is ABS, PC and PP luggage?
    - How do you measure the capacity of each luggage?
    - What are the standard dimensions for bags?
    - What is the maximum weight each luggage can hold?
    - What is the maximum size of baggage and weight that I can carry while travelling?
    Args:
        query (str):customer's query
        category (str): The category of the FAQ to be answered.("Shipping, Returns& Cancellation","Warranty, Service & Support","Security & Locks","Luggage Care & Usage","Product Information")
    Returns:
        str: A concise, helpful answer based on Nasher Miles policies and product details.
    """
    try:
        # Initialize the embedding model
        embeddings = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")
        
        # Load the FAISS index
        vector_store = FAISS.load_local(
            "faiss_indices/faq",
            embeddings,
            allow_dangerous_deserialization=True
        )
        
        # Set up the LLM
        llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)
        
        # Define the prompt template
        prompt_template = """
Your job is to answer the customer's query
Context contains the list of FAQs
Match the customer's query with the most relevant FAQ and answer the query accordingly.
-Response guidelines:
1. Do not use any markdown formatting
2. Do not shorten the answer or give a summary of the answer
3. Do not use any asterisks
4. Do not use any bold
Use the following context to answer the customer's query.

Context: {context}

Query: {question}
Answer:
"""





        
        PROMPT = PromptTemplate(
            template=prompt_template,
            input_variables=["context", "question"]
        )
        
        # Create the retrieval chain
        qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=vector_store.as_retriever(search_kwargs={"k": 3}),
            chain_type_kwargs={"prompt": PROMPT}
        )
        
        # Get the response
        result = qa_chain.invoke({"query": query})
        return result["result"]
        
    except Exception as e:
        print(traceback.format_exc())
        logging.error(f"Error in FAQ response generation: {str(e)}")
        return "I'm sorry, I encountered an error while processing your request. Please try again later."
    
# test=faqs("warranty policy","Warranty, Service & Support")
# print(test)


@tool
def order_cancellation_handler(
    user_id:str,reason: str,
    email: Optional[str] = None,
    order_number: Optional[str] = None,
   
) -> str:
    """
    Cancel an order if not shipped.  Requires email or order_number and reason for cancellation.
    Args:
        user_id (str): The user ID(available in the context)
        reason (str): The reason for cancellation
        email (str): The customer's email.
        order_number (str): The order number starting with "NM"
    Returns:
        str: A response with the cancellation status of the order.
    """
    if email:
        update_email_in_context(user_id,email)
    def _normalize_order_number(order_number: str) -> str:
        """Return the numeric part of an order number, handling both 'NM' prefix and raw numbers."""
        if order_number.startswith('NM'):
            return order_number[2:]
        return order_number

    def _get_order_by_id(order_id: str) -> Optional[dict]:
        """Fetch a single Shopify order by its ID."""
        url = f"{SHOP_BASE_URL}/orders/{order_id}.json"
        try:
            resp = _session.get(url, headers={"X-Shopify-Access-Token": ACCESS_TOKEN}, timeout=30)
            resp.raise_for_status()
            return resp.json().get("order", {})
        except Exception as e:
            return None

    def _get_order_by_number(order_number: str) -> Optional[dict]:
        """
        Search Shopify for an order by its “NM”-prefixed name (e.g., “NM56190”)
        or by the raw numeric part (e.g., “56190”), using the 'name' query parameter.
        Returns the first matching order dict, or None if not found.
        """
        # Normalize input: strip off “NM” prefix if present
        normalized = _normalize_order_number(order_number)
        target_name = f"NM{normalized}"

        # Query Shopify using the 'name' parameter
        url = f"{SHOP_BASE_URL}/orders.json"
        params = {
            "name": target_name,  # filter by order name directly
            "status": "any",      # include open, closed, cancelled, etc.
            "limit": 1            # we only need the first match
        }

        try:
            resp = _session.get(
                url,
                headers={"X-Shopify-Access-Token": ACCESS_TOKEN},
                params=params,
                timeout=30
            )
            resp.raise_for_status()
            orders = resp.json().get("orders", [])

            # If Shopify returns at least one order, return it
            if orders:
                print(orders)
                return orders[0]

            return None

        except Exception:
            return None


    def _create_ticket(subject: str, description: str, tags: List[str]) -> str:
        """Create a ticket in Freshdesk. Returns ticket ID string "FD-<id>" on success."""
        url = f"{FRESHDESK_BASE_URL}/tickets"
        payload = {
            "subject": subject,
            "description": description,
            "status": 2,      # Open status
            "priority": 2,    # Medium priority
            "tags": tags
        }
        try:
            resp = _session.post(
                url,
                auth=(FRESHDESK_API_KEY, "X"),
                json=payload,
                timeout=30
            )
            resp.raise_for_status()
            return f"FD-{resp.json()['id']}"
        except Exception as e:
            return "(could not create ticket)"

    # ── Step 1: Require email ───────────────────────────────────────────────────
    if not email:
        return "Please provide your registered email address to start the cancellation process."

    order = None


    # ── Step 2b: Try fetch by order_number ──────────────────────────────────────
    if not order and order_number:
        order = _get_order_by_number(order_number)
        print(order)
        if order and order.get("email") != email.lower():
            return "The provided Order Number does not match with your email address."

    # ── Step 2c: If still no order, list unshipped orders for that email ───────
    if not order:
        url = f"{SHOP_BASE_URL}/orders.json"
        try:
            resp = _session.get(url, headers={"X-Shopify-Access-Token": ACCESS_TOKEN}, params={"email": email}, timeout=30)
            resp.raise_for_status()
            orders = resp.json().get("orders", [])
            if not orders:
                return "No orders found for your email address. Please verify your email."

            # Format order list
            unshipped = [o for o in orders if o.get("fulfillment_status") != "fulfilled"]
            if not unshipped:
                return "No unshipped orders found for your email address."

            lines = []
            for o in unshipped[:5]:
                status = o.get("fulfillment_status") or "pending"
                financial = o.get("financial_status")
                lines.append(f"Order: NM{o.get('order_number')} - Status: {status} - Payment: {financial}")
            
            return (
                "Please provide either Order ID or Order Number from your unshipped orders:\n"
                f"{chr(10).join(lines)}"
            )
        except Exception as e:
            return "Error fetching your orders. Please try again later."

    # ── Step 3: We have exactly one order object ─────────────────────────────────
    shipped = (order.get("fulfillment_status") == "fulfilled")
    payment_method = order.get("payment_gateway_names", [None])[0]
    order_number = order.get("name", "")
    cancelled_at = order.get("cancelled_at", None)
    if cancelled_at:
        return f"Your order was already cancelled on {cancelled_at}. Please check your order section on nashermiles.com for more details."

    # First check if there are any fulfillments
    if order.get("fulfillments"):
        tracking_number = order["fulfillments"][0].get("tracking_number", "Not Available")
        tracking_link = order["fulfillments"][0].get("tracking_url", "Not Available")
        tracking_company = order["fulfillments"][0].get("tracking_company", "Not Available")
    else:
        tracking_number = "Not Available"
        tracking_link = "Not Available"
        tracking_company = "Not Available"

    # ── Case A: Already shipped or has AWB ─────────────────────────────────────
    if shipped or any(fulfillment.get("tracking_number") for fulfillment in order.get("fulfillments", [])):
        # Create a Freshdesk ticket so a human agent can advise them to do a return
        desc = (
            f"Email: {email}\n"
            f"Order Number: {order_number}\n"
            f"Order ID: {order['id']}\n"
            f"Status: {'Shipped' if shipped else 'Ready to Ship (AWB Generated)'}\n"
            f"Tracking Number: {tracking_number}\n"
            f"Tracking Link: {tracking_link}\n"
            f"Tracking Company: {tracking_company}\n"
            f"Requested at: {datetime.now(timezone.utc).isoformat()}"
        )
        try:
            from freshdesk_helper import create_freshdesk_ticket
            ticket_id = create_freshdesk_ticket("Order Cancellation Request - Already Shipped",desc,"Order Cancellation",email=email,priority=3)
        except Exception:
            ticket_id = "(could not create ticket)"

        return (
            "📦 Your order has already been dispatched. You can request a return once it is delivered.\n\n ✉️"
            f"{ticket_id}"
        )

    # ── Case B: Order is not shipped → proceed to cancel ──────────────────────────
    if not reason:
        return "May we know the reason for cancellation?"

    # Call Shopify's cancel endpoint:
    try:
        cancel_url = f"{SHOP_BASE_URL}/orders/{order['id']}/cancel.json"
        resp = _session.post(
            cancel_url,
            headers={"X-Shopify-Access-Token": ACCESS_TOKEN},
            json={"reason": reason},
            timeout=30
        )
        resp.raise_for_status()
    except Exception:
        return "Unable to cancel your order at this moment. Please try again later."

    # Build the user-facing message based on payment method
    if payment_method == "cash_on_delivery":
        user_msg = "✅ Your order is cancelled. 💰 Since it was Cash on Delivery, no refund is due."
    else:
        user_msg = (
            "✅ Your order is cancelled. 💳 Refund will be issued to the original payment method in 24–48 hours. "
            "⏳ It may take up to 7 banking days to reflect in your statement. 💰 If it was cash on delivery then no refund is due."
        )

    # Create a Freshdesk ticket for the cancelled order
    desc = (
        f"Email: {email}\n"
        f"Order Number: {order_number}\n"
        f"Order ID: {order['id']}\n"
        f"Reason: {reason}\n"
        f"Payment: {payment_method}\n"
        f"Cancelled at: {datetime.now(timezone.utc).isoformat()}"
    )
    try:
        from freshdesk_helper import create_freshdesk_ticket
        ticket_id = create_freshdesk_ticket("Order Cancellation Request- Not Shipped",desc,"Order Cancellation",email=email,priority=3)
    except Exception:
        ticket_id = "(could not create ticket)"

    return f"{user_msg}\n\n"

# test=order_cancellation_handler(user_id="samay.m2504@gmail.com",email="kpatel@nashermiles.com",order_number="NM56291",reason="I want to cancel my order")
# print(test)
# Load store locations from CSV
STORES_DF = get_sheet_as_dataframe("Stores")
def geocode_address_google(address, api_key):
    """
    Geocode the address using Google Maps Geocoding API and return (lat, lon).
    """
    base_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": api_key
    }
    response = requests.get(base_url, params=params)

    if response.status_code == 200:
        data = response.json()
        if data["status"] == "OK":
            location = data["results"][0]["geometry"]["location"]
            return location
        else:
            print("Geocoding API returned status:", data["status"])
            return None
    else:
        print("HTTP Error:", response.status_code)
        return None
@tool
def store_locator(query: str) -> str:
    """
    Find all Nasher Miles stores based on postal code or locality name.

    Args:
        query (str): Postal code or locality name to search for

    Returns:
        str: Information about stores or alternative options if none found
    """
    def haversine(lat1, lon1, lat2, lon2):
        """Calculate the great-circle distance between two points on Earth (in km)."""
        R = 6371.0  # Earth radius in kilometers
        φ1, φ2 = radians(lat1), radians(lat2)
        Δφ = radians(lat2 - lat1)
        Δλ = radians(lon2 - lon1)
        a = sin(Δφ / 2)**2 + cos(φ1) * cos(φ2) * sin(Δλ / 2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    def format_store_info(row, distance_km=None):
        """Format store information with a Google Maps directions link."""
        distance_text = f" (approx {distance_km:.1f} km away)" if distance_km is not None else ""
        # Construct full address string
        address_str = f"{row.get('Address line 1','')} {row.get('Address line 2','')}, {row['City']}, {row['State/Province']} {row['Postal code']}, India.\n Timings: 10 AM to 8 PM"
        # URL-encode for destination parameter
        encoded_dest = quote_plus(address_str)
        maps_link = f"https://www.google.com/maps/dir/?api=1&destination={encoded_dest}"
        short_link=shorten_url(maps_link)

        return (
            f"Store: {row['Name']}{distance_text}\n"
            f"{address_str}\n"
            f"Phone: {row.get('Phone','N/A')}\n"
            f"Directions: {short_link}"
        )

    try:
        
        q = query.strip()
        # 1) Exact postal code lookup
        exact = STORES_DF[STORES_DF['Postal code'].astype(str) == q]
        if not exact.empty:
            lines = [format_store_info(row) for _, row in exact.iterrows()]
            return "\n\n".join(lines)

        # 2) Geocode the query to lat/lon
        loc=None
        try:
            geolocator = Nominatim(user_agent="nasher-miles-store-locator")
            loc = geolocator.geocode(q)
        except Exception as e:
            pass
        
        if loc is None:
            location= geocode_address_google(q, os.environ.get('GOOGLE_API_KEY'))
            if location is None:
                return (
                    "🌟 We couldn't find your location. Could you please help us by providing:\n"
                    "1. A valid postal code \n"
                    "2. A complete locality name with city and state \n"
                    "In the meantime, you can always shop online at:\n"
                    "🌐 https://nashermiles.com/\n"

                    "Or view our full store list at:\n"
                    "📍 https://nashermiles.com/pages/store-locator"
                )
            # 3) Compute distances for all stores
            df = STORES_DF.copy()
            df['distance_km'] = df.apply(
                lambda r: haversine(location['lat'], location["lng"], float(r['latitude']), float(r['longitude'])), axis=1
            )
            # 4) Filter stores within 50 km
            nearby = df[df['distance_km'] <= 50].sort_values('distance_km')
            if not nearby.empty:
                lines = [format_store_info(row, row['distance_km']) for _, row in nearby.iterrows()]
                return "Stores found nearest to you:\n\n" + "\n\n".join(lines)

            # 5) No nearby store
            return (
                "✨ We're sorry, but we don't have any stores near you (within 50 km).\n\n"
                "But don't worry! You can still shop with us:\n"
                "🌐 Visit our online store: https://nashermiles.com/\n"
                "Or explore all our store locations at:\n"
                "📍 https://nashermiles.com/pages/store-locator"
            )
        else:
            # 3) Compute distances for all stores
            df = STORES_DF.copy()
            df['distance_km'] = df.apply(
                lambda r: haversine(loc.latitude, loc.longitude, float(r['latitude']), float(r['longitude'])), axis=1
            )
            # 4) Filter stores within 50 km
            nearby = df[df['distance_km'] <= 50].sort_values('distance_km')
            if not nearby.empty:
                lines = [format_store_info(row, row['distance_km']) for _, row in nearby.iterrows()]
                return "Stores found nearest to you:\n\n" + "\n\n".join(lines)

            # 5) No nearby store
            return (
                "✨ We're sorry, but we don't have any stores near you (within 50 km).\n\n"
                "But don't worry! You can still shop with us:\n"
                "🌐 Visit our online store: https://nashermiles.com/\n"
                "Or explore all our store locations at:\n"
                "📍 https://nashermiles.com/pages/store-locator"
            )

    except Exception as e:
        log_to_openobserve(f"Error in store_locator: {str(e)}")
        return (
            f"Error processing location query: {str(e)}\n"
            "Please try with a different postal code or locality name."
        )
            
def get_rag_response(query: str, index_path: str) -> str:
    """
    Get a response using RAG (Retrieval-Augmented Generation) for product queries.
    
    Args:
        query (str): User's query about a product
        index_path (str): Path to the FAISS index directory
        
    Returns:
        str: Generated response based on the query and available product information
    """
    try:
        # Initialize the embedding model
        embeddings = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")
        
        # Load the FAISS index
        vector_store = FAISS.load_local(
            index_path,
            embeddings,
            allow_dangerous_deserialization=True
        )
        
        # Set up the LLM
        llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)
        
        # Define the prompt template
        prompt_template = """
You are a professional consultative sales assistant from NasherMiles. Help customers choose the right product based on their needs.

FORMATTING RULES - FOLLOW EXACTLY:
- Use ONLY plain text - NO asterisks (*), bold (**), bullets, or markdown formatting
- For multiple items, use numbered format: 1., 2., 3.
- Write everything in regular plain text

Instructions:
1. If unavailable, say so clearly
2. For each product mentioned, include: Product Name, very very short description why it fits their needs and price , Product Link(instead of product link say you can buy it from here)(starting with tinyurl for your reference,include full product link)
3. Always include exactly ONE product link per product mentioned
4. Never repeat the same link
5. Include max 5 products and give a small summary of other products
5. Avoid robotic phrases like "great option" or "perfect for"
6. No greetings like "Hi" or "Hey"
7. Keep under 500 words
8. Use conversational, helpful tone.
9. Use emojis in the response
10. Keep your overall response short and concise.

Use the following context to answer the customer's query.
Context: {context}
Query: {question}
Answer:
"""





        
        PROMPT = PromptTemplate(
            template=prompt_template,
            input_variables=["context", "question"]
        )
        
        # Create the retrieval chain
        qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=vector_store.as_retriever(search_kwargs={"k": 15}),
            chain_type_kwargs={"prompt": PROMPT}
        )
        
        # Get the response
        result = qa_chain.invoke({"query": query})
        return result["result"]
        
    except Exception as e:
        print(traceback.format_exc())
        logging.error(f"Error in RAG response generation: {str(e)}")
        return "I'm sorry, I encountered an error while processing your request. Please try again later."

@tool
def nashermiles_product_info(query: str) -> str:
    """
    Get comprehensive product information for NasherMiles products including all details in a single response.
    
    This tool handles ANY query about a specific NasherMiles product and provides complete information including:
    pricing, dimensions, features, colors, sizes, availability, compartments, wheel type, lock type, etc.
    
    IMPORTANT: Call this tool ONLY ONCE per product query. It returns ALL relevant information in one response.
    
    Args:
        query (str): The customer's complete query about a specific NasherMiles product.
        
    Returns:
        str: Complete product information including price, features, availability, and purchase link.
    """
    try:
        return get_rag_response(query, "faiss_indices/nashermiles")
        
    except Exception as e:
        logging.error(f"Error in query_product_info: {str(e)}")
        return "I'm sorry, I encountered an error while processing your request. Please try again later."
# Helper function to create Freshdesk tickets with screenshot URL
def create_freshdesk_ticket_with_url(contact, utr_number=None,screenshot_url=None):
    key = os.getenv("FRESHDESK_API_KEY")
    dom = os.getenv("FRESHDESK_DOMAIN")
    url = f"https://{dom}/api/v2/tickets"
    if not (key and dom):
        raise RuntimeError("Missing Freshdesk credentials")

    description = f"Contact: {contact}\n"
    if screenshot_url:
        description += f"Payment Screenshot: {screenshot_url}\n"
    description += "Customer reports payment debited but no order in system."

    data = {
        "ticket": {
            "subject": "Payment Issue — Order Not Found",
            "description": description,
            "email": contact if "@" in contact else None,
            "phone": contact if "@" not in contact else None,
            "status": 2,
            "priority": 2,
            "tags": ["payment_issue"]
        }
    }

    resp = requests.post(
        url,
        auth=(key, "X"),
        json=data,
        timeout=30
    )
    resp.raise_for_status()
    return f"FD-{resp.json()['ticket']['id']}"

@tool
def payment_issue_handler(user_id:str,
    screenshot_url: str,
    utr_number: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    complaint_status: Optional[str] = None,
) -> str:
    """
    Handle queries about payment issues. UTR number is required and the payment proof is required and email or phone is required.

    Args:
        user_id (str): User's Unique Id(available in the context)
        screenshot_url (str): Payment proof to be uploaded by the customer.(Check screenshot_url in the chat latest message)
        utr_number (str): UTR number of the payment.
        email (str, optional): Customer's registered email address.
        phone (str, optional): Customer's phone number (10).
        complaint_status (str, optional): Yes or No if the customer is complaining about the order.(to be confirmed by the customer if the order is not created, First time it will be None and if the customer says they cannot find the order then it will be Yes)

    Returns:
        str: A user-friendly message summarizing order results or ticket actions.
    """
    if email:
        update_email_in_context(user_id,email)
    # --- Nested helper functions ---
    def _valid_email(e: str) -> bool:
        rx = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
        return bool(e and rx.match(e.strip()))

    def _valid_phone(p: str) -> bool:
        if not p:
            return False
        digits = re.sub(r"\D", "", p)
        return 10 <= len(digits) <= 15

    def _valid_order_id(o: str) -> bool:
        return bool(o and re.match(r"^\d+$", o.strip()))

    def _generate_contact_id(email: Optional[str], phone: Optional[str]) -> str:
        if email:
            return f"email:{email.lower().strip()}"
        if phone:
            clean = re.sub(r"\D", "", phone)
            return f"phone:{clean}"
        return f"unknown:{datetime.utcnow().isoformat()}"

    def _fetch_shopify_orders(order_id, email, phone):
        shop = os.getenv("SHOP_NAME")
        token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        base = f"https://{shop}.myshopify.com/admin/api/2025-04"
        if not shop or not token:
            raise RuntimeError("Missing Shopify credentials")

        sess = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429,500,502,503,504])
        sess.mount("https://", HTTPAdapter(max_retries=retries))

        # By order ID
        if order_id:
            r = sess.get(f"{base}/orders/{order_id}.json",
                         headers={"X-Shopify-Access-Token": token}, timeout=30)
            if r.status_code == 200:
                o = r.json().get("order")
                return [o] if o else []
            if r.status_code != 404:
                r.raise_for_status()
            return []

        # By email (or phone)
        params = {"status": "any", "limit": 5}
        if email:
            params["email"] = email.strip()
        r = sess.get(f"{base}/orders.json",
                     headers={"X-Shopify-Access-Token": token},
                     params=params, timeout=30)
        r.raise_for_status()
        orders = r.json().get("orders", [])

        if phone and not email:
            clean = re.sub(r"\D","", phone)
            orders = [
                o for o in orders
                if re.sub(r"\D","", o.get("customer", {}).get("phone","")) == clean
            ]

        return orders

    # --- Main logic starts here ---
    try:
        # Input validation
        if email and not _valid_email(email):
            return "The email format seems invalid. Please double-check."
        if phone and not _valid_phone(phone):
            return "The phone number seems invalid. Please provide a 10–15 digit number."

        # Step 1: Shopify lookup
        try:
            orders = _fetch_shopify_orders(None, email, phone)
        except Exception as e:
            log_to_openobserve(f"Shopify lookup failed: {e}")
            return "Sorry, I'm having trouble reaching our order system. Please try again shortly."

        # Found orders
        if orders:
            lines = []
            for o in orders[:3]:
                date = o["created_at"][:10]
                lines.append(f"📋 #{o['order_number']}  ₹{o['total_price']}  on {date}  ({o['financial_status']})")
            more = f"\n…and {len(orders)-3} more." if len(orders) > 3 else ""
            return (
                "\n".join(lines) + more +
                "\n\nThese are the orders found for your email or phone number. Please confirm if this is the order you are looking for."
            )

        # Step 2: No order & no proof → create ticket
        if screenshot_url and complaint_status == "Yes":
            contact_id = _generate_contact_id(email, phone)
            ticket_id = create_freshdesk_ticket_with_url(email or phone, screenshot_url)
            
            return (
                "❌ I don't see an order yet. "
                "If your payment was debited and you don't receive any notification about the order,  wait for 1–2 hours the order will be created if not then the money will be credited back to your payment method."
                f"Although I've opened ticket #{ticket_id} for your issue. Sorry for the inconvenience caused. "
                
            )


    except Exception as e:
        log_to_openobserve(f"Payment issue handler error: {e}")
        return "An unexpected error occurred. Please try again later."
        

@tool
def refund_status_handler(
    user_id: str,
    email: Optional[str] = None,
    order_number: Optional[str] = None
) -> str:
    """
    Check refund status for a given order.
    Args:
        user_id (str): The user ID (available in the context)
        email (str): The customer's email.
        order_number (str): The order number starting with "NM"
    Returns:
        str: A response with the refund status of the order.
    """
    if email:
        update_email_in_context(user_id, email)

    def _normalize_order_number(order_number: str) -> str:
        """Return the numeric part of an order number, handling both 'NM' prefix and raw numbers."""
        if order_number.startswith('NM'):
            return order_number[2:]
        return order_number

    def _get_order_by_number(order_number: str) -> Optional[dict]:
        """
        Search Shopify for a canceled order by its “NM”-prefixed name (e.g., “NM56190”)
        or by the raw numeric part (e.g., “56190”). Return the first matching canceled order dict, or None.
        """
        normalized = _normalize_order_number(order_number)
        target_name = f"NM{normalized}"

        url = f"{SHOP_BASE_URL}/orders.json"
        params = {
            "status": "cancelled",  # only fetch canceled orders
            "limit": 250
        }

        try:
            resp = _session.get(
                url,
                headers={"X-Shopify-Access-Token": ACCESS_TOKEN},
                params=params,
                timeout=30
            )
            resp.raise_for_status()
            orders = resp.json().get("orders", [])

            for o in orders:
                if o.get("name") == target_name:
                    return o

            return None
        except Exception:
            return None

    if not email:
        return "Please provide your registered email address to check refund status."

    order = None
    # Try by order_number
    if order_number:
        order = _get_order_by_number(order_number)
        if order and order.get("email") != email.lower():
            return "❌ Oops! This order number doesn't match your email address. Please double-check your email and try again! 🔄"

    # If still no order, list all orders for that email
    # if not order:
    #     url = f"{SHOP_BASE_URL}/orders.json"
    #     try:
    #         resp = _session.get(
    #             url,
    #             headers={"X-Shopify-Access-Token": ACCESS_TOKEN},
    #             params={"email": email},
    #             timeout=30
    #         )
    #         resp.raise_for_status()
    #         orders = resp.json().get("orders", [])
    #         if not orders:
    #             return "No orders found for your email address. Please verify your email."

    #         lines = []
    #         for o in orders[:5]:
    #             status = o.get("fulfillment_status") or "pending"
    #             financial = o.get("financial_status")
    #             if o.get("cancelled_at")==None:
    #                 lines.append(f"Order: NM{o.get('order_number')} - Status: {status} - Payment: {financial}")
    #             else:
    #                 lines.append(f"Order: NM{o.get('order_number')} - Status: {status} - Payment: {financial} - Cancelled on {o.get('cancelled_at')}")

    #         return (
    #             "Above are the orders found for your email address. Please provide Order Number from your orders:\n"
    #             f"{chr(10).join(lines)}"
    #         )
    #     except Exception:
    #         return "Error fetching your orders. Please try again later."
    if order==None:
        return "❌ We couldn't find any cancelled order with that order number. Please double-check your order number and try again! 🔄"
    
    # Otherwise, look at 'refunds' field
    refunds = order.get("refunds", [])
    if not refunds:
        if order.get("payment_gateway_names") == ["cash_on_delivery"]:
            return "💳 No refund is due for this order since it was paid through cash on delivery."
        else:
            return "💳 No refund has been initiated for this order."

    # Examine the most recent refund object
    last_refund = refunds[-1]
    refund_created = last_refund.get("created_at", "").strftime("%d-%m-%Y %H:%M:%S")  # YYYY-MM-DD
    transactions = last_refund.get("transactions", [])

    if not transactions:
        # Refund record exists but no transaction => refund initiated but not credited
        return f"💳 Your refund request was initiated on {refund_created} and is being processed! Please allow up to 7 business days for the amount to reflect in your account. ⏳ Thank you for your patience! 🙏"

    # If there is a transaction, check its status
    txn = transactions[0]
    txn_status = txn.get("status")
    txn_amount = txn.get("amount")
    txn_date = txn.get("processed_at", "").strftime("%d-%m-%Y %H:%M:%S")  # YYYY-MM-DD

    if txn_status == "success":
        return f"💳 Your refund of ₹{txn_amount} was initiated on {refund_created} and as per our records it was credited on {txn_date}."
    else:
        # Other statuses (e.g., pending, error)
        return f"💳 Your refund of ₹{txn_amount} was initiated on {refund_created}, current transaction status: {txn_status}."
# test=refund_status_handler(user_id="samay.m2504@gmail.com",email="samay.m2504@gmail.com",order_number="NM56460")
# print(test)

@tool
def product_recommendation_handler(query: str) -> str:
    """
    Provides product recommendations based on user queries about materials, categories,kids collection,luggage cover,luggage tags,sale
    ExampleUsage:
    - "What are the best luggage options for a family trip?"
    - "I need a durable backpack for my daily commute."
    - "Can you recommend a lightweight luggage for international travel?"
    - "What's the best material for a travel backpack?"
    - "I'm looking for a durable luggage for hiking."
    - "I m looking for a kids collection"
    - "I m looking for a luggage cover"
    - "I m looking for a luggage tags"
    - "Which products are on sale?"
    - "What's the difference between polycarbonate and polypropylene luggage?"
    
    Args:
        query (str): User's query about products, materials, or categories or sale
        
    Returns:
        str: product recommendations with relevant links and information
    """
    try:
        # Define product category mappings
        PRODUCT_LINKS = {
            "polycarbonate": "https://nashermiles.com/collections/luggage?sort_by=best-selling&filter.p.m.custom.material=Polycarbonate",
            "polypropylene": "https://nashermiles.com/collections/luggage?sort_by=best-selling&filter.p.m.custom.material=Polypropylene",
            "backpack": "https://nashermiles.com/collections/backpack",
            "kids collection": "https://nashermiles.com/collections/kids",
            "luggage cover": "https://nashermiles.com/collections/luggage-cover",
            "luggage tags": "https://nashermiles.com/collections/luggage-tags",
            "sale": "https://nashermiles.com/collections/sale"
        }
        
        # Initialize LLM
        llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash-8b-latest", temperature=0.4)
        
        # Create a prompt that helps identify user intent and relevant products
        prompt = f"""
        You are a consultative sales assistant for NasherMiles. Analyze the customer's query and provide relevant product recommendations.
        
        Available product categories and their links:
        {json.dumps(PRODUCT_LINKS, indent=2)}
        
        Customer Query: {query}
        
        Instructions:
        1. Identify if the query is about:
           - Specific material (polycarbonate/polypropylene)
           - Product category (backpacks/luggage covers/tags)
           - Sale items
           - Specific product features or requirements
        
        2. Provide a helpful response that:
           - don't include hi ,hello etc.. in the response
           - Addresses the customer's specific needs and tell them to visit the link to see the products
           - Includes appropriate product links
           - Use emoji in the response
           - Maintains a conversational, helpful tone
        
        3. Format rules:
           - Use plain text only (no markdown)
           - Keep response short and concise.
           - Include relevant product links
           - Focus on benefits and features that match customer needs
        
        Respond in a helpful, conversational manner.
        """
        
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content
        
    except Exception as e:
        log_to_openobserve(f"Error in product_recommendation_handler: {str(e)}")
        return "I apologize, but I'm having trouble processing your request right now. Please try again later."