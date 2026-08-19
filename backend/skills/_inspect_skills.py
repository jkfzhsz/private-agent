import zipfile, re, sys
z = zipfile.ZipFile(r'D:/Private agent/backend/skills/reasonix-skills-main.zip')
for n in ['reasonix-skills-main/meta/search-first.md','reasonix-skills-main/documents/docx.md','reasonix-skills-main/writing/novelist.md']:
    c = z.read(n).decode('utf-8', errors='replace')
    m = re.match(r'^---\s*\n(.*?)\n---', c, re.S)
    fm = m.group(1) if m else ''
    d = re.search(r'description:\s*"?([^"\n]+)"?\s*$', fm, re.M)
    libs = sorted(set(re.findall(r'(pip install|python-docx|openpyxl|weasyprint|reportlab|pypdf|pdfkit|npx|jest|node|npm install)[\w\-./]*', c)))
    print('='*20, n)
    print('desc:', d.group(1)[:90] if d else '?')
    print('libs:', libs[:10])
    print('lines:', len(c.splitlines()))
    print()
