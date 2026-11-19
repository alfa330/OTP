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
	db         *sql.DB
	wake       chan struct{}
	mediaWake  chan struct{}
	outboxWake chan struct{}
}

// outboxJob — сообщение портала в очереди отправки (sender.go): тело — JSON элемента опроса.
type outboxJob struct {
	ID       int64
	Body     string
	Attempts int
}

// mediaJob — файл сообщения в очереди: часть protobuf с ключами (по ней whatsmeow качает файл) и попытки.
type mediaJob struct {
	ID       int64
	WAID     string
	Kind     string
	Part     []byte
	Attempts int
}

func openSpool(path string) (*spool, error) {
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	for _, ddl := range []string{
		`CREATE TABLE IF NOT EXISTS events (
			id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, created_at INTEGER NOT NULL)`,
		// Файлы ждут своей очереди: событие о сообщении уходит сразу, файл — когда скачался.
		`CREATE TABLE IF NOT EXISTS media (
			id INTEGER PRIMARY KEY AUTOINCREMENT, wa_id TEXT NOT NULL, kind TEXT NOT NULL, part BLOB NOT NULL,
			attempts INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL, created_at INTEGER NOT NULL)`,
		// Ответы из портала: id — номер сообщения в портале; отправленные помнятся неделю (done_at), чтобы
		// повтор того же номера в опросе не ушёл кандидату второй раз.
		`CREATE TABLE IF NOT EXISTS outbox (
			id INTEGER PRIMARY KEY, body TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
			next_at INTEGER NOT NULL, done_at INTEGER, created_at INTEGER NOT NULL)`,
	} {
		if _, err := db.Exec(ddl); err != nil {
			db.Close()
			return nil, err
		}
	}
	return &spool{db: db, wake: make(chan struct{}, 1), mediaWake: make(chan struct{}, 1),
		outboxWake: make(chan struct{}, 1)}, nil
}

// pushMedia — файл в очередь; первая попытка — чуть позже события, чтобы сообщение успело дойти до портала.
func (s *spool) pushMedia(job mediaJob, at time.Time) error {
	if _, err := s.db.Exec("INSERT INTO media (wa_id, kind, part, next_at, created_at) VALUES (?, ?, ?, ?, ?)",
		job.WAID, job.Kind, job.Part, at.Unix(), time.Now().Unix()); err != nil {
		return err
	}
	_, _ = s.db.Exec("DELETE FROM media WHERE id <= (SELECT MAX(id) FROM media) - ?", spoolLimit)
	select {
	case s.mediaWake <- struct{}{}:
	default:
	}
	return nil
}

// nextMedia — старейший файл, чья очередь подошла, или nil.
func (s *spool) nextMedia(now time.Time) (*mediaJob, error) {
	job := &mediaJob{}
	err := s.db.QueryRow("SELECT id, wa_id, kind, part, attempts FROM media WHERE next_at <= ? ORDER BY id LIMIT 1",
		now.Unix()).Scan(&job.ID, &job.WAID, &job.Kind, &job.Part, &job.Attempts)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return job, nil
}

// nextMediaAt — когда подойдёт ближайший файл (false — очередь пуста).
func (s *spool) nextMediaAt() (time.Time, bool) {
	var at sql.NullInt64
	if err := s.db.QueryRow("SELECT MIN(next_at) FROM media").Scan(&at); err != nil || !at.Valid {
		return time.Time{}, false
	}
	return time.Unix(at.Int64, 0), true
}

func (s *spool) delayMedia(id int64, attempts int, at time.Time) error {
	_, err := s.db.Exec("UPDATE media SET attempts = ?, next_at = ? WHERE id = ?", attempts, at.Unix(), id)
	return err
}

func (s *spool) dropMedia(id int64) error {
	_, err := s.db.Exec("DELETE FROM media WHERE id = ?", id)
	return err
}

// pushOutbox — сообщение портала в очередь отправки; false — этот номер уже был (повтор опроса).
func (s *spool) pushOutbox(id int64, body string, now time.Time) (bool, error) {
	result, err := s.db.Exec("INSERT OR IGNORE INTO outbox (id, body, next_at, created_at) VALUES (?, ?, ?, ?)",
		id, body, now.Unix(), now.Unix())
	if err != nil {
		return false, err
	}
	added, _ := result.RowsAffected()
	if added > 0 {
		select {
		case s.outboxWake <- struct{}{}:
		default:
		}
	}
	return added > 0, nil
}

// nextOutbox — старейшее неотправленное сообщение, чья очередь подошла, или nil.
func (s *spool) nextOutbox(now time.Time) (*outboxJob, error) {
	job := &outboxJob{}
	err := s.db.QueryRow("SELECT id, body, attempts FROM outbox WHERE done_at IS NULL AND next_at <= ? ORDER BY id LIMIT 1",
		now.Unix()).Scan(&job.ID, &job.Body, &job.Attempts)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return job, nil
}

// nextOutboxAt — когда подойдёт ближайшее отложенное (false — очередь пуста).
func (s *spool) nextOutboxAt() (time.Time, bool) {
	var at sql.NullInt64
	if err := s.db.QueryRow("SELECT MIN(next_at) FROM outbox WHERE done_at IS NULL").Scan(&at); err != nil || !at.Valid {
		return time.Time{}, false
	}
	return time.Unix(at.Int64, 0), true
}

func (s *spool) delayOutbox(id int64, attempts int, at time.Time) error {
	_, err := s.db.Exec("UPDATE outbox SET attempts = ?, next_at = ? WHERE id = ?", attempts, at.Unix(), id)
	return err
}

func (s *spool) doneOutbox(id int64, now time.Time) error {
	_, err := s.db.Exec("UPDATE outbox SET done_at = ?, body = '' WHERE id = ?", now.Unix(), id)
	return err
}

// pruneOutbox — забыть номера, отправленные раньше before (портал их больше не предложит).
func (s *spool) pruneOutbox(before time.Time) error {
	_, err := s.db.Exec("DELETE FROM outbox WHERE done_at IS NOT NULL AND done_at < ?", before.Unix())
	return err
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
