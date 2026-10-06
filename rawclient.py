#!/usr/bin/env python3
"""Meniru HTTP Custom: kirim payload HTTP Upgrade (dengan Sec-WebSocket-Key statis),
tunggu 101, lalu pipe byte SSH MENTAH (tanpa frame WS) ke stdin/stdout.
Usage: rawclient.py HOST PORT [--tls]"""
import os, select, socket, ssl, sys

host, port = sys.argv[1], int(sys.argv[2])
s = socket.create_connection((host, port), timeout=15)
if "--tls" in sys.argv:
    s = ssl.create_default_context().wrap_socket(s, server_hostname=host)
s.sendall((f"GET / HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
buf = b""
while b"\r\n\r\n" not in buf:
    c = s.recv(1)
    if not c:
        sys.exit("putus saat handshake")
    buf += c
sys.stderr.write("[rawclient] " + buf.split(b"\r\n")[0].decode() + "\n")
if b" 101 " not in buf.split(b"\r\n")[0]:
    sys.exit(buf.decode(errors="replace"))
s.settimeout(None)
fd = sys.stdin.fileno()
while True:
    r, _, _ = select.select([fd, s], [], [])
    if fd in r:
        d = os.read(fd, 65536)
        if not d: break
        s.sendall(d)
    if s in r:
        d = s.recv(65536)
        if not d: break
        os.write(1, d)
