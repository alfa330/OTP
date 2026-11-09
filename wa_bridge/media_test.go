package main

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
)

func u64(value uint64) *uint64 { return &value }

func boolean(value bool) *bool { return &value }

func voiceMessage(size uint64) *waE2E.Message {
	return &waE2E.Message{AudioMessage: &waE2E.AudioMessage{
		PTT: boolean(true), Mimetype: str("audio/ogg; codecs=opus"), FileLength: u64(size),
		DirectPath: str("/v/t62.7117-24/voice"), MediaKey: []byte("0123456789abcdef0123456789abcdef"),
		FileSHA256: []byte("sha"), FileEncSHA256: []byte("enc"),
	}}
}

func TestMediaJobOfKindsAndLimits(t *testing.T) {
	chat := types.NewJID("77055552233", types.DefaultUserServer)
	job := mediaJobOf(incoming(chat, voiceMessage(4096)))
	if job == nil || job.Kind != "voice" || job.WAID != "3EB0A1" {
		t.Fatalf("голосовое: %#v", job)
	}
	part, err := partOf(job.Kind, job.Part)
	if err != nil || part.GetDirectPath() != "/v/t62.7117-24/voice" || string(part.GetFileEncSHA256()) != "enc" {
		t.Fatalf("часть из очереди: %v %#v", err, part)
	}
	document := &waE2E.Message{DocumentWithCaptionMessage: &waE2E.FutureProofMessage{Message: &waE2E.Message{
		DocumentMessage: &waE2E.DocumentMessage{FileName: str("cv.pdf"), FileLength: u64(2048), DirectPath: str("/d")}}}}
	if job := mediaJobOf(incoming(chat, document)); job == nil || job.Kind != "document" {
		t.Fatalf("документ с подписью: %#v", job)
	}
	// Больше 25 МБ, стикер, текст, правка и id с чужими символами — в очередь не идут.
	sticker := &waE2E.Message{StickerMessage: &waE2E.StickerMessage{DirectPath: str("/s")}}
	edit := &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{Type: waE2E.ProtocolMessage_MESSAGE_EDIT.Enum()}}
	for name, msg := range map[string]*waE2E.Message{"большой": voiceMessage(maxMediaBytes + 1), "стикер": sticker,
		"текст": {Conversation: str("привет")}, "правка": edit} {
		if job := mediaJobOf(incoming(chat, msg)); job != nil {
			t.Fatalf("%s попал в очередь: %#v", name, job)
		}
	}
	odd := incoming(chat, voiceMessage(10))
	odd.Info.ID = "../poll"
	if mediaJobOf(odd) != nil {
		t.Fatal("id с «/» попал в путь запроса")
	}
	event := map[string]any{"media": map[string]any{"size": 10}}
	markFetch(event)
	bare := map[string]any{}
	markFetch(bare)
	if event["media"].(map[string]any)["fetch"] != true || bare["media"].(map[string]any)["fetch"] != true {
		t.Fatalf("метка загрузки: %#v %#v", event, bare)
	}
}

type mediaFakes struct {
	downloads []error
	uploads   []error
	data      []byte
	paths     []string
}

