#!/usr/bin/env python3
"""Klien WS (stdlib) untuk dipakai sebagai ssh ProxyCommand.
Usage: wsclient.py HOST PORT [--tls] [--sni NAME] [--path /]
Menyimulasikan klien WebSocket sungguhan: handshake + frame bermask."""
import argparse, base64, os, select, socket, ssl, struct, sys

ap = argparse.ArgumentParser()
ap.add_argument("host"); ap.add_argument("port", type=int)
ap.add_argument("--tls", action="store_true")
ap.add_argument("--sni"); ap.add_argument("--path", default="/")
a = ap.parse_args()

s = socket.create_connection((a.host, a.port), timeout=15)
hostname = a.sni or a.host
if a.tls:
    s = ssl.create_default_context().wrap_socket(s, server_hostname=hostname)
key = base64.b64encode(os.urandom(16)).decode()
s.sendall((f"GET {a.path} HTTP/1.1\r\nHost: {hostname}\r\nUpgrade: websocket\r\n"
           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
buf = b""
while b"\r\n\r\n" not in buf:
    c = s.recv(1)
    if not c:
        sys.exit("handshake: koneksi putus")
    buf += c
status = buf.split(b"\r\n")[0].decode()
sys.stderr.write(f"[wsclient] {status}\n")
if " 101 " not in status:
    sys.exit("handshake gagal: " + buf.decode(errors="replace"))
s.settimeout(None)

def send_frame(data):
    mask = os.urandom(4); n = len(data)
    head = b"\x82" + (bytes([0x80 | n]) if n < 126 else b"\xfe" + struct.pack("!H", n) if n < 65536 else b"\xff" + struct.pack("!Q", n))
    s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

def recvn(n):
    d = b""
    while len(d) < n:
        c = s.recv(n - len(d))
        if not c: raise EOFError
        d += c
    return d

stdin_fd = sys.stdin.fileno(); out = sys.stdout.buffer
rbuf = b""
try:
    while True:
        r, _, _ = select.select([stdin_fd, s], [], [])
        if stdin_fd in r:
            d = os.read(stdin_fd, 65536)
            if not d: break
            send_frame(d)
        if s in r:
            b1, b2 = recvn(2); op = b1 & 0xF; n = b2 & 0x7F
            if n == 126: n = struct.unpack("!H", recvn(2))[0]
            elif n == 127: n = struct.unpack("!Q", recvn(8))[0]
            p = recvn(n) if n else b""
            if op == 8: break
            if op in (0, 1, 2):
                out.write(p); out.flush()
except EOFError:
    pass
