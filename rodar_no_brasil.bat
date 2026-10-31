@echo off
REM ============================================================
REM  Rodar o gerador localmente a partir do Brasil (sem VPN).
REM  Isso garante o catalogo completo PT-BR e streams que o
REM  GitHub Actions (IP dos EUA) pode nao enxergar por geo-block.
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

REM Logs detalhados e regra de 6 falhas (igual aos outros projetos)
set VIDKS_DEBUG=1
set VIDKS_MAX_FALHAS=6
REM Opcional: incluir canais com DRM (nao tocam em SS IPTV/VLC)
REM set VIDKS_INCLUDE_DRM=1
REM Opcional: verificar se o .m3u8 responde (mais lento)
REM set VIDKS_VERIFICAR=1
REM Opcional: API de stream descoberta via F12
REM set VIDKS_SOURCE_API=https://www.vidks.net/api/play/{id}

python gerar_vidks.py
if errorlevel 1 (
  echo.
  echo [ERRO] Falha ao executar. Verifique se o Python esta instalado
  echo        (python.org, marcando "Add python.exe to PATH").
)

echo.
echo Arquivos gerados: playlist.m3u e estado_canais.json
pause