func newMediaWorker(t *testing.T, fakes *mediaFakes) (*mediaWorker, *spool) {
	t.Helper()
	sp, err := openSpool(filepath.Join(t.TempDir(), "spool.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { sp.db.Close() })
	worker := &mediaWorker{spool: sp, log: waLog.Noop, ready: func() bool { return true },
		download: func(context.Context, whatsmeow.DownloadableMessage) ([]byte, error) {
			var err error
			if len(fakes.downloads) > 0 {
				err, fakes.downloads = fakes.downloads[0], fakes.downloads[1:]
			}
			if err != nil {
				return nil, err
			}
			return fakes.data, nil
		},
		upload: func(_ context.Context, path string, _ []byte) error {
			fakes.paths = append(fakes.paths, path)
			var err error
			if len(fakes.uploads) > 0 {
				err, fakes.uploads = fakes.uploads[0], fakes.uploads[1:]
			}
			return err
		}}
	return worker, sp
}

func queueVoice(t *testing.T, sp *spool, at time.Time) *mediaJob {
	t.Helper()
	job := mediaJobOf(incoming(types.NewJID("77055552233", types.DefaultUserServer), voiceMessage(4096)))
	if err := sp.pushMedia(*job, at); err != nil {
		t.Fatal(err)
	}
	next, err := sp.nextMedia(at)
	if err != nil || next == nil {
		t.Fatalf("очередь пуста: %v", err)
	}
	return next
}

func failures(t *testing.T, sp *spool) []map[string]any {
	t.Helper()
	_, bodies, err := sp.batch(10)
	if err != nil {
		t.Fatal(err)
	}
	var out []map[string]any
	for _, body := range bodies {
		var event map[string]any
		_ = json.Unmarshal(body, &event)
		out = append(out, event)
	}
	return out
}

func TestMediaUploadAndQueueTiming(t *testing.T) {
	fakes := &mediaFakes{data: []byte("OggS…")}
	worker, sp := newMediaWorker(t, fakes)
	now := time.Unix(1792047600, 0)
	if err := sp.pushMedia(mediaJob{WAID: "later", Kind: "voice", Part: []byte{}}, now.Add(time.Minute)); err != nil {
		t.Fatal(err)
	}
	if job, _ := sp.nextMedia(now); job != nil {
		t.Fatalf("файл раньше своей очереди: %#v", job)
	}
	if at, ok := sp.nextMediaAt(); !ok || !at.Equal(now.Add(time.Minute)) {
		t.Fatalf("ближайший файл: %v %v", at, ok)
	}
	job := queueVoice(t, sp, now)
	worker.process(context.Background(), job, now)
	if len(fakes.paths) != 1 || fakes.paths[0] != "/api/hiring/wa/agent/media/3EB0A1" {
		t.Fatalf("путь загрузки: %#v", fakes.paths)
	}
	if next, _ := sp.nextMedia(now); next != nil {
		t.Fatalf("отправленный файл остался в очереди: %#v", next)
	}
}

func TestMediaRetriesAndFailures(t *testing.T) {
	now := time.Unix(1792047600, 0)
	// Портал ещё не знает сообщение (404) — повтор через 30 с; сеть — тоже повтор.
	fakes := &mediaFakes{data: []byte("x"), uploads: []error{&httpError{Status: 404}}}
	worker, sp := newMediaWorker(t, fakes)
	job := queueVoice(t, sp, now)
	worker.process(context.Background(), job, now)
	if next, _ := sp.nextMedia(now.Add(29 * time.Second)); next != nil {
		t.Fatal("повтор раньше паузы")
	}
	next, _ := sp.nextMedia(now.Add(30 * time.Second))
	if next == nil || next.Attempts != 1 {
		t.Fatalf("повтор после 404: %#v", next)
	}
	if got := failures(t, sp); len(got) != 0 {
		t.Fatalf("лишнее событие: %#v", got)
	}

	// Файла у WhatsApp уже нет — без повторов, событие media_failed.
	fakes = &mediaFakes{downloads: []error{whatsmeow.ErrMediaDownloadFailedWith410}}
	worker, sp = newMediaWorker(t, fakes)
	worker.process(context.Background(), queueVoice(t, sp, now), now)
	if got := failures(t, sp); len(got) != 1 || got[0]["type"] != "media_failed" || got[0]["reason"] != "gone" || got[0]["id"] != "3EB0A1" {
		t.Fatalf("событие о пропавшем файле: %#v", got)
	}
	if next, _ := sp.nextMedia(now.Add(24 * time.Hour)); next != nil {
		t.Fatal("пропавший файл остался в очереди")
	}

	// Портал отверг файл (400) — тоже без повторов; больше 25 МБ после скачивания — too_large.
	fakes = &mediaFakes{data: []byte("x"), uploads: []error{&httpError{Status: 400}}}
	worker, sp = newMediaWorker(t, fakes)
	worker.process(context.Background(), queueVoice(t, sp, now), now)
	if got := failures(t, sp); len(got) != 1 || got[0]["reason"] != "rejected" {
		t.Fatalf("отказ портала: %#v", got)
	}
	fakes = &mediaFakes{data: []byte(strings.Repeat("x", maxMediaBytes+1))}
	worker, sp = newMediaWorker(t, fakes)
	worker.process(context.Background(), queueVoice(t, sp, now), now)
	if got := failures(t, sp); len(got) != 1 || got[0]["reason"] != "too_large" || len(fakes.paths) != 0 {
		t.Fatalf("большой файл: %#v %#v", got, fakes.paths)
	}

	// Сеть лежит каждый раз — после maxMediaAttempts попыток событие retries.
	network := errors.New("dial tcp: i/o timeout")
	fakes = &mediaFakes{}
	for i := 0; i < maxMediaAttempts; i++ {
		fakes.downloads = append(fakes.downloads, network)
	}
	worker, sp = newMediaWorker(t, fakes)
	job = queueVoice(t, sp, now)
	moment := now
	for attempt := 0; attempt < maxMediaAttempts; attempt++ {
		worker.process(context.Background(), job, moment)
		moment = moment.Add(7 * time.Hour)
		if job, _ = sp.nextMedia(moment); job == nil {
			break
		}
	}
	if got := failures(t, sp); len(got) != 1 || got[0]["reason"] != "retries" {
		t.Fatalf("исчерпаны попытки: %#v", got)
	}
}
