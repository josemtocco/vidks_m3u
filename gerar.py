import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = 'https://www.vidks.net'
DISCOVER = f'{BASE}/discover'
OUT_M3U = Path('vidks.m3u')
OUT_JSON = Path('canais.json')
OUT_DISC = Path('descoberto.json')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36',
    'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
}

session = requests.Session()
session.headers.update(HEADERS)

MEDIA_PATTERNS = [
    r'https?://[^\s"\'<>\\]+?\.m3u8(?:\?[^\s"\'<>\\]*)?',
    r'https?://[^\s"\'<>\\]+?\.mpd(?:\?[^\s"\'<>\\]*)?',
    r'https?://[^\s"\'<>\\]+?\.m3u(?:\?[^\s"\'<>\\]*)?',
]


def clean_url(u):
    if not u:
        return None
    u = u.strip().strip('"\'<>')
    u = u.replace('\\/', '/')
    return u


def extract_media(html, page_url):
    found = []
    soup = BeautifulSoup(html, 'html.parser')

    # Common HTML attributes used by players.
    for tag in soup.find_all(True):
        for attr in ('src', 'data-src', 'data-url', 'data-stream', 'data-video', 'href'):
            value = tag.get(attr)
            if not value or not isinstance(value, str):
                continue
            value = clean_url(value)
            if value.startswith('//'):
                value = 'https:' + value
            elif value.startswith('/'):
                value = urljoin(page_url, value)
            if re.search(r'\.(m3u8|mpd|m3u)(?:\?|$)', value, re.I):
                found.append(value)

    # Raw URLs in HTML/JavaScript.
    text = html.replace('\\u0026', '&').replace('\\/', '/')
    for pattern in MEDIA_PATTERNS:
        found.extend(re.findall(pattern, text, re.I))

    # JSON escaped URLs.
    for m in re.finditer(r'(?:file|src|source|url|stream)\s*[:=]\s*["\']([^"\']+)', text, re.I):
        value = clean_url(m.group(1))
        if re.search(r'\.(m3u8|mpd|m3u)(?:\?|$)', value or '', re.I):
            found.append(value)

    result = []
    for u in found:
        u = clean_url(u)
        if u and u not in result:
            result.append(u)
    return result


def clean_channel_name(name):
    """Remove textos de interface/alt do VidKS que não fazem parte do nome."""
    if not name:
        return ''
    name = ' '.join(str(name).split())
    name = re.sub(r'^logo\s+canal\s*[:\-]?\s*', '', name, flags=re.I)
    return name.strip()


def fetch(url, timeout=25):
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.ok:
            return r
    except requests.RequestException:
        pass
    return None


def discover_channels():
    channels = []
    pages = {}
    for page in range(1, 25):
        url = DISCOVER if page == 1 else f'{DISCOVER}?page={page}'
        r = fetch(url)
        if not r:
            pages[url] = 0
            continue
        soup = BeautifulSoup(r.text, 'html.parser')
        count = 0
        for a in soup.find_all('a', href=True):
            href = urljoin(BASE, a['href'])
            # VidKS channel pages use /vid/*.ks
            if not re.match(r'^https://www\.vidks\.net/vid/[^?#]+\.ks(?:[?#].*)?$', href):
                continue
            name = clean_channel_name(a.get_text(' ', strip=True))
            if not name:
                # Some templates put the name in an image alt/title.
                img = a.find('img')
                name = clean_channel_name((img.get('alt') or img.get('title') or '').strip()) if img else ''
            if not name:
                continue
            category = ''
            parent = a.parent
            for _ in range(3):
                if parent:
                    txt = ' '.join(parent.get_text(' ', strip=True).split())
                    # Prefer a known category appearing after the channel name.
                    cats = ['TV Aberta','WebTV','Desenhos','Filmes','Séries','Esportes','Notícias','Variedades','TV Local','Religiosos','Educação','Cultura','24 horas']
                    for c in cats:
                        if c in txt and c != name:
                            category = c
                            break
                    if category:
                        break
                    parent = parent.parent
            item = {'name': name, 'category': category, 'page': href}
            if not any(x['page'] == href for x in channels):
                channels.append(item)
                count += 1
        pages[url] = count
        time.sleep(0.15)
    return channels, pages


