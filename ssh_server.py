#!/usr/bin/env python3
"""SSH server khusus tunneling (asyncssh) - tanpa root, tanpa shell.

  * Auth: password (hash PBKDF2 di users.json, ada masa berlaku) + public key opsional.
  * Hanya port-forwarding (direct-tcpip) yang diizinkan; sesi shell/exec TIDAK menjalankan apa pun.
  * Egress filter: tujuan loopback/private/link-local/multicast ditolak (supaya akun tunnel
    tidak bisa menyentuh layanan internal mesin ini). Satu-satunya pengecualian adalah
    127.0.0.1:UDPGW_PORT untuk badvpn-udpgw (UDP over TCP).
  * DNS di-resolve di sini dan koneksi di-pin ke IP yang sudah divalidasi (anti DNS-rebinding).

Pakai:
  ssh_server.py serve
  ssh_server.py adduser NAMA [--password PW] [--days N]
  ssh_server.py deluser NAMA
  ssh_server.py listusers
"""
import argparse
import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import sys
import time

import asyncssh

BASE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.environ.get("USERS_FILE", os.path.join(BASE, "users.json"))
HOST_KEY = os.environ.get("HOST_KEY", os.path.join(BASE, "keys", "ssh_host_ed25519"))
AUTH_KEYS = os.environ.get("AUTH_KEYS", os.path.join(BASE, "keys", "authorized_keys"))
LISTEN_HOST = os.environ.get("SSH_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("SSH_LISTEN_PORT", "2222"))
UDPGW_PORT = int(os.environ.get("UDPGW_PORT", "7300"))
PBKDF2_ITERS = 200_000


# ---------------------------------------------------------------- users db
def load_users():
    try:
        with open(USERS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_users(users):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(users, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, USERS_FILE)


def hash_pw(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)
    return salt.hex(), dk.hex()


def check_pw(rec, password):
    salt = bytes.fromhex(rec["salt"])
    _, dk = hash_pw(password, salt)
    return hmac.compare_digest(dk, rec["hash"])


# ---------------------------------------------------------------- egress policy
def addr_allowed(ip):
    return ipaddress.ip_address(ip).is_global


async def resolve_allowed(host, port):
    """Kembalikan IP (string) pertama yang boleh dituju, atau None."""
    try:
        ipaddress.ip_address(host)
        return host if addr_allowed(host) else None
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=2), timeout=8)  # SOCK_STREAM
    except Exception:
        return None
    cands = sorted({i[4][0] for i in infos}, key=lambda a: ":" in a)  # IPv4 dulu
    if not cands or not all(addr_allowed(a) for a in cands):
        return None  # satu saja alamat internal -> tolak semua (anti rebinding)
    return cands[0]


# ---------------------------------------------------------------- ssh server
class TunnelServer(asyncssh.SSHServer):
    def connection_made(self, conn):
        self._conn = conn
        self._user = None

    def connection_lost(self, exc):
        if self._user:
            log("logout", self._user)

    def begin_auth(self, username):
        self._user = username
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        users = load_users()
        rec = users.get(username)
        # tetap hitung hash walau user tidak ada -> waktu respons seragam
        dummy = {"salt": "00" * 16, "hash": "00" * 32}
        ok = check_pw(rec or dummy, password) and rec is not None
        if ok and rec.get("expires") and time.time() > rec["expires"]:
            log("expired", username)
            return False
        log("login-ok" if ok else "login-FAIL", username)
        return ok

    def session_requested(self):
        async def idle(process):  # tidak menjalankan apa pun; hanya menahan sesi
            try:
                while True:
                    if not await process.stdin.read(1024):
                        break
            except Exception:
                pass
            process.exit(0)
        return idle

    def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
        return self._open(dest_host, dest_port)

    async def _open(self, dest_host, dest_port):
        if dest_host in ("127.0.0.1", "localhost") and dest_port == UDPGW_PORT:
            return await self._conn.forward_connection("127.0.0.1", UDPGW_PORT)
        ip = await resolve_allowed(dest_host, dest_port)
        if ip is None:
            log("deny", "%s -> %s:%s" % (self._user, dest_host, dest_port))
            raise asyncssh.ChannelOpenError(
                asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED, "destination not permitted")
        return await self._conn.forward_connection(ip, dest_port)  # pinned


def log(kind, msg):
    print("%s [%s] %s" % (time.strftime("%H:%M:%S"), kind, msg), file=sys.stderr, flush=True)


async def serve():
    if not os.path.exists(HOST_KEY):
        k = asyncssh.generate_private_key("ssh-ed25519")
        k.write_private_key(HOST_KEY)
        os.chmod(HOST_KEY, 0o600)
    kw = {}
    if os.path.exists(AUTH_KEYS):
        kw["authorized_client_keys"] = AUTH_KEYS
    await asyncssh.create_server(
        TunnelServer, LISTEN_HOST, LISTEN_PORT, server_host_keys=[HOST_KEY],
        keepalive_interval=30, keepalive_count_max=4, login_timeout=30,
        allow_scp=False, sftp_factory=None, x11_forwarding=False,
        agent_forwarding=False, line_editor=False, **kw)
    log("start", "ssh_server %s:%d (udpgw %d)" % (LISTEN_HOST, LISTEN_PORT, UDPGW_PORT))
    await asyncio.Event().wait()


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("serve")
    a = sp.add_parser("adduser"); a.add_argument("name"); a.add_argument("--password"); a.add_argument("--days", type=float)
    d = sp.add_parser("deluser"); d.add_argument("name")
    sp.add_parser("listusers")
    args = ap.parse_args()

    if args.cmd == "serve":
        asyncio.run(serve())
    elif args.cmd == "adduser":
        pw = args.password or secrets.token_urlsafe(12)
        salt, h = hash_pw(pw)
        users = load_users()
        users[args.name] = {"salt": salt, "hash": h,
                            "expires": time.time() + args.days * 86400 if args.days else None}
        save_users(users)
        print("user: %s\npassword: %s\nexpires: %s" % (
            args.name, pw,
            time.strftime("%Y-%m-%d %H:%M", time.localtime(users[args.name]["expires"]))
            if users[args.name]["expires"] else "never"))
    elif args.cmd == "deluser":
        users = load_users()
        print("deleted" if users.pop(args.name, None) else "not found")
        save_users(users)
    elif args.cmd == "listusers":
        for n, r in load_users().items():
            e = r.get("expires")
            print(n, time.strftime("%Y-%m-%d %H:%M", time.localtime(e)) if e else "never")


if __name__ == "__main__":
    main()
