// Сервис номера WhatsApp найма на шлюзе (владелец 06.11.2026).
//
// Реклама на найм ведёт в WhatsApp на отдельный номер. Номер остаётся в приложении WhatsApp
// Business на телефоне рекрутера, а этот сервис подключён к нему связанным устройством (как
// WhatsApp Web, библиотека whatsmeow) и приносит в портал каждое сообщение личных чатов: первое
// входящее чата становится откликом в «Воронке найма» (hiring/whatsapp.py). Сам сервис ничего
// не отправляет. Правила безопасности номера — в bridge.go и guard.go.
//
// Настройки — окружением (на шлюзе — /etc/otp-gateway/wa.env):
//
//	CDR_BRIDGE_PORTAL     адрес портала, как у моста
//	CDR_PORTAL_PROXY      прокси шлюза — единственный выход наружу: и к порталу, и к WhatsApp
//	CDR_PORTAL_CA_BUNDLE  необязательно: корневые сертификаты портала
//	WA_AGENT_KEY_FILE     закрытый ключ подписи шлюза (копия ключа моста, владелец — uid сервиса)
//	WA_DATA_DIR           сессия, очередь и предохранители (том, права 700)
//	WA_DEVICE_NAME        имя в «Связанных устройствах» телефона
//	WA_HEARTBEAT_FILE     файл пульса для проверки здоровья контейнера
//	WA_LOG_LEVEL          INFO по умолчанию (DEBUG пишет содержимое протокола — не включать надолго)
package main

import (
	"context"
	"database/sql"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
	waLog "go.mau.fi/whatsmeow/util/log"
	_ "modernc.org/sqlite"
)

type config struct {
	portal    string
	proxy     string
	caBundle  string
	keyFile   string
	dataDir   string
	device    string
	heartbeat string
	logLevel  string
}

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func loadConfig() config {
	return config{
		portal:    env("CDR_BRIDGE_PORTAL", ""),
		proxy:     env("CDR_PORTAL_PROXY", ""),
		caBundle:  env("CDR_PORTAL_CA_BUNDLE", ""),
		keyFile:   env("WA_AGENT_KEY_FILE", "/etc/otp-gateway/wa-agent.key"),
		dataDir:   env("WA_DATA_DIR", "/var/lib/wa-bridge"),
		device:    env("WA_DEVICE_NAME", "iCore Найм"),
		heartbeat: env("WA_HEARTBEAT_FILE", "/tmp/wa-heartbeat"),
		logLevel:  env("WA_LOG_LEVEL", "INFO"),
	}
}

func main() {
	cfg := loadConfig()
	log := waLog.Stdout("Bridge", cfg.logLevel, false)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	if err := run(ctx, cfg, log); err != nil {
		log.Errorf("сервис остановлен: %v", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, cfg config, log waLog.Logger) error {
	if err := os.MkdirAll(cfg.dataDir, 0o700); err != nil {
		return err
	}
	// Одна сессия — один процесс. Вторая копия не подключается и порталу не пишет (иначе её
	// «ожидание» затирало бы состояние живой): просто ждёт, пока замок освободится.
	release, err := acquireLock(filepath.Join(cfg.dataDir, "bridge.lock"))
	for err == errLocked {
		log.Warnf("сессия уже открыта другой копией сервиса — жду")
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(30 * time.Second):
		}
		release, err = acquireLock(filepath.Join(cfg.dataDir, "bridge.lock"))
	}
	if err != nil {
		return err
	}
	defer release()

	g, err := loadGuard(filepath.Join(cfg.dataDir, "guard.json"))
	if err != nil {
		return err
	}
	if tooMany, err := g.recordStart(time.Now()); err != nil {
		return err
	} else if tooMany {
		if fresh, _ := g.park("start_fuse", 0, time.Time{}, time.Now()); fresh {
			log.Warnf("ОСТАНОВЛЕН: больше %d стартов за час", maxStartsPerHour)
		}
	}
	signer, err := loadSigner(cfg.keyFile)
	if err != nil {
		return err
	}
	p, err := newPortal(cfg.portal, cfg.proxy, cfg.caBundle, signer)
	if err != nil {
		return err
	}
	// Имя в «Связанных устройствах» — чтобы рекрутеры не удалили устройство как чужое. Только
	// имя: версия ОС в рукопожатии остаётся библиотечной (SetOSInfo меняет и её).
	name := cfg.device
	store.DeviceProps.Os = &name

	db, err := sql.Open("sqlite", "file:"+filepath.Join(cfg.dataDir, "store.db")+
		"?_pragma=foreign_keys(1)&_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)")
	if err != nil {
		return err
	}
	defer db.Close()
	container := sqlstore.NewWithDB(db, "sqlite3", waLog.Stdout("Store", cfg.logLevel, false))
	if err := container.Upgrade(ctx); err != nil {
		return err
	}
	sp, err := openSpool(filepath.Join(cfg.dataDir, "spool.db"))
	if err != nil {
		return err
	}
	defer sp.db.Close()

	b, err := newBridge(ctx, cfg, log, container, g, sp, p)
	if err != nil {
		return err
	}
	log.Infof("wa-bridge %s, WhatsApp Web %s", bridgeVersion, store.GetWAVersion().String())
	b.start()
	go sp.flush(ctx, b.sendEvents, log.Warnf)
	go b.pollLoop()
	files := &mediaWorker{spool: sp, log: log, upload: p.upload,
		ready: func() bool { return b.currentState() == stateConnected },
		download: func(ctx context.Context, part whatsmeow.DownloadableMessage) ([]byte, error) {
			return b.client().Download(ctx, part)
		}}
	go files.run(ctx)
	go b.resend.run(ctx)

	<-ctx.Done()
	log.Infof("остановка по сигналу: закрываю соединение")
	b.client().Disconnect()
	return nil
}
