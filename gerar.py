#!/usr/bin/env python3
"""
VidKS → M3U automático (versão corrigida)
Gera playlist a partir de https://www.vidks.net/
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.vidks.net"
OUT_M3U = Path("vidks.m3u")
OUT_JSON = Path("canais.json")
OUT_DISC = Path("descoberto.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)

# Categorias conhecidas do site (usadas para limpeza e group-title)
KNOWN_CATS = [
    "TV Aberta", "WebTV", "Desenhos", "Filmes", "Séries", "Esportes",
    "Notícias", "Variedades", "TV Local", "Religiosos", "Educação",
    "Cultura", "24 horas", "Fast",
]

MEDIA_RE = re.compile(
    r'https?://[^\s"\'<>\\]+?\.(?:m3u8|mpd|m3u)(?:\?[^\s"\'<>\\]*)?',
    re.I,
)


def clean_url(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip().strip("\"'<>")
    u = u.replace("\\/", "/")
    if u.startswith("//"):
        u = "https:" + u
    return u


def to_player_url(channel_url: str) -> str:
    """Converte /vid/xxx.ks → /player/vid/xxx.ks"""
    path = urlparse(channel_url).path
    if path.startswith("/player/"):
        return channel_url
    if path.startswith("/vid/"):
        return urljoin(BASE, "/player" + path)
    return channel_url


def extract_media(html: str, page_url: str) -> list[str]:
    found: list[str] = []
    soup = BeautifulSoup(html, "html.parser")

    # 1) Atributos comuns + preload
    for tag in soup.find_all(True):
        for attr in ("src", "data-src", "data-url", "data-stream", "data-video", "href"):
            value = tag.get(attr)
            if not value or not isinstance(value, str):
                continue
            value = clean_url(value)
            if not value:
                continue
            if value.startswith("/"):
                value = urljoin(page_url, value)
            if re.search(r"\.(m3u8|mpd|m3u)(?:\?|$)", value, re.I):
                found.append(value)

    text = (
        html.replace("\\u0026", "&")
        .replace("\\/", "/")
        .replace("&amp;", "&")
    )

    # 2) URLs soltas no HTML/JS
    found.extend(MEDIA_RE.findall(text))

    # 3) Padrão específico do player VidKS: source={"src":"...m3u8"...}
    for m in re.finditer(
        r'(?:source|src|file|url|stream)\s*[:=]\s*["\']([^"\']+\.(?:m3u8|mpd|m3u)[^"\']*)["\']',
        text,
        re.I,
    ):
        value = clean_url(m.group(1))
        if value:
            found.append(value)

    # 4) JSON embutido: "src":"https://....m3u8"
    for m in re.finditer(r'"src"\s*:\s*"([^"]+\.(?:m3u8|mpd|m3u)[^"]*)"', text, re.I):
        value = clean_url(m.group(1))
        if value:
            found.append(value)

    # Deduplicar mantendo ordem
    result: list[str] = []
    for u in found:
        u = clean_url(u)
        if u and u not in result and not u.startswith("blob:"):
            result.append(u)
    return result


def clean_channel_name(raw: str) -> tuple[str, str]:
    """
    Retorna (nome, categoria).
    No discover o texto costuma ser: "Categoria  Nome do Canal"
    """
    if not raw:
        return "", ""
    text = " ".join(str(raw).split())
    text = re.sub(r"^logo\s+canal\s*[:\-]?\s*", "", text, flags=re.I)

    category = ""
    for cat in KNOWN_CATS:
        # categoria no início
        if text.lower().startswith(cat.lower()):
            category = cat
            text = text[len(cat):].strip(" -–|:")
            break
        # ou em qualquer lugar
        if cat in text and not category:
            category = cat

    # remove restos de programação que às vezes vêm depois
    text = re.split(r"\s{2,}|\n", text)[0].strip()
    return text.strip(), category


def fetch(url: str, timeout: int = 25) -> requests.Response | None:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.ok:
            return r
    except requests.RequestException:
        pass
    return None


def discover_channels() -> tuple[list[dict], dict]:
    """Coleta canais de várias páginas de listagem."""
    channels: list[dict] = []
    pages: dict[str, int] = {}
    seen_pages: set[str] = set()

    list_urls = [
        f"{BASE}/",
        f"{BASE}/discover",
        f"{BASE}/search",
    ]
    # algumas páginas extras
    for p in range(2, 8):
        list_urls.append(f"{BASE}/discover?page={p}")
        list_urls.append(f"{BASE}/search?page={p}")

    for url in list_urls:
        if url in seen_pages:
            continue
        seen_pages.add(url)

        r = fetch(url)
        if not r:
            pages[url] = 0
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        count = 0

        for a in soup.find_all("a", href=True):
            href = urljoin(BASE, a["href"])
            if not re.match(r"^https://www\.vidks\.net/vid/[^?#]+\.ks(?:[?#].*)?$", href):
                continue

            # texto do link ou title
            raw_name = a.get("title") or a.get_text(" ", strip=True) or ""
            raw_name = re.sub(r"^Assistir\s+", "", raw_name, flags=re.I)
            raw_name = re.sub(r"\s+ao vivo$", "", raw_name, flags=re.I)

            if not raw_name:
                img = a.find("img")
                if img:
                    raw_name = (img.get("alt") or img.get("title") or "").strip()

            name, category = clean_channel_name(raw_name)
            if not name or len(name) < 2:
                # fallback: usa o slug
                slug = urlparse(href).path.rstrip("/").split("/")[-1].replace(".ks", "")
                name = slug.replace("-", " ").title()

            # tenta pegar categoria do card pai se ainda vazia
            if not category:
                parent = a.parent
                for _ in range(4):
                    if not parent:
                        break
                    txt = " ".join(parent.get_text(" ", strip=True).split())
                    for c in KNOWN_CATS:
                        if c in txt and c.lower() not in name.lower():
                            category = c
                            break
                    if category:
                        break
                    parent = parent.parent

            item = {
                "name": name,
                "category": category or "VidKS",
                "page": href,
            }
            if not any(x["page"] == href for x in channels):
                channels.append(item)
                count += 1

        pages[url] = count
        time.sleep(0.12)

    return channels, pages


def test_stream(url: str) -> bool:
    try:
        headers = {
            "Range": "bytes=0-8191",
            "Referer": BASE + "/",
            "Origin": BASE,
        }
        r = session.get(
            url,
            headers=headers,
            timeout=18,
            allow_redirects=True,
            stream=True,
        )
        if r.status_code not in (200, 206):
            return False

        ctype = (r.headers.get("content-type") or "").lower()
        data = next(r.iter_content(8192), b"")
        r.close()

        if re.search(r"\.m3u8(?:\?|$)", url, re.I) or "mpegurl" in ctype or "x-mpegurl" in ctype:
            return b"#EXTM3U" in data or b"#EXT-X-" in data
        if re.search(r"\.mpd(?:\?|$)", url, re.I) or "dash+xml" in ctype:
            return b"<MPD" in data or b"<mpd" in data
        if re.search(r"\.m3u(?:\?|$)", url, re.I):
            return True
        # endpoints sem extensão
        return b"#EXTM3U" in data or b"#EXT-X-" in data or b"<MPD" in data
    except requests.RequestException:
        return False


def get_stream(channel_url: str) -> tuple[str | None, str]:
    """
    Estratégia:
    1. Abre a página do player (/player/vid/...)
    2. Extrai candidatos de m3u8/mpd
    3. Testa um a um até achar um válido
    """
    player_url = to_player_url(channel_url)
    r = fetch(player_url, timeout=22)
    if not r:
        # fallback: tenta a página do canal
        r = fetch(channel_url, timeout=20)
        if not r:
            return None, "pagina_indisponivel"

    candidates = extract_media(r.text, r.url)

    # também olha iframes rasos (caso existam)
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup.find_all(["iframe", "video", "source"]):
        for attr in ("src", "data-src"):
            u = tag.get(attr)
            if u:
                u = clean_url(urljoin(r.url, u))
                if u and re.search(r"\.(m3u8|mpd|m3u)(?:\?|$)", u, re.I):
                    candidates.append(u)

    # deduplicar
    unique: list[str] = []
    for u in candidates:
        if u and u not in unique:
            unique.append(u)

    for stream in unique:
        if test_stream(stream):
            return stream, ""
    return None, "nenhum_stream_aprovado"


def make_m3u(channels: list[dict]) -> None:
    lines = ["#EXTM3U"]
    for c in sorted(channels, key=lambda x: (x.get("category", ""), x.get("name", "").lower())):
        name = c["name"].replace('"', "'")
        category = c.get("category") or "VidKS"
        stream = c["stream"]
        lines.append(
            f'#EXTINF:-1 tvg-name="{name}" tvg-country="BR" '
            f'tvg-language="Portuguese" group-title="{category}",{name}'
        )
        lines.append(stream)
    OUT_M3U.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    print("Descobrindo canais...", flush=True)
    channels, pages = discover_channels()
    print(f"Descobertos: {len(channels)} canais", flush=True)

    approved: list[dict] = []
    errors: list[dict] = []

    for i, c in enumerate(channels, 1):
        stream, err = get_stream(c["page"])
        c["stream"] = stream
        c["status"] = "ativo" if stream else "inativo"

        if stream:
            approved.append(c)
            print(f"[{i}/{len(channels)}] ✓ {c['name']}", flush=True)
        else:
            errors.append({"page": c["page"], "name": c["name"], "erro": err})
            print(f"[{i}/{len(channels)}] ✗ {c['name']} ({err})", flush=True)

        time.sleep(0.18)  # educado com o servidor

    make_m3u(approved)
    OUT_JSON.write_text(
        json.dumps(approved, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    OUT_DISC.write_text(
        json.dumps(
            {
                "site": BASE,
                "paginas": pages,
                "total_descobertos": len(channels),
                "total_aprovados": len(approved),
                "total_rejeitados": len(channels) - len(approved),
                "erros": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"\nFinalizado: {len(approved)} canais ativos de {len(channels)} descobertos.",
        flush=True,
    )


if __name__ == "__main__":
    main()
