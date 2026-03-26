;===============================================================
; Instalador Cliente - ControlProduccionGF (Opción A: desde dist)
; Fuente: dist\ControlProduccionGF\*
; Salida: installer_output
;===============================================================

#define MainExe "ControlProduccionGF.exe"
#define AppName "ControlProduccionGF"
#define AppVer  "1.0.1"

; === Carpeta de build (PyInstaller) ===
#define BuildDir "dist\ControlProduccionGF"

[Setup]
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName={autopf}\ControlProduccionGF
OutputDir=installer_output
OutputBaseFilename=ControlProduccionGF_Cliente_Installer
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=no

[Files]
; --- Empacar SIEMPRE el build final (dist) ---
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

; plantilla opcional (si existe en la carpeta del .iss)
Source: "desktop.env.plantilla"; DestDir: "{app}"; Flags: ignoreversion; Check: FileExists(ExpandConstant('{src}\desktop.env.plantilla'))

[Tasks]
Name: "desktopicon"; Description: "Crear icono en el Escritorio"; GroupDescription: "Iconos adicionales:"; Flags: unchecked

[Icons]
Name: "{group}\ControlProduccionGF"; Filename: "{app}\run_client.bat"; WorkingDir: "{app}"; IconFilename: "{app}\{#MainExe}"
Name: "{commondesktop}\ControlProduccionGF"; Filename: "{app}\run_client.bat"; WorkingDir: "{app}"; Tasks: desktopicon; IconFilename: "{app}\{#MainExe}"
Name: "{group}\Carpeta de instalacion"; Filename: "{app}"

[Run]
Filename: "{app}\run_client.bat"; Description: "Ejecutar ControlProduccionGF"; Flags: nowait postinstall skipifsilent

[Code]
var
  PageConfig: TInputQueryWizardPage;

const
  DEFAULT_GOOGLE_CLIENT_ID = '326829798148-r5def3v6se5b0j9nseat63sq3ah68728.apps.googleusercontent.com';
  DEFAULT_GOOGLE_CLIENT_SECRET = '';  { opcional: puede ir vacío }

function NormalizeScheme(const S: string): string;
begin
  Result := LowerCase(Trim(S));
end;

function IsValidPort(const S: string): Boolean;
var
  N: Integer;
begin
  N := StrToIntDef(Trim(S), -1);
  Result := (N > 0) and (N <= 65535);
end;

function EscapeJson(const S: string): string;
var
  T: string;
