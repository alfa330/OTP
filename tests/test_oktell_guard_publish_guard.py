"""Защита публикации версии.

Право выложить версию = право заменить программу на всех машинах операторов,
поэтому здесь проверяется не только «есть ли ключ», но и что именно публикуют.
"""

from oktell_guard import routes


# ─── сила ключа ──────────────────────────────────────────────────────────────

def test_weak_tokens_disable_publishing():
    """Со слабым ключом публикация не включается вовсе: «почти защищённо» —
    это незащищённо, а подбирают такое быстро."""
    for weak in ('', '123456', 'publish', 'a' * 23, 'толькобуквы' * 3, '1234567890123456789012345'):
        assert routes.token_is_strong(weak) is False, weak


def test_strong_token_accepted():
    assert routes.token_is_strong('pub_7Kq2fZs9RmT4xW1bYn6Ld3Vc') is True


# ─── что публикуют ───────────────────────────────────────────────────────────

def test_only_windows_executable_accepted():
    """Иначе одна перепутанная кнопка разошлёт операторам произвольный файл,
    и агенты честно поставят его вместо себя."""
    assert routes.is_windows_executable(b'MZ\x90\x00') is True
    assert routes.is_windows_executable(b'PK\x03\x04') is False   # zip
    assert routes.is_windows_executable(b'#!/bin/sh') is False
    assert routes.is_windows_executable(b'') is False


def test_version_format():
    assert routes.is_valid_version('1.0.0') is True
    assert routes.is_valid_version('1.2') is True
    for bad in ('', 'latest', '1', '1.0.0-beta', 'v1.0.0', '1.0.0.0.0', 'a.b.c', '99999.1.1'):
        assert routes.is_valid_version(bad) is False, bad


# ─── перебор ─────────────────────────────────────────────────────────────────

def test_repeated_failures_lock_the_address():
    routes._publish_failures.clear()
    now = 1_000_000.0
    for _ in range(routes.PUBLISH_FAIL_LIMIT):
        routes.note_publish_failure('10.0.0.1', now)
    assert routes.publish_locked('10.0.0.1', now) is True
    # Соседний адрес это не задевает.
    assert routes.publish_locked('10.0.0.2', now) is False


def test_lock_expires_with_the_window():
    routes._publish_failures.clear()
    now = 1_000_000.0
    for _ in range(routes.PUBLISH_FAIL_LIMIT):
        routes.note_publish_failure('10.0.0.3', now)
    later = now + routes.PUBLISH_FAIL_WINDOW_S + 1
    assert routes.publish_locked('10.0.0.3', later) is False


def test_size_bounds_are_sane():
    """Пустышка и гигабайт одинаково подозрительны для 15-мегабайтного агента."""
    assert routes.MIN_RELEASE_BYTES < 15 * 1024 * 1024 < routes.MAX_RELEASE_BYTES
