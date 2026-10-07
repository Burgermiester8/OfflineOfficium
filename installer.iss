; The installer: one Setup .exe that puts the folder build on another PC.
;
;   python build-exe.py --installer
;
; build-exe.py compiles this with Inno Setup 6.7, passing the folder build
; (Source), where the Setup goes (OutputDir), the licence texts (Licenses) and
; the version (AppVersion, the date; NumericVersion, the same as four numbers).
;
; It installs for the current user only, under %LOCALAPPDATA%\Programs, so it
; needs no administrator rights; it adds a Start menu shortcut, an optional
; desktop one, and an entry in Settings > Apps to uninstall it. Running a newer
; Setup replaces the installed app whole.

#ifndef Source
  #error Compile this through build-exe.py --installer, which passes Source.
#endif

#define AppName "Divinum Officium"
#define AppExe "DivinumOfficium.exe"

[Setup]
; The installer's identity: keep it, or a new Setup installs a second copy
; instead of updating the first.
AppId={{250fda77-7847-4a66-818d-124c0c8be711}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} ({#AppVersion})
VersionInfoVersion={#NumericVersion}
VersionInfoDescription={#AppName} Setup
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
; The app holds this mutex while it runs (DivinumOfficium.py), so Setup and the
; uninstaller ask for it to be closed first rather than meet files in use.
AppMutex=DivinumOfficiumApp
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile={#Source}\web\favicon.ico
WizardStyle=modern dynamic windows11
#ifdef WizardSmall
; The app's icon in place of Setup's own pictures (made by build-exe.py)
; (in both modes: dark mode otherwise brings Setup's own back)
WizardSmallImageFile={#WizardSmall}
WizardSmallImageFileDynamicDark={#WizardSmall}
WizardImageFile={#WizardLarge}
WizardImageFileDynamicDark={#WizardLarge}
WizardImageStretch=no
#endif
OutputDir={#OutputDir}
OutputBaseFilename=DivinumOfficium-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An update replaces the app whole, so nothing of an older version lingers.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\perl"
Type: filesandordirs; Name: "{app}\typst"
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\licenses"

[Files]
; app\previous holds what the last build-exe.py --quick replaced: not for others.
Source: "{#Source}\*"; DestDir: "{app}"; Excludes: "\app\previous,__pycache__,*.pyc,*.old"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Licenses}\*"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; An update the app started itself (doupdate.py: /SILENT /relaunch=1) opens it again.
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: Relaunch

[UninstallDelete]
; Python may compile the app's code beside it; that is not in Setup's list.
Type: filesandordirs; Name: "{app}\app\__pycache__"

[Code]
function Relaunch: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;
