#!/usr/bin/env python3
"""WebSocket -> SSH proxy (stdlib only).

Dua mode, otomatis terdeteksi dari request header:
  * Ada `Sec-WebSocket-Key`  -> WebSocket asli (RFC6455, frame binary).
    Ini yang dikirim cloudflared ke origin.
  * Tidak ada                -> mode "payload": balas 101 lalu pipe TCP mentah
    (gaya HTTP Custom langsung tanpa Cloudflare).
"""
import asyncio
import base64
import hashlib
import os
import struct
import sys

LISTEN_HOST = os.environ.get("WS_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("WS_PORT", "8080"))
SSH_HOST = os.environ.get("SSH_HOST", "127.0.0.1")
SSH_PORT = int(os.environ.get("SSH_PORT", "2222"))
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


async def read_headers(reader):
    data = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
    lines = data.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return lines[0], headers


def ws_frame(payload: bytes, opcode=0x2) -> bytes:
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return head + payload


async def read_ws_frame(reader):
    b1, b2 = await reader.readexactly(2)
    fin, opcode = b1 & 0x80, b1 & 0x0F
    masked, n = b2 & 0x80, b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if masked else None
    payload = await reader.readexactly(n) if n else b""
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


async def pump_raw(src, dst):
    try:
        while True:
            d = await src.read(65536)
            if not d:
                break
            dst.write(d)
            await dst.drain()
    except Exception:
        pass
    finally:
        try:
            dst.close()
        except Exception:
            pass


async def ws_to_ssh(reader, ssh_w, client_w):
    try:
        while True:
            fin, op, payload = await read_ws_frame(reader)
            if op in (0x0, 0x1, 0x2):
                ssh_w.write(payload)
                await ssh_w.drain()
            elif op == 0x8:
                client_w.write(ws_frame(b"", 0x8))
                break
            elif op == 0x9:
                client_w.write(ws_frame(payload, 0xA))
                await client_w.drain()
    except Exception:
        pass
    finally:
        for w in (ssh_w, client_w):
            try:
                w.close()
            except Exception:
                pass


async def ssh_to_ws(ssh_r, client_w):
    try:
        while True:
            d = await ssh_r.read(65536)
            if not d:
                break
            client_w.write(ws_frame(d))
            await client_w.drain()
    except Exception:
        pass
    finally:
        try:
            client_w.close()
        except Exception:
            pass


async def handle(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        reqline, h = await read_headers(reader)
    except Exception:
        writer.close()
        return
    if "upgrade" not in h.get("connection", "").lower() and h.get("upgrade", "").lower() != "websocket":
        body = b"OK"
        if reqline.split(" ")[1:2] == ["/termux_proxy.py"]:  # unduhan script klien Termux (tidak rahasia)
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "termux_proxy.py"), "rb") as f:
                    body = f.read()
            except OSError:
                pass
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
        await writer.drain()
        writer.close()
        return
    try:
        ssh_r, ssh_w = await asyncio.open_connection(SSH_HOST, SSH_PORT)
    except Exception as e:
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        print("ssh backend error:", e, file=sys.stderr)
        return

    key = h.get("sec-websocket-key")
    if key:
        accept = base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()
        writer.write(
            ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
             "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept).encode()
        )
        await writer.drain()
        # Deteksi: klien SSH mentah (HTTP Custom) mulai dengan "SSH-" (0x53),
        # sedangkan frame WebSocket sungguhan mulai dengan byte >= 0x80.
        try:
            first = await asyncio.wait_for(reader.read(1), timeout=3)
        except asyncio.TimeoutError:
            first = None  # klien diam menunggu banner server -> mode raw
        if first == b"":
            ssh_w.close()
            writer.close()
            return
        if first and first[0] >= 0x80:
            print("[ws ] %s %s (framed)" % (peer, reqline), file=sys.stderr)
            reader._buffer[0:0] = first  # kembalikan byte yang di-peek ke DEPAN buffer
            await asyncio.gather(ws_to_ssh(reader, ssh_w, writer), ssh_to_ws(ssh_r, writer))
        else:
            print("[ws ] %s %s (raw after 101)" % (peer, reqline), file=sys.stderr)
            if first:
                ssh_w.write(first)
            await asyncio.gather(pump_raw(reader, ssh_w), pump_raw(ssh_r, writer))
    else:
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        await writer.drain()
        print("[raw] %s %s" % (peer, reqline), file=sys.stderr)
        await asyncio.gather(pump_raw(reader, ssh_w), pump_raw(ssh_r, writer))


async def main():
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    print("ws2ssh listen %s:%d -> %s:%d" % (LISTEN_HOST, LISTEN_PORT, SSH_HOST, SSH_PORT), file=sys.stderr)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
