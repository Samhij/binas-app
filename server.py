#!/usr/bin/env python3
"""Static file server plus DeepSeek chat API that searches Binas."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DEV_WATCH_SUFFIXES = {'.html', '.js', '.css', '.json'}
DEV_RELOAD_SCRIPT = (
    '<script>(function(){var s=new EventSource("/__dev_reload");'
    's.onmessage=function(){location.reload()};})();</script>'
)
COOKIE_NAME = 'binas_session'
SESSION_MAX_AGE = 30 * 24 * 3600
PUBLIC_PATHS = {'/login.html', '/favicon.svg'}
STOPWORDS = {
    'de', 'het', 'een', 'van', 'en', 'in', 'is', 'op', 'te', 'dat', 'die', 'voor',
    'met', 'aan', 'als', 'bij', 'naar', 'om', 'ook', 'niet', 'wat', 'wel', 'zijn',
    'er', 'dit', 'of', 'uit', 'tot', 'over', 'the', 'a', 'an', 'of', 'to', 'and',
    'what', 'how', 'where', 'which', 'can', 'me', 'my', 'ik', 'je', 'we', 'zo',
}
TABLE_QUERY_RE = re.compile(r'(?:tabel\s*)?(\d{1,3}[a-z]?)\b', re.I)


def load_dotenv():
    path = ROOT / '.env'
    if not path.is_file():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def site_password() -> str:
    return os.environ.get('SITE_PASSWORD', '').strip()


def auth_required() -> bool:
    return bool(site_password())


def signing_key() -> bytes:
    return hashlib.sha256(f'binas-session:{site_password()}'.encode('utf-8')).digest()


def make_session_token() -> str:
    expires = str(int(time.time()) + SESSION_MAX_AGE)
    digest = hmac.new(signing_key(), expires.encode('ascii'), hashlib.sha256).hexdigest()
    return f'{expires}.{digest}'


def session_token_valid(token: str | None) -> bool:
    if not token or token.count('.') != 1:
        return False
    expires, digest = token.split('.', 1)
    if not expires.isdigit() or not digest:
        return False
    expected = hmac.new(signing_key(), expires.encode('ascii'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, digest):
        return False
    return int(expires) > time.time()


def parse_cookies(raw: str | None) -> dict[str, str]:
    cookie = SimpleCookie()
    if not raw:
        return {}
    try:
        cookie.load(raw)
    except CookieError:
        return {}
    return {key: morsel.value for key, morsel in cookie.items()}


def session_cookie_header(token: str | None) -> str:
    if not token:
        return f'{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'
    return (
        f'{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_MAX_AGE}; '
        'HttpOnly; SameSite=Lax'
    )


def normalize(value: str) -> str:
    text = unicodedata.normalize('NFD', str(value or '').lower())
    return ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')


def tokenize(value: str) -> list[str]:
    tokens = re.findall(r'[a-z0-9]{2,}|[0-9]+[a-z]?', normalize(value))
    return [tok for tok in tokens if tok not in STOPWORDS]


def flatten_tables(sections) -> list[dict]:
    tables = []
    for section in sections:
        section_name = section.get('section', '')
        theme = section.get('theme') or normalize(section_name)
        for item in section.get('items', []):
            parent_label = str(item.get('label', ''))
            tables.append(make_table(section_name, theme, item, parent_label))
            for child in item.get('children') or []:
                display = parent_label + str(child.get('label', '')).replace(parent_label + '.', '')
                tables.append(make_table(section_name, theme, child, display, parent_label))
    return tables


def make_table(section, theme, item, display_label, parent_label=''):
    title = item.get('title', '')
    label = str(item.get('label', ''))
    page = int(item.get('page') or 1)
    blob = f'{display_label} {label} {title} {section}'
    return {
        'section': section,
        'theme': theme,
        'label': label,
        'displayLabel': display_label,
        'title': title,
        'page': page,
        'parent': parent_label,
        'norm': normalize(blob),
        'label_norm': normalize(display_label).replace(' ', ''),
    }


def load_index():
    navigation = json.loads((ROOT / 'data' / 'navigation-data.json').read_text(encoding='utf-8'))
    tables = flatten_tables(navigation)
    align = {}
    align_path = ROOT / 'data' / 'binas-align.json'
    if align_path.is_file():
        align = json.loads(align_path.read_text(encoding='utf-8')).get('items') or {}

    pages = []
    pages_path = ROOT / 'data' / 'binas-pages.json'
    if pages_path.is_file():
        payload = json.loads(pages_path.read_text(encoding='utf-8'))
        for item in payload.get('pages') or []:
            text = item.get('text') or ''
            pages.append({
                'page': int(item.get('page') or 0),
                'text': text,
                'norm': normalize(text),
            })
    by_page = {}
    for table in tables:
        by_page.setdefault(table['page'], []).append(table)
    return {'tables': tables, 'pages': pages, 'tables_by_page': by_page, 'align': align}


INDEX = None
reload_clients: list[queue.Queue[str]] = []
reload_clients_lock = threading.Lock()


def dev_mode() -> bool:
    return os.environ.get('DEV', '').strip().lower() in {'1', 'true', 'yes'}


def notify_dev_reload(reload_index: bool = False):
    global INDEX
    if reload_index:
        INDEX = load_index()
    with reload_clients_lock:
        for client in reload_clients:
            client.put('reload')


def watch_dev_files():
    mtimes: dict[str, float] = {}
    while True:
        changed_data = False
        changed_ui = False
        for path in ROOT.rglob('*'):
            if not path.is_file() or path.suffix not in DEV_WATCH_SUFFIXES:
                continue
            if any(part.startswith('.') for part in path.relative_to(ROOT).parts):
                continue
            key = str(path)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            previous = mtimes.get(key)
            mtimes[key] = mtime
            if previous is None or mtime == previous:
                continue
            if path.parent.name == 'data' and path.suffix == '.json':
                changed_data = True
            else:
                changed_ui = True
        if changed_data:
            notify_dev_reload(reload_index=True)
        elif changed_ui:
            notify_dev_reload()
        time.sleep(1)


def start_dev_watcher():
    if not dev_mode():
        return
    thread = threading.Thread(target=watch_dev_files, name='dev-file-watcher', daemon=True)
    thread.start()


def search_binas(query: str, limit_pages=6, limit_tables=10):
    q = (query or '').strip()
    tokens = tokenize(q)
    q_norm = normalize(q)
    label_hits = {m.group(1).lower() for m in TABLE_QUERY_RE.finditer(q)}

    table_scores = []
    for table in INDEX['tables']:
        score = 0.0
        if table['label_norm'] in label_hits or table['label_norm'] == q_norm.replace(' ', ''):
            score += 80
        for tok in tokens:
            if tok and tok in table['norm']:
                score += 8
                if tok == table['label_norm'] or table['norm'].startswith(tok):
                    score += 6
        if q_norm and q_norm in table['norm']:
            score += 18
        if score:
            table_scores.append((score, table))
    table_scores.sort(key=lambda item: (-item[0], item[1]['page']))
    top_tables = [table for _, table in table_scores[:limit_tables]]

    page_scores = []
    boosted_pages = {table['page'] for table in top_tables[:5]}
    for page in INDEX['pages']:
        score = 0.0
        for tok in tokens:
            count = page['norm'].count(tok)
            if count:
                score += min(12, 1 + count * 0.35)
        if q_norm and len(q_norm) > 4 and q_norm in page['norm']:
            score += 20
        if page['page'] in boosted_pages:
            score += 14
        for table in INDEX['tables_by_page'].get(page['page'], []):
            if table['label_norm'] in label_hits:
                score += 30
        if score:
            page_scores.append((score, page))
    page_scores.sort(key=lambda item: (-item[0], item[1]['page']))

    chosen_pages = []
    seen = set()
    for _, page in page_scores:
        if page['page'] in seen:
            continue
        chosen_pages.append(page)
        seen.add(page['page'])
        if len(chosen_pages) >= limit_pages:
            break
    if not chosen_pages:
        for table in top_tables[:limit_pages]:
            match = next((p for p in INDEX['pages'] if p['page'] == table['page']), None)
            if match and match['page'] not in seen:
                chosen_pages.append(match)
                seen.add(match['page'])

    return top_tables, chosen_pages


def build_context(query: str) -> str:
    tables, pages = search_binas(query)
    seen = {table['displayLabel'] for table in tables}
    for page in pages:
        for table in INDEX['tables_by_page'].get(page['page'], []):
            if table['displayLabel'] not in seen:
                tables.append(table)
                seen.add(table['displayLabel'])
    tables = tables[:12]
    lines = ['Binas 7e editie — relevante tabellen:']
    if tables:
        for table in tables:
            lines.append(
                f"- Tabel {table['displayLabel']}: {table['title']} ({table['section']}, pagina {table['page']})"
            )
    else:
        lines.append('- Geen sterke tabeltreffer in de inhoudsopgave.')
    lines.append('')
    if pages:
        lines.append('Relevante PDF-pagina’s:')
        for page in pages:
            excerpt = page['text'][:2800]
            lines.append(f'\n--- pagina {page["page"]} ---\n{excerpt}')
    else:
        lines.append('Geen doorzoekbare PDF-tekst gevonden; gebruik de tabelindex.')
    return '\n'.join(lines)


def alignment_for(table=None, title='', page=None):
    if not INDEX['align']:
        return None
    candidates = []
    for item in INDEX['align'].values():
        if table and normalize(item.get('label', '')) == normalize(str(table).replace(item.get('section', ''), '')):
            candidates.append(item)
        if title and item.get('title') == title and (page is None or item.get('page') == page):
            candidates.append(item)
    if page is not None and title:
        exact = [item for item in INDEX['align'].values() if item.get('title') == title and item.get('page') == page]
        if exact:
            return exact[0]
    return candidates[0] if candidates else None


def lookup_table(label='', title='', page=None):
    label_n = normalize(label).replace(' ', '')
    title_n = normalize(title)
    for table in INDEX['tables']:
        if label_n and table['label_norm'] == label_n:
            if page is None or table['page'] == page:
                return table
    for table in INDEX['tables']:
        if title_n and table['norm'].find(title_n) >= 0 and (page is None or table['page'] == page):
            if normalize(table['title']) == title_n:
                return table
    return None


SYSTEM_PROMPT = """Je bent de Binas-assistent voor de 7e editie.
Beantwoord de vraag van de gebruiker uitsluitend met de gegeven Binas-context (inhoudsopgave + PDF-tekst).
Verzin geen waarden die niet in de context staan. Als iets ontbreekt, zeg dat eerlijk en wijs de meest nabije tabel aan.
Antwoord in het Nederlands, tenzij de gebruiker een andere taal gebruikt.
Geef concrete getallen, eenheden en tabelnummers wanneer die in de context staan.
De PDF-tekst kan OCR-fouten bevatten; combineer die met de tabeltitels.

