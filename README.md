# VidKS → M3U automático

Gerador de playlist M3U a partir de `https://www.vidks.net/`.

## O que o projeto faz

- percorre as páginas de descoberta do VidKS;
- identifica os canais e suas categorias;
- abre a página individual de cada canal;
- procura o stream real (`m3u8`, `mpd` ou `m3u`), inclusive dentro de players incorporados;
- testa individualmente o stream;
- inclui somente streams aprovados no `vidks.m3u`;
- grava `canais.json` com os canais aprovados;
- grava `descoberto.json` com estatísticas e erros;
- atualiza automaticamente a cada 6 horas pelo GitHub Actions;
- todos os arquivos gerados ficam no diretório principal do repositório.

## Arquivos gerados

- `vidks.m3u` — playlist para o SS IPTV.
- `canais.json` — canais aprovados na última execução.
- `descoberto.json` — diagnóstico da descoberta/testes.

## GitHub Pages / Raw

Depois de publicar no GitHub, o arquivo poderá ser acessado pelo endereço Raw do repositório, por exemplo:

`https://raw.githubusercontent.com/SEU_USUARIO/SEU_REPOSITORIO/main/vidks.m3u`

Substitua `SEU_USUARIO/SEU_REPOSITORIO` pelos dados do seu repositório.
