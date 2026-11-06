package main

// Сообщение whatsmeow → событие для портала (hiring/whatsapp.parse_message). Только личные чаты:
// группы, рассылки, статусы и каналы номеру найма не нужны. Содержимое файлов не качается —
// только вид, подпись и размер (этап 1).

import (
	"context"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

// resolvePN — номер по LID из хранилища whatsmeow (соответствия приходят с сообщениями и историей).
type resolvePN func(ctx context.Context, lid types.JID) types.JID

func directChat(info types.MessageInfo) bool {
	if info.IsGroup {
		return false
	}
	server := info.Chat.Server
	return server == types.DefaultUserServer || server == types.HiddenUserServer
}

// peer — номер и LID собеседника. Чат приходит либо по номеру, либо по LID; второе — в Alt
// (sender_pn / peer_recipient_pn), иначе — из уже сохранённого соответствия.
func peer(ctx context.Context, info types.MessageInfo, resolve resolvePN) (phone, lid string) {
	chat := info.Chat.ToNonAD()
	alt := info.SenderAlt
	if info.IsFromMe {
		alt = info.RecipientAlt
	}
	alt = alt.ToNonAD()
	if chat.Server == types.DefaultUserServer {
		phone = chat.User
		if alt.Server == types.HiddenUserServer {
			lid = alt.User
		}
		return phone, lid
	}
	lid = chat.User
	if alt.Server == types.DefaultUserServer {
		return alt.User, lid
	}
	if resolve != nil {
		if pn := resolve(ctx, chat); !pn.IsEmpty() && pn.Server == types.DefaultUserServer {
			phone = pn.User
		}
	}
	return phone, lid
}

func textOf(msg *waE2E.Message) string {
	if msg == nil {
		return ""
	}
	if text := msg.GetConversation(); text != "" {
		return text
	}
	return msg.GetExtendedTextMessage().GetText()
}

func contextOf(msg *waE2E.Message) *waE2E.ContextInfo {
	switch {
	case msg.GetExtendedTextMessage() != nil:
		return msg.GetExtendedTextMessage().GetContextInfo()
	case msg.GetImageMessage() != nil:
		return msg.GetImageMessage().GetContextInfo()
	case msg.GetVideoMessage() != nil:
		return msg.GetVideoMessage().GetContextInfo()
	case msg.GetAudioMessage() != nil:
		return msg.GetAudioMessage().GetContextInfo()
	case msg.GetDocumentMessage() != nil:
		return msg.GetDocumentMessage().GetContextInfo()
	case msg.GetStickerMessage() != nil:
		return msg.GetStickerMessage().GetContextInfo()
	}
	return nil
}

// adOf — реклама Click-to-WhatsApp, с которой пришёл человек (первое сообщение из объявления).
func adOf(msg *waE2E.Message) map[string]string {
	info := contextOf(msg)
	if info == nil {
		return nil
	}
	reply := info.GetExternalAdReply()
	ad := map[string]string{
		"title":             reply.GetTitle(),
		"body":              reply.GetBody(),
		"source_url":        reply.GetSourceURL(),
		"source_id":         reply.GetSourceID(),
		"source_type":       reply.GetSourceType(),
		"ctwa_clid":         reply.GetCtwaClid(),
		"source_app":        reply.GetSourceApp(),
		"conversion_source": info.GetConversionSource(),
		"entry_point":       info.GetEntryPointConversionSource(),
	}
	for name, value := range ad {
		if value == "" {
			delete(ad, name)
		}
	}
	if len(ad) == 0 {
		return nil
	}
	return ad
}

// classify — вид, текст (подпись) и описание файла.
func classify(msg *waE2E.Message) (kind, text string, media map[string]any) {
	put := func(mime string, size uint64, seconds uint32, name string) map[string]any {
		out := map[string]any{}
		if mime != "" {
			out["mime"] = mime
		}
		if size > 0 {
			out["size"] = size
		}
		if seconds > 0 {
			out["seconds"] = seconds
		}
		if name != "" {
			out["file_name"] = name
		}
		if len(out) == 0 {
			return nil
		}
		return out
	}
	switch {
	case msg.GetImageMessage() != nil:
		m := msg.GetImageMessage()
		return "image", m.GetCaption(), put(m.GetMimetype(), m.GetFileLength(), 0, "")
	case msg.GetVideoMessage() != nil:
		m := msg.GetVideoMessage()
		return "video", m.GetCaption(), put(m.GetMimetype(), m.GetFileLength(), m.GetSeconds(), "")
	case msg.GetAudioMessage() != nil:
		m := msg.GetAudioMessage()
		if m.GetPTT() {
			return "voice", "", put(m.GetMimetype(), m.GetFileLength(), m.GetSeconds(), "")
		}
		return "audio", "", put(m.GetMimetype(), m.GetFileLength(), m.GetSeconds(), "")
	case msg.GetDocumentWithCaptionMessage().GetMessage().GetDocumentMessage() != nil:
		m := msg.GetDocumentWithCaptionMessage().GetMessage().GetDocumentMessage()
		return "document", m.GetCaption(), put(m.GetMimetype(), m.GetFileLength(), 0, m.GetFileName())
	case msg.GetDocumentMessage() != nil:
		m := msg.GetDocumentMessage()
		return "document", m.GetCaption(), put(m.GetMimetype(), m.GetFileLength(), 0, m.GetFileName())
	case msg.GetStickerMessage() != nil:
		return "sticker", "", nil
	case msg.GetLocationMessage() != nil:
		m := msg.GetLocationMessage()
		return "location", firstNonEmpty(m.GetName(), m.GetAddress()), nil
	case msg.GetLiveLocationMessage() != nil:
		return "location", msg.GetLiveLocationMessage().GetCaption(), nil
	case msg.GetContactMessage() != nil:
		return "contact", msg.GetContactMessage().GetDisplayName(), nil
	case msg.GetContactsArrayMessage() != nil:
		return "contact", msg.GetContactsArrayMessage().GetDisplayName(), nil
	case msg.GetButtonsResponseMessage() != nil:
		return "text", msg.GetButtonsResponseMessage().GetSelectedDisplayText(), nil
	case msg.GetListResponseMessage() != nil:
		return "text", msg.GetListResponseMessage().GetTitle(), nil
	case msg.GetTemplateButtonReplyMessage() != nil:
		return "text", msg.GetTemplateButtonReplyMessage().GetSelectedDisplayText(), nil
	}
	if text := textOf(msg); text != "" {
		return "text", text, nil
	}
	return "other", "", nil
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

// serviceMessage — служебное сообщение протокола, а не то, что написал человек: в портал не идёт.
func serviceMessage(msg *waE2E.Message) bool {
	return msg.GetReactionMessage() != nil || msg.GetEncReactionMessage() != nil ||
		msg.GetPollUpdateMessage() != nil || msg.GetKeepInChatMessage() != nil ||
		msg.GetPinInChatMessage() != nil || msg.GetSenderKeyDistributionMessage() != nil
}

// convertMessage — событие для портала или nil (не личный чат, служебное, неинтересное).
func convertMessage(ctx context.Context, evt *events.Message, resolve resolvePN) map[string]any {
	info := evt.Info
	if !directChat(info) || evt.Message == nil {
		return nil
	}
	phone, lid := peer(ctx, info, resolve)
	if phone == "" && lid == "" {
		return nil
	}
	event := map[string]any{
		"type":    "message",
		"id":      info.ID,
		"chat":    info.Chat.ToNonAD().String(),
		"phone":   phone,
		"lid":     lid,
		"from_me": info.IsFromMe,
		"ts":      info.Timestamp.Unix(),
	}
	msg := evt.Message
	if protocol := msg.GetProtocolMessage(); protocol != nil {
		switch protocol.GetType() {
		case waE2E.ProtocolMessage_REVOKE:
			event["revoke_of"] = protocol.GetKey().GetID()
			return event
		case waE2E.ProtocolMessage_MESSAGE_EDIT:
			event["edit_of"] = protocol.GetKey().GetID()
			_, text, _ := classify(protocol.GetEditedMessage())
			event["text"] = text
			return event
		}
		return nil
	}
	if serviceMessage(msg) {
		return nil
	}
	kind, text, media := classify(msg)
	event["kind"] = kind
	event["text"] = text
	if media != nil {
		event["media"] = media
	}
	if ad := adOf(msg); ad != nil {
		event["ad"] = ad
	}
	// Имя отправителя — только у входящих: в исходящем это наше собственное имя.
	if !info.IsFromMe && info.PushName != "" {
		event["push_name"] = info.PushName
	}
	return event
}
