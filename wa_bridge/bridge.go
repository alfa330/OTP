package main

// Жизнь сессии номера найма. Правила безопасности номера (сверка источников 06.11.2026):
//
//   * одна сессия — один процесс (замок в main), привязка — один раз, по QR, только по команде
//     человека из портала; после двух неудачных подряд — стоп;
//   * на любой сигнал ограничения или бана (отвязан, вторая копия, временный бан, устаревший клиент,
//     отказ сервера) — остановка без повторов и перепривязок; снимает её человек («Возобновить»);
//   * обрывы установленного соединения чаще 12 в час — тоже остановка: шторм переподключений —
//     сигнал для антиспама; при недоступной сети пауза между попытками растёт до 5 минут;
//   * сразу после подключения — статус «не в сети»: так телефон рекрутера получает уведомления;
//   * отметки «прочитано» и «печатает» не ставятся, людям ничего не отправляется: единственный запрос
//     от сервиса — своему телефону, прислать историю чата со старым сообщением с файлом (resend.go), как
//     в WhatsApp Web; по одному с паузой и не больше 60 в час;
//   * файлы сообщений качаются по одному, с паузой, как в WhatsApp Web (media.go).

import (
	"context"
	"encoding/json"
	"os"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/proto/waHistorySync"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const bridgeVersion = "1.2.1"

const (
	stateUnpaired   = "unpaired"
	statePairing    = "pairing"
	stateConnecting = "connecting"
	stateConnected  = "connected"
	stateParked     = "parked"
)

type bridge struct {
	cfg       config
	log       waLog.Logger
	clientLog waLog.Logger
	container *sqlstore.Container
	guard     *guard
	spool     *spool
	portal    *portal
	resend    *resender
	drops     dropCounter
	ctx       context.Context
	pollNow   chan struct{}

	mu              sync.Mutex
	cli             *whatsmeow.Client
	state           string
	qr              string
	pairing         bool
	handled         string
	ack             string
	timelock        map[string]any
	wasConnected    bool
	reconnectFails  int
	presencePending bool
	refusedConnects int
}

func newBridge(ctx context.Context, cfg config, log waLog.Logger, container *sqlstore.Container, g *guard, sp *spool,
	p *portal) (*bridge, error) {
	b := &bridge{cfg: cfg, log: log, clientLog: waLog.Stdout("WhatsApp", cfg.logLevel, false), container: container,
		guard: g, spool: sp, portal: p, ctx: ctx, pollNow: make(chan struct{}, 1)}
	b.resend = newResender(log, func() bool { return b.currentState() == stateConnected }, b.requestFromPhone)
	device, err := container.GetFirstDevice(ctx)
	if err != nil {
		return nil, err
	}
	if err := b.useDevice(device); err != nil {
		return nil, err
	}
	return b, nil
}

func (b *bridge) useDevice(device *store.Device) error {
	cli := whatsmeow.NewClient(device, b.clientLog)
	cli.EnableAutoReconnect = true
	cli.InitialAutoReconnect = true
	cli.AutoReconnectHook = b.reconnectHook
	if b.cfg.proxy != "" {
		if err := cli.SetProxyAddress(b.cfg.proxy); err != nil {
			return err
		}
	}
	cli.AddEventHandler(b.handle)
	b.mu.Lock()
	b.cli = cli
	b.mu.Unlock()
	return nil
}

func (b *bridge) client() *whatsmeow.Client {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.cli
}

func (b *bridge) setState(state string) {
	b.mu.Lock()
	changed := b.state != state
	b.state = state
	if state != statePairing {
		b.qr = ""
	}
	b.mu.Unlock()
	if changed {
		b.log.Infof("состояние: %s", state)
		b.triggerPoll()
	}
}

func (b *bridge) currentState() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.state
}

func (b *bridge) triggerPoll() {
	select {
	case b.pollNow <- struct{}{}:
	default:
	}
}

// start — первое решение после старта: остановлен — ждём человека; нет сессии — ждём «Подключить».
func (b *bridge) start() {
	switch {
	case b.guard.parked() != nil:
		b.setState(stateParked)
	case b.client().Store.ID == nil:
		b.setState(stateUnpaired)
	default:
		b.connect()
	}
}

