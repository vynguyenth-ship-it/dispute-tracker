from pathlib import Path
s=Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb').read_text(encoding='utf-8')
patterns=['\n\ndef sync_to_sheet():', '\"def sync_to_sheet():\\n\",', 'def sync_to_sheet():', '\\"def sync_to_sheet():\\n\\",']
for p in patterns:
    print(p, '=>', s.find(p))
print('context around first occurrence:')
idx=s.find('def sync_to_sheet():')
print(repr(s[idx-80:idx+80]))