Je MOET geldige JSON teruggeven (geen markdown-hekjes) in dit formaat:
{
  "answer": "Het antwoord in korte, duidelijke tekst. Gebruik **vet** voor kernwaarden.",
  "page": 19,
  "yFraction": 0.0,
  "table": "7A",
  "title": "Natuurconstanten",
  "highlights": ["G gravitatieconstante 6,674 30·10⁻¹¹ Nm²kg⁻²"]
}
- page is het PDF-paginanummer (1-317) waar het antwoord staat.
- yFraction is 0 tot 1 vanaf de bovenkant van die pagina; gebruik 0 als onbekend.
- table is het Binas-tabelnummer zoals 7A of 40, of "" als onbekend.
- title is de tabeltitel, of "".
- highlights: 1 (maximaal 2) korte, letterlijke PDF-fragmenten van de gevraagde WAARDE in de tabelcel
  (getal, formule, systematische naam, eenheid). NOOIT de tabeltitel, het tabelnummer of een kolomkop.
  Fout: ["Naamgeving chemische stoffen"] of ["Natuurconstanten"].
  Goed: ["2-hydroxypropaan-1,2,3-tricarbonzuur"] of ["6,674 30·10⁻¹¹"].
"""


def parse_model_json(text: str) -> dict:
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', raw, re.S)
    if match:
        return json.loads(match.group(0))
    return {'answer': raw, 'page': None, 'yFraction': 0, 'table': '', 'title': '', 'highlights': []}


def compact_key(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', normalize(value))


def is_broad_highlight(phrase: str) -> bool:
    text = re.sub(r'\s+', ' ', str(phrase or '').strip())
    if len(text) > 72:
        return True
    long_words = [word for word in text.split(' ') if len(compact_key(word)) >= 8]
    return len(long_words) >= 3


def longest_prefix_on_page(phrase: str, page_compact: str, min_len: int = 8) -> str | None:
    text = re.sub(r'\s+', ' ', str(phrase or '').strip()).strip('.,;:()[]')
    compact = compact_key(text)
    if len(compact) < min_len:
        return None
    if not page_compact or compact in page_compact:
        return text
    parts = re.split(r'([-–—,.\s]+)', text)
    while len(parts) >= 3:
        parts = parts[:-2]
        candidate = ''.join(parts).strip('-,. ')
        compact = compact_key(candidate)
        if len(compact) >= min_len and compact in page_compact:
            return candidate
    return None


def highlight_pieces(phrase: str, page_compact: str, question_compact: str) -> list[str]:
    text = re.sub(r'\s+', ' ', str(phrase or '').strip())
    if not text or is_broad_highlight(text):
        return []
    pieces = []
    prefix = longest_prefix_on_page(text, page_compact)
    if prefix:
        pieces.append(prefix)
    for part in re.split(r'[-–—\s]+', text):
        part = part.strip().strip('.,;:()[]')
        compact = compact_key(part)
        if len(compact) < 8:
            continue
        if question_compact and compact in question_compact:
            continue
        if page_compact and compact not in page_compact:
            continue
        pieces.append(part)
    return pieces


def collect_title_keys(page: int | None, title: str = '') -> tuple[set[str], set[str]]:
    titles = [title] if title else []
    if page and INDEX:
        for table in INDEX.get('tables') or []:
            if table.get('page') == page and table.get('title'):
                titles.append(table['title'])
    full, words = set(), set()
    for heading in titles:
        compact = compact_key(heading)
        if len(compact) >= 8:
            full.add(compact)
        for token in re.split(r'[^A-Za-zÀ-ÿ0-9]+', heading):
            word = compact_key(token)
            if len(word) >= 8:
                words.add(word)
    return full, words


def is_title_like(phrase: str, full_titles: set[str], title_words: set[str]) -> bool:
    compact = compact_key(phrase)
    if len(compact) < 8:
        return False
    if compact in full_titles or compact in title_words:
        return True
    for heading in full_titles:
        shorter, longer = (compact, heading) if len(compact) <= len(heading) else (heading, compact)
        if shorter in longer and len(shorter) / len(longer) >= 0.55:
            return True
    if re.search(r'\d', phrase):
        return False
    tokens = [compact_key(token) for token in re.split(r'[^A-Za-zÀ-ÿ0-9]+', phrase)]
    tokens = [token for token in tokens if len(token) >= 5]
    return bool(tokens) and all(token in title_words or any(token in heading for heading in full_titles) for token in tokens)


def highlight_score(phrase: str, answer: str, bold: str) -> float:
    compact = compact_key(phrase)
    score = 0.0
    bold_key = compact_key(bold)
    if bold_key and compact in bold_key:
        score += 80
    if re.search(r'\d', phrase):
        score += 35
    if re.search(r'[·×]|10[⁻\-]|-\d', phrase):
        score += 15
    if '-' in phrase and re.search(r'[A-Za-zÀ-ÿ]{4,}', phrase):
        score += 12
    if compact in compact_key(answer):
        score += 6
    score += min(len(compact), 18) * 0.1
    return score


def extract_highlights(data: dict, page: int | None, query: str = '', title: str = '') -> list[str]:
    page_entry = next((entry for entry in INDEX['pages'] if entry['page'] == page), None) if page else None
    page_compact = compact_key(page_entry['text'] if page_entry else '')
    question_compact = compact_key(query)
    full_titles, title_words = collect_title_keys(page, title)

    raw = data.get('highlights')
    candidates = []
    if isinstance(raw, str) and raw.strip():
        candidates.append(raw.strip())
    elif isinstance(raw, list):
        candidates.extend(str(item).strip() for item in raw if str(item).strip())

    answer = str(data.get('answer') or '')
    bold_parts = ' '.join(match.strip() for match in re.findall(r'\*\*(.+?)\*\*', answer) if match.strip())
    candidates.extend(match.strip() for match in re.findall(r'\*\*(.+?)\*\*', answer) if match.strip())
    answer_plain = re.sub(r'\*+', '', answer)
    candidates.extend(re.findall(
        r'[\d]+(?:[.,]\d+)+(?:\s*[·x×*]\s*10[⁻\-]?\d+)?|[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9\-/,]{6,}',
        answer_plain,
    ))

    scored = []
    seen_compact = set()
    for phrase in candidates:
        for piece in highlight_pieces(phrase, page_compact, question_compact):
            compact = compact_key(piece)
            if len(compact) < 8 or compact in seen_compact:
                continue
            if question_compact and compact in question_compact:
                continue
            if is_title_like(piece, full_titles, title_words):
                continue
            seen_compact.add(compact)
            scored.append(piece)

    scored.sort(key=lambda item: highlight_score(item, answer, bold_parts), reverse=True)
    highlights = []
    kept = []
    for phrase in scored:
        compact = compact_key(phrase)
        if any(compact in existing or existing in compact for existing in kept):
            continue
        kept.append(compact)
        highlights.append(phrase)
        if len(highlights) >= 2:
            break
    return highlights


def call_deepseek(messages: list[dict]) -> dict:
    api_key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if not api_key:
        raise ChatError(503, 'De DeepSeek API-sleutel ontbreekt. Vul DEEPSEEK_API_KEY in .env in.')
    model = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash').strip() or 'deepseek-flash'
    payload = {
        'model': model,
        'messages': messages,
        'response_format': {'type': 'json_object'},
        'thinking': {'type': 'disabled'},
        'temperature': 0.2,
        'max_tokens': 1200,
    }
    request = urllib.request.Request(
        'https://api.deepseek.com/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace')[:400]
        raise ChatError(502, f'DeepSeek gaf een fout ({error.code}). {detail}') from error
    except urllib.error.URLError as error:
        raise ChatError(502, f'Kon DeepSeek niet bereiken: {error.reason}') from error

    choice = (body.get('choices') or [{}])[0]
    message = choice.get('message') or {}
    content = message.get('content') or ''
    if not content:
        raise ChatError(502, 'DeepSeek gaf een leeg antwoord.')
    return parse_model_json(content)


class ChatError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def normalize_reply(data: dict, query: str) -> dict:
    answer = str(data.get('answer') or '').strip() or 'Ik kon geen antwoord formuleren uit de Binas-context.'
    table_label = str(data.get('table') or '').strip()
    title = str(data.get('title') or '').strip()
    try:
        page = int(data.get('page'))
    except (TypeError, ValueError):
        page = None
    try:
        y_fraction = float(data.get('yFraction') or 0)
    except (TypeError, ValueError):
        y_fraction = 0.0

    table = lookup_table(table_label, title, page)
    if table:
        table_label = table['displayLabel']
        title = table['title']
        page = page or table['page']
        align = alignment_for(table=table_label, title=title, page=page)
        if align and not y_fraction:
            y_fraction = float(align.get('yFraction') or 0)
    if page is None:
        tables, pages = search_binas(query, limit_pages=1, limit_tables=1)
        page = (tables[0]['page'] if tables else None) or (pages[0]['page'] if pages else None)
    page = max(1, min(317, int(page or 11)))
    y_fraction = max(0.0, min(0.95, y_fraction))
    return {
        'answer': answer,
        'page': page,
        'yFraction': round(y_fraction, 4),
        'table': table_label,
        'title': title,
        'highlights': extract_highlights(data, page, query, title),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        sys.stderr.write(f'{self.address_string()} {fmt % args}\n')

    def send_json(self, status: int, payload: dict, extra_headers: list[tuple[str, str]] | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_redirect(self, location: str):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def html_path(self, path: str) -> str:
        return '/index.html' if path in {'', '/'} else path

    def serve_html_with_reload(self, path: str):
        rel = self.html_path(path).lstrip('/')
        file_path = ROOT / rel
        if not file_path.is_file():
            self.send_error(404)
            return
        body = file_path.read_text(encoding='utf-8')
        if '</body>' in body:
            body = body.replace('</body>', DEV_RELOAD_SCRIPT + '</body>', 1)
        else:
            body += DEV_RELOAD_SCRIPT
        encoded = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def serve_static_file(self):
        path = urlparse(self.path).path
        if dev_mode() and (path == '/' or path.endswith('.html')):
            return self.serve_html_with_reload(path)
        return super().do_GET()

    def handle_dev_reload_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        client: queue.Queue[str] = queue.Queue()
        with reload_clients_lock:
            reload_clients.append(client)
        try:
            while True:
                try:
                    client.get(timeout=30)
                    self.wfile.write(b'data: reload\n\n')
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b': keepalive\n\n')
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with reload_clients_lock:
                if client in reload_clients:
                    reload_clients.remove(client)

    def is_authenticated(self) -> bool:
        if not auth_required():
            return True
        cookies = parse_cookies(self.headers.get('Cookie'))
        return session_token_valid(cookies.get(COOKIE_NAME))

    def send_auth_status(self):
        if not auth_required():
            return self.send_json(200, {'ok': True, 'required': False})
        if self.is_authenticated():
            return self.send_json(200, {'ok': True, 'required': True})
        return self.send_json(401, {'ok': False, 'required': True})

    def read_json_body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > 64_000:
            self.send_json(400, {'error': 'Ongeldig verzoek.'})
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {'error': 'Ongeldige JSON.'})
            return None
        if not isinstance(payload, dict):
            self.send_json(400, {'error': 'Ongeldige JSON.'})
            return None
        return payload

    def handle_login(self):
        payload = self.read_json_body()
        if payload is None:
            return
        if not auth_required():
            return self.send_json(200, {'ok': True, 'required': False})
        password = str(payload.get('password') or '')
        expected = site_password().encode('utf-8')
        submitted = password.encode('utf-8')
        if len(submitted) != len(expected) or not hmac.compare_digest(submitted, expected):
            return self.send_json(401, {'error': 'Onjuist wachtwoord.'})
        return self.send_json(200, {'ok': True}, [
            ('Set-Cookie', session_cookie_header(make_session_token())),
        ])

    def handle_logout(self):
        return self.send_json(200, {'ok': True}, [
            ('Set-Cookie', session_cookie_header(None)),
        ])

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/__dev_reload':
            if not dev_mode():
                return self.send_json(404, {'error': 'Niet gevonden'})
            return self.handle_dev_reload_sse()
        if path == '/api/auth':
            return self.send_auth_status()
        if path == '/api/health':
            if auth_required() and not self.is_authenticated():
                return self.send_json(401, {'error': 'Niet ingelogd.'})
            key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
            return self.send_json(200, {
                'ok': True,
                'configured': bool(key),
                'pages': len(INDEX['pages']),
                'tables': len(INDEX['tables']),
            })
        if path == '/login.html' and self.is_authenticated():
            return self.send_redirect('/')
        if path in PUBLIC_PATHS:
            if os.environ.get('SERVE_STATIC', '1') == '0':
                return self.send_json(404, {'error': 'Niet gevonden'})
            return self.serve_static_file()
        if auth_required() and not self.is_authenticated():
            if path.startswith('/api/'):
                return self.send_json(401, {'error': 'Niet ingelogd.'})
            return self.send_redirect('/login.html')
        if os.environ.get('SERVE_STATIC', '1') == '0':
            return self.send_json(404, {'error': 'Niet gevonden'})
        return self.serve_static_file()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/login':
            return self.handle_login()
        if path == '/api/logout':
            return self.handle_logout()
        if auth_required() and not self.is_authenticated():
            return self.send_json(401, {'error': 'Niet ingelogd.'})
        if path != '/api/chat':
            return self.send_json(404, {'error': 'Niet gevonden'})
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > 64_000:
            return self.send_json(400, {'error': 'Ongeldig verzoek.'})
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self.send_json(400, {'error': 'Ongeldige JSON.'})

        incoming = payload.get('messages') if isinstance(payload, dict) else None
        if not isinstance(incoming, list) or not incoming:
            return self.send_json(400, {'error': 'Stuur minstens één bericht.'})

        history = []
        for item in incoming[-12:]:
            if not isinstance(item, dict):
                continue
            role = item.get('role')
            content = str(item.get('content') or '').strip()
            if role in {'user', 'assistant'} and content and len(content) <= 4000:
                history.append({'role': role, 'content': content[:4000]})
        if not history or history[-1]['role'] != 'user':
            return self.send_json(400, {'error': 'Het laatste bericht moet van de gebruiker komen.'})

        question = history[-1]['content']
        context = build_context(question)
        messages = [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            *history[:-1],
            {'role': 'user', 'content': f'Vraag: {question}\n\nContext uit Binas:\n{context}'},
        ]
        try:
            raw = call_deepseek(messages)
            reply = normalize_reply(raw, question)
        except ChatError as error:
            return self.send_json(error.status, {'error': error.message})
        except Exception as error:
            return self.send_json(500, {'error': f'Onverwachte fout: {error}'})
        return self.send_json(200, reply)


def main():
    load_dotenv()
    global INDEX
    INDEX = load_index()
    Handler.extensions_map['.mjs'] = 'application/javascript'
    Handler.extensions_map['.js'] = 'application/javascript'
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', '8080'))
    server = ThreadingHTTPServer((host, port), Handler)
    start_dev_watcher()
    auth_note = 'login aan' if auth_required() else 'geen SITE_PASSWORD, site is open'
    dev_note = ', live reload aan' if dev_mode() else ''
    print(
        f'Binas server op http://{host}:{port}  '
        f'(PDF-pagina’s: {len(INDEX["pages"])}, {auth_note}{dev_note})',
        flush=True,
    )
    server.serve_forever()


if __name__ == '__main__':
    main()
