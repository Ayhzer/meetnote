; Installeur MeetNote (Inno Setup 6.3 ou plus) - installation par utilisateur.
;
; Compilation (la version et le dossier source ne sont JAMAIS en dur ici) :
;   ISCC /DAppVersion=<version> /DDossierSource=<dossier PyInstaller> [/O<sortie>] meetnote.iss
; Macros attendues :
;   AppVersion     version affichee et reprise dans le nom de l'installeur
;   DossierSource  sortie onedir de PyInstaller (MeetNote.exe + _internal\)
; Sortie : MeetNote-<AppVersion>-installation.exe
;
; Principes :
;  - aucune elevation : tout va sous le profil de l'utilisateur ;
;  - le demarrage avec Windows ecrit la meme commande que l'application
;    (demarrage_windows.commande_demarrage, mode fige) ;
;  - MeetNote en cours est arrete SANS relance avant installation et desinstallation
;    (l'EXE se supervise lui-meme : il faut deposer clean_exit.flag, puis tuer
;    superviseur et enfant) ;
;  - la desinstallation ne touche jamais aux donnees de l'utilisateur.

#ifndef AppVersion
  #error "Macro AppVersion manquante : compiler avec ISCC /DAppVersion=<version> ..."
#endif
#ifndef DossierSource
  #error "Macro DossierSource manquante : compiler avec ISCC /DDossierSource=<dossier PyInstaller> ..."
#endif

[Setup]
; Identifiant fixe : permet la mise a jour par-dessus une installation existante.
AppId={{6F3A1C52-8B7E-4D19-A2C4-5E90B7D1F3A8}
AppName=MeetNote
AppVersion={#AppVersion}
AppVerName=MeetNote {#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\MeetNote
DefaultGroupName=MeetNote
DisableProgramGroupPage=yes
UsedUserAreasWarning=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=MeetNote-{#AppVersion}-installation
UninstallDisplayName=MeetNote
UninstallDisplayIcon={app}\MeetNote.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; L'arret de MeetNote est fait dans [Code] : le gestionnaire de redemarrage
; relancerait l'enfant via le superviseur.
CloseApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "demarrage"; Description: "Démarrer avec Windows"; GroupDescription: "Options :"
Name: "iconebureau"; Description: "Créer une icône sur le Bureau"; GroupDescription: "Options :"; Flags: unchecked

[Files]
Source: "{#DossierSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\MeetNote"; Filename: "{app}\MeetNote.exe"
Name: "{autodesktop}\MeetNote"; Filename: "{app}\MeetNote.exe"; Tasks: iconebureau

[Registry]
; Meme commande que l'application : "<exe>" --minimized. Valeur retiree a la desinstallation.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MeetNote"; ValueData: """{app}\MeetNote.exe"" --minimized"; Flags: uninsdeletevalue; Tasks: demarrage

[Run]
; Sans --minimized : l'utilisateur doit voir l'application (et l'assistant de premier lancement).
Filename: "{app}\MeetNote.exe"; Description: "Lancer MeetNote"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Uniquement les fichiers laisses dans le dossier du programme. Jamais les donnees
; (%APPDATA%\MeetNote, %LOCALAPPDATA%\MeetNote, Documents\MeetNote).
Type: filesandordirs; Name: "{app}"

[Code]
const
  NomProcessus = 'MeetNote.exe';
  DelaiLiberationMs = 500;

{ Arrête MeetNote sans relance : le drapeau empêche le superviseur de relancer
  l'enfant, puis taskkill tue le superviseur et l'enfant (/T = arbre complet). }
procedure ArreterMeetNote();
var
  DossierDonnees: String;
  CodeRetour: Integer;
begin
  DossierDonnees := ExpandConstant('{userappdata}\MeetNote');
  ForceDirectories(DossierDonnees);
  SaveStringToFile(DossierDonnees + '\clean_exit.flag', '', False);
  { Code 128 si aucun processus : sans importance }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM ' + NomProcessus, '',
    SW_HIDE, ewWaitUntilTerminated, CodeRetour);
  { Laisse à Windows le temps de libérer les fichiers de l'ancienne version }
  Sleep(DelaiLiberationMs);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ArreterMeetNote();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  ArreterMeetNote();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Message: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Message :=
      'MeetNote a été désinstallé.' + #13#10 + #13#10 +
      'Vos données ont été conservées :' + #13#10 +
      '  - ' + ExpandConstant('{userappdata}\MeetNote') + ' (réglages et historique)' + #13#10 +
      '  - ' + ExpandConstant('{localappdata}\MeetNote') + ' (modèles Whisper)' + #13#10 +
      '  - ' + ExpandConstant('{userdocs}\MeetNote') + ' (audio et transcriptions)' + #13#10 + #13#10 +
      'Supprimez ces dossiers à la main si vous n''en avez plus besoin.';
    SuppressibleMsgBox(Message, mbInformation, MB_OK, IDOK);
  end;
end;