func (b *bridge) connect() {
	b.setState(stateConnecting)
	cli := b.client()
	go func() {
		if err := cli.Connect(); err != nil {
			// Сетевые ошибки библиотека повторяет сама (InitialAutoReconnect); сюда — отказ без повтора.
			b.mu.Lock()
			b.refusedConnects++
			refused := b.refusedConnects
			b.mu.Unlock()
			b.log.Warnf("подключение не удалось: %v", err)
			if refused >= 3 {
				b.park("refused", 0, time.Time{})
				return
			}
			select {
			case <-b.ctx.Done():
			case <-time.After(time.Minute):
				if b.guard.parked() == nil && b.currentState() == stateConnecting {
					b.connect()
				}
			}
		}
	}()
}

// reconnectHook — после каждой неудачной попытки автопереподключения: пауза растёт 5 с → 5 мин.
// Сервер нас при этом не видит (сеть), поэтому здесь не останавливаемся — шторм ловит handle.
func (b *bridge) reconnectHook(err error) bool {
	if b.guard.parked() != nil || b.ctx.Err() != nil {
		return false
	}
	b.mu.Lock()
	b.reconnectFails++
	fails := b.reconnectFails
	b.mu.Unlock()
	delay := 5 * time.Second << min(fails-1, 6)
	if delay > 5*time.Minute {
		delay = 5 * time.Minute
	}
	b.log.Warnf("переподключение не удалось (%d подряд): %v — пауза %s", fails, err, delay)
	select {
	case <-b.ctx.Done():
		return false
	case <-time.After(delay):
	}
	return b.guard.parked() == nil
}

// park — остановиться без повторов: дальше решает человек.
func (b *bridge) park(reason string, code int, until time.Time) {
	fresh, err := b.guard.park(reason, code, until, time.Now())
	if err != nil {
		b.log.Errorf("остановка не записана на диск: %v", err)
	}
	if fresh {
		b.log.Warnf("ОСТАНОВЛЕН: %s (код %d)", reason, code)
	}
	b.setState(stateParked)
	go b.client().Disconnect()
}

func (b *bridge) resolvePN(ctx context.Context, lid types.JID) types.JID {
	pn, err := b.client().Store.LIDs.GetPNForLID(ctx, lid)
	if err != nil {
		return types.EmptyJID
	}
	return pn
}

