import requests
from requests.auth import HTTPBasicAuth
from typing import Optional, Dict, Any
from typing import List
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
import os
from dotenv import load_dotenv
load_dotenv()

def create_freshdesk_ticket(
    subject: str,
    description: str,
    ticket_type: str,
    email: str = None,
    contact_number: str = None,
    name: str = None,
    domain: str = "nashermilesorg",
    api_key: str = os.getenv("FRESHDESK_API_KEY"),
    priority: int = 2,
    status: int = 2,
    responder_id: int = 1060009223672,
    **kwargs
) -> Dict[str, Any]:
    """
    Create a new ticket in Freshdesk
    
    Args:
        subject (str): Ticket subject
        description (str): Ticket description
        email (str): Requester's email
        contact_number (str): Requester's phone number
        name (str): Requester's name (required if only phone number is provided)
        domain (str): Freshdesk domain
        api_key (str): Freshdesk API key
        priority (int): Ticket priority (1-4)
        status (int): Ticket status (1-5)
        ticket_type (str): Type of ticket
        responder_id (int): ID of the assigned agent
        **kwargs: Additional ticket parameters
        
    Returns:
        dict: Response from Freshdesk API
    """
    # Basic validation
    if not 1 <= priority <= 4:
        raise ValueError("Priority must be between 1 and 4")
    if not 1 <= status <= 5:
        raise ValueError("Status must be between 1 and 5")

    # Create ticket data with required fields
    ticket_data = {
        "subject": subject,
        "description": description,
        "priority": priority,
        "status": status,
        "type": ticket_type,
        "responder_id": responder_id
    }

    # Add contact information
    if email:
        ticket_data["email"] = email
    if contact_number:
        ticket_data["phone"] = contact_number
        if not email and not name:
            raise ValueError("Name is required when only phone number is provided")
        if name:
            ticket_data["name"] = name

    # Add any additional parameters
    ticket_data.update(kwargs)

    # Prepare request
    url = f"https://{domain}.freshdesk.com/api/v2/tickets"
    auth = HTTPBasicAuth(api_key, "X")
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, auth=auth, headers=headers, json=ticket_data)
        response.raise_for_status()
        return "Ticket created. ID: " + str(response.json()["id"]) + " Our dedicated team has been informed about your complaint and will work diligently to resolve this issue. 🛠️"
    except requests.exceptions.RequestException as e:
        if hasattr(e.response, 'text'):
            error_msg = f"Failed to create ticket: {str(e)}\nResponse: {e.response.text}"
        else:
            error_msg = f"Failed to create ticket: {str(e)}"
        raise Exception(error_msg)

def create_freshdesk_ticket_with_attachments(
    subject: str,
    description: str,
    email: str,
    ticket_type: str,
    attachments: Optional[List[str]] = None,  # can be filesystem paths or HTTP URLs
    domain: str = "nashermilesorg",
    api_key: str = "QYUQ2OE7JiUSI7XBw2Qd",
    priority: int = 2,
    status: int = 2,
    responder_id: int = 1060009223672,
    **kwargs
) -> Dict[str, Any]:
    """
    Create a Freshdesk ticket, streaming attachments from disk or HTTP URLs.
    Returns the full JSON response (which includes attachment URLs).
    """
    # Basic validation
    if not 1 <= priority <= 4:
        raise ValueError("Priority must be between 1 and 4")
    if not 1 <= status <= 5:
        raise ValueError("Status must be between 1 and 5")

    form_data: Dict[str, Any] = {
        "subject": subject,
        "description": description,
        "email": email,
        "priority": priority,
        "status": status,
        "type": ticket_type,
        "responder_id": responder_id,
        **kwargs
    }

    files = []
    if attachments:
        for spec in attachments:
            if spec.lower().startswith(("http://", "https://")):
                # fetch into memory
                r = requests.get(spec)
                r.raise_for_status()
                bio = io.BytesIO(r.content)
                bio.name = os.path.basename(spec)
                files.append(("attachments[]", (bio.name, bio, "application/octet-stream")))
            else:
                # local file
                f = open(spec, "rb")
                files.append(("attachments[]", (os.path.basename(spec), f, "application/octet-stream")))

    url = f"https://{domain}.freshdesk.com/api/v2/tickets"
    auth = HTTPBasicAuth(api_key, "X")
    resp = requests.post(url, auth=auth, data=form_data, files=files)
    resp.raise_for_status()

    # clean up any open file handles
    for _key, fp in files:
        if hasattr(fp[1], "close"):
            fp[1].close()

    return resp.json()


# Example usage:
if __name__ == "__main__":
    try:
        result=create_freshdesk_ticket("test","test","Bulk Order Enquiry",contact_number="+919876543210")
        print(result)
    except Exception as e:
        print(f"Error: {str(e)}")
