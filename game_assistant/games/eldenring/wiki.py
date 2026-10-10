"""The Elden Ring wiki, offline: a local, searchable copy of the Fandom wiki's articles.

Source: the Elden Ring Fandom wiki's own database dump (pages-current XML, about 4 MB as 7z, text
under CC BY-SA, https://eldenring.fandom.com). `Get-GameData.bat` downloads it into
.local/eldenring/wiki/ and builds `wiki.db` there (SQLite with full-text search). Never committed.

Each article is kept as its title, other names (redirects to it), categories, a few infobox fields
(region, location, type) and its sections ("Overview", "Sites of Grace", "Bosses", "Notable Loot",
"Moveset", "Walkthrough"...) as plain text. Nightreign (a separate game) and unused content are left
out. Questions are answered from here first; the online wiki search stays a last resort.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DIR = ROOT / '.local' / 'eldenring' / 'wiki'
DUMP_URL = 'https://s3.amazonaws.com/wikia_xml_dumps/e/el/eldenring_pages_current.xml.7z'
DUMP_7Z = DIR / 'eldenring_pages_current.xml.7z'
DB = DIR / 'wiki.db'
SKIP_CATEGORIES = ('Nightreign', 'Unused Content', 'Elden Ring dialogue', 'Achievements')

# What a question is about -> the sections that answer it.
ASPECTS = {
    'overview': ('Overview', 'Description', 'Lore', 'Notes'),
    'location': ('Location', 'Locations', 'Where to find', 'Acquisition', 'Obtained', 'How to get', 'Availability'),
    'fight': ('Moveset', 'Phase 1', 'Phase 2', 'Boss Fight', 'Strategy', 'Strategies', 'Combat', 'Tips', 'Attacks'),
    'here': ('Sites of Grace', 'Bosses', 'NPCs', 'Characters', 'Notable Loot', 'Items', 'Enemies', 'Locations',
             'Dungeons', 'Landmarks', 'Crafting Materials'),
    'walkthrough': ('Walkthrough', 'Overview'),
    'quest': ('Quest', 'Questline', 'Quest Line', 'Encounters', 'Dialogue', 'Notes'),
    'stats': ('Stats', 'Scaling', 'Requirements', 'Upgrades', 'Effect', 'Effects'),
}


# ---- turning wikitext into plain text ----

# Templates that stand for words; the rest of the small ones are icons or layout and go.
WORD_TEMPLATES = {'er': 'Elden Ring', 'sote': 'Shadow of the Erdtree', 'ern': 'Elden Ring Nightreign', 'ng+': 'NG+'}
TABLE_TEMPLATES = ('drops table', 'enemy table', 'shop table', 'boss table', 'quest item table')


def _split_params(inner: str) -> list[str]:
    """Split a template's inside at its own | (not those inside [[...]] or nested {{...}})."""
    parts, depth, cur, i = [], 0, [], 0
    while i < len(inner):
        two = inner[i:i + 2]
        if two in ('{{', '[['):
            depth += 1
            cur.append(two)
            i += 2
        elif two in ('}}', ']]') and depth:
            depth -= 1
            cur.append(two)
            i += 2
        elif inner[i] == '|' and depth == 0:
            parts.append(''.join(cur))
            cur = []
            i += 1
        else:
            cur.append(inner[i])
            i += 1
    parts.append(''.join(cur))
    return parts


def _expand_template(inner: str, title: str) -> str:
    """The words a template stands for. Item descriptions ({{Description|EN_line1=...}}) and the
    drop, shop, enemy and quest item tables hold much of the wiki's facts; an earlier version dropped
    every template, and pages like "About Spiritspring Jumping" lost their whole text."""
    parts = _split_params(inner)
    name = parts[0].strip().lower().replace('_', ' ')
    named, positional = {}, []
    for p in parts[1:]:
        k, eq, v = p.partition('=')
        if eq and re.fullmatch(r'\s*[\w ]+\s*', k):
            named[k.strip().lower()] = _templates(v, title).strip()
        else:
            positional.append(_templates(p, title).strip())
    if name in WORD_TEMPLATES:
        return WORD_TEMPLATES[name]
    if name == 'pagename':
        return title
    if name == 'description':
        return '\n'.join(v for k, v in sorted(named.items()) if k.startswith('en') and v)
    if name == 'quote':
        text = named.get('text') or (positional[0] if positional else '')
        who = named.get('speaker') or named.get('citation')
        return f'"{text}"' + (f' ({who})' if who else '') if text else ''
    if name == 'textcolor' and positional:
        return positional[-1]
    if name in ('dmg', 'stat') and positional:
        return positional[0].upper() if name == 'stat' else positional[0]
    if name in TABLE_TEMPLATES and positional and positional[0] == 'row':
        cells = [f'{k}: {v}' for k, v in named.items() if v and v != '-' and k != 'image']
        return '\n- ' + ', '.join(cells) if cells else ''
    return ''


