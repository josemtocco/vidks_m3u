# VidKS TV → M3U para SS IPTV

Gerador automático de uma lista **M3U** otimizada para o **SS IPTV**, a partir do
site [VidKS TV](https://www.vidks.net/). O script abre **cada categoria** do
site, percorre **página por página** e coleta **todos os canais** daquela
categoria, passando para a próxima categoria em seguida — exatamente como você
pediu. No fim monta um `playlist.m3u` **agrupado por categoria**, com logo de
cada canal.

- ✅ Somente **Python padrão** (sem `pip install`).
- ✅ **Atualiza de 6 em 6 horas** via GitHub Actions.
- ✅ **Não apaga** canais que estão funcionando. Só remove um canal depois de
  **6 atualizações seguidas** sem funcionar (regra de reconciliação).
- ✅ Canais **separados por categoria** (as mesmas categorias do site).
- ✅ Logo de cada canal (`tvg-logo`) e grupo (`group-title`) para o grid ficar
  bonito no SS IPTV.

---

## Arquivos do projeto

| Arquivo | Para que serve |
|---|---|
| `gerar_vidks.py` | O gerador. Coleta canais e monta o `playlist.m3u`. |
| `.github/workflows/update.yml` | Agenda a atualização automática a cada 6h. |
| `rodar_no_brasil.bat` | Roda localmente no Windows, a partir do Brasil. |
| `playlist.m3u` | **Saída** — a lista que você usa no SS IPTV (gerada ao rodar). |
| `estado_canais.json` | **Estado** — memória da reconciliação (contador de falhas). |

> Todos os arquivos ficam na **raiz do repositório**. Só o workflow do GitHub
> Actions fica em `.github/workflows/`.

---

## Sobre os "botões médios" no SS IPTV

O **tamanho dos botões (pequeno / médio / grande)** das categorias e dos canais
**não é definido pela lista M3U** — é uma configuração do **próprio aplicativo**
SS IPTV. O que a lista faz é fornecer `group-title` (categoria) e `tvg-logo`
(logo) para o app montar o grid com imagens.

Para deixar os botões no tamanho **médio** no SS IPTV:

1. Abra o **SS IPTV** → **Configurações** (ícone de engrenagem).
2. Procure por **Aparência / Grade / Tamanho das miniaturas** (o nome varia
   conforme a versão e o aparelho).
3. Selecione o tamanho **Médio**.
4. Volte e abra a sua playlist — categorias e canais aparecerão com botões
   médios e com os logos.

> Depois de atualizar a lista, **remova e adicione a playlist de novo** no SS
> IPTV para limpar o cache do app e puxar os canais novos.

---

## Como colocar no GitHub (passo a passo)

1. Crie um repositório no GitHub (pode ser **privado**).
2. Envie **todos os arquivos** desta pasta para a raiz do repositório.
3. No GitHub, vá em **Settings → Actions → General → Workflow permissions** e
   marque **Read and write permissions** (para o robô poder salvar o
   `playlist.m3u` atualizado).
4. Vá na aba **Actions**, abra *"Atualizar playlist VidKS (SS IPTV)"* e clique
   em **Run workflow** para a primeira execução. Depois ele roda sozinho a cada
   6 horas.
5. Pegue o link **raw** do `playlist.m3u`:
   `https://raw.githubusercontent.com/SEU_USUARIO/SEU_REPO/main/playlist.m3u`
6. No **SS IPTV**: **Configurações → Conteúdo → Listas externas**, cole esse
   link e salve.

---

## Importante: rode a primeira vez a partir do Brasil

O VidKS entrega o catálogo e os streams de forma completa para **IPs do
Brasil**. O GitHub Actions roda em servidores **nos EUA**, então alguns canais
podem não ser vistos de lá (bloqueio por região).

Por isso o projeto já traz duas proteções:

- **Anti-apagão:** se o site não devolver **nenhum** canal (bloqueio de região
  ou site fora do ar), o script **não conta como falha** e **não apaga nada** —
  ele preserva o `estado_canais.json` e apenas regenera o `playlist.m3u` com o
  que já existia. Assim o robô dos EUA nunca zera a sua lista.
- **Execução local:** use o `rodar_no_brasil.bat` (duplo clique) a partir do seu
  PC no Brasil para gerar o catálogo completo. Depois faça o commit do
  `playlist.m3u` e do `estado_canais.json`. O GitHub Actions passa a só manter
  os links atualizados.

---

## Como rodar localmente (Windows)

1. Instale o Python do site **python.org** marcando **"Add python.exe to
   PATH"** (o `python` da Microsoft Store costuma dar problema).
2. Dê **duplo clique** em `rodar_no_brasil.bat`.
3. Ao terminar, você terá o `playlist.m3u` e o `estado_canais.json` na pasta.

Ou pelo terminal:

```bash
python gerar_vidks.py
```

---

## Opções (variáveis de ambiente)

| Variável | Padrão | O que faz |
|---|---|---|
| `VIDKS_MAX_FALHAS` | `6` | Nº de atualizações seguidas sem funcionar antes de remover o canal. |
| `VIDKS_DEBUG` | `0` | `1` mostra log detalhado (página por página, canal por canal). |
| `VIDKS_INCLUDE_DRM` | `0` | `1` inclui canais com DRM (que **não** tocam em SS IPTV/VLC). |
| `VIDKS_VERIFICAR` | `0` | `1` testa se cada `.m3u8` responde (mais lento; evite nos EUA). |
| `VIDKS_SOURCE_API` | vazio | URL da API de stream, se você descobrir pelo F12 (aceita `{id}` e `{slug}`). |
| `VIDKS_PAUSA` | `0.35` | Pausa (s) entre requisições, para não sobrecarregar o site. |

---

## E se um canal não trouxer o stream?

O VidKS carrega o player por JavaScript. O script tenta pegar o stream de três
formas: (1) link `.m3u8`/`.mpd` embutido na página, (2) chaves típicas de player
(`file`, `source`, `src`…), e (3) seguindo o `iframe` do player.

Se o site passar a servir o stream por uma **API interna** (que só responde com
a sessão do navegador), abra um canal no Chrome/Edge, pressione **F12 →
Network**, filtre por `m3u8` ou `.ks`, veja qual endereço o player chama e
informe esse padrão em `VIDKS_SOURCE_API` (ex.: `https://www.vidks.net/api/play/{id}`).
O script passa a usar essa API automaticamente.

---

## Aviso

O VidKS TV é um **agregador** que aponta para players de terceiros. Use
somente conteúdo que você tenha direito de assistir. Este projeto apenas
organiza, em formato M3U, os links que o próprio site disponibiliza
publicamente.
