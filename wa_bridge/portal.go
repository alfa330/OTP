package main

// Клиент портала: только исходящие запросы, подписанные ключом шлюза. Портал до шлюза не
// достучится (NAT и фаервол), поэтому команды людей сервис забирает сам в ответе на опрос.

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

const (
	pollPath   = "/api/hiring/wa/agent/poll"
	eventsPath = "/api/hiring/wa/agent/events"
	// Файл сообщения: id сообщения — в пути (путь подписан), сам файл — телом.
	mediaPathPrefix = "/api/hiring/wa/agent/media/"
)

type portal struct {
	base   string
	http   *http.Client
	files  *http.Client // файл до 25 МБ через прокси шлюза идёт дольше 30 секунд опроса
	signer *signer
}

type httpError struct {
	Status int
	Body   string
}

func (e *httpError) Error() string { return fmt.Sprintf("портал ответил %d: %s", e.Status, e.Body) }

func newPortal(base, proxy, caBundle string, s *signer) (*portal, error) {
	base = strings.TrimRight(strings.TrimSpace(base), "/")
	if base == "" {
		return nil, errors.New("не задан адрес портала (CDR_BRIDGE_PORTAL)")
	}
	transport := &http.Transport{
		// Единственный выход наружу — прокси шлюза; переменные окружения прокси не читаем, чтобы
		// чужая переменная не увела канал (как у моста).
		Proxy:               nil,
		TLSHandshakeTimeout: 15 * time.Second,
		IdleConnTimeout:     90 * time.Second,
		MaxIdleConns:        4,
	}
	if proxy != "" {
		proxyURL, err := url.Parse(proxy)
		if err != nil {
			return nil, fmt.Errorf("адрес прокси не разобрать: %w", err)
		}
		transport.Proxy = http.ProxyURL(proxyURL)
	}
	if caBundle != "" {
		pem, err := os.ReadFile(caBundle)
		if err != nil {
			return nil, fmt.Errorf("корневые сертификаты портала не читаются: %w", err)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(pem) {
			return nil, errors.New("в файле корневых сертификатов портала нет ни одного сертификата")
		}
		transport.TLSClientConfig = &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}
	}
	return &portal{base: base, http: &http.Client{Transport: transport, Timeout: 30 * time.Second},
		files: &http.Client{Transport: transport, Timeout: 5 * time.Minute}, signer: s}, nil
}

func (p *portal) post(ctx context.Context, path string, payload any, out any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	return p.send(ctx, p.http, path, body, "application/json", out)
}

// upload — файл сообщения порталу. 404 — сообщение туда ещё не дошло (повторить позже).
func (p *portal) upload(ctx context.Context, path string, data []byte) error {
	return p.send(ctx, p.files, path, data, "application/octet-stream", nil)
}

func (p *portal) send(ctx context.Context, client *http.Client, path string, body []byte, contentType string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, p.base+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", contentType)
	req.Header.Set("User-Agent", "otp-wa-bridge/"+bridgeVersion)
	for name, value := range p.signer.headers(http.MethodPost, path, body, time.Now(), randomNonce()) {
		req.Header.Set(name, value)
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		snippet := strings.TrimSpace(string(data))
		if len(snippet) > 200 {
			snippet = snippet[:200]
		}
		return &httpError{Status: resp.StatusCode, Body: snippet}
	}
	if out == nil {
		return nil
	}
	return json.Unmarshal(data, out)
}

type pollCommand struct {
	ID     string `json:"id"`
	Action string `json:"action"`
}

type pollReply struct {
	Command     *pollCommand `json:"command"`
	NextPollSec int          `json:"next_poll_sec"`
}
