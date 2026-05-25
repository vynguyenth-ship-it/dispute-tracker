"""
Dispute Case Tracker — Python / Gmail API equivalent of the Apps Script version.

Features:
  - Polls Gmail every 5 minutes for emails sent to GROUP_EMAIL
  - Classifies each email into a queue (Dispute, Invoice, Update Details, etc.)
  - Stores cases in a local SQLite database (cases.db)
  - Syncs active cases to Google Sheets after every poll
  - Sends Slack notifications (individual for ≤5 new cases, summary for larger batches)
  - Archives completed cases older than 1 day
  - Simple CLI commands: list, assign, complete, reclassify, archive, sync-sheet, diagnose

Auth: uses OAuth 2.0 with credentials.json + token.json (Desktop app type from Google Cloud
  Console). On first run a browser window opens for one-time authorisation. The token is
  saved to token.json and refreshed automatically on subsequent runs.

Setup:
  1. pip install -r requirements.txt
  2. Ensure credentials.json is in this folder (Desktop app OAuth client from Cloud Console).
  3. Delete token.json if it exists (forces re-auth with the new Sheets scope).
  4. Set SHEET_ID in CONFIG to your Google Sheet ID.
  5. Set SLACK_WEBHOOK_URL (or export as env var).
  6. Run:  python dispute_tracker.py diagnose    — opens browser on first run, health check
           python dispute_tracker.py poll         — starts the background poller
           python dispute_tracker.py list         — print open cases
"""

import os
import sys
import time
import sqlite3
import logging
import base64
import json
import requests
import schedule
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
    "GROUP_EMAIL": "account.receivable.vn@grabtaxi.com",
    "PROCESSED_LABEL": "dispute-tracker-processed",
    "POLL_INTERVAL_MINUTES": 5,
    "ARCHIVE_AFTER_DAYS": 1,
    "MAX_RESULTS": 100,
    "TIMEOUT_BUFFER_SECONDS": 5 * 60,  # Stop mid-run if taking too long
    "DB_FILE": os.path.join(os.path.dirname(__file__), "cases.db"),
    "CREDENTIALS_FILE": os.path.join(os.path.dirname(__file__), "credentials.json"),
    "TOKEN_FILE": os.path.join(os.path.dirname(__file__), "token.json"),
    "SHEET_ID": "1tdTFveOGwKRm_d8W_1QyJuM7j1Pn_HioSRC3HhOCmd0",
    "SHEET_TAB_NAME": "Cases",
    "ARCHIVE_TAB_NAME": "Archive",
    "TIMEZONE": "Asia/Ho_Chi_Minh",
}

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/spreadsheets",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TZ = ZoneInfo(CONFIG["TIMEZONE"])


# ============================================================
# QUEUE CLASSIFICATION
# ============================================================
QUEUES = [
    {
        "name": "Internal Invoice",
        "keywords": ["internal invoice"],
        "match_subject_only": True,
    },
    {
        "name": "Dispute",
        "keywords": ["chênh lệch", "sai sót", "bảng kê", "thiếu", "biên bản điều chỉnh", "dispute"],
        "match_subject_only": False,
    },
    {
        "name": "Update Details",
        "keywords": ["thông tin", "không chính xác", "thay đổi", "update details", "update info"],
        "match_subject_only": False,
    },
    {
        "name": "Invoice",
        "keywords": ["request invoice", "xuất hóa đơn", "chưa nhận được hóa đơn", "invoice request"],
        "match_subject_only": False,
    },
]


def classify_email(subject: str, body: str) -> str:
    subject_lower = subject.lower()
    full_text = (subject + " " + body).lower()

    for queue in QUEUES:
        haystack = subject_lower if queue["match_subject_only"] else full_text
        for kw in queue["keywords"]:
            if kw.lower() in haystack:
                return queue["name"]
    return "Others"


