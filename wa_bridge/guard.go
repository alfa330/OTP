package main

// Предохранители номера — то, что переживает перезапуск контейнера (файл на томе):
//
//   * остановка (park): на любой сигнал ограничения или бана сервис сам больше не подключается —
//     решает человек кнопкой «Возобновить» в портале («упорные повторы превращают ограничение в бан»);
//   * частые старты: контейнер, который падает и поднимается по кругу, — тот же шторм подключений;
//   * неудачные привязки: после двух подряд QR больше не крутим;
//   * когда телефон с номером последний раз был в сети: через 14 дней без него привязка слетает.

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"time"
)

const (
	maxStartsPerHour = 6
	maxDropsPerHour  = 12
	maxPairFailures  = 2
)

// almaty — время портала: наивное время Алматы (Казахстан с 2024 года целиком в UTC+5).
var almaty = time.FixedZone("Asia/Almaty", 5*3600)

func stamp(t time.Time) string { return t.In(almaty).Format("2006-01-02T15:04:05") }

type parkInfo struct {
	Reason string `json:"reason"`
	Code   int    `json:"code,omitempty"`
	Since  string `json:"since"`
	Until  string `json:"until,omitempty"`
}

type guardState struct {
	Park           *parkInfo `json:"park,omitempty"`
	Starts         []int64   `json:"starts,omitempty"`
	PairFailures   int       `json:"pair_failures,omitempty"`
	PhoneSeenAt    string    `json:"phone_seen_at,omitempty"`
	ConnectedSince string    `json:"connected_since,omitempty"`
}

type guard struct {
	path string
	mu   sync.Mutex
	st   guardState
}

func loadGuard(path string) (*guard, error) {
	g := &guard{path: path}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return g, nil
	} else if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(data, &g.st); err != nil {
		// Битый файл не должен молча снять остановку: считаем сервис остановленным.
		g.st = guardState{Park: &parkInfo{Reason: "start_fuse", Since: stamp(time.Now())}}
	}
	return g, nil
}

func (g *guard) saveLocked() error {
	data, err := json.MarshalIndent(g.st, "", "  ")
	if err != nil {
		return err
	}
	tmp := g.path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Clean(g.path))
}

func (g *guard) snapshot() guardState {
	g.mu.Lock()
	defer g.mu.Unlock()
	out := g.st
	if g.st.Park != nil {
		park := *g.st.Park
		out.Park = &park
	}
	return out
}

func (g *guard) parked() *parkInfo {
	return g.snapshot().Park
}

// park — остановить; первая причина остаётся (вторая остановка поверх первой её не затирает).
func (g *guard) park(reason string, code int, until time.Time, now time.Time) (bool, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.st.Park != nil {
		return false, nil
	}
	info := &parkInfo{Reason: reason, Code: code, Since: stamp(now)}
	if !until.IsZero() {
		info.Until = stamp(until)
	}
	g.st.Park = info
	return true, g.saveLocked()
}

// resume — человек снял остановку: предохранители начинают счёт заново.
func (g *guard) resume() error {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.st.Park = nil
	g.st.Starts = nil
	g.st.PairFailures = 0
	return g.saveLocked()
}

// recordStart — отметить старт процесса; true — стартов за час больше предела.
func (g *guard) recordStart(now time.Time) (bool, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	cutoff := now.Add(-time.Hour).Unix()
	kept := g.st.Starts[:0]
	for _, at := range g.st.Starts {
		if at > cutoff {
			kept = append(kept, at)
		}
	}
	g.st.Starts = append(kept, now.Unix())
	return len(g.st.Starts) > maxStartsPerHour, g.saveLocked()
}

// pairFailed — неудачная привязка; true — их уже две подряд.
func (g *guard) pairFailed() (bool, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.st.PairFailures++
	return g.st.PairFailures >= maxPairFailures, g.saveLocked()
}

func (g *guard) paired(now time.Time) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.st.PairFailures = 0
	g.st.PhoneSeenAt = stamp(now) // только что сканировали QR этим телефоном
	return g.saveLocked()
}

func (g *guard) connected(now time.Time) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.st.ConnectedSince = stamp(now)
	return g.saveLocked()
}

// phoneSeen — телефон с номером был в сети (ответ или прочтение с него); пишем не чаще раза в час.
func (g *guard) phoneSeen(now time.Time) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	if previous, err := time.ParseInLocation("2006-01-02T15:04:05", g.st.PhoneSeenAt, almaty); err == nil &&
		now.Sub(previous) < time.Hour {
		return nil
	}
	g.st.PhoneSeenAt = stamp(now)
	return g.saveLocked()
}

// forget — номер отвязан: прошлое подключение и телефон больше ни при чём.
func (g *guard) forget() error {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.st.ConnectedSince = ""
	g.st.PhoneSeenAt = ""
	return g.saveLocked()
}

// dropCounter — обрывы установленного соединения за последний час (в памяти: перезапуски ловит recordStart).
type dropCounter struct {
	mu    sync.Mutex
	times []time.Time
}

func (d *dropCounter) add(now time.Time) int {
	d.mu.Lock()
	defer d.mu.Unlock()
	cutoff := now.Add(-time.Hour)
	kept := d.times[:0]
	for _, at := range d.times {
		if at.After(cutoff) {
			kept = append(kept, at)
		}
	}
	d.times = append(kept, now)
	return len(d.times)
}

func (d *dropCounter) reset() {
	d.mu.Lock()
	d.times = nil
	d.mu.Unlock()
}
