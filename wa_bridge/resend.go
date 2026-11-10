package main

// Файлы старых сообщений (владелец 10.11.2026: «фото отображается просто как фото, и непонятно, как его
// посмотреть»). До версии 1.1.0 сервис файлы не качал, а ключ к файлу приходит только вместе с самим
// сообщением — ни у портала, ни у сервиса его нет. Он есть у телефона номера, и сервис просит его у
// телефона — запросом, каким WhatsApp Web подгружает старую переписку (HISTORY_SYNC_ON_DEMAND): «пришли
// историю чата до такого-то сообщения». Телефон присылает пачку старых сообщений с ключами к файлам;
// файлы запрошенных встают в очередь media.go, а сами сообщения в портал не идут — он их уже знает, а
// незнакомые (до привязки номера) откликами становиться не должны. Отсчёт истории — от следующего
// сообщения того же чата (anchor, его даёт портал). Нет следующего — просьба прислать само сообщение
// заново (PLACEHOLDER_MESSAGE_RESEND, так whatsmeow дозапрашивает пришедшие пустыми); проверено
// 10.11.2026: на уже доставленные старые сообщения телефон её молча пропускает, поэтому — только запасной
// путь. Оба запроса уходят только на свой телефон — людям ничего не отправляется.
//
// Что просить, решает портал (ответ на опрос: сообщения без файла, пауза между просьбами и их предел).
// Сервис ничего не копит: запросы — по одному с паузой и не больше resendPerHour в час, лишнее
// отбрасывается — портал попросит снова.

import (
	"context"
	"strings"
	"sync"
	"time"

	"go.mau.fi/whatsmeow/proto/waHistorySync"
	"go.mau.fi/whatsmeow/proto/waWeb"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const (
	resendPause   = 3 * time.Second
	resendPerHour = 60
	resendBuffer  = 20
	resendTimeout = 30 * time.Second
	historyCount  = 10        // сообщений до отсчёта: нужное — первое из них, остальные — запас
	historyWait   = time.Hour // столько ждём историю по просьбе; портал просит снова через 30 минут
)

// resendAnchor — следующее сообщение того же чата: телефон присылает историю ДО него.
type resendAnchor struct {
	ID     string `json:"id"`
	FromMe bool   `json:"from_me"`
	TS     int64  `json:"ts"` // секунды Unix
}

// resendItem — сообщение без файла из ответа портала на опрос.
type resendItem struct {
	ID     string        `json:"id"`
	Chat   string        `json:"chat"`
	FromMe bool          `json:"from_me"`
	Anchor *resendAnchor `json:"anchor,omitempty"`
}

// historyAnchor — отсчёт для просьбы об истории; false — его нет или он битый (тогда просьба
// прислать само сообщение).
func historyAnchor(item resendItem) (*resendAnchor, bool) {
	anchor := item.Anchor
	if anchor == nil || !mediaIDPattern.MatchString(anchor.ID) || anchor.TS <= 0 || anchor.ID == item.ID {
		return nil, false
	}
	return anchor, true
}

// resendChat — JID личного чата из списка портала; false — группа, устройство или мусор.
func resendChat(raw string) (types.JID, bool) {
	jid, err := types.ParseJID(strings.TrimSpace(raw))
	if err != nil || jid.User == "" || jid.Device != 0 ||
		(jid.Server != types.DefaultUserServer && jid.Server != types.HiddenUserServer) {
		return types.EmptyJID, false
	}
	return jid, true
}

// resendSender — автор сообщения для ключа запроса: своё сообщение — пустой автор (ключ с fromMe),
// чужое в личном чате — сам собеседник.
func resendSender(chat types.JID, fromMe bool) types.JID {
	if fromMe {
		return types.EmptyJID
	}
	return chat
}

type resender struct {
	queue chan resendItem
	log   waLog.Logger
	ready func() bool // соединение с WhatsApp есть
	send  func(ctx context.Context, chat types.JID, item resendItem) error
	pause time.Duration

	mu      sync.Mutex
	sent    []time.Time          // запросы за последний час
	pending map[string]time.Time // id сообщения → когда попросили историю с ним
}

func newResender(log waLog.Logger, ready func() bool,
	send func(ctx context.Context, chat types.JID, item resendItem) error) *resender {
	return &resender{queue: make(chan resendItem, resendBuffer), log: log, ready: ready, send: send, pause: resendPause,
		pending: map[string]time.Time{}}
}

// offer — список из ответа портала. Очередь полна — остальное отбрасывается: портал попросит снова.
func (r *resender) offer(items []resendItem) {
	for _, item := range items {
		select {
		case r.queue <- item:
		default:
			return
		}
	}
}

func (r *resender) run(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case item := <-r.queue:
			if !r.request(ctx, item, time.Now()) {
				continue
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(r.pause):
			}
		}
	}
}

