@echo off
REM ===========================================================================
REM WIS - Setup Script para Windows
REM ===========================================================================
REM Uso: Haz doble clic o ejecuta: setup.bat
REM ===========================================================================

echo ============================================
echo  WIS - Cognitive Agent Setup
echo  HackaBOT RENATA 2026
echo ============================================
echo.

REM Verificar Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python no esta instalado.
    echo Descargalo desde: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python detectado

REM Verificar Keys.env
if not exist Keys.env (
    if exist Keys.env.template (
        echo [AVISO] No se encontro Keys.env.
        echo Copiando desde Keys.env.template...
        copy Keys.env.template Keys.env >nul
        echo [IMPORTANTE] Edita Keys.env y pon tu API key antes de ejecutar WIS.
    ) else (
        echo [ERROR] No se encuentra ni Keys.env ni Keys.env.template
        pause
        exit /b 1
    )
) else (
    echo [OK] Keys.env encontrado
)

REM Crear entorno virtual
if not exist venv (
    echo Creando entorno virtual...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
    echo [OK] Entorno virtual creado
) else (
    echo [OK] Entorno virtual existe
)

REM Activar e instalar dependencias
echo Instalando dependencias...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [ERROR] Fallo al instalar dependencias.
    pause
    exit /b 1
)
echo [OK] Dependencias instaladas

REM Instalar binario de Chromium para Playwright (si falta)
echo Verificando navegador Chromium de Playwright...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo [AVISO] No se pudo instalar Chromium de Playwright.
    echo         Puedes hacerlo manualmente con: python -m playwright install chromium
) else (
    echo [OK] Chromium de Playwright listo
)

echo.
echo ============================================
echo  Instalacion completada exitosamente!
echo ============================================
echo.
echo Para iniciar WIS:
echo   1. Asegurate de tener tu API key en Keys.env
echo   2. Ejecuta: python main.py --server
echo   3. Abre http://localhost:7777 en tu navegador
echo.
pause
