import sys, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from bs4 import BeautifulSoup

with open('detail_probe.html', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'lxml')
for tag in soup(['script', 'style', 'noscript']):
    tag.decompose()

print("=== Searching for result-related text ===")
target_patterns = ['6322', 'prize', 'Prize', 'winning', 'Winning', 'result', 'Result', 'Super Prize', 'Draw']
for pat in target_patterns:
    matches = soup.find_all(string=re.compile(pat, re.IGNORECASE))
    if matches:
        print(f'Pattern [{pat}]: {len(matches)} match(es)')
        for m in matches[:3]:
            parent = m.parent
            cls = parent.get('class', [])
            print(f'  Tag={parent.name} Class={cls} -> {repr(str(m)[:100])}')

print()
print("=== All short alphanumeric elements ===")
count = 0
for el in soup.find_all(['span', 'div', 'p', 'li', 'td', 'h1', 'h2', 'h3', 'h4', 'strong', 'b']):
    txt = el.get_text(strip=True)
    safe = txt.encode('ascii', 'replace').decode().strip()
    if safe and 1 <= len(safe) <= 20 and re.match(r'^[A-Za-z0-9 \-/]+$', safe):
        cls = el.get('class', [])
        print(f'  <{el.name} class={cls}> -> {repr(safe)}')
        count += 1
        if count > 80:
            print('  ... (truncated)')
            break
