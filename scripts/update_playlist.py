#!/usr/bin/env python3
"""
VidKS M3U Playlist Updater
- Coleta canais por categoria em https://www.vidks.net
- Testa se o stream está ativo antes de incluir
- Mantém estado persistente: só remove canal após 6 falhas consecutivas
- Gera playlist.m3u otimizada para SS IPTV (group-title + logos)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

BASE_URL = "https://www.vidks.net"
PLAYER_URL = "https://www.vidks.net/player/vid/{slug}.ks"
LOGO_URL = "https://img.vidks.net/v3x/l/{slug}.webp"

# Mapeamento oficial de categorias (id -> nome)
CATEGORIES = {
    1: "Esportes",
    2: "Documentários",
    3: "TV Aberta",
    4: "Religiosos",
    5: "WebTV",
    6: "Filmes",
    7: "Séries",
    8: "Infantil",
    9: "Notícias",
    10: "Variedades",
    11: "Educação",
    12: "Agropecuária",
    13: "Desenhos",
    14: "Universitária",
    15: "Cultura",
    16: "TV Local",
    17: "24 horas",
    18: "Culinária",
    19: "Comércio",
    20: "Natureza",
    21: "Fast",
    22: "Finanças",
    23: "Turismo",
}

# Ordem preferida das categorias no M3U (as que não estiverem aqui vão no final)
CATEGORY_ORDER = [
    "TV Aberta",
    "Esportes",
    "Notícias",
    "WebTV",
    "Filmes",
    "Séries",
    "Desenhos",
    "Infantil",
    "Variedades",
    "TV Local",
    "Religiosos",
    "Educação",
    "Cultura",
    "Documentários",
    "24 horas",
    "Fast",
    "Universitária",
    "Agropecuária",
    "Culinária",
    "Natureza",
    "Turismo",
    "Finanças",
    "Comércio",
]

MAX_FAIL_COUNT = 6          # remove só depois de 6 atualizações consecutivas com falha
REQUEST_TIMEOUT = 12
STREAM_TEST_TIMEOUT = 8
PAGE_DELAY = 0.8            # segundos entre páginas de listagem
CHANNEL_DELAY = 0.35        # segundos entre testes de stream
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# Caminhos relativos ao repositório
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "state.json"
PLAYLIST_FILE = DATA_DIR / "playlist.m3u"

# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_name(raw: str, category: str = "") -> str:
    """Remove prefixos de categoria e limpa espaços."""
    if not raw:
        return ""
    name = " ".join(raw.split())
    # remove prefixos comuns que o site coloca no texto do link
    for prefix in [
        category,
        "TV Aberta",
        "Esportes",
        "WebTV",
        "Filmes",
        "Séries",
        "Desenhos",
        "Notícias",
        "Variedades",
        "TV Local",
        "Religiosos",
        "Infantil",
        "Educação",
        "Cultura",
        "Documentários",
        "Fast",
        "24 horas",
    ]:
        if name.lower().startswith(prefix.lower()):
            name = name[len(prefix):].strip()
    # remove linhas extras / programação atual
    name = name.split("  ")[0].strip()
    return name or raw


def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s


# ---------------------------------------------------------------------------
# Coleta de canais por categoria
# ---------------------------------------------------------------------------

def fetch_category_channels(session: requests.Session, cat_id: int, cat_name: str) -> list[dict[str, Any]]:
    """Pagina a listagem de uma categoria e retorna lista de {slug, name, category}."""
    channels: list[dict[str, Any]] = []
    page = 1
    seen_slugs: set[str] = set()

    while True:
        url = f"{BASE_URL}/search?category={cat_id}&page={page}"
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 403:
                print(f"  [!] 403 Forbidden em {url} — IP provavelmente bloqueado.")
                break
            if r.status_code != 200:
                print(f"  [!] HTTP {r.status_code} em {url}")
                break

            soup = BeautifulSoup(r.text, "lxml")
            links = soup.select('a[href*="/vid/"]')

            if not links:
                break

            new_found = 0
            for a in links:
                href = a.get("href") or ""
                if "/vid/" not in href or not href.endswith(".ks"):
                    continue
                slug = href.split("/vid/")[-1].replace(".ks", "").strip()
                if not slug or slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                raw_name = a.get_text(" ", strip=True)
                name = clean_name(raw_name, cat_name)
                if not name:
                    name = slug.replace("-", " ").title()

                channels.append({
                    "slug": slug,
                    "name": name,
                    "category": cat_name,
                })
                new_found += 1

            print(f"  {cat_name} p{page}: +{new_found} (total cat: {len(channels)})")

            # última página costuma ter bem menos itens
            if new_found < 8:
                break

            page += 1
            time.sleep(PAGE_DELAY)

        except requests.RequestException as e:
            print(f"  [!] Erro de rede em {cat_name} p{page}: {e}")
            break

    return channels


def discover_all_channels(session: requests.Session) -> dict[str, dict[str, Any]]:
    """Retorna dict slug -> {slug, name, category}."""
    all_ch: dict[str, dict[str, Any]] = {}
    print("\n=== Descobrindo canais por categoria ===")
    for cat_id, cat_name in CATEGORIES.items():
        found = fetch_category_channels(session, cat_id, cat_name)
        for ch in found:
            # se o mesmo slug aparecer em mais de uma categoria, mantém a primeira
            if ch["slug"] not in all_ch:
                all_ch[ch["slug"]] = ch
        time.sleep(0.4)
    print(f"Total de canais únicos descobertos: {len(all_ch)}")
    return all_ch


# ---------------------------------------------------------------------------
# Extração e teste de stream
# ---------------------------------------------------------------------------

def extract_stream_url(session: requests.Session, slug: str) -> str | None:
    """Abre a página do player e extrai o m3u8 do preload ou do HTML."""
    url = PLAYER_URL.format(slug=slug)
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None

        html = r.text

        # 1) link rel="preload" type="application/x-mpegURL"
        m = re.search(
            r'<link[^>]+rel=["\']preload["\'][^>]+href=["\']([^"\']+\.m3u8[^"\']*)["\']',
            html,
            re.I,
        )
        if m:
            return m.group(1).strip()

        # 2) qualquer .m3u8 no HTML (fallback)
        m = re.search(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)
        if m:
            return m.group(0).strip()

        # 3) source / video src
        m = re.search(r'src=["\']([^"\']+\.m3u8[^"\']*)["\']', html, re.I)
        if m:
            return m.group(1).strip()

        return None
    except requests.RequestException:
        return None


def is_stream_alive(session: requests.Session, stream_url: str) -> bool:
    """Testa se o m3u8 responde e parece válido."""
    if not stream_url or not stream_url.startswith("http"):
        return False
    try:
        # HEAD primeiro (mais leve)
        r = session.head(stream_url, timeout=STREAM_TEST_TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            ctype = r.headers.get("Content-Type", "").lower()
            if "mpegurl" in ctype or "application/vnd.apple" in ctype or "application/x-mpeg" in ctype:
                return True

        # GET parcial se HEAD não for conclusivo
        r = session.get(stream_url, timeout=STREAM_TEST_TIMEOUT, stream=True, allow_redirects=True)
        if r.status_code != 200:
            return False
        # lê só o começo
        chunk = next(r.iter_content(chunk_size=1024), b"")
        text = chunk.decode("utf-8", errors="ignore")
        if "#EXTM3U" in text or "#EXT-X-" in text:
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Estado e geração do M3U
# ---------------------------------------------------------------------------

def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Erro ao ler state.json: {e}. Começando do zero.")
    return {"channels": {}, "last_update": None, "version": 1}


def save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state["last_update"] = utc_now()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def generate_m3u(active_channels: list[dict[str, Any]]) -> str:
    """Gera o conteúdo do arquivo M3U otimizado para SS IPTV."""
    lines = [
        "#EXTM3U",
        f"#PLAYLIST:VidKS Atualizado {utc_now()}",
        "# Generated for SS IPTV – group-title + logos + fail-safe state",
    ]

    # ordena por CATEGORY_ORDER depois por nome
    def sort_key(ch: dict) -> tuple:
        cat = ch.get("category", "Outros")
        try:
            cat_idx = CATEGORY_ORDER.index(cat)
        except ValueError:
            cat_idx = 999
        return (cat_idx, ch.get("name", "").lower())

    sorted_ch = sorted(active_channels, key=sort_key)

    current_group = None
    for ch in sorted_ch:
        group = ch.get("category") or "Outros"
        if group != current_group:
            current_group = group
            # opcional: comentário de seção (alguns players ignoram)
            lines.append(f"# {group}")

        name = ch.get("name") or ch["slug"]
        logo = ch.get("logo") or LOGO_URL.format(slug=ch["slug"])
        url = ch["url"]
        slug = ch["slug"]

        # Formato limpo e compatível com SS IPTV / TiviMate / IPTV Smarters
        extinf = (
            f'#EXTINF:-1 tvg-id="{slug}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",{name}'
        )
        lines.append(extinf)
        lines.append(url)

    return "\n".join(lines) + "\n"


def update_state_and_playlist(discovered: dict[str, dict[str, Any]], session: requests.Session) -> None:
    state = load_state()
    channels_state: dict[str, dict] = state.setdefault("channels", {})

    now = utc_now()
    active_for_m3u: list[dict[str, Any]] = []

    # 1) Atualiza / adiciona canais descobertos
    print("\n=== Testando streams ===")
    total = len(discovered)
    for i, (slug, info) in enumerate(discovered.items(), 1):
        print(f"[{i}/{total}] {info['name']} ({slug}) ... ", end="", flush=True)

        stream = extract_stream_url(session, slug)
        time.sleep(0.15)

        alive = False
        if stream:
            alive = is_stream_alive(session, stream)

        if alive and stream:
            print("OK")
            entry = channels_state.get(slug, {})
            entry.update({
                "slug": slug,
                "name": info["name"],
                "category": info["category"],
                "url": stream,
                "logo": LOGO_URL.format(slug=slug),
                "fail_count": 0,
                "last_ok": now,
                "first_seen": entry.get("first_seen", now),
            })
            channels_state[slug] = entry
            active_for_m3u.append(entry)
        else:
            print("FALHOU")
            if slug in channels_state:
                channels_state[slug]["fail_count"] = channels_state[slug].get("fail_count", 0) + 1
                channels_state[slug]["last_fail"] = now
            # canais novos que já falharam não entram no state ainda

        time.sleep(CHANNEL_DELAY)

    # 2) Canais que sumiram da listagem também recebem +1 fail
    discovered_slugs = set(discovered.keys())
    for slug, entry in list(channels_state.items()):
        if slug not in discovered_slugs:
            entry["fail_count"] = entry.get("fail_count", 0) + 1
            entry["last_fail"] = now
            print(f"  [sumiu da listagem] {entry.get('name', slug)} → fail={entry['fail_count']}")

    # 3) Remove apenas quem atingiu MAX_FAIL_COUNT
    to_remove = [s for s, e in channels_state.items() if e.get("fail_count", 0) >= MAX_FAIL_COUNT]
    for s in to_remove:
        name = channels_state[s].get("name", s)
        print(f"  [REMOVIDO após {MAX_FAIL_COUNT} falhas] {name}")
        del channels_state[s]

    # 4) Inclui no M3U também os canais antigos que ainda têm fail_count < MAX e url válida
    #    (mesmo que nesta rodada não tenham sido re-testados com sucesso, se já estavam OK)
    #    Na prática já adicionamos os que passaram no teste. Os que falharam nesta rodada
    #    mas ainda têm fail < 6 continuam no state, porém não vão para o M3U desta vez
    #    (só voltam quando o stream voltar a funcionar). Isso evita lista cheia de links mortos.

    # Reconstroi active_for_m3u apenas com fail_count == 0 (os que passaram agora)
    # Se quiser manter os que falharam 1-5 vezes ainda na lista, descomente o bloco abaixo.
    # active_for_m3u = [e for e in channels_state.values() if e.get("fail_count", 0) == 0 and e.get("url")]

    # 5) Salva tudo
    save_state(state)
    m3u_content = generate_m3u(active_for_m3u)
    with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"\n=== Resumo ===")
    print(f"Canais no state.json : {len(channels_state)}")
    print(f"Canais no playlist.m3u (ativos agora): {len(active_for_m3u)}")
    print(f"Removidos nesta rodada: {len(to_remove)}")
    print(f"Arquivos salvos em: {DATA_DIR}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"VidKS M3U Updater – {utc_now()}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    session = create_session()

    # Teste rápido de conectividade
    try:
        r = session.get(BASE_URL, timeout=10)
        if r.status_code == 403:
            print("\n[ERRO] O site está bloqueando este IP (HTTP 403).")
            print("Rode o script em uma máquina/VPS com IP residencial brasileiro,")
            print("ou configure um proxy no requests.Session.")
            return 1
    except Exception as e:
        print(f"[ERRO] Não foi possível conectar em {BASE_URL}: {e}")
        return 1

    discovered = discover_all_channels(session)
    if not discovered:
        print("[ERRO] Nenhum canal descoberto. Abortando.")
        return 1

    update_state_and_playlist(discovered, session)
    print("\nConcluído com sucesso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
