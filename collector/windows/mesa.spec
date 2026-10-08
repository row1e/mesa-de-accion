# Empaquetado de Mesa de Acción para Windows (PyInstaller, modo carpeta). Se corre desde collector/:
#   pyinstaller --noconfirm windows/mesa.spec
# Deja dist/MesaDeAccion/ (MesaDeAccion.exe + _internal/), que el instalador (windows/instalador.iss) copia tal cual.
import os
from PyInstaller.utils.hooks import collect_submodules

C = os.path.abspath(os.path.join(SPECPATH, ".."))          # collector/
a = Analysis(
    [os.path.join(C, "app_windows.py")],
    pathex=[C],
    datas=[(os.path.join(C, "web"), "web"), (os.path.join(C, "certs"), "certs"),
           (os.path.join(C, "..", "ref"), "ref"), (os.path.join(C, "windows", "icono.png"), "windows")],
    # fuentes y módulos que el colector importa por nombre (mesa.indeci_pdf, mesa.backup) y el servidor web
    hiddenimports=collect_submodules("mesa") + collect_submodules("uvicorn") + ["pystray._win32"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MesaDeAccion", console=False,
          icon=os.path.join(C, "windows", "mesa.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="MesaDeAccion")
