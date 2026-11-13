#!/usr/bin/env python3
"""
Coletor VidKS -> playlist M3U otimizada para SS IPTV.

- Abre cada categoria do site e clica em "Carregar mais" ate acabar.
- Abre cada canal e captura o stream .m3u8.
- Nao apaga nada: canais que funcionam sao mantidos, novos entram, e um canal
  so sai da lista depois de 6 atualizacoes seguidas sem funcionar.
- Protecao anti-apagao: se o site nao devolver canal nenhum (bloqueio de
  regiao, site fora do ar, layout mudou), nada e contado como falha e a
  playlist atual e preservada.

Uso:
    python scraper.py            # geracao normal
    VIDKS_DEBUG=1 python scraper.py   # log detalhado (mostra cada canal)
"""
import asyncio
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ------------------------------------------------------------------ config
BASE = "https://www.vidks.net"          # <- SEM markdown, sempre URL pura
SEARCH_URL = BASE + "/search"
STATE_FILE = Path("channels.json")
PLAYLIST_FILE = Path("playlist.m3u")

FALHAS_PARA_REMOVER = 6          # so remove depois de 6 atualizacoes falhando
VERTER_ESTADO_ANTIGO = True      # se o estado antigo nao tiver contador, considera 0

LOAD_MORE_TEXTS = [
    "Carregar mais", "Mais postagens", "Ver mais", "Mostrar mais",
    "Load more", "Mais canais",
]
CHANNEL_RE = re.compile(r"/vid/[^/?#\"']+\.ks")
M3U8_RE = re.compile(r"https?://[^\s\"'<>\\]+?\.m3u8[^\s\"'<>\\]*", re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MAX_LOAD_MORE_CLICKS = 300
CONCURRENCY = 4
PAGE_TIMEOUT = 45_000
DEBUG = os.environ.get("VIDKS_DEBUG") == "1"


# ------------------------------------------------------------------ util
def slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def limpar_url(u: str) -> str:
    """Remove restos de markdown/espacos de uma URL."""
    if not u:
        return ""
    u = u.strip()
    # [texto](url) -> url
    m = re.search(r"\]\((https?://[^)\s]+)\)", u)
    if m:
        return m.group(1)
    # [texto](www.site.com) -> https://www.site.com
    m = re.search(r"\]\(((?:www\.)?[^)\s]+)\)", u)
    if m:
        u = m.group(1)
    return u.replace("[", "").replace("]", "").strip()


def url_valida(u: str) -> bool:
    return bool(u) and bool(re.match(r"^https?://[^\s/]+\.[^\s/]+", u))


async def goto(page, url, **kw):
    """Navegacao tolerante: nunca deixa 'networkidle' derrubar o script."""
    url = limpar_url(url)
    if not url_valida(url):
        raise ValueError(f"URL invalida: {url!r}")
    wait = kw.pop("wait_until", "domcontentloaded")
    try:
        await page.goto(url, wait_until=wait, timeout=PAGE_TIMEOUT, **kw)
    except PWTimeout:
        print(f"  [~] timeout ao abrir {url} (seguindo assim mesmo)")
        return
    # espera curta pela hidratacao do JS, sem depender de networkidle
    try:
        await page.wait_for_load_state("load", timeout=15000)
    except PWTimeout:
        pass
    await page.wait_for_timeout(1200)


# ---------------------------------------------------------------- categorias
async def get_categories(page):
    await goto(page, SEARCH_URL)
    links = await page.eval_on_selector_all(
        "a[href*='category=']",
        "els => els.map(e => ({href: e.href, name: e.textContent.trim()}))",
    )
    cats = {}
    for l in links:
        m = re.search(r"category=(\d+)", l["href"] or "")
        if m and l["name"]:
            cats.setdefault(m.group(1), " ".join(l["name"].split()))
    print(f"[i] {len(cats)} categorias: {list(cats.values())}")
    return cats


async def click_load_more(page) -> bool:
    for txt in LOAD_MORE_TEXTS:
        btn = page.locator(
            f"button:has-text('{txt}'), a:has-text('{txt}'), "
            f"[role=button]:has-text('{txt}')"
        ).first
        try:
            if await btn.count() and await btn.is_visible() and await btn.is_enabled():
                await btn.scroll_into_view_if_needed()
                await btn.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


async def collect_links(page):
    hrefs = await page.eval_on_selector_all(
        "a[href*='/vid/']",
        """els => els.map(e => ({
            href: e.href,
            name: (e.querySelector('h3,h2,h4')?.textContent
                   || e.getAttribute('title') || e.textContent).trim(),
            logo: e.querySelector('img')?.src || ''
        }))""",
    )
    out = {}
    for h in hrefs:
        m = CHANNEL_RE.search(limpar_url(h["href"]))
        if not m:
            continue
        name = re.sub(r"^Assistir\s+|\s+ao vivo$", "", h["name"] or "", flags=re.I)
        name = " ".join(name.split("\n")[0].split())
        out[BASE + m.group(0)] = {"name": name or m.group(0), "logo": h["logo"] or ""}
    return out


async def crawl_category(page, cat_id, cat_name):
    await goto(page, f"{SEARCH_URL}?category={cat_id}")
    found = await collect_links(page)
    clicks = 0
    while clicks < MAX_LOAD_MORE_CLICKS:
        before = len(found)
        clicked = await click_load_more(page)
        if not clicked:
            await page.mouse.wheel(0, 20000)      # fallback: rolagem infinita
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeout:
            pass
        await page.wait_for_timeout(1500)
        found.update(await collect_links(page))
        clicks += 1
        if len(found) == before:
            if not clicked:
                break                             # sem botao e nada novo -> fim
            if not await click_load_more(page):    # clicou e veio vazio: tenta 1x
                break
    print(f"  [{cat_name}] {len(found)} canais ({clicks} carregamentos)")
    for v in found.values():
        v["group"] = cat_name
    return found


# ---------------------------------------------------------------- streams
async def extract_stream(context, url, sem):
    async with sem:
        page = await context.new_page()
        streams = []
        page.on("request", lambda r: streams.append(r.url)
                if ".m3u8" in r.url.lower() else None)
        logo = ""
        try:
            await page.goto(limpar_url(url), wait_until="domcontentloaded",
                            timeout=PAGE_TIMEOUT)
            await page.wait_for_timeout(6000)     # deixa o player iniciar
            for sel in ["button[aria-label*='Play' i]", ".vjs-big-play-button",
                        ".jw-icon-playback", "video"]:
                try:
                    await page.click(sel, timeout=1500)
                    await page.wait_for_timeout(3000)
                    break
                except Exception:
                    pass
            html = await page.content()
            for fr in page.frames:
                try:
                    html += await fr.content()
                except Exception:
                    pass
            streams += M3U8_RE.findall(html)
            logo = await page.evaluate(
                "document.querySelector('meta[property=\"og:image\"]')?.content || ''")
        except Exception as e:
            print(f"  [!] {url}: {str(e).splitlines()[0][:160]}")
        finally:
            await page.close()
        streams = [limpar_url(s).replace("\\/", "/") for s in streams]
        streams = [s for s in streams if url_valida(s)]
        streams.sort(key=lambda s: (("master" not in s and "index" not in s), len(s)))
        if DEBUG:
            print(f"  [dbg] {url} -> {len(streams)} m3u8")
        return (streams[0] if streams else None), logo


async def is_alive(session, url):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                               headers={"User-Agent": UA,
                                        "Referer": BASE + "/"}) as r:
            if r.status != 200:
                return False
            chunk = (await r.content.read(2048)).decode("utf-8", "ignore")
            return "#EXTM3U" in chunk
    except Exception:
        return False


