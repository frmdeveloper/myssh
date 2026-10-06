#!/usr/bin/env python3
"""Proxy lokal untuk Termux (hanya stdlib, python3).

HTTP Custom disetel:  SSH -> Server 127.0.0.1 : 2222 (tanpa payload, tanpa SSL/SNI),
lalu username/password akun SSH. Script ini yang bikin koneksi ke Cloudflare:
  TLS (SNI) -> HTTP Upgrade websocket (Sec-WebSocket-Key) -> pipe byte SSH mentah.

Contoh:
  python3 termux_proxy.py --host candy-myth-experiments-console.trycloudflare.com
  python3 termux_proxy.py --host ssh.domainku.com --connect 104.16.0.1      # connect ke IP lain, Host/SNI tetap
  python3 termux_proxy.py --host ssh.domainku.com --sni bug.host.com        # SNI lain (domain fronting)
Opsi: --listen-port 2222  --port 443  --no-tls  --path /
"""
import argparse
import asyncio
import base64
import os
import ssl
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--host", required=True, help="hostname tunnel (dipakai sebagai header Host)")
ap.add_argument("--connect", help="alamat tujuan koneksi TCP (default = --host)")
ap.add_argument("--sni", help="SNI TLS (default = --host)")
ap.add_argument("--port", type=int, default=443)
ap.add_argument("--no-tls", action="store_true")
ap.add_argument("--path", default="/")
ap.add_argument("--listen-host", default="127.0.0.1")
ap.add_argument("--listen-port", type=int, default=2222)
a = ap.parse_args()

SSL_CTX = None if a.no_tls else ssl.create_default_context()


async def pump(r, w):
    try:
        while True:
            d = await r.read(65536)
            if not d:
                break
            w.write(d)
            await w.drain()
    except Exception:
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def handle(lr, lw):
    try:
        rr, rw = await asyncio.wait_for(asyncio.open_connection(
            a.connect or a.host, a.port, ssl=SSL_CTX,
            server_hostname=None if a.no_tls else (a.sni or a.host)), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        rw.write((f"GET {a.path} HTTP/1.1\r\nHost: {a.host}\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                  "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        await rw.drain()
        head = await asyncio.wait_for(rr.readuntil(b"\r\n\r\n"), timeout=15)
        status = head.split(b"\r\n")[0].decode(errors="replace")
        if " 101 " not in status:
            print("handshake gagal:", status, file=sys.stderr)
            raise ConnectionError(status)
    except Exception as e:
        print("koneksi ke tunnel gagal:", repr(e), file=sys.stderr)
        lw.close()
        return
    await asyncio.gather(pump(lr, rw), pump(rr, lw))


async def main():
    srv = await asyncio.start_server(handle, a.listen_host, a.listen_port)
    print("siap: %s:%d -> wss://%s:%d (Host=%s)  | set HTTP Custom ke 127.0.0.1:%d" % (
        a.listen_host, a.listen_port, a.connect or a.host, a.port, a.host, a.listen_port), file=sys.stderr)
    async with srv:
        await srv.serve_forever()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
