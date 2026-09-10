# -*- coding: utf-8 -*-
"""Страж белого списка станции и пульса моста.

Три ручки станции ходят в AMI: 25.08.2026 три вызова `/freepbx/load/queues` и
`/freepbx/load/agents` зависли без ответа, и станция перестала отвечать целиком
за минуты. Запрет на них держат три независимых рубежа: этот белый список в
коде, локальный прокси перед станцией (`gateway/station-proxy/nginx.conf`) и
правила фаервола. Здесь закреплён первый — тот, который проще всего потерять
при безобидной с виду правке.

Проверяется ещё и то, что попытка позвать запрещённый путь падает ДО обращения
в сеть: иначе «запрет» означал бы запрос, который станция всё-таки получила.

Отдельно закреплён пульс: он ставится только после удавшегося прохода. Если
писать его в начале цикла или безусловно, сторож снаружи будет видеть здоровый
мост при мёртвом канале — ровно та авария, которую он должен ловить.
"""

import os
import tempfile
import unittest

from cdr_bridge.station import ALLOWED_PATHS, Station, StationError
from cdr_bridge import agent as agent_mod


AMI_PATHS = ('/freepbx/load/queues', '/freepbx/load/agents', '/agents/stream')


class AllowedPathsTest(unittest.TestCase):
    def test_ami_paths_not_in_allowlist(self):
        for path in AMI_PATHS:
            self.assertNotIn(path, ALLOWED_PATHS,
                             'путь %s ходит в AMI и клал станцию' % path)

    def test_allowlist_is_exactly_four_reading_paths(self):
        # Список расширяется только осознанно: любое добавление обязано пройти
        # ревью и попасть сюда же, иначе тест краснеет.
        self.assertEqual(
            set(ALLOWED_PATHS),
            {'/freepbx/cdr', '/freepbx/cdr/count', '/agents/map', '/health'})

    def test_forbidden_path_fails_before_network(self):
        # Проверка живёт в _url — то есть срабатывает при сборке адреса, ДО того как
        # запрос уйдёт в сеть. Адрес станции здесь заведомо нерабочий: если запрет
        # однажды переедет ниже по коду, тест получит ошибку соединения вместо
        # forbidden_path и покраснеет.
        station = Station('http://127.0.0.1:9', '', '')
        for path in AMI_PATHS:
            with self.assertRaises(StationError) as caught:
                station._url(path)
            self.assertEqual(caught.exception.code, 'forbidden_path',
                             'путь %s должен отвергаться до похода в сеть' % path)


class HeartbeatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'heartbeat')

    def _bridge(self, heartbeat_file):
        config = {
            'portal': 'http://portal.invalid', 'token': 'x',
            'station': 'http://127.0.0.1:9', 'login': '', 'password': '',
            'heartbeat_file': heartbeat_file,
        }
        return agent_mod.Bridge(config)

    def test_beat_writes_file(self):
        bridge = self._bridge(self.path)
        bridge.beat()
        with open(self.path, encoding='utf-8') as fh:
            stamp, agent_id = fh.read().split()
        self.assertTrue(stamp.isdigit())
        self.assertEqual(agent_id, bridge.agent_id)

    def test_beat_is_silent_without_setting(self):
        # На локальной отладке сторожа нет: пустая настройка не должна ни писать,
        # ни падать.
        self._bridge('').beat()

    def test_beat_survives_unwritable_path(self):
        # Пульс — диагностика, а не работа: сломанный путь не должен ронять мост.
        self._bridge(os.path.join(self.tmp, 'нет-такого-каталога', 'beat')).beat()

    def test_run_beats_only_after_successful_tick(self):
        bridge = self._bridge(self.path)
        bridge.tick = lambda: (_ for _ in ()).throw(RuntimeError('станция молчит'))
        bridge.poll = lambda **kwargs: {}
        # `agent_mod.time` — это сам модуль time из стандартной библиотеки, поэтому подмена
        # здесь глобальная и переживает тест. Без возврата оригинала любой следующий
        # `time.sleep` во всём прогоне бросал KeyboardInterrupt, и pytest обрывался на
        # четверти набора с exit code 2 — красный CI выглядел как поломка чужой правки.
        real_sleep = agent_mod.time.sleep
        self.addCleanup(setattr, agent_mod.time, 'sleep', real_sleep)
        agent_mod.time.sleep = lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt)
        try:
            bridge.run()
        except KeyboardInterrupt:
            pass
        self.assertFalse(os.path.exists(self.path),
                         'пульс поставлен при неудачном проходе — сторож ослеп')

    def test_sleep_is_restored_for_the_rest_of_the_run(self):
        """Подмена time.sleep глобальная и живёт дольше теста, если её не вернуть.
        Один невозвращённый sleep обрывает весь прогон pytest, а не только свой тест."""
        import time as real_time

        self.assertIs(agent_mod.time, real_time)
        real_time.sleep(0)


if __name__ == '__main__':
    unittest.main()
