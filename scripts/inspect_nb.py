import json
from pathlib import Path
p = Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
nb = json.loads(p.read_text(encoding='utf-8'))
for ci, cell in enumerate(nb['cells']):
    if cell.get('cell_type') != 'code':
        continue
    src = cell['source']
    if any('def _write_tab' in line for line in src):
        print('cell', ci)
        for i, line in enumerate(src):
            if i < 30 or 'QUEUE_BACKGROUND_COLORS' in line or 'def sync_to_sheet' in line or 'active_values' in line or 'archive_values' in line:
                print(f'{i}: {line.rstrip()}')
        print('--- end of segment ---')
        break
