from pathlib import Path
s=Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb').read_text(encoding='utf-8')
pat='"def sync_to_sheet():\\n",'
idx=s.find(pat)
print('idx', idx)
print(s[idx-60:idx+60])
