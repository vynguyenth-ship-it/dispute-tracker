from pathlib import Path
p=Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb')
s=p.read_text(encoding='utf-8')
old1='''_write_tab(service, sheet_id, CONFIG["SHEET_TAB_NAME"], active_values)\n        log.info(f"Synced {len(active_rows)} active cases → '{CONFIG['SHEET_TAB_NAME']}' tab.")'''
new1='''_write_tab(service, sheet_id, CONFIG["SHEET_TAB_NAME"], active_values)\n        _apply_queue_row_colors(service, sheet_id, CONFIG["SHEET_TAB_NAME"], active_values)\n        log.info(f"Synced {len(active_rows)} active cases → '{CONFIG['SHEET_TAB_NAME']}' tab.")'''
if old1 in s:
    s=s.replace(old1,new1,1)
else:
    print('active replacement not found')

old2='''_write_tab(service, sheet_id, CONFIG["ARCHIVE_TAB_NAME"], archive_values)\n        log.info(f"Synced {len(archive_rows)} archived cases → '{CONFIG['ARCHIVE_TAB_NAME']}' tab.")'''
new2='''_write_tab(service, sheet_id, CONFIG["ARCHIVE_TAB_NAME"], archive_values)\n        _apply_queue_row_colors(service, sheet_id, CONFIG["ARCHIVE_TAB_NAME"], archive_values)\n        log.info(f"Synced {len(archive_rows)} archived cases → '{CONFIG['ARCHIVE_TAB_NAME']}' tab.")'''
if old2 in s:
    s=s.replace(old2,new2,1)
else:
    print('archive replacement not found')

p.write_text(s,encoding='utf-8')
print('done')
