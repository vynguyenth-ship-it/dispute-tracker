import nbformat
from pathlib import Path
p = Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
nb = nbformat.read(str(p), as_version=nbformat.NO_CONVERT)

for cell in nb.cells:
    if cell.cell_type != 'code':
        continue
    src = cell.source
    if 'def _write_tab' in src and 'def sync_to_sheet' in src:
        # insert helpers before def sync_to_sheet
        parts = src.split('\ndef sync_to_sheet():', 1)
        before = parts[0]
        after = parts[1]
        helper = """

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

"""
        new_src = before + helper + '\ndef sync_to_sheet():' + after
        # replace occurrences in list comprehensions
        new_src = new_src.replace('[r["case_id"], r["date_received"], r["sender"], r["subject"],', '[r["case_id"], _format_date_received(r["date_received"]), r["sender"], r["subject"],')
        cell.source = new_src
        break

nbformat.write(nb, str(p))
print('notebook updated via nbformat')
