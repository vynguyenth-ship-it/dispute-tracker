"""
Dispute Case Tracker — Google Sheets as the sole database.

Features:
  - Polls Gmail every 5 minutes for emails sent to GROUP_EMAIL
  - Classifies each email into a queue (Dispute, Invoice, Update Details, etc.)
  - Appends new cases directly to Google Sheets (no local SQLite)
  - Sends Slack notifications (individual for ≤5 new cases, summary for larger batches)
  - Archives completed/rejected cases older than 1 day into the Archive tab
  - Simple CLI commands: list, assign, complete, reclassify, archive, diagnose

Auth: uses OAuth 2.0 with credentials.json + token.json (Desktop app type from Google Cloud
  Console). On first run a browser window opens for one-time authorisation. The token is
  saved to token.json and refreshed automatically on subsequent runs.

Setup:
  1. pip install -r requirements.txt
  2. Ensure credentials.json is in this folder (Desktop app OAuth client from Cloud Console).
  3. Delete token.json if it exists (forces re-auth with the new Sheets scope).
  4. Run:  python dispute_tracker.py diagnose    — opens browser on first run, health check
           python dispute_tracker.py poll         — starts the background poller
           python dispute_tracker.py list         — print open cases
"""

import os
import sys
import time
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
    "TIMEOUT_BUFFER_SECONDS": 5 * 60,
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
        "keywords": ["chênh lệch", "sai sót", "bảng kê", "thiếu", "biên bản điều chỉnh",
                     "dispute", "Đối chiếu", "sai lệch", "cấn trừ"],
        "match_subject_only": False,
    },
    {
        "name": "Update Details",
        "keywords": ["thông tin", "không chính xác", "thay đổi", "update details", "update info"],
        "match_subject_only": False,
    },
    {
        "name": "Invoice",
        "keywords": ["request invoice", "xuất hóa đơn", "chưa nhận được hóa đơn",
                     "invoice request", "cung cấp hóa đơn", "cung cấp"],
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
# AUTH (OAuth 2.0 — personal Gmail)
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
    sa_file = os.path.join(os.path.dirname(__file__), "service_account.json")
    if os.path.exists(sa_file):
        from google.oauth2.service_account import Credentials as SACredentials
        creds = SACredentials.from_service_account_file(sa_file, scopes=SCOPES)
        return build("sheets", "v4", credentials=creds)
    return build("sheets", "v4", credentials=_get_or_refresh_creds())


# ============================================================
# GOOGLE SHEETS — CORE HELPERS
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


def _get_existing_message_ids() -> set:
    """Extract message IDs from the Email Link column (col J) in the Cases tab."""
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=CONFIG["SHEET_ID"],
            range=f"{CONFIG['SHEET_TAB_NAME']}!J:J",
        ).execute()
        values = result.get("values", [])
        msg_ids = set()
        for row in values[1:]:
            if row and row[0]:
                msg_ids.add(row[0].split("/")[-1])
        return msg_ids
    except Exception as e:
        log.error(f"Failed to load existing message IDs from sheet: {e}")
        return set()


def generate_case_id() -> str:
    now = datetime.now(TZ)
    return f"CASE-{now.strftime('%Y%m%d')}-{str(int(time.time() * 1000))[-5:]}"


