from pathlib import Path
p = Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
text = p.read_text(encoding='utf-8')
print('found get_sheets_service', 'def get_sheets_service' in text)
print('found sync_to_sheet', 'def sync_to_sheet' in text)
print('found fallback', 'if "get_sheets_service" not in globals()' in text)
idx = text.find('def sync_to_sheet')
print('sync idx', idx)
print(text[idx:idx+320])
