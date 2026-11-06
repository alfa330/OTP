package main

import (
	"context"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

func incoming(chat types.JID, msg *waE2E.Message) *events.Message {
	return &events.Message{Info: types.MessageInfo{
		MessageSource: types.MessageSource{Chat: chat, Sender: chat},
		ID:            "3EB0A1", PushName: "Айгерим", Timestamp: time.Unix(1792047600, 0),
	}, Message: msg}
}

func str(value string) *string { return &value }

func TestConvertPhoneChatWithAd(t *testing.T) {
	chat := types.NewJID("77055552233", types.DefaultUserServer)
	evt := incoming(chat, &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{
		Text: str("Здравствуйте, я по вакансии"),
		ContextInfo: &waE2E.ContextInfo{
			ConversionSource: str("FB_Ads"),
			ExternalAdReply:  &waE2E.ContextInfo_ExternalAdReplyInfo{Title: str("Оператор КЦ"), SourceID: str("120208"), CtwaClid: str("clid")},
		},
	}})
	evt.Info.SenderAlt = types.NewJID("205981234567890", types.HiddenUserServer)
	got := convertMessage(context.Background(), evt, nil)
	if got["phone"] != "77055552233" || got["lid"] != "205981234567890" || got["kind"] != "text" ||
		got["text"] != "Здравствуйте, я по вакансии" || got["push_name"] != "Айгерим" || got["ts"] != int64(1792047600) {
		t.Fatalf("событие: %#v", got)
	}
	ad := got["ad"].(map[string]string)
	if ad["title"] != "Оператор КЦ" || ad["source_id"] != "120208" || ad["ctwa_clid"] != "clid" || ad["conversion_source"] != "FB_Ads" {
		t.Fatalf("реклама: %#v", ad)
	}
}

func TestConvertHiddenNumberResolvedFromStore(t *testing.T) {
	chat := types.NewJID("205981234567890", types.HiddenUserServer)
	evt := incoming(chat, &waE2E.Message{Conversation: str("Добрый день")})
	resolve := func(_ context.Context, lid types.JID) types.JID {
		if lid.User == "205981234567890" {
			return types.NewJID("77055556677", types.DefaultUserServer)
		}
		return types.EmptyJID
	}
	got := convertMessage(context.Background(), evt, resolve)
	if got["phone"] != "77055556677" || got["lid"] != "205981234567890" || got["chat"] != "205981234567890@lid" {
		t.Fatalf("скрытый номер: %#v", got)
	}
	// Номер пришёл прямо в сообщении (sender_pn) — хранилище не нужно.
	evt.Info.SenderAlt = types.NewJID("77055550000", types.DefaultUserServer)
	if got := convertMessage(context.Background(), evt, nil); got["phone"] != "77055550000" {
		t.Fatalf("sender_pn: %#v", got)
	}
	// Ни номера, ни соответствия — событие всё равно уходит (по LID), номер доклеится позже.
	evt.Info.SenderAlt = types.EmptyJID
	if got := convertMessage(context.Background(), evt, nil); got["phone"] != "" || got["lid"] != "205981234567890" {
		t.Fatalf("только LID: %#v", got)
	}
}

func TestConvertSkipsGroupsAndService(t *testing.T) {
	group := incoming(types.NewJID("120363000000000000", types.GroupServer), &waE2E.Message{Conversation: str("всем")})
	group.Info.IsGroup = true
	if convertMessage(context.Background(), group, nil) != nil {
		t.Fatal("группа ушла в портал")
	}
	status := incoming(types.StatusBroadcastJID, &waE2E.Message{Conversation: str("статус")})
	if convertMessage(context.Background(), status, nil) != nil {
		t.Fatal("статус ушёл в портал")
	}
	reaction := incoming(types.NewJID("77055552233", types.DefaultUserServer),
		&waE2E.Message{ReactionMessage: &waE2E.ReactionMessage{Text: str("👍")}})
	if convertMessage(context.Background(), reaction, nil) != nil {
		t.Fatal("реакция ушла в портал")
	}
}

func TestConvertEditRevokeAndMedia(t *testing.T) {
	chat := types.NewJID("77055552233", types.DefaultUserServer)
	revokeType := waE2E.ProtocolMessage_REVOKE
	revoke := incoming(chat, &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{
		Type: &revokeType, Key: &waCommon.MessageKey{ID: str("orig1")}}})
	if got := convertMessage(context.Background(), revoke, nil); got["revoke_of"] != "orig1" {
		t.Fatalf("удаление: %#v", got)
	}
	editType := waE2E.ProtocolMessage_MESSAGE_EDIT
	edit := incoming(chat, &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{
		Type: &editType, Key: &waCommon.MessageKey{ID: str("orig1")}, EditedMessage: &waE2E.Message{Conversation: str("Исправил")}}})
	if got := convertMessage(context.Background(), edit, nil); got["edit_of"] != "orig1" || got["text"] != "Исправил" {
		t.Fatalf("правка: %#v", got)
	}
	ptt, seconds, size := true, uint32(17), uint64(4096)
	voice := incoming(chat, &waE2E.Message{AudioMessage: &waE2E.AudioMessage{PTT: &ptt, Seconds: &seconds, FileLength: &size,
		Mimetype: str("audio/ogg; codecs=opus")}})
	got := convertMessage(context.Background(), voice, nil)
	media := got["media"].(map[string]any)
	if got["kind"] != "voice" || media["seconds"] != uint32(17) || media["size"] != uint64(4096) {
		t.Fatalf("голосовое: %#v", got)
	}
	doc := incoming(chat, &waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{FileName: str("cv.pdf"), Caption: str("резюме")}})
	if got := convertMessage(context.Background(), doc, nil); got["kind"] != "document" || got["text"] != "резюме" ||
		got["media"].(map[string]any)["file_name"] != "cv.pdf" {
		t.Fatalf("документ: %#v", got)
	}
	// Ответ рекрутера с телефона: имя «отправителя» — наше, его не отдаём.
	mine := incoming(chat, &waE2E.Message{Conversation: str("Да")})
	mine.Info.IsFromMe = true
	mine.Info.RecipientAlt = types.NewJID("205981234567890", types.HiddenUserServer)
	if got := convertMessage(context.Background(), mine, nil); got["push_name"] != nil || got["from_me"] != true || got["lid"] != "205981234567890" {
		t.Fatalf("исходящее: %#v", got)
	}
}