func (b *bridge) handle(raw any) {
	now := time.Now()
	switch evt := raw.(type) {
	case *events.Connected:
		if b.guard.parked() != nil {
			go b.client().Disconnect() // остановленный сервис на связи не держим, даже если библиотека успела
			return
		}
		b.mu.Lock()
		b.wasConnected = true
		b.reconnectFails = 0
		b.refusedConnects = 0
		b.mu.Unlock()
		if err := b.guard.connected(now); err != nil {
			b.log.Warnf("состояние не записано: %v", err)
		}
		b.setState(stateConnected)
		go b.sendPresence()
	case *events.PushNameSetting:
		b.mu.Lock()
		pending := b.presencePending
		b.mu.Unlock()
		if pending {
			go b.sendPresence()
		}
	case *events.Disconnected:
		b.mu.Lock()
		dropped := b.wasConnected
		b.wasConnected = false
		b.mu.Unlock()
		if dropped && b.drops.add(now) > maxDropsPerHour {
			b.park("reconnect_fuse", 0, time.Time{})
			return
		}
		if b.currentState() == stateConnected {
			b.setState(stateConnecting)
		}
	case *events.LoggedOut:
		reason := "logged_out"
		switch evt.Reason {
		case events.ConnectFailureMainDeviceGone:
			reason = "locked"
		case events.ConnectFailureUnknownLogout:
			reason = "banned"
		}
		code := 0
		if evt.OnConnect {
			code = int(evt.Reason)
		}
		b.park(reason, code, time.Time{})
		_ = b.guard.forget()
		// Сессию библиотека уже стёрла — новая привязка начнётся с чистого устройства.
		go b.freshDevice()
	case *events.StreamReplaced:
		b.park("replaced", 0, time.Time{})
	case *events.TemporaryBan:
		b.park("temp_ban", int(evt.Code), now.Add(evt.Expire))
	case *events.ClientOutdated:
		b.park("outdated", 0, time.Time{})
	case *events.ConnectFailure:
		if evt.Reason != events.ConnectFailureServiceUnavailable && evt.Reason != events.ConnectFailureInternalServerError {
			b.park("refused", int(evt.Reason), time.Time{})
		}
	case *events.NotifyAccountReachoutTimelock:
		lock := map[string]any{"active": evt.IsActive}
		if !evt.TimeEnforcementEnds.IsZero() {
			lock["until"] = stamp(evt.TimeEnforcementEnds.Time)
		}
		b.mu.Lock()
		b.timelock = lock
		b.mu.Unlock()
		b.log.Warnf("WhatsApp ограничил первые сообщения незнакомым: активно=%v", evt.IsActive)
		b.triggerPoll()
	case *events.PairSuccess:
		if err := b.guard.paired(now); err != nil {
			b.log.Warnf("состояние не записано: %v", err)
		}
		b.log.Infof("номер привязан")
		b.setState(stateConnecting)
	case *events.KeepAliveTimeout:
		if evt.ErrorCount == 1 || evt.ErrorCount%10 == 0 {
			b.log.Warnf("сервер не отвечает на пинг (%d подряд)", evt.ErrorCount)
		}
	case *events.KeepAliveRestored:
		b.log.Infof("связь с сервером восстановилась")
	case *events.OfflineSyncCompleted:
		b.log.Infof("досылка накопленного завершена: %d", evt.Count)
	case *events.Message:
		if evt.Info.IsFromMe && evt.Info.Sender.Device == 0 {
			_ = b.guard.phoneSeen(now) // ответ отправлен с самого телефона
		}
		if evt.UnavailableRequestID != "" {
			b.log.Infof("телефон прислал заново сообщение %s", evt.Info.ID)
		}
		if event := convertMessage(b.ctx, evt, b.resolvePN); event != nil {
			if job := mediaJobOf(evt); job != nil {
				if err := b.spool.pushMedia(*job, now.Add(mediaFirstDelay)); err != nil {
					b.log.Errorf("файл не записан в очередь: %v", err)
				} else {
					markFetch(event)
				}
			}
			if err := b.spool.push(event); err != nil {
				b.log.Errorf("сообщение не записано в очередь: %v", err)
			}
		}
	case *events.Receipt:
		if evt.IsFromMe && evt.Sender.Device == 0 {
			_ = b.guard.phoneSeen(now) // рекрутер прочитал на телефоне
		}
	case *events.HistorySync:
		// Только история по нашей просьбе (resend.go); первичная при привязке сервису не нужна.
		if evt.Data.GetSyncType() == waHistorySync.HistorySync_ON_DEMAND {
			b.historyFiles(evt.Data, now)
		}
	}
}

// sendPresence — «не в сети», один раз за подключение. Без имени профиля сервер вызов не примет:
// тогда — после того как имя придёт (PushNameSetting).
func (b *bridge) sendPresence() {
	cli := b.client()
	ctx, cancel := context.WithTimeout(b.ctx, 20*time.Second)
	defer cancel()
	err := cli.SendPresence(ctx, types.PresenceUnavailable)
	b.mu.Lock()
	b.presencePending = err != nil
	b.mu.Unlock()
	if err != nil {
		b.log.Infof("статус «не в сети» отложен: %v", err)
	}
}

// requestFromPhone — просьба к своему телефону (resend.go): история чата до следующего сообщения
// (придёт событием HistorySync) или, если следующего нет, само сообщение заново (придёт обычным).
func (b *bridge) requestFromPhone(ctx context.Context, chat types.JID, item resendItem) error {
	cli := b.client()
	var request *waE2E.Message
	if anchor, ok := historyAnchor(item); ok {
		request = cli.BuildHistorySyncRequest(&types.MessageInfo{
			MessageSource: types.MessageSource{Chat: chat, IsFromMe: anchor.FromMe},
			ID:            anchor.ID,
			Timestamp:     time.Unix(anchor.TS, 0),
		}, historyCount)
	} else {
		request = cli.BuildUnavailableMessageRequest(chat, resendSender(chat, item.FromMe), item.ID)
	}
	_, err := cli.SendPeerMessage(ctx, request)
	return err
}

