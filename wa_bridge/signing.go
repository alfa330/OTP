package main

// Подпись запросов к порталу — тот же протокол, что у моста «Касаний» и автоинформатора
// (cdr/agent_auth.py — единственное определение; здесь его точное повторение, сверяемое тестом
// с вектором, подписанным самим Python-модулем):
//
//	OTP-AGENT-v1 \n POST \n /api/hiring/wa/agent/poll \n <время> \n <одноразовый номер> \n <sha256(тело)>
//
// Ключ — тот же закрытый ключ шлюза (32 байта Ed25519 в base64), открытая часть уже записана на
// портале в CDR_AGENT_KEYS: новый ключ на Render заводить не нужно.

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

const (
	signScheme      = "OTP-AGENT-v1"
	headerKey       = "X-Agent-Key"
	headerTime      = "X-Agent-Time"
	headerNonce     = "X-Agent-Nonce"
	headerSignature = "X-Agent-Signature"
)

type signer struct {
	private ed25519.PrivateKey
	keyID   string
}

func canonical(method, path string, stamp int64, nonce string, body []byte) []byte {
	digest := sha256.Sum256(body)
	return []byte(strings.Join([]string{
		signScheme, strings.ToUpper(method), path, strconv.FormatInt(stamp, 10), nonce, hex.EncodeToString(digest[:]),
	}, "\n"))
}

func newSigner(seedB64 string) (*signer, error) {
	seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(seedB64))
	if err != nil {
		return nil, errors.New("закрытый ключ шлюза должен быть base64")
	}
	if len(seed) != ed25519.SeedSize {
		return nil, fmt.Errorf("закрытый ключ Ed25519 — это 32 байта, получено %d", len(seed))
	}
	private := ed25519.NewKeyFromSeed(seed)
	public := private.Public().(ed25519.PublicKey)
	sum := sha256.Sum256(public)
	return &signer{private: private, keyID: hex.EncodeToString(sum[:])[:16]}, nil
}

func loadSigner(path string) (*signer, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("файл ключа %s не читается: %w", path, err)
	}
	return newSigner(string(data))
}

func randomNonce() string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		panic(err) // без случайных чисел подпись повторяема — работать дальше нельзя
	}
	return hex.EncodeToString(buf)
}

// headers — заголовки подписи одного запроса; тело — ровно те байты, что уйдут в сеть.
func (s *signer) headers(method, path string, body []byte, now time.Time, nonce string) map[string]string {
	stamp := now.Unix()
	signature := ed25519.Sign(s.private, canonical(method, path, stamp, nonce, body))
	return map[string]string{
		headerKey:       s.keyID,
		headerTime:      strconv.FormatInt(stamp, 10),
		headerNonce:     nonce,
		headerSignature: base64.StdEncoding.EncodeToString(signature),
	}
}
