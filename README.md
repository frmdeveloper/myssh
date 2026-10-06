# SSH over WebSocket via Cloudflare (tanpa IP publik, tanpa root)

```
HP (HTTP Custom) ─SSH─▶ [WS/TLS 443] ─▶ Cloudflare ─▶ cloudflared ─▶ ws2ssh :8080 ─▶ ssh_server :2222 ─▶ internet
                                                                                       └▶ badvpn-udpgw :7300 (UDP)
```

## Jalankan
```bash
./stack.sh start      # udpgw + ssh_server + ws2ssh + cloudflared (auto-restart kalau crash)
./stack.sh status | url | logs | stop | restart
```
Quick tunnel (default) memberi URL `*.trycloudflare.com` **acak yang berubah tiap start**.
Untuk hostname tetap: buat Named Tunnel di dashboard Cloudflare (Zero Trust → Networks → Tunnels),
tambahkan Public Hostname `ssh.domainmu.com` → `HTTP` `localhost:8080`, simpan token ke `tunnel.token`,
lalu `./stack.sh restart`.

## Akun
Akun hanya bisa port-forward (tanpa shell). Tujuan ke loopback/jaringan privat diblokir, kecuali `127.0.0.1:7300` (udpgw).

```bash
# Tambah user (password acak, expired 30 hari)
venv/bin/python ssh_server.py adduser BUDI --days 30
# Output:
#   user: BUDI
#   password: 5N-DqhwwcO2mCTe3       ← simpan ini, tidak bisa dilihat lagi
#   expires: 2026-11-05 13:05

# Tambah user dengan password sendiri (tanpa expired)
venv/bin/python ssh_server.py adduser SARI --password 'rahasia123'

# Reset password user yang sudah ada (overwrite)
venv/bin/python ssh_server.py adduser BUDI --password 'baru456' --days 30

# Daftar semua user + tanggal expired
venv/bin/python ssh_server.py listusers
#   hpuser 2026-11-05 07:21
#   BUDI   2026-11-05 13:05

# Hapus user
venv/bin/python ssh_server.py deluser BUDI
#   deleted
```

Catatan:
- File `users.json` disimpan dengan mode `0600` (hanya owner baca).
- Password di-hash PBKDF2-SHA256 (200k iter) + salt per-user, jadi gak ada plaintext.
- Perpanjang masa berlaku: jalankan ulang `adduser NAMA --password XXX --days N` (password bisa sama atau beda).
- Tidak ada cara lihat password lama; reset saja kalo lupa.

## Setelan HTTP Custom — Opsi A: langsung ke Cloudflare
- SSH: Host = hostname tunnel, Port = **443**, user/password akun
- SSL/TLS **aktif**, SNI = hostname tunnel
- Payload (wajib ada `Sec-WebSocket-Key`, tanpa itu Cloudflare membalas 400):
  ```
  GET / HTTP/1.1[crlf]Host: [host][crlf]Upgrade: websocket[crlf]Connection: Upgrade[crlf]Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==[crlf]Sec-WebSocket-Version: 13[crlf][crlf]
  ```
- UDPGW: `127.0.0.1` port `7300`

## Opsi B: lewat Termux (HTTP Custom cukup konek ke localhost)

### B1: Go binary (paling ringan, no Python di Termux)
Download satu file binary langsung dari tunnel (gak perlu install Python):
```bash
# di Termux (HP 64-bit modern):
wget https://<HOSTNAME_TUNNEL>/termux_proxy-android-arm64 -O termux_proxy
chmod +x termux_proxy
./termux_proxy --host <HOSTNAME_TUNNEL>

# HP 32-bit:
wget https://<HOSTNAME_TUNNEL>/termux_proxy-android-arm -O termux_proxy
```
Di HTTP Custom: SSH Host `127.0.0.1`, Port `2222`, **tanpa payload, tanpa SSL**, user/password akun, UDPGW `127.0.0.1:7300`.

### B2: Python (alternatif)
Di Termux: `pkg install python`, salin `termux_proxy.py`, jalankan:
```bash
python3 termux_proxy.py --host <HOSTNAME_TUNNEL>
```
Di HTTP Custom: SSH Host `127.0.0.1`, Port `2222`, **tanpa payload, tanpa SSL**, user/password akun, UDPGW `127.0.0.1:7300`.

Variasi (berlaku B1 dan B2):
- `--connect IP_LAIN` — konek ke IP lain, Host/SNI tetap
- `--sni NAMA` — SNI berbeda (domain fronting ke bug host)
- `--no-tls --port 80` — plain HTTP, tanpa SNI (untuk bug host di port 80)
- `--listen-port N` — ganti port listen (default 2222)

### Build ulang binary Go (di server)
```bash
sudo apt install golang-go   # sekali aja
mkdir -p dist
CGO_ENABLED=0 GOOS=android GOARCH=arm64 go build -ldflags="-s -w" -o dist/termux_proxy-android-arm64 termux_proxy.go
CGO_ENABLED=0 GOOS=linux   GOARCH=arm   go build -ldflags="-s -w" -o dist/termux_proxy-android-arm   termux_proxy.go
```

## Tes
```bash
./test_e2e.sh "" USER PASS        # login, IP keluar, HTTPS, blokir internal, UDP/DNS via udpgw
./test_termux.sh HOST USER PASS   # jalur Termux
```
