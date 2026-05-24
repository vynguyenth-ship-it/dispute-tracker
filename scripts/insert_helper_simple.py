from pathlib import Path
p=Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
s=p.read_text(encoding='utf-8')
needle='\ndef sync_to_sheet():'
if needle not in s:
    print('needle not found')
    raise SystemExit(1)
helper='''\n\nQUEUE_BACKGROUND_COLORS = {\n    "Dispute": {"red": 1.0, "green": 0.8, "blue": 0.8},\n    "Others": {"red": 1.0, "green": 1.0, "blue": 0.8},\n    "Update Details": {"red": 0.8, "green": 0.9, "blue": 1.0},\n    "Invoice": {"red": 0.8, "green": 1.0, "blue": 0.8},\n}\n\ndef _get_sheet_tab_id(service, spreadsheet_id: str, tab_name: str) -> int:\n    meta = service.spreadsheets().get(\n        spreadsheetId=spreadsheet_id,\n        fields="sheets(properties(sheetId,title))",\n    ).execute()\n    for sheet in meta.get("sheets", []):\n        props = sheet.get("properties", {})\n        if props.get("title") == tab_name:\n            return props.get("sheetId")\n    raise ValueError(f"Tab not found: {tab_name}")\n\ndef _apply_queue_row_colors(service, spreadsheet_id: str, tab_name: str, rows):\n    if len(rows) <= 1:\n        return\n    sheet_id = _get_sheet_tab_id(service, spreadsheet_id, tab_name)\n    try:\n        queue_col_index = rows[0].index("Queue")\n    except Exception:\n        queue_col_index = 4\n    range_end_row = len(rows)\n    requests = []\n    for queue, color in QUEUE_BACKGROUND_COLORS.items():\n        formula = f"=${chr(ord('A') + queue_col_index)}2=\\\"{queue}\\\""\n        requests.append({\n            "addConditionalFormatRule": {\n                "rule": {\n                    "ranges": [\n                        {\n                            "sheetId": sheet_id,\n                            "startRowIndex": 1,\n                            "endRowIndex": range_end_row,\n                            "startColumnIndex": 0,\n                            "endColumnIndex": len(rows[0]),\n                        }\n                    ],\n                    "booleanRule": {\n                        "condition": {\n                            "type": "CUSTOM_FORMULA",\n                            "values": [ { "userEnteredValue": formula } ],\n                        },\n                        "format": {\n                            "backgroundColor": color\n                        },\n                    },\n                },\n                "index": 0,\n            }\n        })\n    if not requests:\n        return\n    service.spreadsheets().batchUpdate(\n        spreadsheetId=spreadsheet_id,\n        body={"requests": requests},\n    ).execute()\n\ndef _format_date_received(val):\n    if not val:\n        return ""\n    if isinstance(val, str):\n        return val.split(' ')[0]\n    try:\n        return val.strftime('%d/%m/%Y')\n    except Exception:\n        return str(val)\n\n'''

s2=s.replace(needle, helper+needle, 1)
if s2==s:
    print('no change')
else:
    p.write_text(s2, encoding='utf-8')
    print('inserted helper')
