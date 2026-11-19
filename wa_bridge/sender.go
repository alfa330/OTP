package main

// Ответы кандидатам из портала (владелец 19.11.2026: окно «Чаты» в «Воронке найма» — «сделать интерфейс
// обработки чата ватсапа прямо как в разделе чаты ОП»). Рекрутер пишет в портале, портал отдаёт сообщения в
// ответе на опрос (send), сервис отправляет их связанным устройством номера и сообщает итог событием:
// sent (id сообщения WhatsApp) или send_failed (причина). Отметки доставки и прочтения — событием receipt
// (bridge.go).
//
// Правила, которые держит сервис (номер — живой, его нельзя потерять):
//   - только личные чаты: группы, рассылки, устройства — мимо (портал и не даёт других, но проверяем сами);
//   - по одному, с паузой sendPause плюс случайные до sendJitter (ровный шаг — примета бота), не больше
//     sendPerMinute попыток в минуту и sendPerHour в час — излишек ждёт своей очереди, а не уходит залпом;
//   - сообщение с одним номером из портала уходит один раз: очередь на диске помнит отправленные номера
//     неделю, повтор того же номера в опросе (потерянный ответ) отбрасывается; у всех попыток одного
//     сообщения один id WhatsApp — повтор после сбоя «отправлено ли?» кандидату дублем не придёт;
//   - позже срока (expires_in из опроса, у портала — 30 минут с момента, когда рекрутер нажал «Отправить»)
//     не отправляет: «Сегодня в 14:00?» не должно прийти кандидату завтра;
//   - отказ сервера WhatsApp — сразу ошибка без повторов (повторяет человек); «слишком часто» (429) — пауза
//     всей очереди; повторяем только сетевые сбои.