// request — один запрос телефону; false — запрос не ушёл (неверный элемент, нет связи, предел часа).
func (r *resender) request(ctx context.Context, item resendItem, now time.Time) bool {
	chat, ok := resendChat(item.Chat)
	if !ok || !mediaIDPattern.MatchString(item.ID) {
		r.log.Warnf("просьба телефону пропущена: неверное сообщение %q в чате %q", item.ID, item.Chat)
		return false
	}
	if !r.ready() {
		return false
	}
	_, history := historyAnchor(item)
	r.mu.Lock()
	recent := r.sent[:0]
	for _, at := range r.sent {
		if now.Sub(at) < time.Hour {
			recent = append(recent, at)
		}
	}
	r.sent = recent
	if len(r.sent) >= resendPerHour {
		r.mu.Unlock()
		r.log.Warnf("просьбы телефону: предел %d в час — сообщение %s позже", resendPerHour, item.ID)
		return false
	}
	// Считается и запрос, который вернул ошибку: до сервера он мог дойти.
	r.sent = append(r.sent, now)
	if history {
		for id, at := range r.pending {
			if now.Sub(at) >= historyWait {
				delete(r.pending, id)
			}
		}
		r.pending[item.ID] = now // до отправки: история может прийти раньше, чем вернётся вызов
	}
	r.mu.Unlock()
	ctx, cancel := context.WithTimeout(ctx, resendTimeout)
	defer cancel()
	if err := r.send(ctx, chat, item); err != nil {
		r.log.Warnf("просьба телефону о сообщении %s не ушла: %v", item.ID, err)
		return true
	}
	if history {
		r.log.Infof("попросил у телефона историю чата с сообщением %s (файл)", item.ID)
	} else {
		r.log.Infof("попросил телефон прислать заново сообщение %s (файл)", item.ID)
	}
	return true
}

// take — сообщение из присланной истории ждали (и ждём не дольше historyWait); ждать его больше не нужно.
func (r *resender) take(id string, now time.Time) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	at, ok := r.pending[id]
	if !ok {
		return false
	}
	delete(r.pending, id)
	return now.Sub(at) < historyWait
}

// filesFromHistory — история чата, присланная телефоном по просьбе: файлы запрошенных сообщений — в
// очередь media.go. Остальные сообщения истории не трогаются. Возвращает, сколько сообщений пришло и
// сколько файлов встало в очередь.
func (r *resender) filesFromHistory(data *waHistorySync.HistorySync, now time.Time,
	parse func(chat types.JID, web *waWeb.WebMessageInfo) (*events.Message, error),
	push func(job mediaJob) error) (total, queued int) {
	for _, conv := range data.GetConversations() {
		chat, err := types.ParseJID(conv.GetID())
		if err != nil {
			continue
		}
		for _, entry := range conv.GetMessages() {
			web := entry.GetMessage()
			if web == nil {
				continue
			}
			total++
			if !r.take(web.GetKey().GetID(), now) {
				continue
			}
			evt, err := parse(chat, web)
			if err != nil {
				r.log.Warnf("история: сообщение %s не разобрано: %v", web.GetKey().GetID(), err)
				continue
			}
			job := mediaJobOf(evt)
			if job == nil {
				r.log.Warnf("история: у сообщения %s нет файла для скачивания", web.GetKey().GetID())
				continue
			}
			if err := push(*job); err != nil {
				r.log.Errorf("история: файл %s не записан в очередь: %v", job.WAID, err)
				continue
			}
			queued++
		}
	}
	return total, queued
}