def _templates(text: str, title: str = '') -> str:
    """Replace each {{...}} template (nested) by the words it stands for, or nothing."""
    out, i = [], 0
    while True:
        j = text.find('{{', i)
        if j < 0:
            out.append(text[i:])
            return ''.join(out)
        out.append(text[i:j])
        depth, k = 0, j
        while k < len(text):
            if text.startswith('{{', k):
                depth += 1
                k += 2
            elif text.startswith('}}', k):
                depth -= 1
                k += 2
                if depth == 0:
                    break
            else:
                k += 1
        if depth:  # unclosed: drop the rest, as before
            return ''.join(out)
        out.append(_expand_template(text[j + 2:k - 2], title))
        i = k


def _infobox(text: str) -> dict:
    m = re.search(r'\{\{Infobox[^|}]*\|(.*?)\}\}\s*\n', text, re.S | re.I)
    fields = {}
    if m:
        for part in re.split(r'\n?\|', m.group(1)):
            key, _, val = part.partition('=')
            key, val = key.strip().lower(), clean(val)
            if key in ('region', 'location', 'type', 'bosses', 'drops', 'weapon type', 'category') and val:
                fields[key] = val[:300]
    return fields


def clean(text: str, title: str = '') -> str:
    text = re.sub(r'<ref[^>]*/>|<ref[^>]*>.*?</ref>', '', text, flags=re.S)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    text = re.sub(r'\[\[(File|Image|Category):[^\]]*(\[\[[^\]]*\]\][^\]]*)*\]\]', '', text, flags=re.I)
    text = _templates(text, title)
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    text = re.sub(r'\[https?://\S+\s+([^\]]+)\]', r'\1', text)
    text = re.sub(r"'{2,}", '', text)
    text = re.sub(r'<[^>]+>', '', text)
    # table cell attributes (colspan="2" style="..." |) go
    text = re.sub(r'(?:\b(?:colspan|rowspan|style|class|width|align|scope)\s*=\s*"[^"]*"\s*)+\|?', '', text)
    # tables: rows to lines, cells to commas
    text = re.sub(r'^\{\|.*$|^\|\}.*$|^\|-.*$|^\|\+.*$', '', text, flags=re.M)
    text = re.sub(r'^[!|]\s*', '', text, flags=re.M)
    text = re.sub(r'\s*(\|\||!!)\s*', ', ', text)
    text = re.sub(r'^\s*[*#:;]+\s*', '- ', text, flags=re.M)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _sections(text: str, title: str = '') -> dict[str, str]:
    parts = re.split(r'^(==+)\s*(.+?)\s*\1\s*$', text, flags=re.M)
    secs = {'Summary': clean(parts[0], title)}
    for i in range(1, len(parts) - 2, 3):
        name = clean(parts[i + 1]) or 'Section'
        body = clean(parts[i + 2], title)
        if name in ('Gallery', 'References', 'See also', 'Manga', 'Trivia') or not body:
            continue
        secs[name] = (secs.get(name, '') + '\n' + body).strip()
    return secs


# ---- building ----

def build(xml_path: Path | None = None, db_path: Path = DB) -> dict:
    import xml.etree.ElementTree as ET
    if xml_path is None:
        import py7zr
        with py7zr.SevenZipFile(DUMP_7Z) as z:
            z.extractall(DIR)
        xml_path = next(DIR.glob('*.xml'))
    # Built beside the old index and swapped in: a running assistant may have the old one open
    # (Windows won't replace an open file), and then the swap happens when it next starts.
    new_path = db_path.with_suffix('.new.db')
    if new_path.exists():
        new_path.unlink()
    db = sqlite3.connect(new_path)
    db.executescript('''
        CREATE TABLE pages(id INTEGER PRIMARY KEY, title TEXT UNIQUE, categories TEXT, info TEXT, sections TEXT);
        CREATE TABLE aliases(alias TEXT, page_id INTEGER);
        CREATE VIRTUAL TABLE search USING fts5(title, aliases, body, tokenize='porter unicode61');
    ''')
    redirects, n = [], 0
    for _ev, el in ET.iterparse(xml_path, events=('end',)):
        if el.tag.split('}')[-1] != 'page':
            continue
        title = el.find('{*}title').text or ''
        if el.find('{*}ns').text != '0':
            el.clear()
            continue
        text = el.find('{*}revision/{*}text').text or ''
        el.clear()
        m = re.match(r'#redirect\s*\[\[([^\]|#]+)', text, re.I)
        if m:
            redirects.append((title, m.group(1).strip()))
            continue
        cats = [c.strip() for c in re.findall(r'\[\[Category:([^\]|]+)', text)]
        if any(c.startswith(SKIP_CATEGORIES) for c in cats):
            continue
        secs = _sections(text, title)
        cur = db.execute('INSERT OR IGNORE INTO pages(title, categories, info, sections) VALUES (?, ?, ?, ?)',
                         (title, json.dumps(cats), json.dumps(_infobox(text)), json.dumps(secs)))
        if cur.rowcount:
            db.execute('INSERT INTO search(rowid, title, aliases, body) VALUES (?, ?, ?, ?)',
                       (cur.lastrowid, title, '', '\n'.join(secs.values())))
            n += 1
    ids = dict(db.execute('SELECT title, id FROM pages'))
    for alias, target in redirects:
        if target in ids:
            db.execute('INSERT INTO aliases VALUES (?, ?)', (alias, ids[target]))
    for pid, names in db.execute('SELECT page_id, group_concat(alias, " ; ") FROM aliases GROUP BY page_id').fetchall():
        db.execute('UPDATE search SET aliases = ? WHERE rowid = ?', (names, pid))
    db.commit()
    db.close()
    swapped = _swap_in(db_path)
    return {'articles': n, 'aliases': len(redirects), 'in_use': not swapped}