import (
	"context"
	"encoding/json"
	"errors"
	"math/rand/v2"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"go.mau.fi/whatsmeow"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const (
	sendPause       = 1500 * time.Millisecond
	sendJitter      = 2500 * time.Millisecond
	sendCooldown    = 5 * time.Minute // после «слишком часто» от сервера
	sendDefaultLife = 30 * time.Minute
	sendPerMinute   = 20
	sendPerHour     = 300
	sendTimeout     = 90 * time.Second
	sendKeep        = 7 * 24 * time.Hour
	sendMaxAttempts = 3
	sendIdle        = 30 * time.Second
	maxSendText     = 4096
	maxSendFile     = 16 << 20
)

// Паузы повтора после сетевого сбоя: 20 с, потом минута.
var sendDelays = []time.Duration{20 * time.Second, time.Minute}

type sendQuote struct {
	ID     string `json:"id"`
	FromMe bool   `json:"from_me"`
	Text   string `json:"text"`
}

type sendMedia struct {
	Mime string `json:"mime"`
	Size int64  `json:"size"`
	Name string `json:"name"`
}

// sendItem — сообщение из ответа портала на опрос. Deadline и MessageID сервис ставит сам, когда кладёт его
// в очередь: срок — от expires_in портала (секунды), id WhatsApp — один на все попытки.
type sendItem struct {
	ID        int64      `json:"id"`
	Chat      string     `json:"chat"`
	Kind      string     `json:"kind"`
	Text      string     `json:"text"`
	Quote     *sendQuote `json:"quote,omitempty"`
	Media     *sendMedia `json:"media,omitempty"`
	ExpiresIn int64      `json:"expires_in,omitempty"`
	Deadline  int64      `json:"deadline,omitempty"`
	MessageID string     `json:"message_id,omitempty"`
}

var sendKinds = map[string]bool{"text": true, "image": true, "video": true, "audio": true, "document": true}

// cleanSend — элемент, который можно ставить в очередь: известный вид, личный чат, текст или файл в пределах.
// Битая цитата просто отбрасывается — сообщение уходит без неё.
func cleanSend(item sendItem) (sendItem, error) {
	if item.ID <= 0 {
		return item, errors.New("нет номера сообщения портала")
	}
	if !sendKinds[item.Kind] {
		return item, errors.New("неизвестный вид сообщения: " + item.Kind)
	}
	if _, ok := resendChat(item.Chat); !ok {
		return item, errors.New("не личный чат: " + item.Chat)
	}
	if !utf8.ValidString(item.Text) || len([]rune(item.Text)) > maxSendText {
		return item, errors.New("текст длиннее предела")
	}
	if item.Kind == "text" && strings.TrimSpace(item.Text) == "" {
		return item, errors.New("пустой текст")
	}
	if item.Kind != "text" && (item.Media == nil || item.Media.Size <= 0 || item.Media.Size > maxSendFile) {
		return item, errors.New("файл без размера или больше 16 МБ")
	}
	if item.Quote != nil && !mediaIDPattern.MatchString(item.Quote.ID) {
		item.Quote = nil
	}
	return item, nil
}

// sendError — отказ с причиной для портала (hiring/wa_chat.FAILURE_TEXTS). cooldown — сервер просит не спешить:
// очередь целиком ждёт sendCooldown.
type sendError struct {
	reason    string
	err       error
	transient bool
	cooldown  bool
}

func (e *sendError) Error() string { return e.reason + ": " + e.err.Error() }

func (e *sendError) Unwrap() error { return e.err }

// classifySend — повторять ли: сеть и нет связи — да; ответ сервера WhatsApp — нет.
func classifySend(err error) *sendError {
	var known *sendError
	if errors.As(err, &known) {
		return known
	}
	text := strings.ToLower(err.Error())
	switch {
	case errors.Is(err, whatsmeow.ErrIQRateOverLimit) || strings.Contains(text, "rate-overlimit") ||
		strings.Contains(text, " 429"):
		return &sendError{reason: "rate", err: err, transient: true, cooldown: true}
	case errors.Is(err, context.Canceled):
		// Сервис останавливается посреди отправки: итога нет — повтор после старта тем же id.
		return &sendError{reason: "not_connected", err: err, transient: true}
	case errors.Is(err, whatsmeow.ErrNotConnected), errors.Is(err, whatsmeow.ErrNotLoggedIn),
		errors.Is(err, whatsmeow.ErrIQTimedOut), errors.Is(err, context.DeadlineExceeded):
		return &sendError{reason: "not_connected", err: err, transient: true}
	case errors.Is(err, whatsmeow.ErrServerReturnedError):
		return &sendError{reason: "rejected", err: err}
	}
	if strings.Contains(text, "websocket") || strings.Contains(text, "connection") || strings.Contains(text, "timeout") ||
		strings.Contains(text, "eof") {
		return &sendError{reason: "not_connected", err: err, transient: true}
	}
	return &sendError{reason: "rejected", err: err}
}

type sender struct {
	spool *spool
	log   waLog.Logger
	ready func() bool // соединение с WhatsApp есть
	// deliver — отправить одно сообщение (bridge.sendItem): id WhatsApp и время сервера.
	deliver func(ctx context.Context, item sendItem) (string, time.Time, error)
	pause   time.Duration
	jitter  func() time.Duration
	now     func() time.Time
	newID   func() string

	mu       sync.Mutex
	sent     []time.Time // попытки отправки за последний час
	nextAt   time.Time   // раньше этого — следующей попытки нет (пауза между сообщениями)
	coolTill time.Time   // сервер ответил «слишком часто»
}

func newSender(sp *spool, log waLog.Logger, ready func() bool,
	deliver func(ctx context.Context, item sendItem) (string, time.Time, error)) *sender {
	return &sender{spool: sp, log: log, ready: ready, deliver: deliver, pause: sendPause, now: time.Now,
		jitter: func() time.Duration { return rand.N(sendJitter) },
		newID:  func() string { return string(whatsmeow.GenerateMessageID()) }}
}

// offer — список из ответа портала: в очередь на диске; уже известные номера (повтор опроса) отбрасываются.
func (s *sender) offer(items []sendItem) int {
	added := 0
	for _, raw := range items {
		item, err := cleanSend(raw)
		if err != nil {
			s.log.Warnf("сообщение портала %d не принято: %v", item.ID, err)
			s.report(map[string]any{"type": "send_failed", "outbox": item.ID, "reason": "rejected", "error": err.Error()})
			continue
		}
		life := time.Duration(item.ExpiresIn) * time.Second
		if life <= 0 || life > sendDefaultLife {
			life = sendDefaultLife
		}
		item.Deadline = s.now().Add(life).Unix()
		item.MessageID = s.newID()
		body, err := json.Marshal(item)
		if err != nil {
			continue
		}
		fresh, err := s.spool.pushOutbox(item.ID, string(body), s.now())
		if err != nil {
			s.log.Errorf("очередь отправки: сообщение %d не записано: %v", item.ID, err)
			continue
		}
		if fresh {
			added++
		}
	}
	return added
}

// allowed — можно ли отправить сейчас: пауза после прошлой попытки, «слишком часто» от сервера, пределы
// минуты и часа; иначе — сколько ждать.
func (s *sender) allowed(now time.Time) (bool, time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if now.Before(s.coolTill) {
		return false, s.coolTill.Sub(now)
	}
	if now.Before(s.nextAt) {
		return false, s.nextAt.Sub(now)
	}
	keep := s.sent[:0]
	for _, at := range s.sent {
		if now.Sub(at) < time.Hour {
			keep = append(keep, at)
		}
	}
	s.sent = keep
	if len(s.sent) >= sendPerHour {
		return false, time.Hour - now.Sub(s.sent[0])
	}
	recent := 0
	for _, at := range s.sent {
		if now.Sub(at) < time.Minute {
			recent++
		}
	}
	if recent >= sendPerMinute {
		for _, at := range s.sent {
			if now.Sub(at) < time.Minute {
				return false, time.Minute - now.Sub(at)
			}
		}
	}
	return true, 0
}

// mark — попытка отправки (удачная или нет): в пределы и пауза до следующей.
func (s *sender) mark(at time.Time) {
	s.mu.Lock()
	s.sent = append(s.sent, at)
	s.nextAt = at.Add(s.pause + s.jitter())
	s.mu.Unlock()
}

func (s *sender) coolDown(at time.Time) {
	s.mu.Lock()
	s.coolTill = at.Add(sendCooldown)
	s.mu.Unlock()
}

func (s *sender) report(event map[string]any) {
	if err := s.spool.push(event); err != nil {
		s.log.Errorf("очередь: итог отправки не записан: %v", err)
	}
}

func (s *sender) run(ctx context.Context) {
	prune := time.NewTicker(time.Hour)
	defer prune.Stop()
	for {
		wait := sendIdle
		job, err := s.spool.nextOutbox(s.now())
		if err != nil {
			s.log.Warnf("очередь отправки не читается: %v", err)
		}
		if job != nil && s.ready() {
			if ok, after := s.allowed(s.now()); !ok {
				wait = max(after, 200*time.Millisecond)
			} else {
				s.process(ctx, job)
				wait = 200 * time.Millisecond // следующую попытку всё равно держит allowed (пауза)
			}
		} else if next, ok := s.spool.nextOutboxAt(); ok && job == nil {
			if until := time.Until(next); until < wait {
				wait = max(until, 500*time.Millisecond)
			}
		} else if job != nil {
			wait = 2 * time.Second // ждём связи с WhatsApp
		}
		select {
		case <-ctx.Done():
			return
		case <-s.spool.outboxWake:
		case <-prune.C:
			_ = s.spool.pruneOutbox(s.now().Add(-sendKeep))
		case <-time.After(wait):
		}
	}
}

func (s *sender) process(ctx context.Context, job *outboxJob) {
	var item sendItem
	if err := json.Unmarshal([]byte(job.Body), &item); err != nil {
		s.finish(job, map[string]any{"type": "send_failed", "outbox": job.ID, "reason": "rejected", "error": "битая запись очереди"})
		return
	}
	if item.Deadline > 0 && s.now().Unix() > item.Deadline {
		s.log.Warnf("сообщение портала %d не отправлено: срок прошёл", item.ID)
		s.finish(job, map[string]any{"type": "send_failed", "outbox": item.ID, "reason": "expired",
			"error": "срок отправки прошёл"})
		return
	}
	if item.MessageID == "" {
		item.MessageID = s.newID() // запись очереди до 1.3.0 правок: id на эту попытку
	}
	ctx, cancel := context.WithTimeout(ctx, sendTimeout)
	defer cancel()
	s.mark(s.now())
	waID, ts, err := s.deliver(ctx, item)
	if err == nil {
		if ts.IsZero() {
			ts = s.now()
		}
		s.log.Infof("сообщение портала %d отправлено (%s)", item.ID, waID)
		s.finish(job, map[string]any{"type": "sent", "outbox": item.ID, "id": waID, "ts": ts.Unix()})
		return
	}
	failure := classifySend(err)
	if failure.cooldown {
		s.coolDown(s.now())
		s.log.Warnf("WhatsApp просит не спешить — очередь отправки ждёт %s", sendCooldown)
	}
	attempts := job.Attempts + 1
	if failure.transient && attempts < sendMaxAttempts {
		delay := sendDelays[min(attempts-1, len(sendDelays)-1)]
		s.log.Warnf("сообщение портала %d: попытка %d не удалась (%v) — повтор через %s", item.ID, attempts, err, delay)
		if dbErr := s.spool.delayOutbox(job.ID, attempts, s.now().Add(delay)); dbErr != nil {
			s.log.Warnf("очередь отправки: попытка не записана: %v", dbErr)
		}
		return
	}
	s.log.Warnf("сообщение портала %d не отправлено: %v", item.ID, err)
	s.finish(job, map[string]any{"type": "send_failed", "outbox": item.ID, "reason": failure.reason,
		"error": trimError(failure.err)})
}

// finish — итог в очередь событий и отметка «сделано» (номер помнится неделю — от повторов портала).
func (s *sender) finish(job *outboxJob, event map[string]any) {
	s.report(event)
	if err := s.spool.doneOutbox(job.ID, s.now()); err != nil {
		s.log.Warnf("очередь отправки: не отметить %d: %v", job.ID, err)
	}
}

func trimError(err error) string {
	if err == nil {
		return ""
	}
	text := strings.Join(strings.Fields(err.Error()), " ")
	if len(text) > 300 {
		text = text[:300]
	}
	return text
}
