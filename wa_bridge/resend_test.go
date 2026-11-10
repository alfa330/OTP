package main

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const testLID = "205981234567890@lid"

func TestResendChatAndSender(t *testing.T) {
	for raw, want := range map[string]string{
		testLID:                        testLID,
		" 77055552233@s.whatsapp.net ": "77055552233@s.whatsapp.net",
	} {
		jid, ok := resendChat(raw)
		if !ok || jid.String() != want {
			t.Fatalf("%q → %v %v", raw, jid, ok)
		}
	}
	// Группа, устройство, рассылка, пустой собеседник и мусор — не личный чат.
	for _, raw := range []string{"", "120363@g.us", "77055552233:3@s.whatsapp.net", "status@broadcast", "@lid", "мусор"} {
		if _, ok := resendChat(raw); ok {
			t.Fatalf("%q принят как личный чат", raw)
		}
	}
	chat := types.NewJID("205981234567890", types.HiddenUserServer)
	if !resendSender(chat, true).IsEmpty() || resendSender(chat, false) != chat {
		t.Fatal("автор в ключе запроса: своё — пустой, чужое — собеседник")
	}
}

func TestResenderSkipsAndHourLimit(t *testing.T) {
	var sent []string
	connected := true
	r := newResender(waLog.Noop, func() bool { return connected },
		func(_ context.Context, chat types.JID, item resendItem) error {
			sent = append(sent, chat.String()+"/"+item.ID)
			return nil
		})
	now := time.Unix(1792047600, 0)
	item := resendItem{ID: "3EB0A1", Chat: testLID}
	if !r.request(context.Background(), item, now) || len(sent) != 1 || sent[0] != testLID+"/3EB0A1" {
		t.Fatalf("запрос телефону: %#v", sent)
	}
	// Неверный id или чат и нет связи — запрос не уходит и в предел не идёт.
	for _, bad := range []resendItem{{ID: "../poll", Chat: testLID}, {ID: "3EB0A2", Chat: "120363@g.us"}} {
		if r.request(context.Background(), bad, now) {
			t.Fatalf("ушёл неверный запрос %#v", bad)
		}
	}
	connected = false
	if r.request(context.Background(), item, now) {
		t.Fatal("запрос без связи с WhatsApp")
	}
	connected = true
	// Предел часа: 60 запросов, дальше — только когда первый выйдет из часа.
	for i := 1; i < resendPerHour; i++ {
		if !r.request(context.Background(), item, now.Add(time.Duration(i)*time.Second)) {
			t.Fatalf("запрос %d отвергнут до предела", i+1)
		}
	}
	if r.request(context.Background(), item, now.Add(2*time.Minute)) || len(sent) != resendPerHour {
		t.Fatalf("предел часа не сработал: %d запросов", len(sent))
	}
	if !r.request(context.Background(), item, now.Add(time.Hour)) || len(sent) != resendPerHour+1 {
		t.Fatalf("через час запрос не ушёл: %d", len(sent))
	}
	// Запрос с ошибкой засчитан: до сервера он мог дойти.
	failing := newResender(waLog.Noop, func() bool { return true },
		func(context.Context, types.JID, resendItem) error { return errors.New("i/o timeout") })
	if !failing.request(context.Background(), item, now) || len(failing.sent) != 1 {
		t.Fatal("запрос с ошибкой не засчитан")
	}
}

func TestResenderQueueFromPollReply(t *testing.T) {
	var reply pollReply
	body := `{"command":null,"next_poll_sec":10,"resend":[{"id":"3EB0A1","chat":"` + testLID + `","from_me":true}]}`
	if err := json.Unmarshal([]byte(body), &reply); err != nil || len(reply.Resend) != 1 ||
		reply.Resend[0] != (resendItem{ID: "3EB0A1", Chat: testLID, FromMe: true}) {
		t.Fatalf("ответ опроса: %v %#v", err, reply)
	}
	// Очередь разбирается по одному; неверный элемент пропускается; переполнение отбрасывает лишнее.
	var mu sync.Mutex
	var got []string
	done := make(chan struct{})
	r := newResender(waLog.Noop, func() bool { return true },
		func(_ context.Context, _ types.JID, item resendItem) error {
			mu.Lock()
			defer mu.Unlock()
			got = append(got, item.ID)
			if len(got) == 2 {
				close(done)
			}
			return nil
		})
	r.pause = time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go r.run(ctx)
	r.offer([]resendItem{{ID: "A1", Chat: testLID}, {ID: "bad/", Chat: testLID}, {ID: "A2", Chat: testLID, FromMe: true}})
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("очередь просьб не разобрана")
	}
	mu.Lock()
	if len(got) != 2 || got[0] != "A1" || got[1] != "A2" {
		t.Fatalf("просьбы: %#v", got)
	}
	mu.Unlock()
	full := newResender(waLog.Noop, func() bool { return true }, func(context.Context, types.JID, resendItem) error { return nil })
	full.offer(make([]resendItem, resendBuffer+5))
	if len(full.queue) != resendBuffer {
		t.Fatalf("переполненная очередь: %d", len(full.queue))
	}
}
