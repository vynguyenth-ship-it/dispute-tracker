from pathlib import Path
p=Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
s=p.read_text(encoding='utf-8')
anchor='"def sync_to_sheet():\\n",'
idx=s.find(anchor)
if idx==-1:
    print('anchor not found')
    raise SystemExit(1)

helper_code = [
    'QUEUE_BACKGROUND_COLORS = {',
    '    "Dispute": {"red": 1.0, "green": 0.8, "blue": 0.8},',
    '    "Others": {"red": 1.0, "green": 1.0, "blue": 0.8},',
    '    "Update Details": {"red": 0.8, "green": 0.9, "blue": 1.0},',
    '    "Invoice": {"red": 0.8, "green": 1.0, "blue": 0.8},',
    '}',
    '',
    'def _get_sheet_tab_id(service, spreadsheet_id: str, tab_name: str) -> int:',
    '    meta = service.spreadsheets().get(',
    '        spreadsheetId=spreadsheet_id,',
    '        fields="sheets(properties(sheetId,title))",',
    '    ).execute()',
    '    for sheet in meta.get("sheets", []):',
    '        props = sheet.get("properties", {})',
    '        if props.get("title") == tab_name:',
    '            return props.get("sheetId")',
    '    raise ValueError(f"Tab not found: {tab_name}")',
    '',
    'def _apply_queue_row_colors(service, spreadsheet_id: str, tab_name: str, rows):',
    '    if len(rows) <= 1:',
    '        return',
    '    sheet_id = _get_sheet_tab_id(service, spreadsheet_id, tab_name)',
    '    try:',
    '        queue_col_index = rows[0].index("Queue")',
    '    except Exception:',
    '        queue_col_index = 4',
    '    range_end_row = len(rows)',
    '    requests = []',
    '    for queue, color in QUEUE_BACKGROUND_COLORS.items():',
    '        formula = f"=${chr(ord(\'A\') + queue_col_index)}2=\\\"{queue}\\\""',
    '        requests.append({',
    '            "addConditionalFormatRule": {',
    '                "rule": {',
    '                    "ranges": [',
    '                        {',
    '                            "sheetId": sheet_id,',
    '                            "startRowIndex": 1,',
    '                            "endRowIndex": range_end_row,',
    '                            "startColumnIndex": 0,',
    '                            "endColumnIndex": len(rows[0]),',
    '                        }',
    '                    ],',
    '                    "booleanRule": {',
    '                        "condition": {',
    '                            "type": "CUSTOM_FORMULA",',
    '                            "values": [ { "userEnteredValue": formula } ],',
    '                        },',
    '                        "format": {',
    '                            "backgroundColor": color',
    '                        },',
    '                    },',
    '                },',
    '                "index": 0,',
    '            }',
    '        })',
    '    if not requests:',
    '        return',
    '    service.spreadsheets().batchUpdate(',
    '        spreadsheetId=spreadsheet_id,',
    '        body={"requests": requests},',
    '    ).execute()',
    '',
    'def _format_date_received(val):',
    '    if not val:',
    '        return ""',
    '    if isinstance(val, str):',
    '        return val.split(\' \')[0]',
    '    try:',
    '        return val.strftime(\'%d/%m/%Y\')',
    '    except Exception:',
    '        return str(val)',
    ''
]

# build helper_json entries matching notebook source formatting: 4 spaces + "<line>\n",
entries = []
for line in helper_code:
    esc = line.replace('\\', '\\\\').replace('"', '\\"')
    entries.append('    "' + esc + '\\n",')

helper_json = '\n'.join(entries) + '\n'

s2 = s[:idx] + helper_json + s[idx:]
if s2 == s:
    print('no change')
else:
    p.write_text(s2, encoding='utf-8')
    print('inserted helper json')
