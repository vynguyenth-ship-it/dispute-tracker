from pathlib import Path
s=Path(r'c:\Users\vy.nguyenth\gmail-classifer\gmail_auth.ipynb').read_text(encoding='utf-8')
for i in range(len(s)):
    j=s.find('date_received', i)
    if j==-1:
        break
    print('pos', j)
    print(s[j-50:j+50])
    i=j+1
