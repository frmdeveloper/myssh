#!/bin/bash
# Tes end-to-end via Cloudflare: login password -> SOCKS internet -> blokir internal -> udpgw.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
H=${1:-$(./stack.sh url | sed 's#https://##')}
USER_=${2:-hpuser}; PASS_=${3:?usage: $0 [host] user password}
echo "host: $H"
cat > run/askpass.sh <<EOF
#!/bin/sh
echo '$PASS_'
EOF
chmod +x run/askpass.sh
export SSH_ASKPASS=$PWD/run/askpass.sh SSH_ASKPASS_REQUIRE=force DISPLAY=:0
SSHO=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PubkeyAuthentication=no \
      -o PreferredAuthentications=password -o NumberOfPasswordPrompts=1 -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=15 -o "ProxyCommand=python3 rawclient.py $H 443 --tls")

ssh "${SSHO[@]}" -N -D 127.0.0.1:1081 -L 127.0.0.1:7301:127.0.0.1:7300 "$USER_@x" > run/e2e_ssh.log 2>&1 < /dev/null &
SP=$!
for i in $(seq 1 15); do (echo > /dev/tcp/127.0.0.1/1081) 2>/dev/null && break; sleep 1; done

echo "== 1. IP keluar via SOCKS (harus = IP VPS ini) =="
echo "   direct : $(curl -s -m 8 ipv4.icanhazip.com)"
echo "   tunnel : $(curl -s -m 15 --socks5-hostname 127.0.0.1:1081 ipv4.icanhazip.com)"
echo "== 2. HTTPS + DNS lewat tunnel =="
curl -s -m 15 --socks5-hostname 127.0.0.1:1081 -o /dev/null -w "   https://example.com -> HTTP %{http_code}\n" https://example.com
echo "== 3. Egress filter: akses internal HARUS ditolak =="
for t in 127.0.0.1:9119 127.0.0.1:20241 localhost:22 10.81.10.134:22; do
  r=$(curl -s -m 6 --socks5-hostname 127.0.0.1:1081 -o /dev/null -w "%{http_code}" http://$t/ 2>/dev/null)
  echo "   $t -> ${r:-000} $([ "${r:-000}" = 000 ] && echo DITOLAK-ok || echo '!!! BOCOR')"
done
echo "== 4. UDP via udpgw =="
python3 test_udpgw.py 7301

kill $SP 2>/dev/null; wait $SP 2>/dev/null
echo "== log ssh klien =="; grep -iE "denied|refused|prohibit|error" run/e2e_ssh.log | sort | uniq -c | head -8
rm -f run/askpass.sh
