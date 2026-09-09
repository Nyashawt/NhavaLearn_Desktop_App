; Inno Setup script for NhavaLearn Desktop.
;
; Produces a real Windows installer (Setup.exe): Program Files install,
; Start Menu / optional desktop shortcuts, and an uninstaller registered
; with Windows "Apps & features".
;
; Build order:
;   1. build.bat            (PyInstaller -> dist\NhavaLearn\NhavaLearn.exe)
;   2. ISCC.exe NhavaLearn.iss   (this script -> installer\NhavaLearn-Setup-<version>.exe)
;
; Requires Inno Setup 6 (https://jrsoftware.org/isinfo.php).

#define MyAppName "NhavaLearn"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "NhavaLearn"
#define MyAppExeName "NhavaLearn.exe"
#define MyDistDir "dist\NhavaLearn"

[Setup]
AppId={{6E6C0F1E-6B0A-4E9B-9E4A-2B7C6E3B9A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=NhavaLearn-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
SetupIconFile=ui\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Leaves %LOCALAPPDATA%\NhavaLearn (the database, media, and any local AI
; model) in place on uninstall — deliberate, so removing/reinstalling the
; app never destroys a school's lesson data.
