; =============================================================================
; Nexus Scalp Engine — Windows Installer (Inno Setup 6)
; =============================================================================
; Build with:  ISCC.exe installer\NexusScalpEngine.iss
; Version/source/output injected by build_release.ps1 via command-line defines:
;   ISCC.exe installer\NexusScalpEngine.iss
;     /DNSE_VERSION=9.0.0 /DNSE_SOURCE_DIR=... /DNSE_OUTPUT_DIR=...
;     /DNSE_CHANNEL=stable
;
; Design:
;   * Per-user install (no admin required) under {localappdata}\Programs.
;   * User DATA (config/logs/databases/models) is NEVER stored under the
;     install dir — it lives in {localappdata}\NexusScalpEngine, so upgrades,
;     repairs and uninstalls preserve it by default (sections 23/24/37).
;   * Idempotent: re-running the installer upgrades without touching user
;     data. Uninstall leaves user data intact unless the checkbox is ticked.
;   * Architecture check: x64 only (ARM64 unsupported by the dependency
;     stack — we refuse loudly instead of installing a broken payload).
; =============================================================================
#ifndef NSE_VERSION
  #define NSE_VERSION "9.0.0"
#endif
#ifndef NSE_CHANNEL
  #define NSE_CHANNEL "stable"
#endif
#ifndef NSE_SOURCE_DIR
  #define NSE_SOURCE_DIR "release\build\windows-x64\onedir\NexusScalpEngine"
#endif
#ifndef NSE_OUTPUT_DIR
  #define NSE_OUTPUT_DIR "release"
#endif

#define MyAppName "Nexus Scalp Engine"
#define MyAppPublisher "Nexus Quantitative Engineering"
#define MyAppExeName "NexusScalpEngine.exe"
#define MyAppVersion NSE_VERSION

[Setup]
AppId={{8C4E6F2E-9D0A-4C7A-9E8E-NEXUSSCALP001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\NexusScalpEngine
DefaultGroupName=Nexus Scalp Engine
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#NSE_OUTPUT_DIR}
OutputBaseFilename=NexusScalpEngine-{#MyAppVersion}-win-x64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
; Idempotent re-install = upgrade path (user data untouched)
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start the engine automatically when I log in"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#NSE_SOURCE_DIR}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Always ship a safe (PAPER) config template that the first-run wizard copies.
Source: "..\configs\base.yaml"; DestDir: "{localappdata}\NexusScalpEngine\config"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: recursesubdirs createallsubdirs ignoreversion
; BUG-160/BUG-166: ship the release verification contract INSIDE the
; installed tree so post-install `verify-release` can self-verify. The
; full SHA256SUMS.txt/manifest are generated AFTER this step (they hash
; the installer itself), so release.yml PRE-STAGES a contract subset
; (portable + cli + zip hashes, manifest without the setup.exe entry)
; into checksums/ + manifests/ BEFORE invoking ISCC. skipifsourcedoesntexist
; keeps installation resilient if pre-staging ever changes - verify-release
; then reports the gap honestly instead of failing hard.
Source: "{#NSE_OUTPUT_DIR}\checksums\SHA256SUMS.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#NSE_OUTPUT_DIR}\manifests\release-manifest.json"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\Nexus Scalp Engine"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Doctor (diagnostics)"; Filename: "{app}\{#MyAppExeName}"; Parameters: "doctor"
Name: "{autodesktop}\Nexus Scalp Engine"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "health"; Description: "Run post-install health check"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Parameters: "setup"; Description: "Open first-run setup wizard"; Flags: nowait postinstall skipifsilent

[Registry]
; Per-user data root marker (used by the CLI to find user data)
Root: HKCU; Subkey: "Software\NexusScalpEngine"; ValueType: string; ValueName: "DataRoot"; ValueData: "{localappdata}\NexusScalpEngine"; Flags: uninsdeletekey

[Code]
function InitializeSetup(): Boolean;
begin
  if not Is64BitInstallMode then
  begin
    MsgBox('This installer requires a 64-bit (x64) Windows system.' + #13#10 +
           'ARM64 is not supported by the PyTorch/Polars/MetaTrader5 dependency stack.',
           mbError, MB_OK);
    Result := False;
    Exit;
  end;
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // First-run marker: the CLI reads this to know the wizard should run.
    SaveStringToFile(ExpandConstant('{localappdata}\NexusScalpEngine\config\.first_run'),
                     '1', False);
  end;
end;

[UninstallDelete]
; Remove ONLY application files. User data (config/logs/db/models) is never
; touched unless the user explicitly selects the checkbox below.
Type: filesandordirs; Name: "{app}"

[Code]
var
  RemoveDataPage: TInputOptionWizardPage;

procedure InitializeWizard;
begin
  // Only shown in interactive uninstalls; silent (/VERYSILENT) uninstalls
  // must complete without input and always preserve user data.
  RemoveDataPage := CreateInputOptionPage(wpReady, 'Preserve your data?', '',
    'Your trading data (databases, models, configuration, logs) is stored outside ' +
    'the application folder and is preserved by default. Tick the box below ONLY ' +
    'if you really want to delete it too.', True, False);
  RemoveDataPage.Add('Delete my user data as well (databases, models, config, logs)');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    if not UninstallSilent then
    begin
      if RemoveDataPage.Values[0] then
      begin
        DelTree(ExpandConstant('{localappdata}\NexusScalpEngine'), True, True, True);
      end;
    end;
  end;
end;