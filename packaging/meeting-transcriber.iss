#ifndef SourceDir
  #define SourceDir "..\dist\Meeting Transcriber"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif
#ifndef AppVersion
  #define AppVersion "0.1.2"
#endif

#define AppName "Meeting Transcriber"
#define AppExeName "MeetingTranscriber.exe"
#define AppPublisher "Meeting Transcriber contributors"
#define AppUrl "https://github.com/Dadaranger/Meeting-Transcriber"

[Setup]
AppId={{3EAF40F9-1ED1-46F7-82B6-B97C4309B18A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=Meeting-Transcriber-{#AppVersion}-Setup
SetupIconFile={#SourcePath}\meeting-transcriber.ico
UninstallDisplayIcon={app}\{#AppExeName}
InfoBeforeFile={#SourcePath}\installer-info.txt
LicenseFile={#SourcePath}\..\LICENSE
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern dynamic
CloseApplications=yes
RestartApplications=no
RestartIfNeededByRun=no
ChangesEnvironment=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Remove the obsolete Xet transport retained by pre-0.1.1 in-place upgrades.
Type: filesandordirs; Name: "{app}\_internal\hf_xet"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