def _swap_in(db_path: Path) -> bool:
    new_path = db_path.with_suffix('.new.db')
    if not new_path.exists():
        return True
    try:
        os.replace(new_path, db_path)
        return True
    except PermissionError:  # the old index is open in a running assistant
        return False


# ---- looking things up ----

def _fold(name: str) -> str:
    """Lower case without accents: "Kale" finds "Kalé"."""
    return ''.join(c for c in unicodedata.normalize('NFKD', name.strip().lower()) if not unicodedata.combining(c))


class Wiki:
    def __init__(self, db_path: Path = DB):
        _swap_in(db_path)  # a newer index built while the assistant was running
        self.ok = db_path.exists()
        self.db = sqlite3.connect(db_path, check_same_thread=False) if self.ok else None
        self._titles: dict[str, int] | None = None

    def _title_index(self) -> dict[str, int]:
        if self._titles is None:
            t = {_fold(k): v for k, v in self.db.execute('SELECT alias, page_id FROM aliases')}
            t.update({_fold(k): v for k, v in self.db.execute('SELECT title, id FROM pages')})  # titles win
            self._titles = t
        return self._titles

    def find_page(self, query: str) -> int | None:
        """Best page for a name: exact title or alias, a close spelling, then full-text search."""
        q = _fold(query)
        titles = self._title_index()
        if q in titles:
            return titles[q]
        close = difflib.get_close_matches(q, list(titles), n=1, cutoff=0.85)
        if close:
            return titles[close[0]]
        words = re.findall(r'\w+', q)
        if not words:
            return None
        fts = ' '.join(f'"{w}"' for w in words)
        row = self.db.execute('SELECT rowid FROM search WHERE search MATCH ? ORDER BY bm25(search, 10.0, 6.0, 1.0) LIMIT 1',
                              (fts,)).fetchone()
        return row[0] if row else None

    def page(self, pid: int) -> dict:
        title, cats, info, secs = self.db.execute('SELECT title, categories, info, sections FROM pages WHERE id = ?',
                                                  (pid,)).fetchone()
        return {'title': title, 'categories': json.loads(cats), 'info': json.loads(info), 'sections': json.loads(secs)}

    def lookup(self, query: str, aspect: str = 'overview', limit_chars: int = 1800) -> dict:
        """The parts of the best page that answer `aspect` (overview, location, fight, here,
        walkthrough, quest, stats), trimmed to about limit_chars."""
        if not self.ok:
            return {'error': 'the offline wiki is not installed: run Get-GameData.bat'}
        pid = self.find_page(query)
        if pid is None:
            return {'error': f'no wiki page found for "{query}"'}
        p = self.page(pid)
        secs = p['sections']
        wanted = ASPECTS.get(aspect, ASPECTS['overview'])
        picked = {k: v for k, v in secs.items() if any(k.lower().startswith(w.lower()) for w in wanted)}
        text_parts = [f'{k}:\n{v}' for k, v in picked.items()]
        if aspect in ('overview', 'location') or not picked:
            text_parts.insert(0, secs.get('Summary', ''))
        text = '\n\n'.join(t for t in text_parts if t.strip())
        if len(text) > limit_chars:
            text = text[:limit_chars].rsplit(' ', 1)[0] + ' ...'
        others = [k for k in secs if k not in picked and k != 'Summary']
        return {'page': p['title'], 'info': p['info'], 'text': text, 'other_sections': others[:15],
                'source': 'offline copy of the Elden Ring Fandom wiki (CC BY-SA)'}

    def search(self, query: str, limit: int = 5) -> list[str]:
        words = re.findall(r'\w+', query.lower())
        if not self.ok or not words:
            return []
        fts = ' OR '.join(f'"{w}"' for w in words)
        return [r[0] for r in self.db.execute(
            'SELECT title FROM search WHERE search MATCH ? ORDER BY bm25(search, 10.0, 6.0, 1.0) LIMIT ?', (fts, limit))]