# ============================================================
# DATABASE
# ============================================================
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(CONFIG["DB_FILE"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                case_id       TEXT PRIMARY KEY,
                message_id    TEXT UNIQUE NOT NULL,
                date_received TEXT,
                sender        TEXT,
                subject       TEXT,
                queue         TEXT,
                status        TEXT DEFAULT 'New',
                assigned_to   TEXT DEFAULT '',
                assigned_at   TEXT DEFAULT '',
                completed_at  TEXT DEFAULT '',
                email_link    TEXT,
                archived      INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_message_id ON cases(message_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON cases(status)")


def get_existing_message_ids() -> set:
    with get_db() as conn:
        rows = conn.execute("SELECT message_id FROM cases").fetchall()
    return {r["message_id"] for r in rows}


def generate_case_id() -> str:
    now = datetime.now(TZ)
    return f"CASE-{now.strftime('%Y%m%d')}-{str(int(time.time() * 1000))[-5:]}"


def insert_cases(rows: list[dict]):
    with get_db() as conn:
        conn.executemany("""
            INSERT OR IGNORE INTO cases
              (case_id, message_id, date_received, sender, subject, queue,
               status, assigned_to, assigned_at, completed_at, email_link)
            VALUES
              (:case_id, :message_id, :date_received, :sender, :subject, :queue,
               'New', '', '', '', :email_link)
        """, rows)


def find_case(case_id: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()


_FIELD_COL = {
    "date_received": 1, "sender": 2, "subject": 3, "queue": 4,
    "status": 5, "assigned_to": 6, "assigned_at": 7,
    "completed_at": 8, "email_link": 9, "reject_reason": 10,
}


def update_case(case_id: str, **fields):
    """Update specific cells for a case row directly in the Google Sheet."""
    if not fields:
        return
    try:
        service = get_sheets_service()
        sheet_id = CONFIG["SHEET_ID"]
        tab = CONFIG["SHEET_TAB_NAME"]

        # Fetch only column A to locate the row
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{tab}!A:A",
        ).execute()
        col_a = result.get("values", [])

        row_num = None
        for i, cell in enumerate(col_a):
            if cell and cell[0] == case_id:
                row_num = i + 1  # 1-based sheet row
                break

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
                spreadsheetId=sheet_id,
                body={"valueInputOption": "RAW", "data": data},
            ).execute()
            log.info(f"update_case: {case_id} → {fields}")
    except Exception as e:
        log.error(f"update_case failed for {case_id}: {e}")


# ============================================================
# AUTH (OAuth 2.0 — personal Gmail)
# ============================================================
def _get_or_refresh_creds() -> Credentials:
    """Load token.json; refresh silently if expired; run browser flow if missing."""
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


def get_gmail_service():
    return build("gmail", "v1", credentials=_get_or_refresh_creds())


def get_sheets_service():
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
            from google.oauth2.service_account import Credentials as SACredentials
            info = dict(st.secrets["gcp_service_account"])
            creds = SACredentials.from_service_account_info(info, scopes=SCOPES)
            return build("sheets", "v4", credentials=creds)
    except Exception:
        pass
    # fallback: use service_account.json locally
    sa_file = os.path.join(os.path.dirname(__file__), "service_account.json")
    if os.path.exists(sa_file):
        from google.oauth2.service_account import Credentials as SACredentials
        creds = SACredentials.from_service_account_file(sa_file, scopes=SCOPES)
        return build("sheets", "v4", credentials=creds)
    return build("sheets", "v4", credentials=_get_or_refresh_creds())


def get_or_create_label(service, name: str) -> str:
    """Returns the label ID, creating it if it doesn't exist."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == name:
            return label["id"]
    created = service.users().labels().create(
        userId="me",
        body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return created["id"]


def apply_label(service, message_id: str, label_id: str):
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": [label_id]},
    ).execute()


def get_message_body(payload: dict) -> str:
    """Recursively extracts plain-text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""

    for part in payload.get("parts", []):
        text = get_message_body(part)
        if text:
            return text
    return ""


# ============================================================
# CORE POLLING LOGIC
# ============================================================
def check_new_emails():
    log.info("=== Polling Gmail ===")
    start = time.time()

    try:
        service = get_gmail_service()
    except Exception as e:
        log.error(f"Gmail auth failed: {e}")
        return

    label_id = get_or_create_label(service, CONFIG["PROCESSED_LABEL"])
    existing_ids = get_existing_message_ids()
    log.info(f"Loaded {len(existing_ids)} existing message IDs from DB.")

    query = f"to:{CONFIG['GROUP_EMAIL']} -label:{CONFIG['PROCESSED_LABEL']}"
    try:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=CONFIG["MAX_RESULTS"]
        ).execute()
    except Exception as e:
        log.error(f"Gmail search failed: {e}")
        return

    messages = result.get("messages", [])
    log.info(f"Found {len(messages)} unprocessed messages.")

    new_rows = []
    slack_queue = []
    labeled_ids = []
    skipped = 0

    for msg_ref in messages:
        if time.time() - start > CONFIG["TIMEOUT_BUFFER_SECONDS"]:
            log.warning("Timeout buffer reached. Stopping early.")
            break

        msg_id = msg_ref["id"]

        if msg_id in existing_ids:
            skipped += 1
            continue

        try:
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
        except Exception as e:
            log.warning(f"Could not fetch message {msg_id}: {e}")
            continue

        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "")
        date_ms = int(msg.get("internalDate", 0))
        date = datetime.fromtimestamp(date_ms / 1000, tz=TZ)
        body = get_message_body(msg.get("payload", {}))
        queue = classify_email(subject, body)
        case_id = generate_case_id()
        email_link = f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"

        row = {
            "case_id": case_id,
            "message_id": msg_id,
            "date_received": date.strftime("%d/%m/%Y %H:%M"),
            "sender": sender,
            "subject": subject,
            "queue": queue,
            "email_link": email_link,
        }
        new_rows.append(row)
        slack_queue.append(row)
        existing_ids.add(msg_id)
        labeled_ids.append(msg_id)

    if new_rows:
        insert_cases(new_rows)
        log.info(f"Wrote {len(new_rows)} new cases to DB.")

    for mid in labeled_ids:
        try:
            apply_label(service, mid, label_id)
        except Exception as e:
            log.warning(f"Could not label {mid}: {e}")

    send_slack_batch(slack_queue)
    sync_to_sheet()

    duration = time.time() - start
    log.info(
        f"=== Done in {duration:.1f}s | New: {len(new_rows)} | Skipped: {skipped} ==="
    )