// historyFiles — история чата от телефона: файлы сообщений, которые просил портал, — в очередь.
func (b *bridge) historyFiles(data *waHistorySync.HistorySync, now time.Time) {
	total, queued := b.resend.filesFromHistory(data, now, b.client().ParseWebMessage,
		func(job mediaJob) error { return b.spool.pushMedia(job, now) })
	b.log.Infof("телефон прислал историю чата: сообщений %d, файлов в очередь %d", total, queued)
}

func (b *bridge) freshDevice() {
	old := b.client()
	old.Disconnect()
	old.RemoveEventHandlers()
	if err := b.useDevice(b.container.NewDevice()); err != nil {
		b.log.Errorf("новое устройство не создано: %v", err)
	}
}

// ─── Команды людей из портала ─────────────────────────────────────────────

func (b *bridge) command(cmd pollCommand) {
	b.mu.Lock()
	if cmd.ID == "" || cmd.ID == b.handled {
		b.mu.Unlock()
		return
	}
	b.handled = cmd.ID
	b.ack = cmd.ID
	b.mu.Unlock()
	b.log.Infof("команда из портала: %s", cmd.Action)
	switch cmd.Action {
	case "pair":
		b.pair()
	case "resume":
		b.resume()
	case "logout":
		b.logout()
	}
	b.triggerPoll()
}

func (b *bridge) pair() {
	if park := b.guard.parked(); park != nil {
		if park.Reason != "logged_out" {
			return
		}
		_ = b.guard.resume()
	}
	b.mu.Lock()
	if b.pairing || b.cli.Store.ID != nil {
		b.mu.Unlock()
		return
	}
	b.pairing = true
	cli := b.cli
	b.mu.Unlock()
	ctx, cancel := context.WithTimeout(b.ctx, 5*time.Minute)
	codes, err := cli.GetQRChannel(ctx)
	if err == nil {
		err = cli.Connect()
	}
	if err != nil {
		cancel()
		b.mu.Lock()
		b.pairing = false
		b.mu.Unlock()
		b.log.Warnf("привязка не началась: %v", err)
		b.setState(stateUnpaired)
		return
	}
	b.setState(statePairing)
	go func() {
		defer cancel()
		defer func() {
			b.mu.Lock()
			b.pairing = false
			b.mu.Unlock()
		}()
		for item := range codes {
			switch item.Event {
			case whatsmeow.QRChannelEventCode:
				b.mu.Lock()
				b.qr = item.Code
				b.mu.Unlock()
				b.triggerPoll()
			case whatsmeow.QRChannelSuccess.Event:
				b.setState(stateConnecting)
			case whatsmeow.QRChannelTimeout.Event:
				// Код никто не отсканировал — это не неудача привязки, номер не участвовал.
				b.setState(stateUnpaired)
			case whatsmeow.QRChannelClientOutdated.Event:
				b.park("outdated", 0, time.Time{})
			case whatsmeow.QRChannelEventPasskeyRequest:
				// Шаг passkey (WebAuthn в браузере на базе Chrome) на этапе 1 не поддержан: стоп и человек.
				b.park("passkey", 0, time.Time{})
			case whatsmeow.QRChannelEventError, whatsmeow.QRChannelScannedWithoutMultidevice.Event:
				b.pairFailure(item.Error)
			}
		}
	}()
}

func (b *bridge) pairFailure(err error) {
	tooMany, saveErr := b.guard.pairFailed()
	if saveErr != nil {
		b.log.Warnf("состояние не записано: %v", saveErr)
	}
	b.log.Warnf("привязка не удалась: %v", err)
	if tooMany {
		b.park("pair_failed", 0, time.Time{})
		return
	}
	go b.client().Disconnect()
	b.setState(stateUnpaired)
}

func (b *bridge) resume() {
	if b.guard.parked() == nil {
		return
	}
	if err := b.guard.resume(); err != nil {
		b.log.Errorf("снятие остановки не записано: %v", err)
		return
	}
	b.drops.reset()
	b.mu.Lock()
	b.reconnectFails = 0
	b.refusedConnects = 0
	b.timelock = nil
	b.mu.Unlock()
	if b.client().Store.ID == nil {
		b.setState(stateUnpaired)
		return
	}
	b.connect()
}