begin
  T := S;
  StringChangeEx(T, '\', '\\', True);
  StringChangeEx(T, '"', '\"', True);
  Result := T;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Host, Port, Scheme: string;
begin
  Result := True;

  if CurPageID = PageConfig.ID then
  begin
    Host := Trim(PageConfig.Values[0]);
    Port := Trim(PageConfig.Values[1]);
    Scheme := NormalizeScheme(PageConfig.Values[2]);

    if Host = '' then
    begin
      MsgBox('Debes ingresar la IP o el host del servidor.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    if not IsValidPort(Port) then
    begin
      MsgBox('El puerto del backend debe ser un numero valido entre 1 y 65535.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    if (Scheme <> 'http') and (Scheme <> 'https') then
    begin
      MsgBox('El protocolo debe ser "http" o "https".', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    if Trim(PageConfig.Values[3]) = '' then
    begin
      MsgBox('GOOGLE_CLIENT_ID no puede ir vacío.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

procedure InitializeWizard;
begin
  PageConfig := CreateInputQueryPage(
    wpSelectDir,
    'Configuracion del cliente',
    'Parametros del sistema',
    'Ingrese IP/Host y puerto del backend. Ejemplo: http://IP:PUERTO'
  );

  PageConfig.Add('IP o Host del Servidor:', False);
  PageConfig.Add('Puerto del Backend:', False);
  PageConfig.Add('Protocolo (http o https):', False);

  PageConfig.Add('GOOGLE_CLIENT_ID (login Google):', False);
  PageConfig.Add('GOOGLE_CLIENT_SECRET (opcional):', True);
  PageConfig.Add('GOOGLE_ALLOWED_HD (opcional, dominio Workspace):', False);

  PageConfig.Values[0] := '172.16.20.215';
  PageConfig.Values[1] := '8000';
  PageConfig.Values[2] := 'http';

  PageConfig.Values[3] := DEFAULT_GOOGLE_CLIENT_ID;
  PageConfig.Values[4] := DEFAULT_GOOGLE_CLIENT_SECRET;
  PageConfig.Values[5] := '';
end;

procedure _WriteText(const Path, Content: string);
begin
  SaveStringToFile(Path, Content, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Host, Port, Scheme, BaseUrl: string;
  GId, GSecret, GHD: string;

  DesktopEnvPath, DotEnvPath, JsonPath, BatPath: string;
  EnvContent, JsonContent, BatContent: string;

  InternalDir, InternalDesktopDir: string;
begin
  if CurStep = ssPostInstall then
  begin
    Host   := Trim(PageConfig.Values[0]);
    Port   := Trim(PageConfig.Values[1]);
    Scheme := NormalizeScheme(PageConfig.Values[2]);

    GId     := Trim(PageConfig.Values[3]);
    GSecret := Trim(PageConfig.Values[4]);
    GHD     := Trim(PageConfig.Values[5]);

    BaseUrl := Scheme + '://' + Host + ':' + Port;

    { --- desktop.env y .env --- }
    DesktopEnvPath := ExpandConstant('{app}\desktop.env');
    EnvContent :=
      '# Generated by installer' + #13#10 +
      'BACKEND_HOST=' + Host + #13#10 +
      'BACKEND_PORT=' + Port + #13#10 +
      'API_BASE_URL=' + BaseUrl + #13#10 +
      'PUBLIC_BASE_URL=' + BaseUrl + #13#10 +
      'GOOGLE_CLIENT_ID=' + GId + #13#10 +
      'GOOGLE_CLIENT_SECRET=' + GSecret + #13#10 +
      'GOOGLE_ALLOWED_HD=' + GHD + #13#10;

    _WriteText(DesktopEnvPath, EnvContent);

    DotEnvPath := ExpandConstant('{app}\.env');
    _WriteText(DotEnvPath, EnvContent);

    { --- desktop_config.json (completo) --- }
    JsonContent :=
      '{' + #13#10 +
      '  "API_BASE_URL": "' + EscapeJson(BaseUrl) + '",' + #13#10 +
      '  "PUBLIC_BASE_URL": "' + EscapeJson(BaseUrl) + '",' + #13#10 +
      '  "GOOGLE_CLIENT_ID": "' + EscapeJson(GId) + '",' + #13#10 +
      '  "GOOGLE_ALLOWED_HD": "' + EscapeJson(GHD) + '"' + #13#10 +
      '}' + #13#10;

    JsonPath := ExpandConstant('{app}\desktop_config.json');
    _WriteText(JsonPath, JsonContent);

    { --- también dentro de _internal si existe --- }
    InternalDir := ExpandConstant('{app}\_internal');
    if DirExists(InternalDir) then
      _WriteText(InternalDir + '\desktop_config.json', JsonContent);

    InternalDesktopDir := ExpandConstant('{app}\_internal\desktop');
    if DirExists(InternalDesktopDir) then
      _WriteText(InternalDesktopDir + '\desktop_config.json', JsonContent);

    { --- run_client.bat --- }
    BatPath := ExpandConstant('{app}\run_client.bat');
    BatContent :=
      '@echo off' + #13#10 +
      'set "PUBLIC_BASE_URL=' + BaseUrl + '"' + #13#10 +
      'set "GOOGLE_CLIENT_ID=' + GId + '"' + #13#10 +
      'set "GOOGLE_CLIENT_SECRET=' + GSecret + '"' + #13#10 +
      'set "GOOGLE_ALLOWED_HD=' + GHD + '"' + #13#10 +
      'cd /d "%~dp0"' + #13#10 +
      'start "" "%~dp0{#MainExe}"' + #13#10;

    _WriteText(BatPath, BatContent);
  end;
end;
