// Command termux_proxy is a local proxy for Termux/HTTP Custom.
//
// HTTP Custom connects to 127.0.0.1:listen-port (no payload, no SSL),
// then this proxy opens a TLS+WebSocket connection to the Cloudflare tunnel
// and pipes raw SSH bytes both ways.
//
// Usage:
//
//	./termux_proxy --host candy-myth-experiments-console.trycloudflare.com
//	./termux_proxy --host ssh.domainku.com --connect 104.16.0.1   # connect to a different IP, Host/SNI unchanged
//	./termux_proxy --host ssh.domainku.com --sni bug.host.com     # different SNI (domain fronting)
//	./termux_proxy --host ssh.domainku.com --no-tls --port 80     # plain HTTP (port 80, no SNI)
//
// Flags mirror termux_proxy.py 1:1 so the two are interchangeable.
package main

import (
	"bufio"
	"bytes"
	"crypto/rand"
	"crypto/tls"
	"encoding/base64"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"strings"
	"sync"
	"time"
)

const bufSize = 65536

// dialTimeout for TCP connect + TLS handshake + HTTP response read.
const dialTimeout = 15 * time.Second

func main() {
	host := flag.String("host", "", "hostname tunnel (used as Host header) (required)")
	connect := flag.String("connect", "", "TCP destination (default = --host)")
	sni := flag.String("sni", "", "TLS SNI (default = --host)")
	port := flag.Int("port", 443, "remote port")
	noTLS := flag.Bool("no-tls", false, "plain TCP, no TLS (use with --port 80)")
	path := flag.String("path", "/", "HTTP request path")
	listenHost := flag.String("listen-host", "127.0.0.1", "bind address")
	listenPort := flag.Int("listen-port", 2222, "bind port")
	flag.Parse()

	if *host == "" {
		fmt.Fprintln(os.Stderr, "error: --host is required")
		os.Exit(2)
	}

	dst := *connect
	if dst == "" {
		dst = *host
	}
	sniName := *sni
	if sniName == "" {
		sniName = *host
	}

	addr := fmt.Sprintf("%s:%d", *listenHost, *listenPort)
	l, err := net.Listen("tcp", addr)
	if err != nil {
		log.Fatalf("listen %s: %v", addr, err)
	}

	scheme := "wss"
	if *noTLS {
		scheme = "ws"
	}
	log.Printf("siap: %s -> %s://%s:%d (Host=%s)  | set HTTP Custom ke %s",
		addr, scheme, dst, *port, *host, addr)

	for {
		c, err := l.Accept()
		if err != nil {
			log.Printf("accept: %v", err)
			continue
		}
		go handle(c, *host, dst, sniName, *port, *noTLS, *path)
	}
}

func handle(clientConn net.Conn, host, dst, sniName string, port int, noTLS bool, path string) {
	defer clientConn.Close()

	remoteConn, err := net.DialTimeout("tcp", net.JoinHostPort(dst, fmt.Sprintf("%d", port)), dialTimeout)
	if err != nil {
		log.Printf("koneksi ke tunnel gagal: %v", err)
		return
	}

	if !noTLS {
		tlsConf := &tls.Config{
			ServerName: sniName,
			MinVersion: tls.VersionTLS12,
		}
		// Wrap in a deadline-bounded handshake via a temporary conn with SetDeadline.
		_ = remoteConn.SetDeadline(time.Now().Add(dialTimeout))
		tlsConn := tls.Client(remoteConn, tlsConf)
		if err := tlsConn.Handshake(); err != nil {
			log.Printf("TLS handshake gagal: %v", err)
			remoteConn.Close()
			return
		}
		_ = remoteConn.SetDeadline(time.Time{}) // clear deadline
		remoteConn = tlsConn
	}

	// Generate Sec-WebSocket-Key (16 random bytes, base64).
	keyBytes := make([]byte, 16)
	if _, err := rand.Read(keyBytes); err != nil {
		log.Printf("rand: %v", err)
		remoteConn.Close()
		return
	}
	key := base64.StdEncoding.EncodeToString(keyBytes)

	// Build and send the Upgrade request. Hand-written to control exact framing.
	req := fmt.Sprintf(
		"GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n",
		path, host, key,
	)
	if _, err := remoteConn.Write([]byte(req)); err != nil {
		log.Printf("write upgrade: %v", err)
		remoteConn.Close()
		return
	}

	// Read response until \r\n\r\n, verify 101.
	_ = remoteConn.SetReadDeadline(time.Now().Add(dialTimeout))
	br := bufio.NewReader(remoteConn)
	var respBuf bytes.Buffer
	for {
		line, err := br.ReadString('\n')
		if err != nil {
			log.Printf("read handshake: %v", err)
			remoteConn.Close()
			return
		}
		respBuf.WriteString(line)
		if line == "\r\n" || line == "\n" {
			break
		}
	}
	_ = remoteConn.SetReadDeadline(time.Time{})

	status := strings.TrimSpace(strings.SplitN(respBuf.String(), "\r\n", 2)[0])
	if !strings.Contains(status, " 101 ") {
		log.Printf("handshake gagal: %s", status)
		remoteConn.Close()
		return
	}

	// Anything still buffered in br (peeked bytes beyond \r\n\r\n) must be flushed
	// to the client first — copy it directly.
	if br.Buffered() > 0 {
		if peeked, err := br.Peek(br.Buffered()); err == nil {
			if _, err := clientConn.Write(peeked); err != nil {
				remoteConn.Close()
				return
			}
		}
	}

	// Bidirectional pipe.
	var wg sync.WaitGroup
	wg.Add(2)
	go pipe(remoteConn, clientConn, &wg)
	go pipe(clientConn, remoteConn, &wg)
	wg.Wait()
}

// pipe copies from src to dst. On EOF or error, closes dst (which signals the
// other direction) and returns.
func pipe(dst io.Writer, src io.Reader, wg *sync.WaitGroup) {
	defer wg.Done()
	buf := make([]byte, bufSize)
	_, _ = io.CopyBuffer(dst, src, buf)
	// Close the write side of dst if it supports CloseWrite (TCP does); else close.
	type closeWriter interface{ CloseWrite() error }
	if cw, ok := dst.(closeWriter); ok {
		_ = cw.CloseWrite()
	} else if c, ok := dst.(io.Closer); ok {
		_ = c.Close()
	}
	_ = errors.New("") // no-op to silence unused import if any
}
