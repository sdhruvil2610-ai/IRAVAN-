"""
tools.py — Writes AI project evaluation scores to Google Sheets (ADK-safe)
Author: ADK-Compatible | Version: Robust | Stack: gspread + service_account
"""
import logging
import gspread
from google.oauth2.service_account import Credentials
# --- FIX: REMOVED @FunctionTool import, it's not needed here ---
from typing import Any, Dict # Import Dict and Any
import os # Import os to check for service account

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Constants
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1M7Bpv9STwqJZoJ_OWF9nqZrKTibevDPv13MtejEh0bY/edit#gid=0"
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")

# ---------------------------------------------------------------------------
#  🔧  Tool: Write Score to Sheet
# ---------------------------------------------------------------------------

# --- FIX: REMOVED the @FunctionTool decorator ---
# This makes it a plain Python function that can be imported
# and called by other tools (like record_answer).
def write_score_to_sheet(project_name: str, variable: str, score: str, state: Dict[str, Any]) -> str:
    """
    Writes the score for a given variable and project to a Google Sheet.
    Sheet structure:
    - Row 2: Header row with variable names (including "Project_Name")
    - First column: Contains project names

    Behavior:
    - Adds the project row if not present.
    - Adds the column header if variable not present.
    - Updates the appropriate cell with the score.
    """

    # STEP 1: Resolve sheet URL
    sheet_url = (
        state.get("SHEET_URL")
        or state.get("sheet_url")
        or os.getenv("SHEET_URL") # Check env var as well
        or DEFAULT_SHEET_URL
    )

    if not sheet_url:
        logging.error(" ❌  No valid Google Sheet URL found in state or fallback.")
        return " ❌  Sheet URL not configured."

    logging.info(f" 📄  Connecting to Google Sheet: {sheet_url}")

    # STEP 2: Authenticate and access sheet
    try:
        if not os.path.exists(SERVICE_ACCOUNT_FILE):
             logging.error(f" ❌  Service account file not found at: {SERVICE_ACCOUNT_FILE}")
             return f" ❌  Service account file not found. Please create '{SERVICE_ACCOUNT_FILE}' or set GOOGLE_APPLICATION_CREDENTIALS."
        
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        ws = client.open_by_url(sheet_url).sheet1
    except Exception as e:
        logging.exception(" ❌  Google Sheet authentication failed.")
        return f" ❌  Could not access Google Sheet: {e}"

    # STEP 3: Load header row and locate columns
    try:
        header_row = 2
        headers = ws.row_values(header_row)
        
        # --- FIX: Standardize on "Project_Name" (underscore) ---
        header_to_find = "Project_Name"

        if not headers or header_to_find not in headers:
            # If header is missing or empty, rebuild it
            if not headers:
                headers = []
            if header_to_find not in headers:
                headers.insert(0, header_to_find)
            
            # Use update_row instead of delete/insert to be safer
            ws.update(f'A{header_row}:{gspread.utils.rowcol_to_a1(header_row, len(headers))}', [headers])
            headers = ws.row_values(header_row) # Re-fetch headers

        # Ensure variable exists in headers
        if variable not in headers:
            headers.append(variable)
            ws.update(f'A{header_row}:{gspread.utils.rowcol_to_a1(header_row, len(headers))}', [headers])
            headers = ws.row_values(header_row) # Re-fetch headers

        col_project = headers.index(header_to_find) + 1
        col_variable = headers.index(variable) + 1

    except Exception as e:
        logging.exception(" ❌  Failed to process header row.")
        return f" ❌  Error processing headers: {e}"

    # STEP 4: Locate project row or create new
    try:
        project_cells = ws.col_values(col_project)
        project_row = None

        for i, val in enumerate(project_cells, start=1):
            if val.strip().lower() == project_name.strip().lower():
                project_row = i
                break

        if project_row is None:
            project_row = len(project_cells) + 1
            ws.update_cell(project_row, col_project, project_name)
            logging.info(f" 🆕  Added new project row at {project_row} for '{project_name}'")

    except Exception as e:
        logging.exception(" ❌  Could not locate or create project row.")
        return f" ❌  Error finding/creating project row: {e}"

    # STEP 5: Write score to resolved cell
    try:
        # --- FIX: Convert score string to number for sheet ---
        score_value = 0
        try:
            score_value = int(score)
        except ValueError:
            logging.warning(f" ⚠️  Score '{score}' is not a number. Saving as 0.")
            score_value = 0 # Default to 0 if conversion fails
            
        ws.update_cell(project_row, col_variable, score_value)
        logging.info(f" ✅  Wrote score {score_value} for '{variable}' under '{project_name}'")
        return f" ✅  Wrote {variable} = {score_value} for '{project_name}'"

    except Exception as e:
        logging.exception(" ❌  Failed to write score to cell.")
        return f" ❌  Error writing score: {e}"

