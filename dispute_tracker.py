"""
Dispute Tracker — Sheets interface for the Streamlit UI.

Gmail polling and archiving are handled entirely by Google Apps Script
triggers on the spreadsheet. This module only provides the read/write
functions that the Streamlit UI needs.

Exported:
    CONFIG, TZ
    load_cases(tab_name)          — read all rows from a sheet tab
    update_case(case_id, **fields) — write specific cells for a case
    archive_case(case_id)          — move a row from Cases → Archive tab
    archive_old_completed()        — archive completed/rejected cases > 1 day old
"""
#===========================================================================
# import libraries and modules
#===========================================================================
import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ============================================================
# CONFIGURATION
# ============================================================
CONFIG = {
    "GROUP_EMAIL":      "account.receivable.vn@grabtaxi.com",
    "SHEET_ID":         "1tdTFveOGwKRm_d8W_1QyJuM7j1Pn_HioSRC3HhOCmd0",
    "SHEET_TAB_NAME":   "Cases",
    "ARCHIVE_TAB_NAME": "Archive",
    "ARCHIVE_AFTER_DAYS": 1,
    "TIMEZONE":         "Asia/Ho_Chi_Minh",
    "CREDENTIALS_FILE": os.path.join(os.path.dirname(__file__), "credentials.json"),
    "TOKEN_FILE":       os.path.join(os.path.dirname(__file__), "token.json"),
}

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # Only Sheets API access is needed for this app

TZ = ZoneInfo(CONFIG["TIMEZONE"])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SHEET_COLUMNS = [
    "Case ID", "Date Received", "Sender", "Subject", "Queue",
    "Status", "Assigned To", "Assigned At", "Completed At", "Email Link", "Reject Reason",
]

_COL_MAP = {
    "Case ID": "case_id", "Date Received": "date_received", "Sender": "sender",
    "Subject": "subject", "Queue": "queue", "Status": "status",
    "Assigned To": "assigned_to", "Assigned At": "assigned_at",
    "Completed At": "completed_at", "Email Link": "email_link",
    "Reject Reason": "reject_reason",
}

_FIELD_COL = {
    "date_received": 1, "sender": 2, "subject": 3, "queue": 4,
    "status": 5, "assigned_to": 6, "assigned_at": 7,
    "completed_at": 8, "email_link": 9, "reject_reason": 10,
}


# ============================================================
# AUTH
# ============================================================
def _get_or_refresh_creds() -> Credentials:
    creds = None
    token_file = CONFIG["TOKEN_FILE"]
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CONFIG["CREDENTIALS_FILE"], SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(token_file, "w") as f:
            f.write(creds.to_json())
    return creds


def get_sheets_service():
    # On Streamlit Cloud: use service account from st.secrets
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
            from google.oauth2.service_account import Credentials as SACredentials
            creds = SACredentials.from_service_account_info(
                dict(st.secrets["gcp_service_account"]), scopes=SCOPES
            )
            return build("sheets", "v4", credentials=creds)
    except Exception:
        pass
    # Local fallback: service_account.json or OAuth token
    sa_file = os.path.join(os.path.dirname(__file__), "service_account.json")
    if os.path.exists(sa_file):
        from google.oauth2.service_account import Credentials as SACredentials
        creds = SACredentials.from_service_account_file(sa_file, scopes=SCOPES)
        return build("sheets", "v4", credentials=creds)
    return build("sheets", "v4", credentials=_get_or_refresh_creds())


# ============================================================
# SHEET READ / WRITE
# ============================================================  
def load_cases(tab_name: str | None = None) -> list[dict]:
    """Read all rows from a sheet tab; return as list of field-keyed dicts."""
    tab = tab_name or CONFIG["SHEET_TAB_NAME"]
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=CONFIG["SHEET_ID"],
            range=f"{tab}!A:K",
        ).execute()
        values = result.get("values", [])
        if len(values) < 2:
            return []
        header = values[0]
        rows = []
        for row in values[1:]:
            d = {
                _COL_MAP.get(h, h.lower().replace(" ", "_")): (row[i] if i < len(row) else "")
                for i, h in enumerate(header)
            }
            if any(d.values()):
                rows.append(d)
        return rows
    except Exception as e:
        log.error(f"load_cases from '{tab}' failed: {e}")
        return []


