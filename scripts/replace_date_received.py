from pathlib import Path
p=Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
s=p.read_text(encoding='utf-8')
if 'r["date_received"]' not in s:
    print('pattern not found')
else:
    s2=s.replace('r["date_received"]', '_format_date_received(r["date_received"])')
    p.write_text(s2, encoding='utf-8')
    print('replaced date_received usages')
