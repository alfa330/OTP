//go:build !windows

package main

import (
	"errors"
	"os"
	"syscall"
)

var errLocked = errors.New("сессия уже открыта другим процессом")

// acquireLock — одна сессия, один процесс: вторая копия с теми же ключами выбила бы первую
// (StreamReplaced), а это лишний сигнал для антиспама. Замок снимает ядро, если процесс умер.
func acquireLock(path string) (func(), error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		file.Close()
		return nil, errLocked
	}
	return func() {
		_ = syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
		file.Close()
	}, nil
}