def get_stream(channel_url):
    r = fetch(channel_url)
    if not r:
        return None, 'pagina_indisponivel'

    # First pass: media URLs in the channel page.
    candidates = extract_media(r.text, r.url)

    # Then inspect iframes/player pages; many VidKS entries embed the actual player.
    soup = BeautifulSoup(r.text, 'html.parser')
    frames = []
    for tag in soup.find_all(['iframe', 'video', 'source']):
        for attr in ('src', 'data-src'):
            u = tag.get(attr)
            if u:
                u = urljoin(r.url, clean_url(u))
                if u not in frames:
                    frames.append(u)
    for frame in frames[:8]:
        fr = fetch(frame, timeout=20)
        if fr:
            candidates.extend(extract_media(fr.text, fr.url))

    # Look one level deeper when the iframe points to another HTML player.
    for u in list(candidates):
        if not re.search(r'\.(m3u8|mpd|m3u)(?:\?|$)', u, re.I):
            continue

    unique = []
    for u in candidates:
        if u not in unique:
            unique.append(u)

    for stream in unique:
        if test_stream(stream):
            return stream, ''
    return None, 'nenhum_stream_aprovado'


def test_stream(url):
    try:
        headers = {'Range': 'bytes=0-4095', 'Referer': BASE + '/'}
        r = session.get(url, headers=headers, timeout=15, allow_redirects=True, stream=True)
        if r.status_code not in (200, 206):
            return False
        ctype = (r.headers.get('content-type') or '').lower()
        data = next(r.iter_content(4096), b'')
        r.close()
        if re.search(r'\.m3u8(?:\?|$)', url, re.I) or 'mpegurl' in ctype:
            return b'#EXTM3U' in data
        if re.search(r'\.mpd(?:\?|$)', url, re.I) or 'dash+xml' in ctype:
            return b'<MPD' in data or b'<mpd' in data
        if re.search(r'\.m3u(?:\?|$)', url, re.I):
            return True
        # For extensionless HLS endpoints, accept a playlist signature.
        return b'#EXTM3U' in data or b'<MPD' in data or b'<mpd' in data
    except requests.RequestException:
        return False


def make_m3u(channels):
    lines = ['#EXTM3U']
    for c in sorted(channels, key=lambda x: (x.get('category',''), x.get('name','').lower())):
        name = c['name'].replace('"', "'")
        category = c.get('category') or 'VidKS'
        stream = c['stream']
        # VidKS is a Brazilian directory; metadata explicitly identifies source/site.
        lines.append(f'#EXTINF:-1 tvg-name="{name}" tvg-country="BR" tvg-language="Portuguese" group-title="{category}",{name}')
        lines.append(stream)
    OUT_M3U.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    channels, pages = discover_channels()
    approved = []
    errors = []
    for i, c in enumerate(channels, 1):
        stream, err = get_stream(c['page'])
        c['stream'] = stream
        c['status'] = 'ativo' if stream else 'inativo'
        if stream:
            approved.append(c)
        else:
            errors.append({'page': c['page'], 'name': c['name'], 'erro': err})
        print(f'[{i}/{len(channels)}] {c["name"]}: {"OK" if stream else "SEM STREAM"}', flush=True)
        time.sleep(0.10)

    make_m3u(approved)
    OUT_JSON.write_text(json.dumps(approved, ensure_ascii=False, indent=2), encoding='utf-8')
    OUT_DISC.write_text(json.dumps({
        'site': BASE,
        'pagina_descoberta': DISCOVER,
        'paginas': pages,
        'total_descobertos': len(channels),
        'total_aprovados': len(approved),
        'total_rejeitados': len(channels) - len(approved),
        'erros': errors,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Finalizado: {len(approved)} canais ativos de {len(channels)} descobertos.')

if __name__ == '__main__':
    main()