# ============================================================
# ARCHIVE
# ============================================================
def _parse_completed_at(raw: str) -> datetime | None:
    """Parse completed_at stored as DD/MM/YYYY HH:MM, returning a timezone-aware datetime."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d/%m/%Y %H:%M").replace(tzinfo=TZ)
    except ValueError:
        pass
    # Fallback: try ISO format
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=TZ)
    except ValueError:
        return None


def archive_old_completed() -> int:
    log.info("=== Archive run ===")
    cutoff = datetime.now(TZ) - timedelta(days=CONFIG["ARCHIVE_AFTER_DAYS"])
    rows = load_cases(CONFIG["SHEET_TAB_NAME"])
    archived = 0
    for row in rows:
        status = row.get("status", "")
        if status not in ("Completed", "Rejected"):
            continue
        # Use completed_at for Completed; fall back to assigned_at for Rejected
        timestamp_raw = row.get("completed_at") or row.get("assigned_at", "")
        closed_dt = _parse_completed_at(timestamp_raw)
        if closed_dt is None:
            continue
        if closed_dt < cutoff:
            archive_case(row["case_id"])
            archived += 1
    log.info(f"Archived {archived} cases older than {CONFIG['ARCHIVE_AFTER_DAYS']} day(s).")
    return archived


# ============================================================
# GOOGLE SHEETS SYNC
# ============================================================
SHEET_COLUMNS = [
    "Case ID", "Date Received", "Sender", "Subject", "Queue",
    "Status", "Assigned To", "Assigned At", "Completed At", "Email Link", "Reject Reason",
]


def _ensure_tab_exists(service, spreadsheet_id: str, tab_name: str):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if tab_name not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        log.info(f"Created sheet tab: {tab_name}")


# Pastel background colours per queue (RGB 0-1 scale)
QUEUE_SHEET_COLORS = {
    "Dispute":          {"red": 1.0,  "green": 0.82, "blue": 0.82},
    "Update Details":   {"red": 0.80, "green": 0.88, "blue": 1.0 },
    "Invoice":          {"red": 0.83, "green": 0.97, "blue": 0.83},
    "Internal Invoice": {"red": 0.92, "green": 0.85, "blue": 1.0 },
    "Others":           {"red": 0.95, "green": 0.95, "blue": 0.95},
}
_HEADER_COLOR = {"red": 0.0, "green": 0.694, "blue": 0.310}   # #00B14F Grab green
_HEADER_TEXT  = {"red": 1.0, "green": 1.0,   "blue": 1.0}


def _fmt_date(val: str) -> str:
    """Return dd/mm/yyyy only — strips HH:MM if present."""
    if not val:
        return val
    return val[:10]


def _get_sheet_tab_id(service, spreadsheet_id: str, tab_name: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    raise ValueError(f"Tab '{tab_name}' not found in spreadsheet.")


def _write_tab(service, sheet_id: str, tab_name: str, rows):
    _ensure_tab_exists(service, sheet_id, tab_name)
    service.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=f"{tab_name}!A:Z",
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{tab_name}!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()


def _apply_sheet_formatting(service, spreadsheet_id: str, tab_name: str, data_rows: list):
    """Apply header colour + per-row queue background colours."""
    tab_id = _get_sheet_tab_id(service, spreadsheet_id, tab_name)
    n_cols = len(SHEET_COLUMNS)
    QUEUE_COL = 4

    requests = []

    requests.append({
        "repeatCell": {
            "range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": n_cols},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": _HEADER_COLOR,
                    "textFormat": {"bold": True, "foregroundColor": _HEADER_TEXT},
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat)",
        }
    })

    requests.append({
        "updateSheetProperties": {
            "properties": {"sheetId": tab_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }
    })

    for i, row in enumerate(data_rows, start=1):
        queue = row[QUEUE_COL] if len(row) > QUEUE_COL else ""
        color = QUEUE_SHEET_COLORS.get(queue, {"red": 1.0, "green": 1.0, "blue": 1.0})
        requests.append({
            "repeatCell": {
                "range": {"sheetId": tab_id, "startRowIndex": i, "endRowIndex": i + 1,
                          "startColumnIndex": 0, "endColumnIndex": n_cols},
                "cell": {"userEnteredFormat": {"backgroundColor": color}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        })

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()


def sync_to_sheet():
    sheet_id = CONFIG["SHEET_ID"]

    if sheet_id == "YOUR_SHEET_ID_HERE":
        log.warning("SHEET_ID not configured — skipping sheet sync.")
        return

    try:
        service = get_sheets_service()

        with get_db() as conn:
            active_rows = conn.execute(
                "SELECT case_id, date_received, sender, subject, queue, "
                "status, assigned_to, assigned_at, completed_at, email_link "
                "FROM cases WHERE archived = 0 ORDER BY date_received DESC"
            ).fetchall()

        active_data = [
            [r["case_id"], _fmt_date(r["date_received"]), r["sender"], r["subject"],
             r["queue"], r["status"], r["assigned_to"], r["assigned_at"],
             r["completed_at"], r["email_link"]]
            for r in active_rows
        ]
        _write_tab(service, sheet_id, CONFIG["SHEET_TAB_NAME"], [SHEET_COLUMNS] + active_data)
        _apply_sheet_formatting(service, sheet_id, CONFIG["SHEET_TAB_NAME"], active_data)
        log.info(f"Synced {len(active_rows)} active cases → '{CONFIG['SHEET_TAB_NAME']}' tab.")

        with get_db() as conn:
            archive_rows = conn.execute(
                "SELECT case_id, date_received, sender, subject, queue, "
                "status, assigned_to, assigned_at, completed_at, email_link "
                "FROM cases WHERE archived = 1 ORDER BY completed_at DESC"
            ).fetchall()

        archive_data = [
            [r["case_id"], _fmt_date(r["date_received"]), r["sender"], r["subject"],
             r["queue"], r["status"], r["assigned_to"], r["assigned_at"],
             r["completed_at"], r["email_link"]]
            for r in archive_rows
        ]
        _write_tab(service, sheet_id, CONFIG["ARCHIVE_TAB_NAME"], [SHEET_COLUMNS] + archive_data)
        _apply_sheet_formatting(service, sheet_id, CONFIG["ARCHIVE_TAB_NAME"], archive_data)
        log.info(f"Synced {len(archive_rows)} archived cases → '{CONFIG['ARCHIVE_TAB_NAME']}' tab.")

    except Exception as e:
        log.error(f"Sheet sync failed: {e}")


# ============================================================
# SLACK NOTIFICATIONS
# ============================================================
QUEUE_EMOJI = {
    "Dispute": "🚨",
    "Update Details": "📝",
    "Invoice": "🧾",
    "Internal Invoice": "🏢",
    "Others": "📨",
}


def send_slack_raw(text: str):
    if not SLACK_WEBHOOK_URL:
        log.warning("SLACK_WEBHOOK_URL not set — skipping Slack notification.")
        return
    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": text},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning(f"Slack webhook returned {resp.status_code}: {resp.text}")
    except Exception as e:
        log.error(f"Slack webhook error: {e}")


def send_slack_notification(case: dict):
    emoji = QUEUE_EMOJI.get(case["queue"], "📨")
    text = (
        f"{emoji} *New {case['queue']} Case*\n"
        f"*Case ID:* {case['case_id']}\n"
        f"*From:* {case['sender']}\n"
        f"*Subject:* {case['subject']}\n"
        f"*Email:* {case['email_link']}"
    )
    send_slack_raw(text)


def send_slack_batch(items: list[dict]):
    if not items:
        return

    if len(items) <= 5:
        for item in items:
            send_slack_notification(item)
            time.sleep(0.2)
        return

    by_queue: dict[str, int] = {}
    for item in items:
        by_queue[item["queue"]] = by_queue.get(item["queue"], 0) + 1

    lines = [f"📥 *{len(items)} new cases received*", "━" * 24]
    for q, count in by_queue.items():
        lines.append(f"{QUEUE_EMOJI.get(q, '📨')} *{q}:* {count}")
    lines.append("")
    lines.append("First few cases:")
    for item in items[:5]:
        lines.append(f"• `{item['case_id']}` - {item['subject']}")
    if len(items) > 5:
        lines.append(f"...and {len(items) - 5} more.")

    send_slack_raw("\n".join(lines))


# ============================================================
# CLI COMMANDS
# ============================================================
def cmd_list(args):
    status_filter = args[0] if args else None
    with get_db() as conn:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM cases WHERE archived = 0 AND status = ? ORDER BY date_received DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cases WHERE archived = 0 ORDER BY date_received DESC"
            ).fetchall()

    if not rows:
        print("No cases found.")
        return

    print(f"\n{'Case ID':<22} {'Date':<17} {'Queue':<18} {'Status':<12} {'Sender'}")
    print("-" * 100)
    for r in rows:
        print(
            f"{r['case_id']:<22} {r['date_received']:<17} {r['queue']:<18} "
            f"{r['status']:<12} {r['sender'][:40]}"
        )
    print(f"\n{len(rows)} case(s).")


def cmd_assign(args):
    if not args:
        print("Usage: python dispute_tracker.py assign <CASE-ID> [user@email.com]")
        return
    case_id = args[0]
    user = args[1] if len(args) > 1 else os.environ.get("USER", "unknown")
    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    case = find_case(case_id)
    if not case:
        print(f"Case not found: {case_id}")
        return
    update_case(case_id, status="In Progress", assigned_to=user, assigned_at=now)
    print(f"Assigned {case_id} to {user}.")


def cmd_complete(args):
    if not args:
        print("Usage: python dispute_tracker.py complete <CASE-ID>")
        return
    case_id = args[0]
    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    case = find_case(case_id)
    if not case:
        print(f"Case not found: {case_id}")
        return
    update_case(case_id, status="Completed", completed_at=now)
    print(f"Completed {case_id}.")


def cmd_reclassify(args):
    if len(args) < 2:
        print("Usage: python dispute_tracker.py reclassify <CASE-ID> <Queue>")
        print("Queues: Dispute, Invoice, Update Details, Internal Invoice, Others")
        return
    case_id, queue = args[0], args[1]
    case = find_case(case_id)
    if not case:
        print(f"Case not found: {case_id}")
        return
    update_case(case_id, queue=queue)
    print(f"Reclassified {case_id} → {queue}.")


def cmd_archive(_args):
    archive_old_completed()


def cmd_poll(_args):
    log.info(f"Starting poller — checking every {CONFIG['POLL_INTERVAL_MINUTES']} min.")
    check_new_emails()  # run once immediately
    schedule.every(CONFIG["POLL_INTERVAL_MINUTES"]).minutes.do(check_new_emails)
    schedule.every().day.at("23:00").do(archive_old_completed)

    while True:
        schedule.run_pending()
        time.sleep(30)


def cmd_diagnose(_args):
    print("\n=== Dispute Tracker Diagnostic ===")

    if SLACK_WEBHOOK_URL:
        print("✅ SLACK_WEBHOOK_URL is set")
    else:
        print("⚠️  SLACK_WEBHOOK_URL not set (export it or edit this file)")

    try:
        with get_db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM cases WHERE archived = 0").fetchone()[0]
            archived = conn.execute("SELECT COUNT(*) FROM cases WHERE archived = 1").fetchone()[0]
        print(f"✅ Database OK — {total} active cases, {archived} archived")
    except Exception as e:
        print(f"❌ DB error: {e}")

    try:
        service = get_gmail_service()
        profile = service.users().getProfile(userId="me").execute()
        print(f"✅ Gmail authenticated as {profile['emailAddress']}")
        query = f"to:{CONFIG['GROUP_EMAIL']} -label:{CONFIG['PROCESSED_LABEL']}"
        result = service.users().messages().list(userId="me", q=query, maxResults=5).execute()
        count = result.get("resultSizeEstimate", 0)
        print(f"📧 Unprocessed emails (estimate): {count}")
    except Exception as e:
        print(f"❌ Gmail error: {e}")

    print("=== Done ===\n")


def cmd_test_classify(_args):
    TEST_CASES = [
        ("Khiếu nại chênh lệch hóa đơn tháng 4", "chênh lệch trong bảng kê", "Dispute"),
        ("Yêu cầu cập nhật thông tin công ty", "thông tin không chính xác", "Update Details"),
        ("Request Invoice for March 2025", "chưa nhận được hóa đơn", "Invoice"),
        ("Internal Invoice Q1 Settlement", "internal invoice between entities", "Internal Invoice"),
        ("General inquiry about payment", "question about my recent payment", "Others"),
    ]
    passed = 0
    for subject, body, expected in TEST_CASES:
        got = classify_email(subject, body)
        ok = got == expected
        if ok:
            passed += 1
        print(f"[{'PASS' if ok else 'FAIL'}] \"{subject[:45]}\" → {got}" + ("" if ok else f" (expected {expected})"))
    print(f"\n{passed}/{len(TEST_CASES)} passed.")


def cmd_debug_archive(_args):
    print("\n=== Archive Debug ===")
    cutoff = datetime.now(TZ) - timedelta(days=CONFIG["ARCHIVE_AFTER_DAYS"])
    print(f"Cutoff: cases completed before {cutoff.strftime('%d/%m/%Y')} will be archived")
    print(f"Today: {datetime.now(TZ).strftime('%d/%m/%Y')}\n")

    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM cases WHERE status = 'Completed' AND archived = 0"
        ).fetchall()

    if not rows:
        print("No completed cases.")
        return

    would_archive = 0
    for row in rows:
        completed_dt = _parse_completed_at(row["completed_at"])
        if completed_dt is None:
            print(f"⚠️  {row['case_id']} — could not parse completed_at: {row['completed_at']!r}")
            continue
        age_days = (datetime.now(TZ) - completed_dt).days
        if completed_dt < cutoff:
            would_archive += 1
            print(f"✅ {row['case_id']} — completed {row['completed_at']} ({age_days}d ago) → WOULD ARCHIVE")
        else:
            print(f"⏳ {row['case_id']} — completed {row['completed_at']} ({age_days}d ago) → too recent, keep")

    print(f"\nTotal completed: {len(rows)} | Would archive: {would_archive}")
    print("=== Done ===\n")


def cmd_diagnose_rows(_args):
    print("\n=== Sheet Health Check ===")

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM cases WHERE archived = 0").fetchall()

    print(f"Total active rows: {len(rows)}")

    empty_rows = 0
    in_progress_rows = 0
    in_progress_empty = 0

    for row in rows:
        if not row["case_id"] or not row["case_id"].strip():
            empty_rows += 1
            print(f"⚠️  Empty case_id row: message_id={row['message_id']!r}")

        if row["status"] == "In Progress":
            in_progress_rows += 1
            if not row["case_id"]:
                in_progress_empty += 1
                print(f"❌ In Progress row has no case_id! message_id={row['message_id']!r}")

    print(f"\n=== Summary ===")
    print(f"Empty rows (no case_id): {empty_rows}")
    print(f"Total In Progress: {in_progress_rows}")
    print(f"In Progress with no case_id: {in_progress_empty}")
    print("=== Done ===\n")


def cmd_cleanup_corrupted(_args):
    with get_db() as conn:
        result = conn.execute(
            "DELETE FROM cases WHERE case_id IS NULL OR TRIM(case_id) = ''"
        )
        deleted = result.rowcount
    print(f"Cleaned up {deleted} corrupted row(s).")


def cmd_test_archive(_args):
    print("\n=== Archive Test Started ===")

    now = datetime.now(TZ)
    old_dt = now - timedelta(days=5)
    old_completed_at = old_dt.strftime("%d/%m/%Y %H:%M")
    today_completed_at = now.strftime("%d/%m/%Y %H:%M")
    today_received = now.strftime("%d/%m/%Y %H:%M")

    test_rows = [
        {"case_id": "ARCHTEST-001", "message_id": "archtest-msg-001", "date_received": today_received,
         "sender": "old.user.1@test.com", "subject": "[ARCHTEST] Old case 1", "queue": "Dispute",
         "email_link": "https://mail.google.com"},
        {"case_id": "ARCHTEST-002", "message_id": "archtest-msg-002", "date_received": today_received,
         "sender": "old.user.2@test.com", "subject": "[ARCHTEST] Old case 2", "queue": "Invoice",
         "email_link": "https://mail.google.com"},
        {"case_id": "ARCHTEST-003", "message_id": "archtest-msg-003", "date_received": today_received,
         "sender": "today.user@test.com", "subject": "[ARCHTEST] Today completed", "queue": "Invoice",
         "email_link": "https://mail.google.com"},
        {"case_id": "ARCHTEST-004", "message_id": "archtest-msg-004", "date_received": today_received,
         "sender": "stuck.user@test.com", "subject": "[ARCHTEST] In progress", "queue": "Others",
         "email_link": "https://mail.google.com"},
        {"case_id": "ARCHTEST-005", "message_id": "archtest-msg-005", "date_received": today_received,
         "sender": "new.user@test.com", "subject": "[ARCHTEST] Brand new", "queue": "Others",
         "email_link": "https://mail.google.com"},
    ]

    insert_cases(test_rows)
    update_case("ARCHTEST-001", status="Completed", completed_at=old_completed_at)
    update_case("ARCHTEST-002", status="Completed", completed_at=old_completed_at)
    update_case("ARCHTEST-003", status="Completed", completed_at=today_completed_at)
    update_case("ARCHTEST-004", status="In Progress")

    with get_db() as conn:
        before_active = conn.execute("SELECT COUNT(*) FROM cases WHERE archived = 0").fetchone()[0]
        before_archived = conn.execute("SELECT COUNT(*) FROM cases WHERE archived = 1").fetchone()[0]

    print(f"BEFORE: active={before_active}, archived={before_archived}")
    print("Created 5 test cases (2 should archive, 3 should not)\n")

    print("--- Running archive_old_completed() ---")
    archive_old_completed()

    with get_db() as conn:
        after_active = conn.execute("SELECT COUNT(*) FROM cases WHERE archived = 0").fetchone()[0]
        after_archived = conn.execute("SELECT COUNT(*) FROM cases WHERE archived = 1").fetchone()[0]

    active_delta = after_active - before_active
    archived_delta = after_archived - before_archived

    print(f"\nAFTER: active={after_active} (was {before_active}), archived={after_archived} (was {before_archived})")
    print("\n=== Verification ===")
    if active_delta == 3 and archived_delta == 2:
        print(f"✅ PASS: active +{active_delta} (expected +3)")
        print(f"✅ PASS: archived +{archived_delta} (expected +2)")
        print("✅ Archive function works correctly!")
    else:
        print(f"❌ FAIL: active delta={active_delta} (expected +3), archived delta={archived_delta} (expected +2)")

    print("\nRun 'cleanup-test' to remove ARCHTEST-* rows.")
    print("=== Test Done ===\n")


def cmd_cleanup_test(_args):
    with get_db() as conn:
        result = conn.execute(
            "DELETE FROM cases WHERE case_id LIKE 'TEST-%' OR case_id LIKE 'ARCHTEST-%'"
        )
        deleted = result.rowcount
    print(f"Cleaned up {deleted} test row(s).")


def cmd_test_assign(_args):
    with get_db() as conn:
        row = conn.execute(
            "SELECT case_id FROM cases WHERE status = 'New' AND archived = 0 LIMIT 1"
        ).fetchone()

    if not row:
        print("No 'New' case found to test with.")
        return

    case_id = row["case_id"]
    print(f"Testing assign on: {case_id}")

    user = os.environ.get("USER", "test@grab.com")
    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")

    try:
        update_case(case_id, status="In Progress", assigned_to=user, assigned_at=now)
        result = find_case(case_id)
        if result and result["status"] == "In Progress":
            print(f"✅ assignCase succeeded: assigned_to={result['assigned_to']}, at={result['assigned_at']}")
        else:
            print("❌ Update did not apply correctly.")
    except Exception as e:
        print(f"❌ Error: {e}")


def load_cases(tab_name: str | None = None) -> list[dict]:
    """Read all rows from a sheet tab; return as list of field-keyed dicts for the Streamlit UI."""
    tab = tab_name or CONFIG["SHEET_TAB_NAME"]
    _COL_MAP = {
        "Case ID": "case_id", "Date Received": "date_received", "Sender": "sender",
        "Subject": "subject", "Queue": "queue", "Status": "status",
        "Assigned To": "assigned_to", "Assigned At": "assigned_at",
        "Completed At": "completed_at", "Email Link": "email_link",
        "Reject Reason": "reject_reason",
    }
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
            d = {}
            for i, h in enumerate(header):
                field = _COL_MAP.get(h, h.lower().replace(" ", "_"))
                d[field] = row[i] if i < len(row) else ""
            if any(d.values()):
                rows.append(d)
        return rows
    except Exception as e:
        log.error(f"load_cases from '{tab}' failed: {e}")
        return []


def archive_case(case_id: str):
    """Move a single row from the Cases tab to the Archive tab."""
    try:
        service = get_sheets_service()
        sheet_id = CONFIG["SHEET_ID"]
        cases_tab = CONFIG["SHEET_TAB_NAME"]
        archive_tab = CONFIG["ARCHIVE_TAB_NAME"]

        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{cases_tab}!A:J",
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
            spreadsheetId=sheet_id, range=f"{archive_tab}!A:J",
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


def cmd_sync_sheet(_args):
    sync_to_sheet()


def cmd_list_triggers(_args):
    jobs = schedule.jobs
    print(f"\n=== Scheduled Jobs ({len(jobs)}) ===")
    if not jobs:
        print("No jobs scheduled. Run 'poll' to start the poller.")
        return
    for job in jobs:
        print(f"  {job}")
    print("=== Done ===\n")


COMMANDS = {
    "list": cmd_list,
    "assign": cmd_assign,
    "complete": cmd_complete,
    "reclassify": cmd_reclassify,
    "archive": cmd_archive,
    "poll": cmd_poll,
    "diagnose": cmd_diagnose,
    "test-classify": cmd_test_classify,
    "debug-archive": cmd_debug_archive,
    "diagnose-rows": cmd_diagnose_rows,
    "cleanup-corrupted": cmd_cleanup_corrupted,
    "test-archive": cmd_test_archive,
    "cleanup-test": cmd_cleanup_test,
    "test-assign": cmd_test_assign,
    "sync-sheet": cmd_sync_sheet,
    "list-triggers": cmd_list_triggers,
}

USAGE = """
Usage: python dispute_tracker.py <command> [args]

Core commands:
  poll                         Start the background email poller (runs indefinitely)
  list [status]                List cases (optional filter: New / In Progress / Completed)
  assign <CASE-ID> [email]     Assign a case to yourself or a given email
  complete <CASE-ID>           Mark a case as completed
  reclassify <CASE-ID> <Queue> Change the queue of a case
  archive                      Archive completed cases older than 1 day
  sync-sheet                   Push all active cases to Google Sheets

Diagnostic commands:
  diagnose                     Health check (Gmail auth, DB, Slack)
  diagnose-rows                Check DB for empty or corrupted rows
  debug-archive                Preview which cases would be archived (dry run)
  list-triggers                Show currently scheduled jobs

Test & utility commands:
  test-classify                Test the keyword classification logic
  test-archive                 Create test data and verify archive logic end-to-end
  test-assign                  Test the assign flow on the first New case
  cleanup-test                 Remove all TEST-* and ARCHTEST-* rows from DB
  cleanup-corrupted            Delete rows with empty case_id from DB
"""

if __name__ == "__main__":
    init_db()

    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(USAGE)
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]
    COMMANDS[cmd](args)
