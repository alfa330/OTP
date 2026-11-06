package main

import (
	"testing"
	"time"
)

// Вектор подписан самим Python-модулем шлюза (cdr_bridge.signing.Signer поверх cdr/agent_auth.py):
// Ed25519 детерминирован, поэтому совпадение подписи байт в байт доказывает, что канонический вид
// строки и идентификатор ключа здесь те же, что проверяет портал.
func TestSignatureMatchesPythonBridge(t *testing.T) {
	s, err := newSigner("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
	if err != nil {
		t.Fatal(err)
	}
	got := s.headers("POST", "/api/hiring/wa/agent/poll", []byte(`{"state":"unpaired"}`),
		time.Unix(1792047600, 0), "00112233445566778899aabbccddeeff")
	want := map[string]string{
		headerKey:       "56475aa75463474c",
		headerTime:      "1792047600",
		headerNonce:     "00112233445566778899aabbccddeeff",
		headerSignature: "NHMdLLCbK8V3XLO2nz7Dbi4kuVANwrMgvuguWXHZtOToyQvhxxAExe3ByQBww0TlaDdvt4XgG9J9OeqjX4MgBA==",
	}
	for name, value := range want {
		if got[name] != value {
			t.Fatalf("%s = %q, ждали %q", name, got[name], value)
		}
	}
}

func TestSignerRejectsBadKeys(t *testing.T) {
	if _, err := newSigner("не base64"); err == nil {
		t.Fatal("ключ не base64 принят")
	}
	if _, err := newSigner("AAEC"); err == nil {
		t.Fatal("короткий ключ принят")
	}
	if len(randomNonce()) != 32 {
		t.Fatal("одноразовый номер не 32 hex-знака (портал ждёт 16–64)")
	}
}
