"""Relé peruano: deja que una Mesa en la nube consulte SENAMHI, MIDIS y PROVIAS por la conexión de esta computadora.

Esos sitios no responden a IPs de nubes (AWS, aunque esté en Lima). Este relé corre en una PC con internet peruano,
escucha solo en 127.0.0.1 y acepta únicamente túneles HTTPS (CONNECT) al puerto 443 de los dominios permitidos: no
ve el contenido (va cifrado de punta a punta) ni sirve de proxy para otra cosa. El servidor llega a él por SSH:

  python3 rele.py                                   # en la PC peruana (solo biblioteca estándar)
  ssh -N -R 8888:127.0.0.1:8888 ubuntu@<servidor>   # desde la misma PC: el 8888 del servidor llega aquí
  MESA_PROXY=http://127.0.0.1:8888                  # en la configuración de la Mesa del servidor

Opciones: --puerto (8888) y --dominios (los mismos que MESA_PROXY_HOSTS por defecto).
"""
import argparse
import asyncio
import time

DOMINIOS = ("senamhi.gob.pe", "midis.gob.pe", "proviasnac.gob.pe")


def permitido(host, dominios):
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in dominios)


async def _copiar(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass


async def atender(reader, writer, dominios):
    destino = "?"
    try:
        cabecera = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 20)
        metodo, objetivo, *_ = cabecera.split(b"\r\n", 1)[0].decode("latin-1").split()
        destino = objetivo
        host, _, puerto = objetivo.rpartition(":")
        if metodo != "CONNECT" or puerto != "443" or not permitido(host, dominios):
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            print(time.strftime("%H:%M:%S"), "rechazado", metodo, objetivo, flush=True)
            return writer.close()
        r2, w2 = await asyncio.wait_for(asyncio.open_connection(host, 443), 30)
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        print(time.strftime("%H:%M:%S"), "túnel a", host, flush=True)
        await asyncio.gather(_copiar(reader, w2), _copiar(r2, writer))
    except Exception as e:  # noqa: BLE001 — un pedido malo no debe tumbar el relé
        print(time.strftime("%H:%M:%S"), "error", destino, type(e).__name__, e, flush=True)
        try:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--puerto", type=int, default=8888)
    ap.add_argument("--dominios", default=",".join(DOMINIOS))
    a = ap.parse_args()
    dominios = tuple(d.strip().lower() for d in a.dominios.split(",") if d.strip())
    srv = await asyncio.start_server(lambda r, w: atender(r, w, dominios), "127.0.0.1", a.puerto)
    print(f"Relé en 127.0.0.1:{a.puerto} · dominios: {', '.join(dominios)}", flush=True)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