func (b *bridge) logout() {
	cli := b.client()
	if cli.Store.ID == nil {
		return
	}
	ctx, cancel := context.WithTimeout(b.ctx, 30*time.Second)
	defer cancel()
	if err := cli.Logout(ctx); err != nil {
		// Без связи сервер просьбу не получит: стираем сессию у себя, устройство на телефоне
		// останется в списке «Связанных устройств» — его удаляют там.
		b.log.Warnf("отвязка через сервер не удалась (%v) — сессия стёрта только здесь", err)
		cli.Disconnect()
		_ = cli.Store.Delete(ctx)
	}
	_ = b.guard.resume()
	_ = b.guard.forget()
	b.freshDevice()
	b.setState(stateUnpaired)
}

// ─── Опрос портала ────────────────────────────────────────────────────────

func (b *bridge) detail() map[string]any {
	cli := b.client()
	g := b.guard.snapshot()
	b.mu.Lock()
	qr, state, timelock := b.qr, b.state, b.timelock
	b.mu.Unlock()
	d := map[string]any{
		"paired":  cli.Store.ID != nil,
		"version": "wa-bridge " + bridgeVersion + " · WA " + store.GetWAVersion().String(),
	}
	if cli.Store.ID != nil {
		d["number"] = cli.Store.ID.User
		if cli.Store.PushName != "" {
			d["push_name"] = cli.Store.PushName
		}
	}
	if qr != "" && state == statePairing {
		d["qr"] = qr
	}
	if g.ConnectedSince != "" && state == stateConnected {
		d["connected_since"] = g.ConnectedSince
	}
	if g.PhoneSeenAt != "" {
		d["phone_seen_at"] = g.PhoneSeenAt
	}
	if g.PairFailures > 0 {
		d["pair_failures"] = g.PairFailures
	}
	if g.Park != nil {
		d["park"] = g.Park
	}
	if timelock != nil {
		d["timelock"] = timelock
	}
	return d
}

func (b *bridge) pollLoop() {
	interval := 10 * time.Second
	failures := 0
	for {
		b.mu.Lock()
		ack := b.ack
		b.mu.Unlock()
		// features — что умеет эта версия: портал не просит лишнего у старой.
		payload := map[string]any{"state": b.currentState(), "detail": b.detail(), "features": []string{"resend"}}
		if ack != "" {
			payload["ack"] = ack
		}
		var reply pollReply
		err := b.portal.post(b.ctx, pollPath, payload, &reply)
		if err == nil {
			failures = 0
			b.heartbeat()
			if reply.NextPollSec > 0 && reply.NextPollSec <= 60 {
				interval = time.Duration(reply.NextPollSec) * time.Second
			}
			if reply.Command != nil {
				b.command(*reply.Command)
			}
			if len(reply.Resend) > 0 {
				b.resend.offer(reply.Resend)
			}
		} else if b.ctx.Err() == nil {
			failures++
			if failures == 1 || failures%20 == 0 {
				b.log.Warnf("портал не ответил на опрос (%d подряд): %v", failures, err)
			}
			interval = 15 * time.Second
		}
		select {
		case <-b.ctx.Done():
			return
		case <-b.pollNow:
		case <-time.After(interval):
		}
	}
}

func (b *bridge) sendEvents(ctx context.Context, bodies []json.RawMessage) error {
	var reply struct {
		Accepted int            `json:"accepted"`
		Outcomes map[string]int `json:"outcomes"`
	}
	if err := b.portal.post(ctx, eventsPath, map[string]any{"events": bodies}, &reply); err != nil {
		return err
	}
	if reply.Outcomes["created"] > 0 {
		b.log.Infof("новых откликов: %d", reply.Outcomes["created"])
	}
	return nil
}

// heartbeat — файл пульса для проверки здоровья контейнера: обмен с порталом удался.
func (b *bridge) heartbeat() {
	if b.cfg.heartbeat == "" {
		return
	}
	_ = os.WriteFile(b.cfg.heartbeat, []byte(stamp(time.Now())), 0o600)
}
