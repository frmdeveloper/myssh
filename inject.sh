#!/data/data/com.termux/files/usr/bin/bash
# inject.sh — Inject SSH-over-WebSocket dari Termux (pengganti HTTP Custom)
#
# Jalur: Termux -> WS/TLS :443 -> hehe1.frmchan.my.id -> ws2ssh -> SSH :2222
# Hasil: SOCKS5 di 127.0.0.1:1080 (+ forward UDPGW di 127.0.0.1:7300)
# TUN/VPN-nya arahin ke SOCKS itu (misal pake SocksTun).
#
# Pakai:
#   1. Isi SSH_USER di bawah pake akun tunnel lo.
#   2. Taruh wsclient.py di folder yang sama (atau di $HOME).
#   3. ./inject.sh        (Ctrl+C buat stop; auto-reconnect kalo putus)
#
# Password diketik pas connect. Biar nggak ditanya mulu:
#   SSHPASS='passwordlo' ./inject.sh     (butuh: pkg install sshpass)

set -u

# ==================== CONFIG ====================
SSH_USER="GANTI_USER"                 # akun tunnel lo (di users.json server)
SERVER_HOST="hehe1.frmchan.my.id"
SERVER_PORT="443"
SOCKS_PORT="1080"                     # SOCKS5 lokal buat TUN/app
UDPGW_PORT="7300"                     # forward ke badvpn-udpgw di server
WS_SNI=""                             # opsional: --sni buat bug (kosongin kalo nggak pake)
# ================================================

DIR="$(cd "$(dirname "$0")" && pwd)"
WSCLIENT="$DIR/wsclient.py"
[ -f "$WSCLIENT" ] || WSCLIENT="$HOME/wsclient.py"
if [ ! -f "$WSCLIENT" ]; then
  echo "[!] wsclient.py nggak ketemu — taruh di folder yang sama kayak inject.sh"
  exit 1
fi
command -v ssh >/dev/null 2>&1 || { echo "[!] openssh belum ada: pkg install openssh"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "[!] python belum ada: pkg install python"; exit 1; }
if [ "$SSH_USER" = "GANTI_USER" ]; then
  echo "[!] Isi dulu SSH_USER di dalem script ini (baris CONFIG)"
  exit 1
fi

PROXY="python3 $WSCLIENT $SERVER_HOST $SERVER_PORT --tls"
[ -n "$WS_SNI" ] && PROXY="$PROXY --sni $WS_SNI"

# Login pake SSH key kalo ada (nggak perlu password); password cuma cadangan
KEY_OPTS=()
if [ -f "$HOME/.ssh/id_ed25519" ]; then
  KEY_OPTS=(-i "$HOME/.ssh/id_ed25519" -o IdentitiesOnly=yes -o PreferredAuthentications=publickey,password)
fi

SSH_CMD=(ssh -o "ProxyCommand=$PROXY"
  "${KEY_OPTS[@]}"
  -o StrictHostKeyChecking=accept-new
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3
  -o ExitOnForwardFailure=yes
  -N -D "$SOCKS_PORT" -L "$UDPGW_PORT:127.0.0.1:7300"
  "$SSH_USER@inject")

if [ -n "${SSHPASS:-}" ]; then
  command -v sshpass >/dev/null 2>&1 || { echo "[!] sshpass belum ada: pkg install sshpass"; exit 1; }
  RUN=(sshpass -e "${SSH_CMD[@]}")
else
  RUN=("${SSH_CMD[@]}")
fi

termux-wake-lock 2>/dev/null
SSH_PID=""
cleanup() {
  echo; echo "[*] Stop."
  [ -n "$SSH_PID" ] && kill "$SSH_PID" 2>/dev/null
  termux-wake-unlock 2>/dev/null
  exit 0
}
trap cleanup INT TERM
echo "[*] Inject siap: SOCKS5 127.0.0.1:$SOCKS_PORT — Ctrl+C buat stop"
while true; do
  IP=""
  "${RUN[@]}" &
  SSH_PID=$!
  # Tunggu sampe SOCKS beneran jawab (= login sukses, tunnel naik)
  while kill -0 "$SSH_PID" 2>/dev/null; do
    IP=$(curl -s --max-time 3 --socks5-hostname "127.0.0.1:$SOCKS_PORT" https://api.ipify.org 2>/dev/null)
    [ -n "$IP" ] && break
    sleep 2
  done
  if [ -n "$IP" ]; then
    echo "[✓] NYAMBUNG — IP: $IP"
    wait "$SSH_PID" 2>/dev/null
  fi
  echo "[!] Putus — nyambung lagi 3 detik... (Ctrl+C buat stop beneran)"
  sleep 3
done
