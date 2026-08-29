# -*- coding: utf-8 -*-
"""Тумблер «Линия / Чат» не должен переключаться сам.

Жалоба владельца 29.08.2026: после нескольких переключений тумблер
самостоятельно прыгает на прошлое направление и раздел «лагает».

Причина — гонка, а не тумблер. Снапшот грузится асинхронно, а `applySnapshot`
безусловно делал `setDirection(safe.direction_mode)`. Ответ, выписанный ДО
переключения, приезжал после и возвращал человека на прошлый прогон вместе с
чужими сменами. Дальше становилось только хуже: `snapshotRequestRef` не пускал
запрос нового направления, пока чужой в пути, и отложенный запрос уходил уже за
откаченным направлением — то есть клик пользователя терялся целиком.

Воспроизведено в браузере на стенде с задержкой ответа 800 мс: до правки серия
переключений заканчивалась чужим направлением и одним самопроизвольным скачком
за три секунды покоя, после — ноль скачков и запросы ровно своего направления.

Здесь сторожим сам код: правило живёт в React-компоненте, поднимать его целиком
ради проверки гонки дороже, чем закрепить каждое звено текстом.
"""
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEW = os.path.join(REPO_ROOT, "src", "components", "resources", "ShiftAuctionView.jsx")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class DirectionToggleRaceTests(unittest.TestCase):
    def setUp(self):
        self.source = _read(VIEW)

    def _block(self, start_marker, end_marker):
        start = self.source.index(start_marker)
        return self.source[start:self.source.index(end_marker, start)]

    def test_snapshot_never_moves_the_toggle_of_someone_who_owns_it(self):
        """Управляющему направление выбирает тумблер, оператору — сервер."""
        apply_block = self._block("const canSwitch = Boolean(safe.can_switch_direction);",
                                  "setCanSwitchDirection(canSwitch);")
        self.assertIn("if (safe.direction_mode && !canSwitch) setDirection(", apply_block)
        self.assertNotIn(
            "if (safe.direction_mode) setDirection(",
            self.source,
            "безусловный setDirection из снапшота и есть тот самый самопроизвольный скачок",
        )

    def test_switch_starts_a_new_generation_and_drops_the_request_in_flight(self):
        switch = self._block("const handleSwitchDirection = useCallback(", "}, [direction]);")
        self.assertIn("directionTokenRef.current += 1", switch)
        self.assertIn("snapshotAbortRef.current?.abort?.()", switch)
        # Замок обязан сняться, иначе загрузка нового направления встанет в
        # очередь за чужим ответом и клик будет выглядеть «залипшим».
        self.assertIn("snapshotRequestRef.current = false", switch)
        self.assertIn("snapshotEtagRef.current = ''", switch)

    def test_stale_answer_is_neither_applied_nor_remembered(self):
        fetch = self._block("const fetchSnapshot = useCallback(",
                            "}, [apiRoot, applySnapshot, buildHeaders, notify, user?.id, withDirection]);")
        self.assertIn("const token = directionTokenRef.current;", fetch)
        self.assertIn("const isCurrent = () => token === directionTokenRef.current;", fetch)
        # Возврат ДО записи ETag: иначе следующий запрос получит 304 и экран
        # навсегда останется с данными чужого направления.
        applied = fetch.index("if (!isCurrent()) return;")
        self.assertLess(applied, fetch.index("snapshotEtagRef.current = etag"))
        self.assertLess(applied, fetch.index("applySnapshot("))
        self.assertIn("signal: controller.signal", fetch)

    def test_stale_answer_does_not_release_the_lock_of_the_new_request(self):
        fetch = self._block("const fetchSnapshot = useCallback(",
                            "}, [apiRoot, applySnapshot, buildHeaders, notify, user?.id, withDirection]);")
        finally_block = fetch[fetch.index("} finally {"):]
        self.assertIn("if (isCurrent()) {", finally_block)
        self.assertIn("snapshotRequestRef.current = false;", finally_block)

    def test_queued_refresh_keeps_its_loudness(self):
        """Отложенный «громкий» запрос обязан погасить «Загружаем…».

        Иначе переключение, пришедшееся на чужой запрос в пути, оставляло бы
        раздел на заглушке загрузки — это и читается как «лагает».
        """
        fetch = self._block("const fetchSnapshot = useCallback(",
                            "}, [apiRoot, applySnapshot, buildHeaders, notify, user?.id, withDirection]);")
        self.assertIn("if (!silent) snapshotRefreshLoudRef.current = true;", fetch)
        self.assertIn("const wasLoud = snapshotRefreshLoudRef.current;", fetch)
        self.assertIn("fetchSnapshotRef.current?.({ silent: !wasLoud })", fetch)

    def test_aborted_request_does_not_shout_at_the_user(self):
        fetch = self._block("const fetchSnapshot = useCallback(",
                            "}, [apiRoot, applySnapshot, buildHeaders, notify, user?.id, withDirection]);")
        self.assertIn("ERR_CANCELED", fetch)
        self.assertIn("if (aborted || !isCurrent()) return;", fetch)

    def test_stream_still_reopens_on_direction_change(self):
        """Поток событий по-прежнему привязан к направлению."""
        self.assertIn("}, [apiRoot, canOpenStream, direction, user?.id]);", self.source)


if __name__ == "__main__":
    unittest.main()
