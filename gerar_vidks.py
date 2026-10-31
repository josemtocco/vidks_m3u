#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerador de lista M3U para SS IPTV a partir do VidKS TV (https://www.vidks.net/)

O que faz:
  - Percorre TODAS as categorias do site (23), abrindo categoria por categoria,
    pagina por pagina, e coletando TODOS os canais de cada categoria.
  - Extrai o stream (HLS .m3u8 / DASH .mpd) da pagina de cada canal.
  - Gera playlist.m3u agrupada por categoria (group-title) com logo (tvg-logo).
  - Reconciliacao: mantem os canais que estao funcionando; so remove um canal
    apos 6 atualizacoes consecutivas sem funcionar; adiciona os novos que
    aparecerem. O estado fica em estado_canais.json.
  - Protecao contra apagao do site inteiro (geo-block): se nenhuma categoria
    retornar canais, NAO conta como falha e NAO apaga nada; preserva o estado.
  - Somente biblioteca padrao do Python (sem pip install).

Autor: projeto gerado nos moldes dos demais geradores de M3U.
"""

import json
import os
import re
import sys
import time
import html as htmllib
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

# ----------------------------------------------------------------------------
# Configuracao (tudo ajustavel por variavel de ambiente)
# ----------------------------------------------------------------------------
BASE = "https://www.vidks.net"
IMG_BASE = "https://img.vidks.net"

OUT_M3U = os.environ.get("VIDKS_OUT", "playlist.m3u")
ESTADO = os.environ.get("VIDKS_ESTADO", "estado_canais.json")

# Numero de atualizacoes consecutivas sem funcionar antes de remover o canal.
MAX_FALHAS = int(os.environ.get("VIDKS_MAX_FALHAS", "6"))

# Logs detalhados (VIDKS_DEBUG=1)
DEBUG = os.environ.get("VIDKS_DEBUG", "") not in ("", "0", "false", "False")

# Incluir canais com DRM (que normalmente NAO tocam em SS IPTV/VLC).
INCLUDE_DRM = os.environ.get("VIDKS_INCLUDE_DRM", "") not in ("", "0", "false", "False")

# Verificar se o .m3u8 realmente responde (pode dar falso-negativo fora do
# Brasil por geo-block; por isso vem DESLIGADO por padrao).
VERIFICAR = os.environ.get("VIDKS_VERIFICAR", "") not in ("", "0", "false", "False")

# Endpoint opcional de stream, caso voce descubra a API pelo F12 do navegador.
# Aceita {id} e/ou {slug}. Exemplos:
#   VIDKS_SOURCE_API="https://www.vidks.net/api/play/{id}"
#   VIDKS_SOURCE_API="https://www.vidks.net/api/source/{slug}"
SOURCE_API = os.environ.get("VIDKS_SOURCE_API", "").strip()

MAX_PAGINAS = int(os.environ.get("VIDKS_MAX_PAGINAS", "50"))
PAUSA = float(os.environ.get("VIDKS_PAUSA", "0.35"))
TIMEOUT = int(os.environ.get("VIDKS_TIMEOUT", "25"))

# Categorias do VidKS (id -> nome), na ordem em que aparecem no site.
CATEGORIAS = {
    1: "Esportes", 2: "Documentarios", 3: "TV Aberta", 4: "Religiosos",
    5: "WebTV", 6: "Filmes", 7: "Series", 8: "Infantil", 9: "Noticias",
    10: "Variedades", 11: "Educacao", 12: "Agropecuaria", 13: "Desenhos",
    14: "Universitaria", 15: "Cultura", 16: "TV Local", 17: "24 horas",
    18: "Culinaria", 19: "Comercio", 20: "Natureza", 21: "Fast",
    22: "Financas", 23: "Turismo",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
}

# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def log(msg):
    print(msg, flush=True)


def dbg(msg):
    if DEBUG:
        print("   [debug] " + msg, flush=True)


def agora_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def http_get(url, timeout=TIMEOUT, as_bytes=False, extra_headers=None):
    """GET simples. Retorna (texto_ou_bytes_ou_None, status, url_final)."""
    hdrs = dict(HEADERS)
    if extra_headers:
        hdrs.update(extra_headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            if as_bytes:
                return data, getattr(r, "status", 200), r.geturl()
            enc = r.headers.get_content_charset() or "utf-8"
            return data.decode(enc, "replace"), getattr(r, "status", 200), r.geturl()
    except urllib.error.HTTPError as e:
        dbg("HTTP %s em %s" % (e.code, url))
        return None, e.code, url
    except Exception as e:
        dbg("erro GET %s: %s" % (url, e))
        return None, 0, url


def unescape_urls(texto):
    """Normaliza URLs escapadas em JSON/HTML (\\/ -> / e entidades)."""
    if not texto:
        return ""
    t = texto.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
    return htmllib.unescape(t)


# ----------------------------------------------------------------------------
# 1) Descoberta de canais por categoria
# ----------------------------------------------------------------------------
# Cada card de canal e um <a href="/vid/<slug>.ks" title="Assistir <nome> ao vivo">
A_TAG_RE = re.compile(r'<a\b[^>]*href="/vid/([^"/?#]+)\.ks"[^>]*>', re.I)
TITLE_RE = re.compile(r'title="Assistir\s+(.*?)\s+ao vivo"', re.I | re.S)
PAG_RE = re.compile(r'P\xe1gina\s+\d+\s+de\s+(\d+)', re.I)


def logo_do_slug(slug):
    return "%s/v3x/l/%s.webp?q=70" % (IMG_BASE, slug)


def parse_cards(html):
    """Extrai [(slug, nome)] de uma pagina de categoria/busca."""
    achados = []
    for m in A_TAG_RE.finditer(html):
        slug = m.group(1).strip()
        tag = m.group(0)
        tm = TITLE_RE.search(tag)
        nome = htmllib.unescape(tm.group(1).strip()) if tm else slug
        achados.append((slug, nome))
    return achados


def coletar_categoria(cat_id, cat_nome):
    """Percorre todas as paginas de uma categoria e devolve dict slug->nome."""
    encontrados = {}
    total_paginas = None
    pagina = 1
    while pagina <= MAX_PAGINAS:
        url = "%s/search?category=%d&page=%d" % (BASE, cat_id, pagina)
        html, status, _ = http_get(url)
        if html is None:
            dbg("categoria %s pagina %d: sem resposta (status %s)" % (cat_nome, pagina, status))
            break
        if total_paginas is None:
            pm = PAG_RE.search(html)
            if pm:
                total_paginas = int(pm.group(1))
        cards = parse_cards(html)
        novos = 0
        for slug, nome in cards:
            if slug not in encontrados:
                encontrados[slug] = nome
                novos += 1
        dbg("categoria %s pagina %d: %d cards (%d novos)" % (cat_nome, pagina, len(cards), novos))
        # Para quando a pagina nao traz nada novo (fim real da lista).
        if novos == 0 and pagina > 1:
            break
        if total_paginas is not None and pagina >= total_paginas:
            break
        if not cards:
            break
        pagina += 1
        time.sleep(PAUSA)
    return encontrados


# ----------------------------------------------------------------------------
# 2) Extracao do stream da pagina de cada canal
# ----------------------------------------------------------------------------
M3U8_RE = re.compile(r'https?://[^\s"\'<>()\\]+?\.m3u8[^\s"\'<>()\\]*', re.I)
MPD_RE = re.compile(r'https?://[^\s"\'<>()\\]+?\.mpd[^\s"\'<>()\\]*', re.I)
IFRAME_RE = re.compile(r'<iframe\b[^>]*\bsrc="([^"]+)"', re.I)
KEY_RE = re.compile(
    r'"(?:file|source|src|url|stream|hls|playback|manifest|playlist)"\s*:\s*"([^"]+)"',
    re.I)
ID_RE = re.compile(
    r'(?:"(?:id|channelId|channel_id|canalId)"\s*:\s*"?(\d{1,8})"?'
    r'|data-(?:id|channel-id)="(\d{1,8})")', re.I)
DRM_HINT_RE = re.compile(r'widevine|playready|fairplay|clearkey|license|drm', re.I)


def achar_id(html):
    m = ID_RE.search(html)
    if m:
        return m.group(1) or m.group(2)
    return None


def procurar_em_texto(texto):
    """Procura um .m3u8 (preferido) ou .mpd em um texto ja desescapado.
    Retorna (url, drm_bool) ou (None, False)."""
    if not texto:
        return None, False
    t = unescape_urls(texto)
    m = M3U8_RE.search(t)
    if m:
        return m.group(0), False
    # Chaves tipicas de player apontando para o stream
    for km in KEY_RE.finditer(t):
        val = unescape_urls(km.group(1))
        if ".m3u8" in val.lower():
            mm = M3U8_RE.search(val)
            return (mm.group(0) if mm else val), False
    m = MPD_RE.search(t)
    if m:
        return m.group(0), True
    return None, False


def resolver_iframe(src, depth=1):
    """Segue um iframe ate 'depth' niveis procurando um stream."""
    if depth < 0 or not src:
        return None, False
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = BASE + src
    if not src.startswith("http"):
        return None, False
    html, status, _ = http_get(src, extra_headers={"Referer": BASE + "/"})
    if html is None:
        return None, False
    url, drm = procurar_em_texto(html)
    if url:
        return url, drm
    for im in IFRAME_RE.finditer(html):
        u, d = resolver_iframe(im.group(1), depth - 1)
        if u:
            return u, d
    return None, False


def extrair_stream(slug):
    """Abre a pagina do canal e tenta obter o stream.
    Retorna dict {stream, drm, id} ou None."""
    url = "%s/vid/%s.ks" % (BASE, slug)
    html, status, _ = http_get(url)
    if html is None:
        dbg("canal %s: pagina inacessivel (status %s)" % (slug, status))
        return None
    cid = achar_id(html)

    # (a) Endpoint de API configurado manualmente (descoberto via F12)
    if SOURCE_API:
        api = SOURCE_API
        if "{id}" in api:
            if not cid:
                dbg("canal %s: SOURCE_API precisa de {id} e nao achei id" % slug)
            else:
                api = api.replace("{id}", cid)
        api = api.replace("{slug}", slug)
        if "{" not in api:
            txt, st, _ = http_get(api, extra_headers={
                "Referer": url, "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/plain, */*"})
            u, drm = procurar_em_texto(txt)
            if u:
                return {"stream": u, "drm": drm, "id": cid}

    # (b) Stream embutido na propria pagina
    u, drm = procurar_em_texto(html)
    if u:
        return {"stream": u, "drm": drm, "id": cid}

    # (c) Segue iframes do player
    for im in IFRAME_RE.finditer(html):
        u, drm = resolver_iframe(im.group(1), depth=2)
        if u:
            return {"stream": u, "drm": drm, "id": cid}

    return None


def stream_responde(url):
    """Verificacao leve: o .m3u8 responde com conteudo de playlist?"""
    txt, status, _ = http_get(url, timeout=12, extra_headers={"Referer": BASE + "/"})
    if txt is None:
        return False
    return ("#EXTM3U" in txt) or ("#EXT-X" in txt) or status == 200


# ----------------------------------------------------------------------------
# 3) Estado (reconciliacao) e geracao do M3U
# ----------------------------------------------------------------------------
def carregar_estado():
    if os.path.exists(ESTADO):
        try:
            with open(ESTADO, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("canais", {}) if isinstance(d, dict) else {}
        except Exception as e:
            dbg("nao consegui ler %s: %s" % (ESTADO, e))
    return {}


def salvar_estado(canais):
    d = {"atualizado_em": agora_iso(), "max_falhas": MAX_FALHAS,
         "total": len(canais), "canais": canais}
    tmp = ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ESTADO)


def m3u_escape(txt):
    return (txt or "").replace("\n", " ").replace("\r", " ").strip()


def gerar_m3u(canais):
    """Monta o playlist.m3u agrupado por categoria, so com canais tocaveis."""
    # Ordem das categorias conforme o site; canais de A a Z dentro de cada uma.
    ordem_cat = list(CATEGORIAS.values()) + ["Outros"]
    buckets = {}
    for slug, c in canais.items():
        if not c.get("stream"):
            continue
        if c.get("drm") and not INCLUDE_DRM:
            continue
        cat = c.get("categoria") or "Outros"
        buckets.setdefault(cat, []).append((slug, c))

    linhas = ["#EXTM3U"]
    linhas.append("# VidKS TV -> SS IPTV | gerado em %s" % agora_iso())
    linhas.append("# Canais tocaveis: veja o README para o tamanho dos botoes (medios) no app.")
    total = 0
    for cat in ordem_cat:
        itens = buckets.get(cat)
        if not itens:
            continue
        itens.sort(key=lambda t: (t[1].get("nome") or t[0]).lower())
        for slug, c in itens:
            nome = m3u_escape(c.get("nome") or slug)
            logo = c.get("logo") or logo_do_slug(slug)
            drmtag = " [DRM]" if c.get("drm") else ""
            linhas.append(
                '#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-logo="%s" group-title="%s",%s%s'
                % (slug, nome, logo, cat, nome, drmtag))
            linhas.append(c["stream"])
            total += 1
    conteudo = "\n".join(linhas) + "\n"
    tmp = OUT_M3U + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(conteudo)
    os.replace(tmp, OUT_M3U)
    return total


# ----------------------------------------------------------------------------
# 4) Fluxo principal
# ----------------------------------------------------------------------------
def main():
    log("=" * 70)
    log("VidKS TV -> M3U para SS IPTV")
    log("Inicio: %s" % agora_iso())
    log("Regra de remocao: apos %d atualizacoes consecutivas sem funcionar" % MAX_FALHAS)
    log("Incluir DRM: %s | Verificar stream: %s | Debug: %s"
        % (INCLUDE_DRM, VERIFICAR, DEBUG))
    if SOURCE_API:
        log("API de stream configurada: %s" % SOURCE_API)
    log("=" * 70)

    estado = carregar_estado()
    log("Estado anterior: %d canais conhecidos" % len(estado))

    # ---- Descoberta: abre cada categoria e coleta os canais ----
    descobertos = {}   # slug -> {nome, categoria}
    for cat_id in sorted(CATEGORIAS):
        cat_nome = CATEGORIAS[cat_id]
        log("\n>> Categoria %02d - %s" % (cat_id, cat_nome))
        achados = coletar_categoria(cat_id, cat_nome)
        log("   %d canais encontrados nesta categoria" % len(achados))
        for slug, nome in achados.items():
            if slug not in descobertos:   # mantem a 1a categoria onde aparece
                descobertos[slug] = {"nome": nome, "categoria": cat_nome}
        time.sleep(PAUSA)

    total_descobertos = len(descobertos)
    log("\nTotal de canais distintos descobertos: %d" % total_descobertos)

    # ---- Protecao contra apagao do site inteiro (geo-block etc.) ----
    if total_descobertos == 0:
        log("\n[AVISO] Nenhum canal foi encontrado em NENHUMA categoria.")
        log("Isso costuma ser bloqueio por regiao (ex.: rodando fora do Brasil)")
        log("ou o site fora do ar. NAO vou contar como falha nem apagar nada.")
        log("Mantendo o estado e regenerando o playlist a partir do que ja existe.")
        total_m3u = gerar_m3u(estado)
        log("playlist.m3u regenerado com %d canais do estado anterior." % total_m3u)
        log("Rode a partir de um IP do Brasil para coletar o catalogo completo.")
        return 0

    # ---- Extracao de stream + reconciliacao ----
    vistos = set()
    com_stream = com_drm = sem_stream = 0
    for i, (slug, info) in enumerate(sorted(descobertos.items()), 1):
        vistos.add(slug)
        nome = info["nome"]
        cat = info["categoria"]
        res = extrair_stream(slug)
        ent = estado.get(slug, {})
        ent.update({"nome": nome, "categoria": cat, "logo": logo_do_slug(slug),
                    "url_pagina": "%s/vid/%s.ks" % (BASE, slug)})
        ok = False
        if res and res.get("stream"):
            drm = bool(res.get("drm"))
            stream = res["stream"]
            if VERIFICAR and not drm:
                if not stream_responde(stream):
                    dbg("canal %s: stream nao respondeu na verificacao" % slug)
                    stream = None
            if stream:
                ok = True
                ent["stream"] = stream
                ent["drm"] = drm
                ent["id"] = res.get("id") or ent.get("id")
                ent["falhas"] = 0
                ent["ultimo_ok"] = agora_iso()
                if drm:
                    com_drm += 1
                    log("   [%4d/%d] %-34s OK (DRM) -> %s" % (i, total_descobertos, nome[:34], cat))
                else:
                    com_stream += 1
                    log("   [%4d/%d] %-34s OK -> %s" % (i, total_descobertos, nome[:34], cat))
        if not ok:
            sem_stream += 1
            ent["falhas"] = int(ent.get("falhas", 0)) + 1
            # mantem o stream anterior (se existia) ate estourar o limite
            log("   [%4d/%d] %-34s sem stream (falha %d/%d)"
                % (i, total_descobertos, nome[:34], ent["falhas"], MAX_FALHAS))
        estado[slug] = ent
        time.sleep(PAUSA)

    # Canais que estavam no estado e NAO apareceram nesta rodada: +1 falha
    sumidos = 0
    for slug, ent in list(estado.items()):
        if slug not in vistos:
            ent["falhas"] = int(ent.get("falhas", 0)) + 1
            estado[slug] = ent
            sumidos += 1

    # Remocao: so apaga quem passou do limite de falhas consecutivas
    removidos = []
    for slug in list(estado.keys()):
        if int(estado[slug].get("falhas", 0)) >= MAX_FALHAS:
            removidos.append(estado[slug].get("nome", slug))
            del estado[slug]

    salvar_estado(estado)
    total_m3u = gerar_m3u(estado)

    # ---- Resumo ----
    log("\n" + "=" * 70)
    log("RESUMO")
    log("  Categorias percorridas .......: %d" % len(CATEGORIAS))
    log("  Canais descobertos ...........: %d" % total_descobertos)
    log("  Com stream (tocavel) .........: %d" % com_stream)
    log("  Com DRM (nao toca em SS IPTV) : %d" % com_drm)
    log("  Sem stream nesta rodada ......: %d" % sem_stream)
    log("  No estado e nao vistos .......: %d" % sumidos)
    log("  Removidos (>= %d falhas) ......: %d" % (MAX_FALHAS, len(removidos)))
    if removidos:
        log("    -> " + ", ".join(removidos[:30]) + (" ..." if len(removidos) > 30 else ""))
    log("  Canais no playlist.m3u .......: %d" % total_m3u)
    log("  Arquivos: %s , %s" % (OUT_M3U, ESTADO))
    log("  Fim: %s" % agora_iso())
    log("=" * 70)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("\nInterrompido pelo usuario.")
        sys.exit(130)

