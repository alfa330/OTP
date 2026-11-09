package main

// Файлы сообщений → портал (владелец 09.11.2026: «давай реализуем, так как есть ещё и аудио файлы,
// которые тоже надо бы видеть»). Событие о сообщении уходит сразу (convert.go), файл — следом из своей
// очереди: скачать у WhatsApp так же, как это делает любой WhatsApp Web (через прокси шлюза), и отдать
// порталу подписанным запросом. По одному и с паузой: после простоя очередь не выкачивается залпом.
//
// Повторы — с растущей паузой: ссылка на файл живёт у WhatsApp неделями, спешить некуда. Файл, которого
// у WhatsApp уже нет (удалён у всех, истёк), и отказ портала — событие media_failed: в портале плашка
// «не сохранился — на телефоне», а не вечное «загружается».

import (
	"context"
	"errors"
	"regexp"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

const (
	maxMediaBytes    = 25 << 20 // больше не качаем: портал покажет «больше 25 МБ — откройте на телефоне»
	maxMediaAttempts = 8
	mediaPause       = 2 * time.Second
	mediaFirstDelay  = 3 * time.Second // событие о сообщении должно успеть дойти до портала раньше файла
	mediaIdle        = 30 * time.Second
	mediaTimeout     = 3 * time.Minute
)

// Паузы между попытками: 30 с, минута, 5 и 15 минут, час, 3 и 6 часов — всего около 11 часов.
var mediaDelays = []time.Duration{30 * time.Second, time.Minute, 5 * time.Minute, 15 * time.Minute, time.Hour,
	3 * time.Hour, 6 * time.Hour}

// id сообщения идёт в путь запроса порталу (путь подписан) — только безопасные символы.
var mediaIDPattern = regexp.MustCompile(`^[A-Za-z0-9._-]{1,128}$`)

// mediaJobOf — файл сообщения для очереди или nil (не файл, больше предела, служебное сообщение).
func mediaJobOf(evt *events.Message) *mediaJob {
	if evt.Message == nil || evt.Message.GetProtocolMessage() != nil || !mediaIDPattern.MatchString(evt.Info.ID) {
		return nil
	}
	kind, part, size := mediaPart(evt.Message)
	if part == nil || size > maxMediaBytes {
		return nil
	}
	message, ok := part.(proto.Message)
	if !ok {
		return nil
	}
	raw, err := proto.Marshal(message)
	if err != nil {
		return nil
	}
	return &mediaJob{WAID: evt.Info.ID, Kind: kind, Part: raw}
}

// markFetch — портал показывает «загружается…», пока файл не придёт; у старых сообщений метки нет.
func markFetch(event map[string]any) {
	media, _ := event["media"].(map[string]any)
	if media == nil {
		media = map[string]any{}
		event["media"] = media
	}
	media["fetch"] = true
}

// partOf — часть protobuf из очереди обратно в сообщение, которое умеет качать whatsmeow.
func partOf(kind string, raw []byte) (whatsmeow.DownloadableMessage, error) {
	var part interface {
		proto.Message
		whatsmeow.DownloadableMessage
	}
	switch kind {
	case "image":
		part = &waE2E.ImageMessage{}
	case "video":
		part = &waE2E.VideoMessage{}
	case "audio", "voice":
		part = &waE2E.AudioMessage{}
	case "document":
		part = &waE2E.DocumentMessage{}
	default:
		return nil, errors.New("неизвестный вид файла: " + kind)
	}
	if err := proto.Unmarshal(raw, part); err != nil {
		return nil, err
	}
	return part, nil
}

type mediaWorker struct {
	spool    *spool
	log      waLog.Logger
	ready    func() bool // соединение с WhatsApp есть: адреса файлов выдаёт оно
	download func(ctx context.Context, part whatsmeow.DownloadableMessage) ([]byte, error)
	upload   func(ctx context.Context, path string, data []byte) error
}

func (w *mediaWorker) run(ctx context.Context) {
	for {
		job, err := w.spool.nextMedia(time.Now())
		if err != nil {
			w.log.Warnf("очередь файлов не читается: %v", err)
		}
		if job != nil && w.ready() {
			w.process(ctx, job, time.Now())
			select {
			case <-ctx.Done():
				return
			case <-time.After(mediaPause):
			}
			continue
		}
		wait := mediaIdle
		if next, ok := w.spool.nextMediaAt(); ok && job == nil {
			if until := time.Until(next); until < wait {
				wait = max(until, 500*time.Millisecond)
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-w.spool.mediaWake:
		case <-time.After(wait):
		}
	}
}

func (w *mediaWorker) process(ctx context.Context, job *mediaJob, now time.Time) {
	part, err := partOf(job.Kind, job.Part)
	if err != nil {
		w.fail(job, "broken", err)
		return
	}
	ctx, cancel := context.WithTimeout(ctx, mediaTimeout)
	defer cancel()
	data, err := w.download(ctx, part)
	switch {
	case err == nil && len(data) > maxMediaBytes:
		w.fail(job, "too_large", nil)
		return
	case err == nil:
	case errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith404), errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith410),
		errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith403), errors.Is(err, whatsmeow.ErrNoURLPresent),
		errors.Is(err, whatsmeow.ErrInvalidMediaSHA256):
		// Файла у WhatsApp уже нет (удалён у всех, истёк) или он битый — повтор не поможет.
		w.fail(job, "gone", err)
		return
	default:
		w.retry(job, now, err)
		return
	}
	err = w.upload(ctx, mediaPathPrefix+job.WAID, data)
	var refused *httpError
	switch {
	case err == nil:
		if dropErr := w.spool.dropMedia(job.ID); dropErr != nil {
			w.log.Warnf("очередь файлов: не убрать отправленный %s: %v", job.WAID, dropErr)
		}
	case errors.As(err, &refused) && refused.Status == 404:
		w.retry(job, now, err) // сообщение ещё не дошло до портала событием
	case errors.As(err, &refused) && refused.Status >= 400 && refused.Status < 500 && refused.Status != 401 && refused.Status != 429:
		w.fail(job, "rejected", err)
	default:
		w.retry(job, now, err)
	}
}

func (w *mediaWorker) retry(job *mediaJob, now time.Time, err error) {
	attempts := job.Attempts + 1
	if attempts >= maxMediaAttempts {
		w.fail(job, "retries", err)
		return
	}
	delay := mediaDelays[min(attempts-1, len(mediaDelays)-1)]
	if attempts == 1 || attempts%3 == 0 {
		w.log.Warnf("файл %s: попытка %d не удалась (%v) — следующая через %s", job.WAID, attempts, err, delay)
	}
	if dbErr := w.spool.delayMedia(job.ID, attempts, now.Add(delay)); dbErr != nil {
		w.log.Warnf("очередь файлов: попытка не записана: %v", dbErr)
	}
}

// fail — файл не будет сохранён: портал узнаёт об этом событием (плашка «не сохранился — на телефоне»).
func (w *mediaWorker) fail(job *mediaJob, reason string, err error) {
	w.log.Warnf("файл %s не сохранён: %s (%v)", job.WAID, reason, err)
	if pushErr := w.spool.push(map[string]any{"type": "media_failed", "id": job.WAID, "reason": reason}); pushErr != nil {
		w.log.Errorf("очередь: отметка о файле не записана: %v", pushErr)
	}
	if dropErr := w.spool.dropMedia(job.ID); dropErr != nil {
		w.log.Warnf("очередь файлов: не убрать %s: %v", job.WAID, dropErr)
	}
}
