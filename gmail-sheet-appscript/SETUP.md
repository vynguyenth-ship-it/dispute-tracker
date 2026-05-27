# Dispute Case Tracker — Setup Guide

## What you'll have at the end
- A Google Sheet that auto-logs every email sent to `account.receivable.vn@grabtaxi.com`
- A web app where your team can assign, complete, and reclassify cases
- Slack notifications on every new email

---

## Step 1 — Create the Google Sheet

1. Go to [sheets.google.com](https://sheets.google.com) and create a new spreadsheet.
2. Name it: **Dispute Case Tracker**
3. Note the spreadsheet URL — you'll need it later.

---

## Step 2 — Open Apps Script

1. In the spreadsheet, click **Extensions → Apps Script**.
2. Delete all existing code in `Code.gs`.

---

## Step 3 — Paste the scripts

### Code.gs
1. Copy the entire contents of `Code.gs` from this folder.
2. Paste it into the `Code.gs` file in Apps Script.

### Index.html
1. In Apps Script, click **+ (Add a file) → HTML**.
2. Name it exactly: `Index` (no .html suffix — Apps Script adds it automatically).
3. Delete the default content and paste the entire contents of `Index.html` from this folder.

---

## Step 4 — Add your Slack Webhook URL

In `Code.gs`, find this line near the top:

```javascript
SLACK_WEBHOOK_URL: "PASTE_YOUR_SLACK_WEBHOOK_URL_HERE",
```

Replace `PASTE_YOUR_SLACK_WEBHOOK_URL_HERE` with your actual Slack webhook URL.

---

## Step 5 — Save and authorize

1. Click **Save** (Ctrl+S or the floppy disk icon).
2. In the function dropdown (top toolbar), select `installTrigger`.
3. Click **Run**.
4. A permissions popup will appear — click **Review permissions → Allow**.
   - You may see a "Google hasn't verified this app" warning — click **Advanced → Go to (your project name)**.
   - This is normal for personal Apps Script projects.

This installs a trigger that checks Gmail every 5 minutes automatically.

---

## Step 6 — Deploy the Web App

1. Click **Deploy → New deployment**.
2. Click the gear icon next to "Select type" → choose **Web app**.
3. Set:
   - **Description**: Dispute Case Tracker
   - **Execute as**: Me (your account)
   - **Who has access**: Anyone within Grab (or "Anyone" if external access is needed)
4. Click **Deploy**.
5. Copy the **Web app URL** — share this with your team.

---

## Step 7 — Test it

1. Send a test email to `account.receivable.vn@grabtaxi.com` with subject containing one of your keywords (e.g. "chênh lệch").
2. In Apps Script, manually run `checkNewEmails` once to process immediately (no need to wait 5 minutes).
3. Check your Google Sheet — the case should appear.
4. Check Slack — you should see the notification.
5. Open the Web App URL — click **Assign to Me** on the case.

---

## Queue classification rules

| Queue | Triggers on (subject or body) |
|---|---|
| Internal Invoice | Subject contains "internal invoice" |
| Dispute | chênh lệch, sai sót, bảng kê, thiếu, biên bản điều chỉnh |
| Update Details | thông tin, không chính xác, thay đổi |
| Invoice | request invoice, xuất hóa đơn, chưa nhận được hóa đơn |
| Others | Everything else |

Rules are checked in order. The team can always manually reclassify from the web app.

---

## Granting team access

- **Google Sheet**: Share the spreadsheet with your team (view or edit).
- **Web App**: Share the web app URL. Each person who opens it will use their own Google account — the "Assign to Me" button captures their email automatically.

---

## Notes

- The script runs under **your Google account**. Your account must be a member of the Google Group to receive the emails in Gmail.
- The trigger polls every 5 minutes. To change this, edit `POLL_INTERVAL_MINUTES` in `Code.gs` and re-run `installTrigger`.
- Processed emails get a Gmail label `dispute-tracker-processed` so they're never double-logged.
