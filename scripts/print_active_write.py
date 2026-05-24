from pathlib import Path
s=Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb').read_text(encoding='utf-8')
pat='_write_tab(service, sheet_id, CONFIG["SHEET_TAB_NAME"], active_values)'
idx=s.find(pat)
print('idx', idx)
if idx!=-1:
    print(s[idx:idx+300])
else:
    print('not found')
