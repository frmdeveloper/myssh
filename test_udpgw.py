#!/usr/bin/env python3
"""Tes udpgw lewat tunnel: kirim query DNS (UDP) ke 8.8.8.8:53 via badvpn-udpgw.
Usage: test_udpgw.py LOCAL_PORT   (port lokal yang di-forward ke 127.0.0.1:7300 di server)"""
import socket, struct, sys

port = int(sys.argv[1])
q = (b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
     b"\x07example\x03com\x00\x00\x01\x00\x01")
# udpgw: [len u16 LE][flags u8][conid u16 LE][ipv4 4B][port u16 BE][payload]
body = struct.pack("<BH", 0, 1) + socket.inet_aton("8.8.8.8") + struct.pack("!H", 53) + q
s = socket.create_connection(("127.0.0.1", port), timeout=10)
s.sendall(struct.pack("<H", len(body)) + body)
hdr = s.recv(2)
n = struct.unpack("<H", hdr)[0]
data = b""
while len(data) < n:
    data += s.recv(n - len(data))
flags, conid = struct.unpack("<BH", data[:3])
dns = data[3 + 6:]
ok = dns[:2] == b"\x12\x34" and (dns[2] & 0x80) and dns[3] & 0x0F == 0
print("udpgw reply: %d bytes, conid=%d, DNS rcode=%d, answers=%d -> %s" % (
    len(dns), conid, dns[3] & 0x0F, struct.unpack("!H", dns[6:8])[0], "OK" if ok else "GAGAL"))
sys.exit(0 if ok else 1)
