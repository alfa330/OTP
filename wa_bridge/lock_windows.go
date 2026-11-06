//go:build windows

package main

import "errors"

var errLocked = errors.New("сессия уже открыта другим процессом")

// acquireLock на Windows — только для тестов на рабочей машине: сервис живёт на Linux-шлюзе.
func acquireLock(path string) (func(), error) {
	return func() {}, nil
}
