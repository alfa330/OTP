package main

// Файлы старых сообщений (владелец 10.11.2026: «фото отображается просто как фото, и непонятно, как его
// посмотреть»). До версии 1.1.0 сервис файлы не качал, а ключ к файлу приходит только вместе с самим
// сообщением — ни у портала, ни у сервиса его нет. Он есть у телефона номера: сервис просит телефон
// прислать сообщение заново — тем же запросом, каким whatsmeow сам дозапрашивает у телефона сообщения,
// пришедшие пустыми (PLACEHOLDER_MESSAGE_RESEND; так делает и WhatsApp Web). Запрос уходит только на
// свой телефон — людям ничего не отправляется. Телефон отвечает обычным сообщением (events.Message с
// UnavailableRequestID): портал узнаёт его по id и второй раз не пишет, а файл встаёт в очередь media.go.
//
// Что просить, решает портал (ответ на опрос: сообщения без файла, пауза между просьбами и их предел).
// Сервис ничего не копит: запросы — по одному с паузой и не больше resendPerHour в час, лишнее
// отбрасывается — портал попросит снова.

import (
	"context"
	"strings"
	"sync"
	"time"

	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const (
	resendPause   = 3 * time.Second
	resendPerHour = 60
	resendBuffer  = 20
	resendTimeout = 30 * time.Second
)

// resendItem — сообщение без файла из ответа портала на опрос.
type resendItem struct {
	ID     string `json:"id"`
	Chat   string `json:"chat"`
	FromMe bool   `json:"from_me"`
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

	mu   sync.Mutex
	sent []time.Time // запросы за последний час
}

func newResender(log waLog.Logger, ready func() bool,
	send func(ctx context.Context, chat types.JID, item resendItem) error) *resender {
	return &resender{queue: make(chan resendItem, resendBuffer), log: log, ready: ready, send: send, pause: resendPause}
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
	r.mu.Unlock()
	ctx, cancel := context.WithTimeout(ctx, resendTimeout)
	defer cancel()
	if err := r.send(ctx, chat, item); err != nil {
		r.log.Warnf("просьба телефону прислать заново %s не ушла: %v", item.ID, err)
		return true
	}
	r.log.Infof("попросил телефон прислать заново сообщение %s (файл)", item.ID)
	return true
}
