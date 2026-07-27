@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist "venv\Scripts\python.exe" set "PYTHON_EXE=venv\Scripts\python.exe"

echo Installing canonical build dependencies...
"%PYTHON_EXE%" -m pip install --editable ".[dev]"
if errorlevel 1 exit /b %errorlevel%

echo Building dist\WolfRAT2.exe from WolfRAT2.spec...
"%PYTHON_EXE%" -m PyInstaller --clean --noconfirm WolfRAT2.spec
if errorlevel 1 exit /b %errorlevel%

if not exist "dist\WolfRAT2.exe" (
    echo Build completed without producing dist\WolfRAT2.exe.
    exit /b 1
)

echo Build complete: dist\WolfRAT2.exe
exit /b 0
