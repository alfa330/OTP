package main

// Сборка исходящего сообщения WhatsApp из элемента портала (sender.go) и отметки доставки от кандидата.
//
// Текст без цитаты — простое сообщение (Conversation), с цитатой — ExtendedTextMessage с ContextInfo: так же,
// как отвечает на сообщение WhatsApp Web. Файл: портал отдаёт его подписанным запросом, сервис шифрует и
// загружает его в WhatsApp (whatsmeow Upload) и отправляет фото, видео, аудио или документом с подписью.

import (
	"context"
	"errors"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

// quoteContext — цитата ответа: id сообщения, его автор и текст (его кандидат видит над ответом). Автор чужого
// сообщения — сам собеседник; своего — наш номер (в чате по LID — наш LID).
func quoteContext(chat types.JID, own, ownLID types.JID, quote *sendQuote) *waE2E.ContextInfo {
	if quote == nil || !mediaIDPattern.MatchString(quote.ID) {
		return nil
	}
	participant := chat.ToNonAD()
	if quote.FromMe {
		participant = own.ToNonAD()
		if chat.Server == types.HiddenUserServer && !ownLID.IsEmpty() {
			participant = ownLID.ToNonAD()
		}
	}
	text := quote.Text
	if text == "" {
		text = "Сообщение"
	}
	return &waE2E.ContextInfo{
		StanzaID:      proto.String(quote.ID),
		Participant:   proto.String(participant.String()),
		QuotedMessage: &waE2E.Message{Conversation: proto.String(text)},
	}
}

// textMessage — текст, с цитатой или без.
func textMessage(text string, info *waE2E.ContextInfo) *waE2E.Message {
	if info == nil {
		return &waE2E.Message{Conversation: proto.String(text)}
	}
	return &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String(text), ContextInfo: info}}
}

func mediaTypeOf(kind string) whatsmeow.MediaType {
	switch kind {
	case "image":
		return whatsmeow.MediaImage
	case "video":
		return whatsmeow.MediaVideo
	case "audio":
		return whatsmeow.MediaAudio
	}
	return whatsmeow.MediaDocument
}

// fileMessage — файл после загрузки в WhatsApp: фото и видео с подписью, аудио, остальное — документом.
func fileMessage(item sendItem, up whatsmeow.UploadResponse, info *waE2E.ContextInfo) *waE2E.Message {
	mime := item.Media.Mime
	if mime == "" {
		mime = "application/octet-stream"
	}
	var caption *string
	if item.Text != "" {
		caption = proto.String(item.Text)
	}
	switch item.Kind {
	case "image":
		return &waE2E.Message{ImageMessage: &waE2E.ImageMessage{
			Caption: caption, Mimetype: proto.String(mime), URL: proto.String(up.URL), DirectPath: proto.String(up.DirectPath),
			MediaKey: up.MediaKey, FileEncSHA256: up.FileEncSHA256, FileSHA256: up.FileSHA256,
			FileLength: proto.Uint64(up.FileLength), ContextInfo: info}}
	case "video":
		return &waE2E.Message{VideoMessage: &waE2E.VideoMessage{
			Caption: caption, Mimetype: proto.String(mime), URL: proto.String(up.URL), DirectPath: proto.String(up.DirectPath),
			MediaKey: up.MediaKey, FileEncSHA256: up.FileEncSHA256, FileSHA256: up.FileSHA256,
			FileLength: proto.Uint64(up.FileLength), ContextInfo: info}}
	case "audio":
		return &waE2E.Message{AudioMessage: &waE2E.AudioMessage{
			Mimetype: proto.String(mime), URL: proto.String(up.URL), DirectPath: proto.String(up.DirectPath),
			MediaKey: up.MediaKey, FileEncSHA256: up.FileEncSHA256, FileSHA256: up.FileSHA256,
			FileLength: proto.Uint64(up.FileLength), ContextInfo: info}}
	}
	name := item.Media.Name
	if name == "" {
		name = "file"
	}
	return &waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{
		Title: proto.String(name), FileName: proto.String(name), Caption: caption, Mimetype: proto.String(mime),
		URL: proto.String(up.URL), DirectPath: proto.String(up.DirectPath), MediaKey: up.MediaKey,
		FileEncSHA256: up.FileEncSHA256, FileSHA256: up.FileSHA256, FileLength: proto.Uint64(up.FileLength),
		ContextInfo: info}}
}

// sendItem — одно сообщение портала кандидату (sender.go). Возвращает id WhatsApp и время сервера.
func (b *bridge) sendItem(ctx context.Context, item sendItem) (string, time.Time, error) {
	cli := b.client()
	chat, ok := resendChat(item.Chat)
	if !ok {
		return "", time.Time{}, &sendError{reason: "bad_chat", err: errors.New(item.Chat)}
	}
	if cli.Store.ID == nil {
		return "", time.Time{}, &sendError{reason: "not_connected", err: whatsmeow.ErrNotLoggedIn, transient: true}
	}
	info := quoteContext(chat, *cli.Store.ID, cli.Store.LID, item.Quote)
	var msg *waE2E.Message
	if item.Kind == "text" {
		msg = textMessage(item.Text, info)
	} else {
		data, err := b.portal.fetchOutboxFile(ctx, item.ID)
		var refused *httpError
		switch {
		case errors.As(err, &refused) && refused.Status == 404:
			return "", time.Time{}, &sendError{reason: "no_file", err: err}
		case err != nil:
			return "", time.Time{}, &sendError{reason: "no_file", err: err, transient: true}
		}
		up, err := cli.Upload(ctx, data, mediaTypeOf(item.Kind))
		if err != nil {
			return "", time.Time{}, &sendError{reason: "upload", err: err, transient: true}
		}
		msg = fileMessage(item, up, info)
	}
	// Один id на все попытки сообщения (sender.go): повтор после сбоя «дошло ли?» кандидату дублем не придёт.
	var extra []whatsmeow.SendRequestExtra
	if mediaIDPattern.MatchString(item.MessageID) {
		extra = append(extra, whatsmeow.SendRequestExtra{ID: types.MessageID(item.MessageID)})
	}
	resp, err := cli.SendMessage(ctx, chat, msg, extra...)
	if err != nil {
		return "", time.Time{}, err
	}
	return resp.ID, resp.Timestamp, nil
}

// receiptEvent — отметка кандидата о нашем сообщении (доставлено, прочитано, прослушано) для портала или nil.
// Отметки своих устройств (read-self и т. п.) и групп — мимо: их ловит guard как «телефон в сети».
func receiptEvent(evt *events.Receipt) map[string]any {
	if evt.IsFromMe || evt.IsGroup {
		return nil
	}
	if server := evt.Chat.Server; server != types.DefaultUserServer && server != types.HiddenUserServer {
		return nil
	}
	var status string
	switch evt.Type {
	case types.ReceiptTypeDelivered:
		status = "delivered"
	case types.ReceiptTypeRead:
		status = "read"
	case types.ReceiptTypePlayed:
		status = "played"
	default:
		return nil
	}
	ids := make([]string, 0, len(evt.MessageIDs))
	for _, id := range evt.MessageIDs {
		if mediaIDPattern.MatchString(id) {
			ids = append(ids, id)
		}
	}
	if len(ids) == 0 {
		return nil
	}
	return map[string]any{"type": "receipt", "ids": ids, "status": status, "ts": evt.Timestamp.Unix()}
}
