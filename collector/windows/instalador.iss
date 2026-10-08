; Instalador de Mesa de Acción para Windows (Inno Setup 6). Lo compila GitHub Actions
; (.github/workflows/instalador-windows.yml) después de PyInstaller:
;   iscc /DAppVer=1.0.0 collector\windows\instalador.iss
; Instala sin permisos de administrador, en la carpeta del usuario. Datos y configuración van aparte, en
; %LOCALAPPDATA%\MesaDeAccion, y se conservan al desinstalar o actualizar.

#ifndef AppVer
  #define AppVer "0.0.0"
#endif

[Setup]
AppId={{6E3C2B1A-4F7D-4C1E-9B8A-2D5F0A7C3E91}
AppName=Mesa de Acción
AppVersion={#AppVer}
AppVerName=Mesa de Acción {#AppVer}
AppPublisher=Mesa de Acción
DefaultDirName={localappdata}\Programs\Mesa de Accion
DefaultGroupName=Mesa de Acción
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=MesaDeAccion-IRTP-Setup-{#AppVer}
SetupIconFile=mesa.ico
UninstallDisplayIcon={app}\MesaDeAccion.exe
UninstallDisplayName=Mesa de Acción
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "inicio"; Description: "Iniciar Mesa de Acción con Windows (recomendado: así sigue recolectando datos)"
Name: "escritorio"; Description: "Crear un ícono en el escritorio"

[Files]
Source: "..\dist\MesaDeAccion\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Mesa de Acción"; Filename: "{app}\MesaDeAccion.exe"
Name: "{group}\Carpeta de datos de Mesa de Acción"; Filename: "{localappdata}\MesaDeAccion"
Name: "{autodesktop}\Mesa de Acción"; Filename: "{app}\MesaDeAccion.exe"; Tasks: escritorio
Name: "{userstartup}\Mesa de Acción"; Filename: "{app}\MesaDeAccion.exe"; Parameters: "--minimizado"; Tasks: inicio

[Run]
Filename: "{app}\MesaDeAccion.exe"; Description: "Abrir Mesa de Acción ahora"; Flags: nowait postinstall skipifsilent

[Code]
var
  ClavePage: TInputQueryWizardPage;

procedure CerrarMesa();
var
  Res: Integer;
begin
  { Una Mesa abierta bloquea sus archivos: se cierra antes de instalar, actualizar o desinstalar }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM MesaDeAccion.exe', '', SW_HIDE, ewWaitUntilTerminated, Res);
end;

function InitializeSetup(): Boolean;
begin
  CerrarMesa();
  Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  CerrarMesa();
  Result := True;
end;

procedure InitializeWizard();
begin
  ClavePage := CreateInputQueryPage(wpSelectTasks,
    'Licencia y asistente de IA',
    'Código de licencia y clave de la API de Claude',
    'Pegue el código de licencia que le entregaron (empieza con MESA-). Sin él, la Mesa abre una página para ' +
    'ingresarlo. La clave de Claude (empieza con sk-ant-api) es opcional: sin ella solo queda desactivado el asistente. ' +
    'Ambos se guardan únicamente en esta computadora. Si está actualizando, deje vacío lo que ya había ingresado.');
  ClavePage.Add('Código de licencia:', False);
  ClavePage.Add('Clave de Claude (opcional):', True);
end;

{ config.env: conserva lo que ya tenga y fija la marca IRTP; la clave solo se reemplaza si se ingresó una nueva }
procedure GuardarConfig();
var
  F, Clave, Licencia: String;
  S: TStringList;
  i: Integer;
begin
  F := ExpandConstant('{localappdata}\MesaDeAccion\config.env');
  ForceDirectories(ExtractFileDir(F));
  Licencia := Trim(ClavePage.Values[0]);
  Clave := Trim(ClavePage.Values[1]);
  S := TStringList.Create;
  try
    if FileExists(F) then
      S.LoadFromFile(F);
    { Se quitan los comentarios y se reescribe uno sin tildes: Inno guarda el archivo en ANSI y la Mesa lo lee como UTF-8 }
    for i := S.Count - 1 downto 0 do
      if (Pos('#', Trim(S[i])) = 1) or (Pos('MESA_MARCA=', S[i]) = 1) or ((Clave <> '') and (Pos('ANTHROPIC_API_KEY=', S[i]) = 1))
         or ((Licencia <> '') and (Pos('MESA_LICENCIA=', S[i]) = 1)) then
        S.Delete(i);
    S.Insert(0, '# Configuracion de Mesa de Accion. Una linea CLAVE=valor por ajuste; se aplica al reiniciar la Mesa.');
    S.Add('MESA_MARCA=irtp');
    if Clave <> '' then
      S.Add('ANTHROPIC_API_KEY=' + Clave);
    if Licencia <> '' then
      S.Add('MESA_LICENCIA=' + Licencia);
    S.SaveToFile(F);
  finally
    S.Free;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    GuardarConfig();
end;
