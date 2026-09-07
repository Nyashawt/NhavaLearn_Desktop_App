@echo off
REM Builds the Windows desktop executable for NhavaLearn.
REM Run from the repo root with the project's virtualenv activated
REM (or after `pip install -r requirements.txt pyinstaller`).

setlocal
cd /d "%~dp0"

echo Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo Building NhavaLearn.exe with PyInstaller...
pyinstaller NhavaLearn.spec --noconfirm
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Build complete: dist\NhavaLearn\NhavaLearn.exe
endlocal
