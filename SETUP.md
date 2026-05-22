# Dispute Case Tracker — Setup Guide

## What you'll have at the end
- A Python script that polls Gmail every 5 minutes for emails sent to `account.receivable.vn@grabtaxi.com`
- Cases stored in a local SQLite database (`cases.db`)
- Cases automatically synced to a Google Sheet after every poll
- Slack notifications on every new email
- CLI commands to assign, complete, and reclassify cases

---

## Prerequisites

- Python 3.11+
- A Google account that is a **member of the group inbox** (`account.receivable.vn@grabtaxi.com`)
- `credentials.json` already in this folder (Desktop app OAuth client from Google Cloud Console)

---

## Step 1 — OAuth credentials (one-time)

The script uses **your personal Google account** — no Workspace admin required.

1. Your `credentials.json` (Desktop app type) should already be in this folder. If not:
   - Go to [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services → Credentials**
   - Ensure both **Gmail API** and **Google Sheets API** are enabled under **Enabled APIs**
   - Click **Create Credentials → OAuth client ID → Desktop app**
   - Download the JSON and save it as `credentials.json` in this folder

2. **Delete `token.json`** if it exists — the old token only has Gmail scope and needs to be replaced with one that includes Sheets scope.

3. Run the health check (Step 5) — a browser window will open **once** asking you to log in and approve access. After you approve, `token.json` is saved and **all future runs are fully automatic with no browser popup**.

---

## Step 2 — Google Sheets sync

1. Open [Google Sheets](https://sheets.google.com/) and create a new spreadsheet (or use an existing one).
2. The sheet must be accessible to the Google account you used in Step 1 (owner or shared).
3. Copy the **Spreadsheet ID** from the URL:
   `https://docs.google.com/spreadsheets/d/`**`<YOUR_SHEET_ID>`**`/edit`
4. Open `dispute_tracker.py` and paste it into CONFIG:
   ```python
   "SHEET_ID": "paste-your-sheet-id-here",
   ```
5. The "Cases" tab will be created automatically on first sync.
6. Test with: `python dispute_tracker.py sync-sheet`

---

## Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

---

## Step 4 — Set your Slack Webhook URL

**Option A — Environment variable (recommended):**

```bash
# Windows Command Prompt
set SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# Windows PowerShell
$env:SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

**Option B — Edit the script directly:**

Open `dispute_tracker.py` and replace the empty string on this line:

```python
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/...")
```

---

## Step 5 — Run a health check

```bash
python dispute_tracker.py diagnose
```

**First run:** a browser window opens — log in with your Google account and click Allow. This happens once only.

Expected output after authorising:
```
✅ SLACK_WEBHOOK_URL is set
✅ Database OK — 0 active cases, 0 archived
✅ Gmail authenticated as yourname@grabtaxi.com
📧 Unprocessed emails (estimate): 3
```

---

## Step 6 — Start the poller

```bash
python dispute_tracker.py poll
```

This runs indefinitely:
- Checks Gmail every **5 minutes** for new emails
- Syncs all active cases to Google Sheet after each poll
- Archives completed cases older than **1 day** every night at 23:00
- Press `Ctrl+C` to stop

---

## CLI Reference

**Core commands:**

| Command | Description |
|---|---|
| `poll` | Start the background poller (runs indefinitely) |
| `list` | List all active cases |
| `list New` | Filter by status: `New`, `In Progress`, `Completed` |
| `assign <CASE-ID>` | Assign a case to yourself |
| `assign <CASE-ID> user@email.com` | Assign to a specific person |
| `complete <CASE-ID>` | Mark a case as completed |
| `reclassify <CASE-ID> <Queue>` | Change the queue of a case |
| `archive` | Manually archive completed cases older than 1 day |
| `sync-sheet` | Push all active cases to Google Sheets (manual) |

**Diagnostic commands:**

| Command | Description |
|---|---|
| `diagnose` | Health check (Gmail auth, DB, Slack) |
| `diagnose-rows` | Check DB for empty or corrupted rows |
| `debug-archive` | Preview which cases would be archived (dry run) |
| `list-triggers` | Show currently scheduled jobs |

**Test & utility commands:**

| Command | Description |
|---|---|
| `test-classify` | Test keyword classification without hitting Gmail |
| `test-archive` | Create fake cases and verify archive logic end-to-end |
| `test-assign` | Test the assign flow on the first New case in DB |
| `cleanup-test` | Remove all TEST-* and ARCHTEST-* rows from DB |
| `cleanup-corrupted` | Delete rows with empty case_id from DB |

---

## Queue classification rules

| Queue | Triggers on |
|---|---|
| Internal Invoice | Subject contains "internal invoice" |
| Dispute | chênh lệch, sai sót, bảng kê, thiếu, biên bản điều chỉnh, dispute |
| Update Details | thông tin, không chính xác, thay đổi, update details, update info |
| Invoice | request invoice, xuất hóa đơn, chưa nhận được hóa đơn, invoice request |
| Others | Everything else |

Rules are checked in order. Use `reclassify` to override manually.

---

## Running as a background service (optional)

**Windows — Task Scheduler:**

1. Open Task Scheduler → Create Basic Task.
2. Set trigger: **At startup** or **Daily**.
3. Action: Start a program → `python` with argument `C:\Users\vy.nguyenth\gmail-classifer\dispute_tracker.py poll`.

**Windows — keep it running in a terminal:**

```powershell
Start-Process python -ArgumentList "dispute_tracker.py poll" -WindowStyle Hidden
```

---

## Git setup

This folder is a git repository. Sensitive files are excluded via `.gitignore`.

```bash
# View status
git status

# Stage and commit changes
git add dispute_tracker.py requirements.txt SETUP.md gmail_auth.ipynb
git commit -m "describe your change"
```

**Never commit:** `credentials.json`, `token.json`, `service_account.json`, `cases.db` — all listed in `.gitignore`.

---

## Files in this folder

| File | Purpose |
|---|---|
| `dispute_tracker.py` | Main script |
| `gmail_auth.ipynb` | Jupyter notebook version of the pipeline |
| `requirements.txt` | Python dependencies |
| `credentials.json` | OAuth client secrets — keep private, **never commit** |
| `token.json` | OAuth token (auto-created on first run) — keep private, **never commit** |
| `cases.db` | SQLite database (auto-created on first run) |
| `.gitignore` | Excludes sensitive files from git |
| `SETUP.md` | This file |
