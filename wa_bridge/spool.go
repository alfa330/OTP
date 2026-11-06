package main

// Очередь событий на диске (SQLite рядом с сессией): WhatsApp считает сообщение доставленным, как
// только его принял сервис, поэтому оно не должно пропасть, пока портал недоступен (выкладка на
// Render, обрыв сети). Отправка — пачками; повтор пачки портал переносит спокойно (id сообщения).

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"
)

const (
	spoolBatch = 50
	spoolLimit = 100000 // защита диска: дальше старейшие уходят (на деле не встречается)
)

type spool struct {
	db   *sql.DB
	wake chan struct{}
}

func openSpool(path string) (*spool, error) {
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS events (
		id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, created_at INTEGER NOT NULL)`); err != nil {
		db.Close()
		return nil, err
	}
	return &spool{db: db, wake: make(chan struct{}, 1)}, nil
}

func (s *spool) push(event map[string]any) error {
	body, err := json.Marshal(event)
	if err != nil {
		return err
	}
	if _, err := s.db.Exec("INSERT INTO events (body, created_at) VALUES (?, ?)", string(body), time.Now().Unix()); err != nil {
		return err
	}
	_, _ = s.db.Exec("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?", spoolLimit)
	select {
	case s.wake <- struct{}{}:
	default:
	}
	return nil
}

func (s *spool) batch(limit int) ([]int64, []json.RawMessage, error) {
	rows, err := s.db.Query("SELECT id, body FROM events ORDER BY id LIMIT ?", limit)
	if err != nil {
		return nil, nil, err
	}
	defer rows.Close()
	var ids []int64
	var bodies []json.RawMessage
	for rows.Next() {
		var id int64
		var body string
		if err := rows.Scan(&id, &body); err != nil {
			return nil, nil, err
		}
		ids = append(ids, id)
		bodies = append(bodies, json.RawMessage(body))
	}
	return ids, bodies, rows.Err()
}

func (s *spool) remove(ids []int64) error {
	if len(ids) == 0 {
		return nil
	}
	_, err := s.db.Exec("DELETE FROM events WHERE id <= ?", ids[len(ids)-1])
	return err
}

func (s *spool) pending() int {
	var count int
	_ = s.db.QueryRow("SELECT COUNT(*) FROM events").Scan(&count)
	return count
}

// flush — отправлять очередь, пока она не пуста или пока не отменят. Ошибка сети — пауза
// с ростом до минуты; пачку, которую портал признал битой (400), не держим: она заперла бы
// всю очередь за собой.
func (s *spool) flush(ctx context.Context, send func(ctx context.Context, events []json.RawMessage) error, logf func(string, ...any)) {
	backoff := time.Second
	for {
		ids, bodies, err := s.batch(spoolBatch)
		if err == nil && len(ids) > 0 {
			err = send(ctx, bodies)
			var refused *httpError
			switch {
			case err == nil:
				backoff = time.Second
				if removeErr := s.remove(ids); removeErr != nil {
					logf("очередь: не удалить отправленное: %v", removeErr)
				}
				continue
			case errors.As(err, &refused) && refused.Status == 400:
				logf("очередь: портал отверг пачку из %d событий (%s) — пропускаем", len(ids), refused.Body)
				_ = s.remove(ids)
				continue
			}
		}
		if err != nil {
			logf("очередь: отправка не удалась (%v), в очереди %d", err, s.pending())
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
			}
			if backoff < time.Minute {
				backoff *= 2
			}
			continue
		}
		select {
		case <-ctx.Done():
			return
		case <-s.wake:
		case <-time.After(5 * time.Second):
		}
	}
}
