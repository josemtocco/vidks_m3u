# VidKS M3U – Playlist otimizada para SS IPTV

Playlist automática de canais gratuitos do [vidks.net](https://www.vidks.net), organizada por categoria, com teste de stream e lógica de retenção inteligente.

## Características

- **Categorias nativas** do site (TV Aberta, Esportes, WebTV, Filmes, Séries, Desenhos, Notícias, TV Local, etc.)
- **Teste real de stream** antes de incluir no M3U (só entra se o m3u8 responder e contiver `#EXTM3U` / `#EXT-X-`)
- **Não apaga canais que estão funcionando**. Só remove um canal depois de **6 atualizações consecutivas** com falha (≈ 36 horas se o cron for de 6 em 6 h)
- Atualização automática a cada **6 horas** via GitHub Actions
- Formato limpo e otimizado para **SS IPTV** (group-title + tvg-logo + tvg-id)
- Logos oficiais do próprio VidKS

## Como usar a playlist

1. Depois de publicar o repositório no GitHub (e esperar a primeira execução ou rodar o workflow manualmente), a playlist estará em:

   ```
   https://raw.githubusercontent.com/SEU_USUARIO/vidks-m3u/main/data/playlist.m3u
   ```

2. No **SS IPTV**:
   - Adicionar lista → URL externa → cole o link acima
   - Em **Configurações → Aparência** escolha o tamanho dos botões (**Médios**) para categorias e canais

3. Outros players compatíveis: TiviMate, IPTV Smarters, VLC, Kodi (PVR IPTV Simple Client), etc.

## Estrutura do projeto

```
vidks-m3u/
├── .github/workflows/update.yml   # cron a cada 6 h
├── scripts/update_playlist.py     # scraper + tester + gerador
├── data/
│   ├── state.json                 # estado persistente (canais + fail_count)
│   └── playlist.m3u               # a lista final
├── requirements.txt
└── README.md
```

## Como instalar e rodar localmente (recomendado na primeira vez)

O site bloqueia muitos IPs de datacenter/GitHub Actions (HTTP 403). O ideal é rodar pelo menos a primeira vez em uma máquina com IP residencial brasileiro.

```bash
git clone https://github.com/SEU_USUARIO/vidks-m3u.git
cd vidks-m3u
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/update_playlist.py
```

O script vai:
1. Percorrer todas as categorias e páginas
2. Extrair o m3u8 de cada canal
3. Testar se está no ar
4. Atualizar `data/state.json` e gerar `data/playlist.m3u`

Depois faça commit e push:

```bash
git add data/
git commit -m "chore: initial playlist"
git push
```

## Configuração do GitHub Actions

1. Crie o repositório no GitHub e faça push de todos os arquivos.
2. Vá em **Actions** e habilite os workflows se necessário.
3. O cron `0 */6 * * *` roda a cada 6 horas (UTC).
4. Você também pode clicar em **Run workflow** para forçar uma atualização.

> **Nota sobre bloqueio 403**: se o Actions falhar com “403 Forbidden”, o IP dos runners do GitHub está bloqueado. Nesse caso:
> - Continue rodando o script localmente / em VPS residencial e faça push manual, **ou**
> - Use um self-hosted runner, **ou**
> - Adapte o script para usar proxy (variáveis de ambiente `HTTP_PROXY` / `HTTPS_PROXY`).

## Lógica de retenção (fail_count)

| Situação                              | Ação                                      |
|---------------------------------------|-------------------------------------------|
| Stream OK                             | `fail_count = 0` → entra no M3U           |
| Stream falhou (ou sumiu da listagem)  | `fail_count += 1`                         |
| `fail_count` chega a 6                | Canal é **removido** do state e do M3U   |
| Stream volta a funcionar antes de 6   | `fail_count` zera e o canal reaparece     |

Assim a lista não fica “piscando” com canais que caem por algumas horas.

## Personalização

No arquivo `scripts/update_playlist.py` você pode alterar:

- `MAX_FAIL_COUNT` (padrão 6)
- `CATEGORY_ORDER` (ordem das categorias no M3U)
- Delays (`PAGE_DELAY`, `CHANNEL_DELAY`) se o site começar a rate-limitar
- User-Agent

## Aviso legal

Esta playlist apenas agrega links públicos de streams já disponibilizados pelo próprio VidKS.net.  
Não hospedamos nenhum conteúdo. Use por sua conta e risco e respeite os termos de uso do site original e da legislação local sobre direitos autorais.

## Licença

Código-fonte: MIT.  
Os streams pertencem aos seus respectivos donos/emissores.
