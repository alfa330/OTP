package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

func testSender(t *testing.T, deliver func(context.Context, sendItem) (string, time.Time, error)) (*sender, *spool) {
	t.Helper()
	sp, err := openSpool(filepath.Join(t.TempDir(), "spool.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { sp.db.Close() })
	s := newSender(sp, waLog.Noop, func() bool { return true }, deliver)
	s.jitter = func() time.Duration { return 0 }
	return s, sp
}

func spooled(t *testing.T, sp *spool) []map[string]any {
	t.Helper()
	_, bodies, err := sp.batch(100)
	if err != nil {
		t.Fatal(err)
	}
	var out []map[string]any
	for _, body := range bodies {
		var event map[string]any
		if err := json.Unmarshal(body, &event); err != nil {
			t.Fatal(err)
		}
		out = append(out, event)
	}
	return out
}

func TestCleanSendKeepsOnlyDirectChatsAndLimits(t *testing.T) {
	good := sendItem{ID: 7, Chat: "77055552233@s.whatsapp.net", Kind: "text", Text: "Здравствуйте",
		Quote: &sendQuote{ID: "../poll", Text: "x"}}
	item, err := cleanSend(good)
	if err != nil || item.Quote != nil {
		t.Fatalf("личный чат с битой цитатой: цитата отбрасывается, сообщение остаётся (%v, %#v)", err, item.Quote)
	}
	for name, bad := range map[string]sendItem{
		"группа":       {ID: 1, Chat: "120363@g.us", Kind: "text", Text: "x"},
		"устройство":   {ID: 1, Chat: "77055552233:2@s.whatsapp.net", Kind: "text", Text: "x"},
		"пустой текст": {ID: 1, Chat: testLID, Kind: "text", Text: "  "},
		"без номера":   {Chat: testLID, Kind: "text", Text: "x"},
		"вид":          {ID: 1, Chat: testLID, Kind: "sticker", Text: "x"},
		"длинный":      {ID: 1, Chat: testLID, Kind: "text", Text: strings.Repeat("я", maxSendText+1)},
		"файл без":     {ID: 1, Chat: testLID, Kind: "image"},
		"файл 20 МБ":   {ID: 1, Chat: testLID, Kind: "document", Media: &sendMedia{Size: 20 << 20}},
	} {
		if _, err := cleanSend(bad); err == nil {
			t.Fatalf("%s: принято %#v", name, bad)
		}
	}
}

func TestSenderSendsOnceAndReportsResult(t *testing.T) {
	var delivered []int64
	s, sp := testSender(t, func(_ context.Context, item sendItem) (string, time.Time, error) {
		delivered = append(delivered, item.ID)
		return fmt.Sprintf("3EB0C0%d", item.ID), time.Unix(1792047660, 0), nil
	})
	item := sendItem{ID: 5, Chat: testLID, Kind: "text", Text: "Добрый день"}
	if s.offer([]sendItem{item, item}) != 1 || s.offer([]sendItem{item}) != 0 {
		t.Fatal("один номер портала — одна запись в очереди, повтор опроса отбрасывается")
	}
	job, err := sp.nextOutbox(time.Now())
	if err != nil || job == nil || job.ID != 5 {
		t.Fatalf("очередь: %#v %v", job, err)
	}
	s.process(context.Background(), job)
	if len(delivered) != 1 {
		t.Fatalf("отправлено %v", delivered)
	}
	if next, _ := sp.nextOutbox(time.Now()); next != nil {
		t.Fatal("отправленное не должно уходить второй раз")
	}
	// Повтор того же номера после отправки (потерянный ответ опроса) — не уходит: номер помнится.
	if s.offer([]sendItem{item}) != 0 {
		t.Fatal("отправленный номер принят заново")
	}
	events := spooled(t, sp)
	if len(events) != 1 || events[0]["type"] != "sent" || events[0]["outbox"] != float64(5) ||
		events[0]["id"] != "3EB0C05" || events[0]["ts"] != float64(1792047660) {
		t.Fatalf("итог для портала: %#v", events)
	}
	// Через неделю номер забывается.
	if err := sp.pruneOutbox(time.Now().Add(time.Hour)); err != nil {
		t.Fatal(err)
	}
	if s.offer([]sendItem{item}) != 1 {
		t.Fatal("старый номер должен забываться")
	}
}

func TestSenderRetriesNetworkButNotServerRefusal(t *testing.T) {
	failure := whatsmeow.ErrNotConnected
	var ids []string
	s, sp := testSender(t, func(_ context.Context, item sendItem) (string, time.Time, error) {
		ids = append(ids, item.MessageID)
		return "", time.Time{}, failure
	})
	now := time.Unix(1792047600, 0)
	s.now = func() time.Time { return now }
	s.offer([]sendItem{{ID: 9, Chat: testLID, Kind: "text", Text: "x", ExpiresIn: 3600}})
	job, _ := sp.nextOutbox(now)
	s.process(context.Background(), job)
	if next, _ := sp.nextOutbox(now); next != nil {
		t.Fatal("сетевой сбой — пауза перед повтором")
	}
	if at, ok := sp.nextOutboxAt(); !ok || at.Sub(now) != 20*time.Second {
		t.Fatalf("повтор через 20 с, а не %v", at.Sub(now))
	}
	if len(spooled(t, sp)) != 0 {
		t.Fatal("пока есть попытки, портал об ошибке не узнаёт")
	}
	// Отказ сервера WhatsApp — сразу ошибка, без повторов.
	failure = errors.New("server returned error 463")
	now = now.Add(time.Minute)
	job, _ = sp.nextOutbox(now)
	s.process(context.Background(), job)
	if len(ids) != 2 || ids[0] == "" || ids[0] != ids[1] {
		t.Fatalf("у всех попыток одного сообщения — один id WhatsApp: %v", ids)
	}
	events := spooled(t, sp)
	if len(events) != 1 || events[0]["type"] != "send_failed" || events[0]["reason"] != "rejected" ||
		events[0]["error"] != "server returned error 463" {
		t.Fatalf("отказ сервера: %#v", events)
	}
	if next, _ := sp.nextOutbox(now.Add(time.Hour)); next != nil {
		t.Fatal("после отказа сообщение не повторяется")
	}
	// Причина из sendItem (файла нет у портала) доходит до портала как есть.
	failure = &sendError{reason: "no_file", err: errors.New("портал ответил 404")}
	s.offer([]sendItem{{ID: 10, Chat: testLID, Kind: "image", Media: &sendMedia{Mime: "image/jpeg", Size: 100}}})
	job, _ = sp.nextOutbox(now.Add(time.Hour))
	s.process(context.Background(), job)
	if last := spooled(t, sp); last[len(last)-1]["reason"] != "no_file" {
		t.Fatalf("причина: %#v", last)
	}
}

func TestSenderExpiresAndCoolsDown(t *testing.T) {
	delivered := 0
	s, sp := testSender(t, func(_ context.Context, item sendItem) (string, time.Time, error) {
		delivered++
		return "", time.Time{}, errors.New("info query returned status 429: rate-overlimit")
	})
	now := time.Unix(1792047600, 0)
	s.now = func() time.Time { return now }
	// Портал дал 60 секунд: через две минуты (сервис долго был без связи) сообщение не уходит, а «expired».
	s.offer([]sendItem{{ID: 21, Chat: testLID, Kind: "text", Text: "Сегодня в 14:00?", ExpiresIn: 60}})
	now = now.Add(2 * time.Minute)
	job, _ := sp.nextOutbox(now)
	s.process(context.Background(), job)
	events := spooled(t, sp)
	if delivered != 0 || len(events) != 1 || events[0]["reason"] != "expired" {
		t.Fatalf("просроченное не отправляется: %d %#v", delivered, events)
	}
	// «Слишком часто» от сервера — повтор позже и пауза всей очереди.
	s.offer([]sendItem{{ID: 22, Chat: testLID, Kind: "text", Text: "x", ExpiresIn: 1800}})
	job, _ = sp.nextOutbox(now)
	s.process(context.Background(), job)
	if ok, wait := s.allowed(now.Add(time.Minute)); ok || wait < 3*time.Minute {
		t.Fatalf("после 429 очередь ждёт: %v %v", ok, wait)
	}
	// Пауза между попытками держится и без сигнала очереди.
	s.coolTill = time.Time{}
	s.mark(now)
	if ok, wait := s.allowed(now.Add(time.Second)); ok || wait != sendPause-time.Second {
		t.Fatalf("пауза между сообщениями: %v %v", ok, wait)
	}
}

func TestSenderMinuteAndHourLimits(t *testing.T) {
	s, _ := testSender(t, nil)
	now := time.Unix(1792047600, 0)
	for i := 0; i < sendPerMinute; i++ {
		s.mark(now.Add(time.Duration(i) * time.Second))
	}
	s.nextAt = time.Time{}
	if ok, wait := s.allowed(now.Add(30 * time.Second)); ok || wait != 30*time.Second {
		t.Fatalf("21-е за минуту ждёт, пока выйдет первое: %v %v", ok, wait)
	}
	if ok, _ := s.allowed(now.Add(61 * time.Second)); !ok {
		t.Fatal("через минуту — можно")
	}
	s.sent = nil
	for i := 0; i < sendPerHour; i++ {
		s.mark(now.Add(time.Duration(i) * 10 * time.Second))
	}
	s.nextAt = time.Time{}
	if ok, _ := s.allowed(now.Add(55 * time.Minute)); ok {
		t.Fatal("больше 300 в час — ждать")
	}
}

func TestQuoteContextAndMessages(t *testing.T) {
	own := types.NewJID("77081536084", types.DefaultUserServer)
	ownLID := types.NewJID("199911112222333", types.HiddenUserServer)
	pnChat := types.NewJID("77055552233", types.DefaultUserServer)
	lidChat := types.NewJID("205981234567890", types.HiddenUserServer)
	if quoteContext(pnChat, own, ownLID, nil) != nil || quoteContext(pnChat, own, ownLID, &sendQuote{ID: "../x"}) != nil {
		t.Fatal("без цитаты и с битой — без ContextInfo")
	}
	theirs := quoteContext(pnChat, own, ownLID, &sendQuote{ID: "3EB0AA", Text: "Хочу работать"})
	if theirs.GetStanzaID() != "3EB0AA" || theirs.GetParticipant() != "77055552233@s.whatsapp.net" ||
		theirs.GetQuotedMessage().GetConversation() != "Хочу работать" {
		t.Fatalf("цитата сообщения кандидата: %v", theirs)
	}
	if got := quoteContext(pnChat, own, ownLID, &sendQuote{ID: "3EB0AB", FromMe: true}).GetParticipant(); got != "77081536084@s.whatsapp.net" {
		t.Fatalf("своё сообщение в чате по номеру — наш номер: %s", got)
	}
	if got := quoteContext(lidChat, own, ownLID, &sendQuote{ID: "3EB0AB", FromMe: true}).GetParticipant(); got != "199911112222333@lid" {
		t.Fatalf("своё сообщение в чате по LID — наш LID: %s", got)
	}
	if textMessage("Да", nil).GetConversation() != "Да" ||
		textMessage("Да", theirs).GetExtendedTextMessage().GetContextInfo().GetStanzaID() != "3EB0AA" {
		t.Fatal("текст с цитатой — ExtendedTextMessage")
	}
	up := whatsmeow.UploadResponse{URL: "https://mmg", DirectPath: "/v/t62", FileLength: 1234}
	photo := fileMessage(sendItem{Kind: "image", Text: "Адрес", Media: &sendMedia{Mime: "image/jpeg"}}, up, nil)
	if photo.GetImageMessage().GetCaption() != "Адрес" || photo.GetImageMessage().GetFileLength() != 1234 {
		t.Fatalf("фото с подписью: %v", photo)
	}
	doc := fileMessage(sendItem{Kind: "document", Media: &sendMedia{Mime: "application/pdf", Name: "Договор.pdf"}}, up, theirs)
	if doc.GetDocumentMessage().GetFileName() != "Договор.pdf" || doc.GetDocumentMessage().GetContextInfo() == nil {
		t.Fatalf("документ: %v", doc)
	}
	if mediaTypeOf("video") != whatsmeow.MediaVideo || mediaTypeOf("document") != whatsmeow.MediaDocument {
		t.Fatal("тип загрузки по виду файла")
	}
}

func TestReceiptEventOnlyFromCandidates(t *testing.T) {
	chat := types.NewJID("77055552233", types.DefaultUserServer)
	at := time.Unix(1792047700, 0)
	receipt := func(kind types.ReceiptType, fromMe bool, ids ...string) *events.Receipt {
		return &events.Receipt{MessageSource: types.MessageSource{Chat: chat, Sender: chat, IsFromMe: fromMe},
			MessageIDs: ids, Timestamp: at, Type: kind}
	}
	got := receiptEvent(receipt(types.ReceiptTypeDelivered, false, "3EB0AA", "../bad"))
	if got["status"] != "delivered" || len(got["ids"].([]string)) != 1 || got["ts"] != at.Unix() {
		t.Fatalf("доставлено: %#v", got)
	}
	if receiptEvent(receipt(types.ReceiptTypeRead, false, "3EB0AA"))["status"] != "read" ||
		receiptEvent(receipt(types.ReceiptTypePlayed, false, "3EB0AA"))["status"] != "played" {
		t.Fatal("прочитано и прослушано")
	}
	for name, evt := range map[string]*events.Receipt{
		"своё устройство": receipt(types.ReceiptTypeRead, true, "3EB0AA"),
		"read-self":       receipt(types.ReceiptTypeReadSelf, false, "3EB0AA"),
		"повтор":          receipt(types.ReceiptTypeRetry, false, "3EB0AA"),
		"без id":          receipt(types.ReceiptTypeRead, false, "../bad"),
	} {
		if receiptEvent(evt) != nil {
			t.Fatalf("%s не должно идти в портал", name)
		}
	}
	group := receipt(types.ReceiptTypeRead, false, "3EB0AA")
	group.IsGroup = true
	if receiptEvent(group) != nil {
		t.Fatal("группы — мимо")
	}
}
