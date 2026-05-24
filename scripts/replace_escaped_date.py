from pathlib import Path
p=Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
s=p.read_text(encoding='utf-8')
old='r[\\"date_received\\"]'
if old in s:
    s2=s.replace(old, '_format_date_received(r[\\"date_received\\"])')
    p.write_text(s2, encoding='utf-8')
    print('replaced escaped date_received')
else:
    print('escaped pattern not found')
