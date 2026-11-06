package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
)

func TestParkSurvivesRestartAndKeepsFirstReason(t *testing.T) {
	path := filepath.Join(t.TempDir(), "guard.json")
	g, _ := loadGuard(path)
	now := time.Date(2026, 11, 6, 10, 0, 0, 0, time.UTC)
	if fresh, err := g.park("temp_ban", 101, now.Add(24*time.Hour), now); !fresh || err != nil {
		t.Fatalf("первая остановка: %v %v", fresh, err)
	}
	if fresh, _ := g.park("replaced", 0, time.Time{}, now); fresh {
		t.Fatal("вторая остановка затёрла первую")
	}
	again, _ := loadGuard(path)
	park := again.parked()
	if park == nil || park.Reason != "temp_ban" || park.Code != 101 || park.Since != "2026-11-06T15:00:00" || park.Until != "2026-11-07T15:00:00" {
		t.Fatalf("после перезапуска: %#v", park)
	}
	if err := again.resume(); err != nil || again.parked() != nil {
		t.Fatal("«Возобновить» не сняло остановку")
	}
}

func TestBrokenGuardFileStaysParked(t *testing.T) {
	path := filepath.Join(t.TempDir(), "guard.json")
	_ = os.WriteFile(path, []byte("{битый"), 0o600)
	g, err := loadGuard(path)
	if err != nil || g.parked() == nil {
		t.Fatal("битый файл молча снял остановку")
	}
}

func TestStartFuseAndPairFailures(t *testing.T) {
	g, _ := loadGuard(filepath.Join(t.TempDir(), "guard.json"))
	now := time.Now()
	for i := 0; i < maxStartsPerHour; i++ {
		if tooMany, _ := g.recordStart(now.Add(time.Duration(i) * time.Minute)); tooMany {
			t.Fatalf("сработал на %d-м старте", i+1)
		}
	}
	if tooMany, _ := g.recordStart(now.Add(10 * time.Minute)); !tooMany {
		t.Fatal("седьмой старт за час не остановил")
	}
	if tooMany, _ := g.recordStart(now.Add(3 * time.Hour)); tooMany {
		t.Fatal("старые старты не забылись")
	}
	if tooMany, _ := g.pairFailed(); tooMany {
		t.Fatal("одна неудачная привязка уже стоп")
	}
	if tooMany, _ := g.pairFailed(); !tooMany {
		t.Fatal("вторая неудачная привязка не стоп")
	}
	_ = g.paired(now)
	if g.snapshot().PairFailures != 0 || g.snapshot().PhoneSeenAt == "" {
		t.Fatal("удачная привязка не сбросила счёт")
	}
	var drops dropCounter
	for i := 0; i < maxDropsPerHour; i++ {
		drops.add(now)
	}
	if drops.add(now) <= maxDropsPerHour || drops.add(now.Add(2*time.Hour)) != 1 {
		t.Fatal("счёт обрывов за час неверен")
	}
}

func TestSpoolRetriesAndDropsPoisonBatch(t *testing.T) {
	sp, err := openSpool(filepath.Join(t.TempDir(), "spool.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer sp.db.Close()
	for _, id := range []string{"a", "b", "c"} {
		_ = sp.push(map[string]any{"type": "message", "id": id})
	}
	var calls atomic.Int32
	delivered := make(chan int, 4)
	send := func(_ context.Context, events []json.RawMessage) error {
		switch calls.Add(1) {
		case 1:
			return errors.New("сеть")
		case 2:
			return &httpError{Status: 400, Body: "битая"}
		}
		delivered <- len(events)
		return nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { sp.flush(ctx, send, func(string, ...any) {}); close(done) }()
	waitFor(t, func() bool { return calls.Load() == 2 && sp.pending() == 0 })
	// Пачку после ошибки сети отправили повтором; отвергнутая порталом (400) не заперла очередь:
	// следующее событие уходит сразу, без ожидания.
	_ = sp.push(map[string]any{"type": "message", "id": "next"})
	select {
	case n := <-delivered:
		if n != 1 {
			t.Fatalf("ушло %d событий, ждали одно", n)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("после отказа очередь не пошла")
	}
	waitFor(t, func() bool { return sp.pending() == 0 })
	cancel()
	<-done
}

func waitFor(t *testing.T, ok func() bool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for !ok() {
		if time.Now().After(deadline) {
			t.Fatal("не дождались")
		}
		time.Sleep(20 * time.Millisecond)
	}
}
