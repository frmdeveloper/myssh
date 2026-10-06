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
Untuk hostname tetap, pakai domain pribadi lewat Named Tunnel (lihat bagian **Domain Pribadi** di bawah).

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

## Tes
```bash
./test_e2e.sh "" USER PASS        # login, IP keluar, HTTPS, blokir internal, UDP/DNS via udpgw
```

## Domain Pribadi (Named Tunnel)

Quick tunnel menghasilkan URL `*.trycloudflare.com` acak yang **berubah tiap restart**.
Untuk hostname tetap (mis. `ssh.domainmu.com`), pakai Named Tunnel — gratis, tanpa root, tanpa IP publik.

### Prasyarat
- Punya domain yang **NS-nya ke Cloudflare** (di dashboard Cloudflare, domain muncul di list Websites dengan status "Active"). Kalo belum, daftar domain murah (~Rp 150k/tahun di Namecheap/dll) lalu arahkan NS ke Cloudflare.
- Akun Cloudflare (gratis).

### Langkah 1: Bikin tunnel di dashboard
1. Login Cloudflare → **Zero Trust** (sidebar kiri bawah, ada logo umbrella).
2. **Networks → Tunnels → Create a tunnel**.
3. Pilih type: **Cloudflared**.
4. Kasih nama tunnel (mis. `tunnel-saya`) → **Save tunnel**.
5. Di halaman "Install and run cloudflared", pilih tab **Environment: Docker / Linux**.
6. Copy token panjang yang muncul di kotak `cloudflared tunnel run --token eyJhIjoi...` — ambil bagian `eyJ...` saja (tanpa `cloudflared tunnel run --token `).
   Simpan token ini ke file `tunnel.token` (langkah 3 di bawah).
7. **Next** → di halaman "Route traffic" klik **Cancel** dulu (kita tambahkan hostname setelah tunnel jalan).

### Langkah 2: Simpan token ke server
```bash
# di direktori project (server):
echo "eyJhIjoi...." > tunnel.token   # paste token panjang tadi
chmod 600 tunnel.token
```
Cek isi file — harus dimulai dengan `eyJ`, tanpa spasi/newline di akhir:
```bash
cat tunnel.token | head -c 20; echo "..."
```

### Langkah 3: Tambah Public Hostname di dashboard
1. Balik ke **Zero Trust → Networks → Tunnels**.
2. Klik nama tunnel kamu (`tunnel-saya`).
3. Tab **Public Hostname** → **Add a public hostname**.
4. Isi:
   - **Subdomain**: mis. `ssh` (atau `tunnel`, `vpn`, bebas)
   - **Domain**: `domainmu.com` (harus domain yang aktif di Cloudflare)
   - **Path**: kosongkan
   - **Service Type**: `HTTP`
   - **URL**: `localhost:8080`
5. **Save hostname**.
6. Tunggu ~30 detik biar Cloudflare propagasi DNS + sertifikat TLS.

### Langkah 4: Restart stack di server
```bash
./stack.sh restart
./stack.sh status
```
Output harus muncul `mode: named tunnel (tunnel.token)` (bukan `URL quick tunnel: ...`).

### Langkah 5: Verifikasi
```bash
# DNS harus resolve ke IP Cloudflare
dig +short ssh.domainmu.com

# HTTP ke root harus balas "OK" (lebih baik 200 daripada 530/404)
curl -I https://ssh.domainmu.com/

# WebSocket handshake harus balas 101
curl -sS -o /dev/null -w "HTTP %{http_code}\n" \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Sec-WebSocket-Version: 13" \
  https://ssh.domainmu.com/
# -> HTTP 101
```

### Langkah 6: Update config di HP
Ganti semua `*.trycloudflare.com` jadi `ssh.domainmu.com`:

- Host: `ssh.domainmu.com`
- Port: `443` (TLS) atau `80` (plain, tanpa SNI)
- SSL/SNI: `ssh.domainmu.com` (kalo port 443)
- Payload:
  ```
  GET / HTTP/1.1[crlf]Host: ssh.domainmu.com[crlf]Upgrade: websocket[crlf]Connection: Upgrade[crlf]Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==[crlf]Sec-WebSocket-Version: 13[crlf][crlf]
  ```

### Troubleshooting Named Tunnel
| Gejala | Penyebab | Solusi |
|---|---|---|
| `dig` balas `0.0.0.0` / NXDOMAIN | DNS belum propagasi | Tunggu 1-5 menit, atau cek tab DNS di dashboard Cloudflare — harus ada record CNAME `ssh` → `<tunnel-id>.cfargotunnel.com` (otomatis dibikin) |
| `curl -I` balas `530` | cloudflared belum jalan / tunnel.token salah | `./stack.sh status` — pastikan `cloudflared UP`. Cek `cat tunnel.token` — harus mulai `eyJ` |
| `curl -I` balas `404` | Public Hostname belom ditambah | Ulangi langkah 3, pastikan Service = `HTTP` `localhost:8080` (bukan `https://`, bukan port lain) |
| `curl -I` balas `1011` / `502` | ws2ssh mati | `./stack.sh restart`, cek `logs/ws2ssh.log` |
| `mode: quick tunnel` muncul di status | tunnel.token gak kebaca | pastikan file `tunnel.token` ada di direktori yang sama dengan `stack.sh`. `ls -la tunnel.token` harus muncul dengan size > 100 byte |
| Tunnel jalan tapi SSH gagal login | user belum dibuat / password salah | `venv/bin/python ssh_server.py listusers` — bikin ulang kalo perlu |

### Multiple tunnels / multiple subdomains
Bisa bikin beberapa hostname ke tunnel yang sama (mis. `ssh1.domainmu.com`, `ssh2.domainmu.com`, `vpn.domainmu.com`) — semua di-route ke `localhost:8080`. Berguna untuk:
- Pisah user (ssh1 untuk user A, ssh2 untuk user B) — walau auth gak per-host, tetap sama
- Bug host rotation (kalo satu hostname ke-block, pakai yang lain)
- Backup kalo satu hostname bermasalah

### Pindah dari quick tunnel ke named tunnel (atau sebaliknya)
- **Quick → Named**: bikin tunnel.token, `./stack.sh restart`. URL lama mati otomatis.
- **Named → Quick**: `rm tunnel.token && ./stack.sh restart`. URL `*.trycloudflare.com` baru muncul di `./stack.sh url`.

### Hapus tunnel
- Di dashboard: **Zero Trust → Networks → Tunnels** → klik tunnel → **Delete**.
- Di server: `rm tunnel.token && ./stack.sh restart`.
