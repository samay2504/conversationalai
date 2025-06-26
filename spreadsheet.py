import gspread
from google.oauth2.service_account import Credentials
from tenacity import retry, wait_exponential, stop_after_attempt
from dotenv import load_dotenv
load_dotenv()
import os
import pandas as pd

# Define the scope and credentials
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)

# Authorize and create a client
client = gspread.authorize(creds)

# Function to fetch all values from a sheet by name
@retry(wait=wait_exponential(min=1, max=4), stop=stop_after_attempt(5))
def get_sheet_values(sheet_name, sheet_id=os.getenv("SHEET_ID")):
    try:
        # Open the spreadsheet by key
        spreadsheet = client.open_by_key(sheet_id)
        
        # Access the sheet by name
        sheet = spreadsheet.worksheet(sheet_name)
        
        # Get all values from the sheet
        values = sheet.get_all_values()
        
        return values
    except gspread.exceptions.SpreadsheetNotFound:
        print("The spreadsheet was not found.")
        return None
    except gspread.exceptions.WorksheetNotFound:
        print(f"The sheet named '{sheet_name}' was not found.")
        return None
    
@retry(wait=wait_exponential(min=1, max=4), stop=stop_after_attempt(5))
def get_sheet_as_dataframe(sheet_name, sheet_id=None):
    """
    Get all values from a Google Sheet and return as a pandas DataFrame.
    
    Args:
        sheet_name (str): Name of the sheet to retrieve
        sheet_id (str, optional): ID of the Google Sheet. If not provided, uses SHEET_ID from environment.
        
    Returns:
        pandas.DataFrame: DataFrame containing the sheet data, or None if an error occurs
    """
    try:
        # Get the raw values from the sheet
        values = get_sheet_values(sheet_name, sheet_id or os.getenv("SHEET_ID"))
        
        if not values:
            print(f"No data found in sheet '{sheet_name}'")
            return None
            
        # Convert to DataFrame
        df = pd.DataFrame(values[1:], columns=values[0])
        return df
        
    except Exception as e:
        print(f"Error getting sheet as DataFrame: {str(e)}")
        return None

@retry(wait=wait_exponential(min=1, max=4), stop=stop_after_attempt(5))
def append_to_sheet(sheet_id: str,sheet_name: str, data: list):
    """
    Append data to a specified sheet in a Google Spreadsheet.
    If the sheet does not exist, it will be created.

    Args:
        sheet_id (str): The ID of the Google Spreadsheet.
        sheet_name (str): The name of the sheet within the spreadsheet.
        data (list): A list of values to append to the sheet. For a single row, pass a list of values.
                     For multiple rows, pass a list of lists.

    Returns:
        dict: The response from the Google Sheets API.
    """
    try:

        
        # Open the spreadsheet by key
        spreadsheet = client.open_by_key(sheet_id)
        
        # Check if the sheet exists
        try:
            sheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            # If the sheet does not exist, create it
            print(f"Sheet '{sheet_name}' not found. Creating a new sheet...")
            row=len(data)
            col=len(data[0])
            sheet = spreadsheet.add_worksheet(title=sheet_name, rows=str(row+10), cols=str(col+5))
        
        # Append the data to the sheet
        response = sheet.append_rows(data)
        
        return response
    except gspread.exceptions.SpreadsheetNotFound:
        print("The spreadsheet was not found.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
@retry(wait=wait_exponential(min=1, max=4), stop=stop_after_attempt(5))
def write_to_sheet(sheet_name, data, fieldnames, sheet_id=None):
    """
    Write data to a Google Sheet, creating the sheet if it doesn't exist
    and clearing any existing data first.
    """
    try:
        # Get the spreadsheet
        spreadsheet = client.open_by_key(sheet_id or os.getenv("SHEET_ID"))
        
        # Check if the sheet exists, create if it doesn't
        try:
            sheet = spreadsheet.worksheet(sheet_name)
            # Clear existing data
            sheet.clear()
        except gspread.exceptions.WorksheetNotFound:
            # Create a new sheet with enough rows and columns
            sheet = spreadsheet.add_worksheet(
                title=sheet_name,
                rows=str(len(data) + 10),  # Add some extra rows
                cols=str(len(fieldnames) + 5)  # Add some extra columns
            )
        
        # Update the sheet with headers and data
        sheet.update([fieldnames] + [[row.get(field, '') for field in fieldnames] for row in data])
        
        print(f"Successfully updated sheet '{sheet_name}' with {len(data)} rows.")
        return True
    except Exception as e:
        print(f"Error writing to Google Sheet: {str(e)}")
        return False
    

print(get_sheet_values(sheet_name="May 12-18, 2025_conflict1696015679",sheet_id="1PqE7K-L9Yj2pASwYQOM3pPwtXIl_vf4BpQaWfNIhTws"))