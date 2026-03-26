;===============================================================
;  Instalador del Servidor - ControlProduccionGF
;  Versión mínima funcional (sin MySQL automático, sin Python)
;===============================================================

[Setup]
AppName=ControlProduccionGF - Servidor
AppVersion=1.0
DefaultDirName=C:\ControlProduccionGF_Server
OutputDir=installer_output
OutputBaseFilename=ControlProduccionGF_Server_Installer
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
; Copia el backend completo (carpeta que está un nivel arriba de DESPLIEGUE)
Source: "..\backend\*"; DestDir: "{app}\backend"; Flags: recursesubdirs

; Copia requirements.txt desde la raíz del proyecto
Source: "..\requirements.txt"; DestDir: "{app}"

; Copia la plantilla .env del backend desde la raíz
Source: "..\backend.env.plantilla"; DestDir: "{app}"

; Copia el script de inicio en producción desde la raíz
Source: "..\run_backend_prod.bat"; DestDir: "{app}"

[Icons]
Name: "{group}\Iniciar Backend (Manual)"; Filename: "{app}\run_backend_prod.bat"
Name: "{group}\Carpeta de instalación"; Filename: "{app}"

[Run]
; 1) Crear entorno virtual (.venv) usando el Python del sistema
Filename: "cmd.exe"; \
  Parameters: "/c python -m venv ""{app}\.venv"""; \
  StatusMsg: "Creando entorno virtual para el backend..."; \
  Flags: runhidden waituntilterminated

; 2) Actualizar pip dentro del entorno virtual
Filename: "cmd.exe"; \
  Parameters: "/c ""{app}\.venv\Scripts\python.exe"" -m pip install --upgrade pip"; \
  StatusMsg: "Actualizando pip en el entorno virtual..."; \
  Flags: runhidden waituntilterminated

; 3) Instalar dependencias desde requirements.txt
;    Se guarda un log por si hay errores: pip_install.log
Filename: "cmd.exe"; \
  Parameters: "/c ""{app}\.venv\Scripts\pip.exe"" install -r ""{app}\requirements.txt"" > ""{app}\pip_install.log"" 2>&1"; \
  StatusMsg: "Instalando dependencias del backend (esto puede tardar)..."; \
  Flags: runhidden waituntilterminated

; 4) Verificar que FastAPI y Uvicorn se importan correctamente
Filename: "{app}\.venv\Scripts\python.exe"; \
  Parameters: "-c ""import fastapi, uvicorn; print('Dependencias OK')"""; \
  StatusMsg: "Verificando dependencias del backend..."; \
  Flags: waituntilterminated
  
  
[Code]

var
  PageConfig: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  { Página personalizada para pedir datos de configuración }
  PageConfig := CreateInputQueryPage(
    wpSelectDir,
    'Configuración del servidor',
    'Parámetros del backend',
    'Introduce los datos necesarios para generar el archivo .env del backend.'#13#10 +
    'Puedes dejar los valores por defecto si coinciden con tu entorno.');

  { 0 } PageConfig.Add('IP o host del servidor en la LAN (para que se conecten los clientes):', False);
  { 1 } PageConfig.Add('Puerto HTTP del backend (ej: 8000):', False);

  { Datos MySQL }
  { 2 } PageConfig.Add('Host de MySQL (ej: 127.0.0.1):', False);
  { 3 } PageConfig.Add('Puerto de MySQL (ej: 3307):', False);
  { 4 } PageConfig.Add('Usuario MySQL (ej: gf_app):', False);
  { 5 } PageConfig.Add('Contraseña MySQL:', True);
  { 6 } PageConfig.Add('Nombre de la base de datos principal (bank):', False);
  { 7 } PageConfig.Add('Nombre de la base de datos de auth (auth_sso):', False);

  { Admin inicial y JWT }
  { 8 } PageConfig.Add('Email del admin inicial:', False);
  { 9 } PageConfig.Add('Password del admin inicial:', True);
  {10 } PageConfig.Add('JWT_SECRET (secreto largo para los tokens JWT):', True);

  { Valores por defecto (ajusta a tu entorno real) }
  PageConfig.Values[0] := '127.0.0.1';      { IP LAN }
  PageConfig.Values[1] := '8000';           { Puerto HTTP backend }

  PageConfig.Values[2] := '127.0.0.1';      { MySQL host }
  PageConfig.Values[3] := '3307';           { MySQL port (tú usas 3307) }
  PageConfig.Values[4] := 'gf_app';         { Usuario MySQL }
  PageConfig.Values[5] := 'GF_app_2025!';   { Contraseña MySQL por defecto }
  PageConfig.Values[6] := 'bank';           { Nombre BD principal }
  PageConfig.Values[7] := 'auth_sso';       { Nombre BD auth }

  PageConfig.Values[8]  := 'admin@empresa.com';
  PageConfig.Values[9]  := 'CambioEstaClave123!';
  PageConfig.Values[10] := 'CAMBIAR_POR_UN_SECRETO_LARGO_Y_UNICO';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  EnvFile: string;
  HostLAN, HttpPort: string;
  DbHost, DbPort, DbUser, DbPass, DbBank, DbAuth: string;
  AdminEmail, AdminPass, JwtSecret: string;
  DBUrl, AuthUrl: string;
  EnvContent: string;
begin
  { Cuando termina la instalación de archivos y [Run] (ssPostInstall),
    generamos el archivo .env en la carpeta de instalación }
  if CurStep = ssPostInstall then
  begin
    HostLAN   := PageConfig.Values[0];
    HttpPort  := PageConfig.Values[1];

    DbHost    := PageConfig.Values[2];
    DbPort    := PageConfig.Values[3];
    DbUser    := PageConfig.Values[4];
    DbPass    := PageConfig.Values[5];
    DbBank    := PageConfig.Values[6];
    DbAuth    := PageConfig.Values[7];

    AdminEmail := PageConfig.Values[8];
    AdminPass  := PageConfig.Values[9];
    JwtSecret  := PageConfig.Values[10];

    { Construimos las dos URLs de conexión }
    DBUrl :=
      'mysql+pymysql://' + DbUser + ':' + DbPass + '@' +
      DbHost + ':' + DbPort + '/' + DbBank + '?charset=utf8mb4';

    AuthUrl :=
      'mysql+pymysql://' + DbUser + ':' + DbPass + '@' +
      DbHost + ':' + DbPort + '/' + DbAuth + '?charset=utf8mb4';

    EnvFile := ExpandConstant('{app}\.env');

    EnvContent :=
      '# Archivo .env generado por el instalador' #13#10 +
      '# Puedes editarlo a mano si es necesario' #13#10#13#10 +

      'DB_URL=' + DBUrl + #13#10 +
      'AUTH_DB_URL=' + AuthUrl + #13#10#13#10 +

      'PUBLIC_BASE_URL=http://' + HostLAN + ':' + HttpPort + #13#10 +
      'APP_HOST=0.0.0.0' + #13#10 +
      'APP_PORT=' + HttpPort + #13#10#13#10 +

      'FIRST_ADMIN_EMAIL=' + AdminEmail + #13#10 +
      'FIRST_ADMIN_PASSWORD=' + AdminPass + #13#10#13#10 +

      'JWT_SECRET=' + JwtSecret + #13#10 +
      'JWT_ALG=HS256' + #13#10 +
      'JWT_HOURS=24' + #13#10;

    if not SaveStringToFile(EnvFile, EnvContent, False) then
    begin
      MsgBox('No se pudo escribir el archivo .env en: ' + EnvFile,
        mbError, MB_OK);
    end;
  end;
end;
