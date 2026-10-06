#!/bin/bash
# Tes jalur Termux: klien SSH polos -> 127.0.0.1:2299 (termux_proxy.py) -> Cloudflare -> server.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
H=${1:?usage: $0 host user password}; U=$2; P=$3
python3 termux_proxy.py --host "$H" --listen-port 2299 > run/termux.log 2>&1 &
TP=$!
sleep 1
printf '#!/bin/sh\necho "%s"\n' "$P" > run/askpass.sh; chmod +x run/askpass.sh
export SSH_ASKPASS=$PWD/run/askpass.sh SSH_ASKPASS_REQUIRE=force DISPLAY=:0
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PubkeyAuthentication=no \
    -o PreferredAuthentications=password -o NumberOfPasswordPrompts=1 -o ExitOnForwardFailure=yes \
    -N -D 127.0.0.1:1082 -p 2299 "$U@127.0.0.1" > run/termux_ssh.log 2>&1 < /dev/null &
SP=$!
for i in $(seq 1 15); do (echo > /dev/tcp/127.0.0.1/1082) 2>/dev/null && break; sleep 1; done
echo "IP via Termux-path: $(curl -s -m 15 --socks5-hostname 127.0.0.1:1082 ipv4.icanhazip.com)"
curl -s -m 15 --socks5-hostname 127.0.0.1:1082 -o /dev/null -w "https://example.com -> HTTP %{http_code}\n" https://example.com
kill $SP $TP 2>/dev/null; wait 2>/dev/null; rm -f run/askpass.sh
cat run/termux.log | head -3; head -3 run/termux_ssh.log
