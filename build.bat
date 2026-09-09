@echo off
REM Builds the Windows desktop app and installer for NhavaLearn.
REM Run from the repo root with the project's virtualenv activated
REM (or after `pip install -r requirements.txt pyinstaller`).
REM
REM Requires Inno Setup 6 for the installer step (https://jrsoftware.org/isinfo.php).
REM Skips that step gracefully if ISCC.exe isn't found, leaving just the
REM unpackaged dist\NhavaLearn\NhavaLearn.exe from the PyInstaller step.

setlocal
cd /d "%~dp0"

echo Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist installer rmdir /s /q installer

echo Building NhavaLearn.exe with PyInstaller...
pyinstaller NhavaLearn.spec --noconfirm
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if exist "%ISCC%" (
    echo Building installer with Inno Setup...
    "%ISCC%" NhavaLearn.iss
    if errorlevel 1 (
        echo Installer build failed.
        exit /b 1
    )
    echo.
    echo Build complete:
    echo   App only:  dist\NhavaLearn\NhavaLearn.exe
    echo   Installer: installer\NhavaLearn-Setup-*.exe
) else (
    echo.
    echo Inno Setup not found ^(ISCC.exe^) - skipping installer step.
    echo Install it from https://jrsoftware.org/isinfo.php to also produce a Setup.exe.
    echo Build complete: dist\NhavaLearn\NhavaLearn.exe
)
endlocal
