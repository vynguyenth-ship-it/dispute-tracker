from pathlib import Path
s=Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb').read_text(encoding='utf-8')
start=s.find('def _write_tab')
print(start)
print(s[start:start+1200])