def _find_case_row(service, case_id: str) -> int | None:
    """Return 1-based row number of case_id in the Cases tab, or None."""
    result = service.spreadsheets().values().get(
        spreadsheetId=CONFIG["SHEET_ID"],
        range=f"{CONFIG['SHEET_TAB_NAME']}!A:A",
    ).execute()
    col_a = [r[0] if r else "" for r in result.get("values", [])]
    try:
        return col_a.index(case_id) + 1
    except ValueError:
        return None


def update_case(case_id: str, **fields):
    """Write specific cells for a case directly in the Google Sheet."""
    if not fields:
        return
    try:
        service = get_sheets_service()
        tab = CONFIG["SHEET_TAB_NAME"]
        row_num = _find_case_row(service, case_id)
        if row_num is None:
            log.error(f"update_case: {case_id} not found in sheet '{tab}'")
            return
        data = []
        for field, value in fields.items():
            col_idx = _FIELD_COL.get(field)
            if col_idx is None:
                continue
            col_letter = chr(ord("A") + col_idx)
            data.append({"range": f"{tab}!{col_letter}{row_num}", "values": [[value]]})
        if data:
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=CONFIG["SHEET_ID"],
                body={"valueInputOption": "RAW", "data": data},
            ).execute()
            log.info(f"update_case: {case_id} → {fields}")
    except Exception as e:
        log.error(f"update_case failed for {case_id}: {e}")


def archive_case(case_id: str):
    """Move a single row from the Cases tab to the Archive tab."""
    try:
        service = get_sheets_service()
        sheet_id = CONFIG["SHEET_ID"]
        cases_tab = CONFIG["SHEET_TAB_NAME"]
        archive_tab = CONFIG["ARCHIVE_TAB_NAME"]

        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{cases_tab}!A:K",
        ).execute()
        values = result.get("values", [])
        row_num = next((i + 1 for i, r in enumerate(values) if r and r[0] == case_id), None)
        if row_num is None:
            log.error(f"archive_case: {case_id} not found")
            return

        row_data = values[row_num - 1][:]
        while len(row_data) < len(SHEET_COLUMNS):
            row_data.append("")

        meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        tab_ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}

        if archive_tab not in tab_ids:
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": archive_tab}}}]},
            ).execute()
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"{archive_tab}!A1",
                valueInputOption="RAW", body={"values": [SHEET_COLUMNS]},
            ).execute()
            meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
            tab_ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}

        service.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=f"{archive_tab}!A:K",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": [row_data]},
        ).execute()

        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"deleteDimension": {"range": {
                "sheetId": tab_ids[cases_tab], "dimension": "ROWS",
                "startIndex": row_num - 1, "endIndex": row_num,
            }}}]},
        ).execute()
        log.info(f"Archived {case_id}")
    except Exception as e:
        log.error(f"archive_case failed for {case_id}: {e}")


# ============================================================
# ARCHIVE
# ============================================================
def _parse_completed_at(raw: str) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=TZ)
    except ValueError:
        return None


def archive_old_completed() -> int:
    """Move Completed/Rejected cases older than ARCHIVE_AFTER_DAYS to the Archive tab."""
    log.info("=== Archive run ===")
    cutoff = datetime.now(TZ) - timedelta(days=CONFIG["ARCHIVE_AFTER_DAYS"])
    rows = load_cases(CONFIG["SHEET_TAB_NAME"])
    archived = 0
    for row in rows:
        if row.get("status") not in ("Completed", "Rejected"):
            continue
        timestamp_raw = row.get("completed_at") or row.get("assigned_at", "")
        closed_dt = _parse_completed_at(timestamp_raw)
        if closed_dt and closed_dt < cutoff:
            archive_case(row["case_id"])
            archived += 1
    log.info(f"Archived {archived} case(s) older than {CONFIG['ARCHIVE_AFTER_DAYS']} day(s).")
    return archived
