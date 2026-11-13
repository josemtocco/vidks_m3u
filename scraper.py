#!/usr/bin/env python3
"""
Coletor VidKS -> playlist M3U otimizada para SS IPTV.
- Percorre todas as categorias e clica em "Carregar mais" até acabar.
- Abre cada canal e captura o stream .m3u8.
- Valida os streams: mantém ativos, remove inativos, adiciona novos.
"""
import asyncio
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

BASE = "[vidks.net](https://www.vidks.net)"
SEARCH_URL = f"{BASE}/search"
STATE_FILE = Path("channels.json")
PLAYLIST_FILE = Path("playlist.m3u")

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


def slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


# ---------------------------------------------------------------- categorias
async def get_categories(page):
    await page.goto(SEARCH_URL, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    links = await page.eval_on_selector_all(
        "a[href*='category=']",
        "els => els.map(e => ({href: e.href, name: e.textContent.trim()}))",
    )
    cats = {}
    for l in links:
        m = re.search(r"category=(\d+)", l["href"])
        if m and l["name"]:
            cats.setdefault(m.group(1), l["name"])
    print(f"[i] {len(cats)} categorias: {list(cats.values())}")
    return cats


async def click_load_more(page) -> bool:
    """Tenta clicar no botão de carregar mais. Retorna False se não existir."""
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
        m = CHANNEL_RE.search(h["href"])
        if m:
            name = re.sub(r"^Assistir\s+|\s+ao vivo$", "", h["name"], flags=re.I)
            out[BASE + m.group(0)] = {"name": name.split("\n")[0].strip(),
                                      "logo": h["logo"]}
    return out


async def crawl_category(page, cat_id, cat_name):
    url = f"{SEARCH_URL}?category={cat_id}"
    await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    found = await collect_links(page)
    clicks = 0
    while clicks < MAX_LOAD_MORE_CLICKS:
        before = len(found)
        clicked = await click_load_more(page)
        if not clicked:
            # fallback: rolagem infinita
            await page.mouse.wheel(0, 20000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeout:
            pass
        await page.wait_for_timeout(1500)
        found.update(await collect_links(page))
        clicks += 1
        if len(found) == before and not clicked:
            break                       # nada novo e sem botão -> fim
        if len(found) == before and clicked:
            # clicou mas não veio nada: tenta mais uma vez e encerra
            if not await click_load_more(page):
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
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await page.wait_for_timeout(6000)   # deixa o player iniciar
            # tenta dar play caso precise de interação
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
            print(f"  [!] {url}: {e}")
            logo = ""
        finally:
            await page.close()
        streams = [s.replace("\\/", "/") for s in streams]
        # prioriza playlist master
        streams.sort(key=lambda s: ("master" not in s and "index" not in s, len(s)))
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
    lines = [f'#EXTM3U x-tvg-url="" url-tvg=""',
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
    old = json.loads(STATE_FILE.read_text("utf-8")) if STATE_FILE.exists() else {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=UA, locale="pt-BR")
        page = await context.new_page()

        cats = await get_categories(page)
        discovered = {}
        for cid, cname in cats.items():
            try:
                for url, data in (await crawl_category(page, cid, cname)).items():
                    discovered.setdefault(url, data)   # 1ª categoria vence
            except Exception as e:
                print(f"  [!] categoria {cname}: {e}")
        # canais em destaque da home (podem não estar em categorias)
        await page.goto(BASE, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        for url, data in (await collect_links(page)).items():
            discovered.setdefault(url, {**data, "group": "Outros"})
        await page.close()
        print(f"[i] Total descoberto: {len(discovered)}")

        sem = asyncio.Semaphore(CONCURRENCY)
        urls = list(discovered)
        results = await asyncio.gather(*(extract_stream(context, u, sem) for u in urls))
        await browser.close()

    new_state = {}
    async with aiohttp.ClientSession() as session:
        checks = []
        for url, (stream, logo) in zip(urls, results):
            d = discovered[url]
            stream = stream or old.get(url, {}).get("stream")  # fallback ao antigo
            if stream:
                checks.append((url, d, stream, logo or d.get("logo", "")))
        alive = await asyncio.gather(*(is_alive(session, c[2]) for c in checks))

    added = removed = kept = 0
    for (url, d, stream, logo), ok in zip(checks, alive):
        if ok:
            new_state[url] = {"name": d["name"], "group": d["group"],
                              "logo": logo, "stream": stream, "page": url}
            if url in old: kept += 1
            else: added += 1
    removed = len(set(old) - set(new_state))

    STATE_FILE.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), "utf-8")
    write_m3u(new_state)
    print(f"[✓] Mantidos: {kept} | Novos: {added} | Removidos: {removed} | "
          f"Total: {len(new_state)}")


if __name__ == "__main__":
    asyncio.run(main())
