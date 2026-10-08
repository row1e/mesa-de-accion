"""Emite códigos de licencia para la app de Windows. Se corre solo en la computadora de quien emite.

La clave privada vive fuera del repositorio, en ~/.config/mesa-de-accion/licencia_privada.pem (permiso 600).
Sin ella no se pueden emitir licencias: guárdela con un respaldo seguro. La pública está en mesa/licencia.py.

    python windows/emitir_licencia.py --cliente IRTP --vence 2026-12-08 --contacto "Manya · r@manya.pe"
    python windows/emitir_licencia.py --verificar MESA-…
"""
import argparse
import base64
import datetime
import json
import pathlib
import sys

from cryptography.hazmat.primitives import serialization

PRIVADA = pathlib.Path.home() / ".config" / "mesa-de-accion" / "licencia_privada.pem"


def b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def emitir(cliente, vence, contacto):
    datetime.date.fromisoformat(vence)
    clave = serialization.load_pem_private_key(PRIVADA.read_bytes(), password=None)
    datos = json.dumps({"cliente": cliente, "vence": vence, "contacto": contacto,
                        "emitida": datetime.date.today().isoformat()}, ensure_ascii=False, separators=(",", ":")).encode()
    return f"MESA-{b64(datos)}.{b64(clave.sign(datos))}"


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cliente"); ap.add_argument("--vence", help="AAAA-MM-DD, último día de uso")
    ap.add_argument("--contacto", default=""); ap.add_argument("--verificar", metavar="CODIGO")
    a = ap.parse_args()
    if a.verificar:
        from mesa import licencia
        print(licencia.verificar(a.verificar))
    elif a.cliente and a.vence:
        print(emitir(a.cliente, a.vence, a.contacto))
    else:
        ap.error("use --cliente y --vence, o --verificar")
