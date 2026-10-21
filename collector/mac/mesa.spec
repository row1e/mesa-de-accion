# Empaquetado de Mesa de Acción para macOS (PyInstaller). Se corre desde collector/:
#   pyinstaller --noconfirm mac/mesa.spec
# Deja "dist/Mesa de Acción.app": app de barra de menús (sin ícono en el Dock), que mac/dmg.sh mete en la imagen .dmg.
import os
from PyInstaller.utils.hooks import collect_submodules

C = os.path.abspath(os.path.join(SPECPATH, ".."))          # collector/
VERSION = os.environ.get("MESA_VERSION", "0.0.0")
a = Analysis(
    [os.path.join(C, "app_escritorio.py")],
    pathex=[C],
    datas=[(os.path.join(C, "web"), "web"), (os.path.join(C, "certs"), "certs"),
           (os.path.join(C, "..", "ref"), "ref"), (os.path.join(C, "windows", "icono.png"), "windows")],
    hiddenimports=collect_submodules("mesa") + collect_submodules("uvicorn") + ["pystray._darwin"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MesaDeAccion", console=False,
          icon=os.path.join(C, "windows", "icono.png"))
coll = COLLECT(exe, a.binaries, a.datas, name="MesaDeAccion")
app = BUNDLE(coll, name="Mesa de Acción.app", icon=os.path.join(C, "windows", "icono.png"),
             bundle_identifier="pe.mesadeaccion.app", version=VERSION,
             info_plist={"LSUIElement": True,                       # solo en la barra de menús, como la bandeja de Windows
                         "CFBundleDisplayName": "Mesa de Acción", "CFBundleShortVersionString": VERSION,
                         "LSMinimumSystemVersion": "11.0", "NSHumanReadableCopyright": "Mesa de Acción"})
