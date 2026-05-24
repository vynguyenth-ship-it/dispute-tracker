from pathlib import Path
import json
p = Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
nb = json.loads(p.read_text(encoding='utf-8'))
for i, cell in enumerate(nb['cells']):
    if cell.get('cell_type') != 'code':
        continue
    src = ''.join(cell.get('source', []))
    if 'date_received' in src or 'strftime("%d/%m/%Y' in src or 'strftime("%d/%m/%Y %H:%M' in src:
        print('CELL', i)
        print(src)
        print('-'*80)
