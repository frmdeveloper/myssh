#!/bin/bash
# Kontrol stack tunnel (tanpa root). Tiap komponen dijalankan dalam loop respawn.
#   ./stack.sh start|stop|restart|status|url|logs
# Cloudflared:
#   - Jika file ./tunnel.token ada  -> named tunnel (hostname diatur di dashboard Cloudflare,
#                                      arahkan ke http://localhost:8080)
#   - Jika tidak                    -> quick tunnel (URL trycloudflare.com acak, berubah tiap start)
cd "$(dirname "$(readlink -f "$0")")" || exit 1
D=$PWD
RUN=$D/run; LOGS=$D/logs
mkdir -p "$RUN" "$LOGS"
PY=$D/venv/bin/python
UDPGW_PORT=${UDPGW_PORT:-7300}
SSH_PORT=${SSH_LISTEN_PORT:-2222}
WS_PORT=${WS_PORT:-8080}

# format: nama|perintah
components() {
  echo "udpgw|$D/badvpn/build/udpgw/badvpn-udpgw --listen-addr 127.0.0.1:$UDPGW_PORT --max-clients 500 --max-connections-for-client 20"
  echo "sshd|SSH_LISTEN_PORT=$SSH_PORT UDPGW_PORT=$UDPGW_PORT $PY $D/ssh_server.py serve"
  echo "ws2ssh|WS_PORT=$WS_PORT SSH_PORT=$SSH_PORT $PY $D/ws2ssh.py"
  echo "ws2ssh-shell|WS_PORT=8081 SSH_PORT=22 $PY $D/ws2ssh.py"
  if [ -s "$D/tunnel.token" ]; then
    echo "cloudflared|cloudflared tunnel --no-autoupdate run --token \$(cat $D/tunnel.token)"
  else
    echo "cloudflared|cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$WS_PORT"
  fi
}

start_one() {
  local name=$1 cmd=$2
  if [ -f "$RUN/$name.pid" ] && kill -0 "$(cat "$RUN/$name.pid")" 2>/dev/null; then
    echo "  $name sudah jalan"; return
  fi
  setsid bash -c "while true; do $cmd; echo \"[\$(date +%T)] $name exit, restart 2s\" >&2; sleep 2; done" \
    >> "$LOGS/$name.log" 2>&1 < /dev/null &
  echo $! > "$RUN/$name.pid"
  echo "  $name start (pid $(cat "$RUN/$name.pid"))"
}

stop_one() {
  local name=$1
  if [ -f "$RUN/$name.pid" ]; then
    local pid; pid=$(cat "$RUN/$name.pid")
    kill -- -"$pid" 2>/dev/null
    rm -f "$RUN/$name.pid"
    echo "  $name stop"
  fi
}

url() {
  grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOGS/cloudflared.log" 2>/dev/null | tail -1
}

case "$1" in
  start)
    components | while IFS='|' read -r n c; do start_one "$n" "$c"; done
    if [ ! -s "$D/tunnel.token" ]; then
      for i in $(seq 1 25); do u=$(url); [ -n "$u" ] && break; sleep 1; done
      echo "URL: ${u:-<belum muncul, cek ./stack.sh logs>}"
    fi
    ;;
  stop)
    components | while IFS='|' read -r n c; do stop_one "$n"; done
    ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
  status)
    components | while IFS='|' read -r n c; do
      if [ -f "$RUN/$n.pid" ] && kill -0 "$(cat "$RUN/$n.pid")" 2>/dev/null; then echo "  UP    $n"; else echo "  DOWN  $n"; fi
    done
    if [ -s "$D/tunnel.token" ]; then echo "  mode: named tunnel (tunnel.token)"; else echo "  URL quick tunnel: $(url)"; fi
    ;;
  url) url ;;
  logs) tail -n 15 "$LOGS"/*.log ;;
  *) echo "usage: $0 start|stop|restart|status|url|logs"; exit 1 ;;
esac
