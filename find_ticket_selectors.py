import sys, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from bs4 import BeautifulSoup

with open('winway_dom_dump.html', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'lxml')

print('=== Ticket serial patterns (letter + 6 digits) ===')
for el in soup.find_all(string=re.compile(r'^[A-Z]\d{6}$')):
    parent = el.parent
    gp = parent.parent
    cls = parent.get('class', [])
    gp_cls = gp.get('class', []) if gp else []
    print(f'  Text={el.strip()} tag={parent.name} class={cls}')
    print(f'    GrandParent: tag={gp.name} class={gp_cls}')

print()
print('=== Elements near Add buttons ===')
for btn in soup.find_all('button', string='Add')[:3]:
    parent = btn.parent
    pname = parent.name
    pcls = parent.get('class', [])
    print(f'Button parent: tag={pname} class={pcls}')
    for sib in parent.children:
        if not hasattr(sib, 'get_text'):
            continue
        txt = sib.get_text(strip=True)
        if not txt:
            continue
        tag = sib.name
        cls = sib.get('class', [])
        safe = txt.encode('ascii', 'replace').decode()[:60]
        print(f'  tag={tag} class={cls} -> {repr(safe)}')
    print()