def append_cases_to_sheet(rows: list[dict]):
    """Append new case rows directly to the Cases tab."""
    if not rows:
        return
    service = get_sheets_service()
    values = [
        [
            r["case_id"], r["date_received"], r["sender"], r["subject"],
            r["queue"], "New", "", "", "", r["email_link"], "",
        ]
        for r in rows
    ]
    service.spreadsheets().values().append(
        spreadsheetId=CONFIG["SHEET_ID"],
        range=f"{CONFIG['SHEET_TAB_NAME']}!A:K",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()
    log.info(f"Appended {len(rows)} new case(s) to sheet.")


_FIELD_COL = {
    "date_received": 1, "sender": 2, "subject": 3, "queue": 4,
    "status": 5, "assigned_to": 6, "assigned_at": 7,
    "completed_at": 8, "email_link": 9, "reject_reason": 10,
}


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
    """Update specific cells for a case row directly in the Google Sheet."""
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
    try:
        return datetime.strptime(raw, "%d/%m/%Y %H:%M").replace(tzinfo=TZ)
    except ValueError:
        pass
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
# GMAIL HELPERS
# ============================================================
def get_or_create_label(service, name: str) -> str:
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
    existing_ids = _get_existing_message_ids()
    log.info(f"Loaded {len(existing_ids)} existing message IDs from sheet.")

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
        email_link = f"https://mail.google.com/mail/u/{CONFIG['GROUP_EMAIL']}/#all/{msg_id}"

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
        append_cases_to_sheet(new_rows)

    for mid in labeled_ids:
        try:
            apply_label(service, mid, label_id)
        except Exception as e:
            log.warning(f"Could not label {mid}: {e}")

    send_slack_batch(slack_queue)

    duration = time.time() - start
    log.info(f"=== Done in {duration:.1f}s | New: {len(new_rows)} | Skipped: {skipped} ===")


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
        resp = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
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
    lines += ["", "First few cases:"]
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
    rows = load_cases()
    if status_filter:
        rows = [r for r in rows if r.get("status") == status_filter]
    if not rows:
        print("No cases found.")
        return
    print(f"\n{'Case ID':<22} {'Date':<17} {'Queue':<18} {'Status':<12} {'Sender'}")
    print("-" * 100)
    for r in rows:
        print(
            f"{r.get('case_id',''):<22} {r.get('date_received',''):<17} {r.get('queue',''):<18} "
            f"{r.get('status',''):<12} {r.get('sender','')[:40]}"
        )
    print(f"\n{len(rows)} case(s).")


def cmd_assign(args):
    if not args:
        print("Usage: python dispute_tracker.py assign <CASE-ID> [user@email.com]")
        return
    case_id = args[0]
    user = args[1] if len(args) > 1 else os.environ.get("USER", "unknown")
    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    update_case(case_id, status="In Progress", assigned_to=user, assigned_at=now)
    print(f"Assigned {case_id} to {user}.")


def cmd_complete(args):
    if not args:
        print("Usage: python dispute_tracker.py complete <CASE-ID>")
        return
    case_id = args[0]
    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    update_case(case_id, status="Completed", completed_at=now)
    print(f"Completed {case_id}.")


def cmd_reclassify(args):
    if len(args) < 2:
        print("Usage: python dispute_tracker.py reclassify <CASE-ID> <Queue>")
        print("Queues: Dispute, Invoice, Update Details, Internal Invoice, Others")
        return
    case_id, queue = args[0], args[1]
    update_case(case_id, queue=queue)
    print(f"Reclassified {case_id} → {queue}.")


def cmd_archive(_args):
    archive_old_completed()


def cmd_poll(_args):
    log.info(f"Starting poller — checking every {CONFIG['POLL_INTERVAL_MINUTES']} min.")
    check_new_emails()
    schedule.every(CONFIG["POLL_INTERVAL_MINUTES"]).minutes.do(check_new_emails)
    schedule.every().day.at("23:00").do(archive_old_completed)
    while True:
        schedule.run_pending()
        time.sleep(30)


def cmd_diagnose(_args):
    print("\n=== Dispute Tracker Diagnostic ===")
    print("✅ SLACK_WEBHOOK_URL is set" if SLACK_WEBHOOK_URL else "⚠️  SLACK_WEBHOOK_URL not set")

    try:
        cases = load_cases()
        print(f"✅ Sheet OK — {len(cases)} active case(s) in '{CONFIG['SHEET_TAB_NAME']}' tab")
    except Exception as e:
        print(f"❌ Sheet error: {e}")

    try:
        service = get_gmail_service()
        profile = service.users().getProfile(userId="me").execute()
        print(f"✅ Gmail authenticated as {profile['emailAddress']}")
        query = f"to:{CONFIG['GROUP_EMAIL']} -label:{CONFIG['PROCESSED_LABEL']}"
        result = service.users().messages().list(userId="me", q=query, maxResults=5).execute()
        print(f"📧 Unprocessed emails (estimate): {result.get('resultSizeEstimate', 0)}")
    except Exception as e:
        print(f"❌ Gmail error: {e}")

    print(f"\nSheet ID: {CONFIG['SHEET_ID']}")
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

    rows = load_cases()
    completed = [r for r in rows if r.get("status") == "Completed"]

    if not completed:
        print("No completed cases.")
        return

    would_archive = 0
    for r in completed:
        raw = r.get("completed_at", "")
        completed_dt = _parse_completed_at(raw)
        if completed_dt is None:
            print(f"⚠️  {r.get('case_id')} — could not parse completed_at: {raw!r}")
            continue
        age_days = (datetime.now(TZ) - completed_dt).days
        if completed_dt < cutoff:
            would_archive += 1
            print(f"✅ {r.get('case_id')} — {raw} ({age_days}d ago) → WOULD ARCHIVE")
        else:
            print(f"⏳ {r.get('case_id')} — {raw} ({age_days}d ago) → too recent, keep")

    print(f"\nTotal completed: {len(completed)} | Would archive: {would_archive}")
    print("=== Done ===\n")


COMMANDS = {
    "list":           cmd_list,
    "assign":         cmd_assign,
    "complete":       cmd_complete,
    "reclassify":     cmd_reclassify,
    "archive":        cmd_archive,
    "poll":           cmd_poll,
    "diagnose":       cmd_diagnose,
    "test-classify":  cmd_test_classify,
    "debug-archive":  cmd_debug_archive,
}

USAGE = """
Usage: python dispute_tracker.py <command> [args]

Core commands:
  poll                         Start the background email poller (runs indefinitely)
  list [status]                List cases (optional filter: New / In Progress / Completed)
  assign <CASE-ID> [email]     Assign a case to yourself or a given email
  complete <CASE-ID>           Mark a case as completed
  reclassify <CASE-ID> <Queue> Change the queue of a case
  archive                      Archive completed/rejected cases older than 1 day

Diagnostic commands:
  diagnose                     Health check (Gmail auth, Sheet, Slack)
  debug-archive                Preview which cases would be archived (dry run)
  test-classify                Test the keyword classification logic
"""

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(USAGE)
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]
    COMMANDS[cmd](args)