# ---------------------------------------------------------------- M3U
def write_m3u(channels):
    lines = ['#EXTM3U x-tvg-url="" url-tvg=""',
             f"# Atualizado: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
             f"# Canais ativos: {len(channels)}"]
    for ch in sorted(channels.values(), key=lambda c: (c["group"], c["name"].lower())):
        name = ch["name"].replace(",", " ")
        lines.append(
            f'#EXTINF:-1 tvg-id="{slugify(name)}" tvg-name="{name}" '
            f'tvg-logo="{ch.get("logo", "")}" group-title="{ch["group"]}",{name}'
        )
        lines.append(ch["stream"])
    PLAYLIST_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- main
async def main():
    old = {}
    if STATE_FILE.exists():
        try:
            old = json.loads(STATE_FILE.read_text("utf-8"))
        except Exception:
            print("[!] channels.json ilegivel, comecando do zero")

    discovered = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=UA, locale="pt-BR")
        page = await context.new_page()

        try:
            cats = await get_categories(page)
        except Exception as e:
            print(f"[!] nao consegui ler as categorias: {e}")
            cats = {}

        for cid, cname in cats.items():
            try:
                for url, data in (await crawl_category(page, cid, cname)).items():
                    discovered.setdefault(url, data)      # 1a categoria vence
            except Exception as e:
                print(f"  [!] categoria {cname}: {str(e).splitlines()[0][:160]}")

        # canais em destaque da home (podem nao estar em categorias)
        try:
            await goto(page, BASE)
            for url, data in (await collect_links(page)).items():
                discovered.setdefault(url, {**data, "group": "Outros"})
        except Exception as e:
            print(f"  [!] home: {str(e).splitlines()[0][:160]}")
        await page.close()
        print(f"[i] Total descoberto: {len(discovered)}")

        results = []
        if discovered:
            sem = asyncio.Semaphore(CONCURRENCY)
            urls = list(discovered)
            results = await asyncio.gather(*(extract_stream(context, u, sem) for u in urls))
        else:
            urls = []
        await browser.close()

    # ---------------- anti-apagao: nada descoberto = nao mexe em nada
    if not discovered:
        print("[!] ZERO canais encontrados. Nenhuma falha foi contada e a "
              "playlist atual foi preservada (site bloqueado/fora do ar ou "
              "layout mudou).")
        if not PLAYLIST_FILE.exists():
            PLAYLIST_FILE.write_text(
                '#EXTM3U\n# Nenhum canal coletado nesta execucao\n', encoding="utf-8")
        return

    new_state = {}
    checks = []
    for url, (stream, logo) in zip(urls, results):
        d = discovered[url]
        stream = stream or (old.get(url, {}) or {}).get("stream")   # fallback ao antigo
        if stream:
            checks.append((url, d, stream, logo or d.get("logo", "")))

    async with aiohttp.ClientSession() as session:
        alive = await asyncio.gather(*(is_alive(session, c[2]) for c in checks))

    # ---------------- reconciliacao com contador de falhas
    falhas = {}
    for (url, d, stream, logo), ok in zip(checks, alive):
        prev = old.get(url) or {}
        n = int(prev.get("falhas", 0)) if VERTER_ESTADO_ANTIGO else 0
        if ok:
            new_state[url] = {"name": d["name"], "group": d["group"],
                              "logo": logo, "stream": stream,
                              "page": url, "falhas": 0}
        else:
            falhas[url] = n + 1
            print(f"  [x] sem sinal ({falhas[url]}/{FALHAS_PARA_REMOVER}): {d['name']}")

    # canais que sumiram do site tambem contam falha (nao sao apagados de imediato)
    for url, prev in old.items():
        if url in new_state or url in falhas:
            continue
        n = int(prev.get("falhas", 0)) + 1
        falhas[url] = n

    # mantem os que ainda nao atingiram o limite
    mantidos_por_tolerancia = 0
    for url, n in falhas.items():
        prev = old.get(url) or {}
        if n < FALHAS_PARA_REMOVER and prev.get("stream"):
            new_state[url] = {**prev, "falhas": n}
            mantidos_por_tolerancia += 1

    def _nomes(d):
        return {v.get("name") for v in d.values()}

    old_n, new_n = _nomes(old), _nomes(new_state)
    kept = len(old_n & new_n)
    added = len(new_n - old_n)
    removed = len(old_n - new_n)

    STATE_FILE.write_text(json.dumps(new_state, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    write_m3u(new_state)
    print(f"[OK] Mantidos: {kept} | Novos: {added} | Removidos: {removed} | "
          f"Em tolerancia: {mantidos_por_tolerancia} | Total: {len(new_state)}")
    print(f"[i] Arquivos: {PLAYLIST_FILE.resolve()} e {STATE_FILE.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
