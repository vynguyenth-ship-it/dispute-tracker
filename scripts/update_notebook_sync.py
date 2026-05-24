from pathlib import Path

p = Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
s = p.read_text(encoding='utf-8')

old = (
    'def _write_tab(service, sheet_id: str, tab_name: str, rows):\n'
    "    _ensure_tab_exists(service, sheet_id, tab_name)\n"
    "    service.spreadsheets().values().clear(\n"
    "        spreadsheetId=sheet_id, range=f\"{tab_name}!A:Z\"\n"
    "    ).execute()\n"
    "    service.spreadsheets().values().update(\n"
    "        spreadsheetId=sheet_id,\n"
    "        range=f\"{tab_name}!A1\",\n"
    "        valueInputOption=\"RAW\",\n"
    "        body={\"values\": rows},\n"
    "    ).execute()\n\n"
    "def sync_to_sheet():"
)

if old not in s:
    print('pattern not found; aborting')
    raise SystemExit(1)

ins = """
def _write_tab(service, sheet_id: str, tab_name: str, rows):
    _ensure_tab_exists(service, sheet_id, tab_name)
    service.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=f"{tab_name}!A:Z"
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{tab_name}!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()

QUEUE_BACKGROUND_COLORS = {
    "Dispute": {"red": 1.0, "green": 0.8, "blue": 0.8},
    "Others": {"red": 1.0, "green": 1.0, "blue": 0.8},
    "Update Details": {"red": 0.8, "green": 0.9, "blue": 1.0},
    "Invoice": {"red": 0.8, "green": 1.0, "blue": 0.8},
}


def _get_sheet_tab_id(service, spreadsheet_id: str, tab_name: str) -> int:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == tab_name:
            return props.get("sheetId")
    raise ValueError(f"Tab not found: {tab_name}")


def _apply_queue_row_colors(service, spreadsheet_id: str, tab_name: str, rows):
    if len(rows) <= 1:
        return
    sheet_id = _get_sheet_tab_id(service, spreadsheet_id, tab_name)
    try:
        queue_col_index = rows[0].index("Queue")
    except Exception:
        queue_col_index = 4
    range_end_row = len(rows)
    requests = []
    for queue, color in QUEUE_BACKGROUND_COLORS.items():
        formula = f"=${chr(ord('A') + queue_col_index)}2=\"{queue}\""
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [
                        {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": range_end_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": len(rows[0]),
                        }
                    ],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [ { "userEnteredValue": formula } ],
                        },
                        "format": {
                            "backgroundColor": color
                        },
                    },
                },
                "index": 0,
            }
        })
    if not requests:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def _format_date_received(val):
    if not val:
        return ""
    if isinstance(val, str):
        return val.split(' ')[0]
    try:
        return val.strftime('%d/%m/%Y')
    except Exception:
        return str(val)


def sync_to_sheet():"""

s2 = s.replace(old, ins)

# Also modify occurrences of [r["case_id"], r["date_received"], ...] to use _format_date_received
s2 = s2.replace('[r["case_id"], r["date_received"], r["sender"], r["subject",', '[r["case_id"], _format_date_received(r["date_received"]), r["sender"], r["subject",')
# Try alternate pattern without the trailing comma in the bracket
s2 = s2.replace('[r["case_id"], r["date_received"], r["sender"], r["subject"],', '[r["case_id"], _format_date_received(r["date_received"]), r["sender"], r["subject"],')

if s2 == s:
    print('no changes made')
else:
    p.write_text(s2, encoding='utf-8')
    print('notebook patched')
