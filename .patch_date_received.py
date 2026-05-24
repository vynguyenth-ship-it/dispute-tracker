from pathlib import Path
import json
p = Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
text = p.read_text(encoding='utf-8')
for pattern in ['date.strftime("%d/%m/%Y %H:%M")', 'date.strftime("%d/%m/%Y")', 'today = now.strftime("%d/%m/%Y %H:%M")', 'today_received = now.strftime("%d/%m/%Y %H:%M")']:
    idx = text.find(pattern)
    print('PATTERN:', pattern, 'IDX:', idx)
    if idx != -1:
        start = text.rfind('"', 0, idx)
        end = text.find('\n', idx)
        print(repr(text[start:end]))
        print('---')
