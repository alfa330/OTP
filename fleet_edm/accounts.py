"""Какой учёткой кабинета идти в выгрузку.

Учёток у портала в кабинете две: своя у «Провайдера ЭДО» (fleet_edm_session) и
учётка раздела «Рассылки» (driver_mailing_session), которая с 28.10.2026
помогает с карточками. До 12.11.2026 основной могла быть только своя, и когда
Яндекс её разлогинил (401 с 03.11.2026 — и на сервере, и в профиле браузера,
из которого она пришла), раздел встал целиком: «Выгрузки временно недоступны»,
выгрузка на 228 строк упала на первом же запросе. При этом вторая учётка была
жива и видела больше диспетчерских — 89 против 86.

От чьего имени читать, выгрузке ЭДО всё равно: провайдер у водителя один, кто
бы ни спросил, а парк, закрытый для учётки, обход и так помечает «нет
доступа», а не теряет молча. («Рассылки» на чужую учётку как раз НЕ
откатываются: там учётка — это отправитель, см. driver_mailings.routes.choose_session.)

Поэтому основной становится первая ЖИВАЯ по порядку: своя, затем остальные.
Живость проверяется тем же запросом, с которого начинается любой обход, —
списком диспетчерских, — и его ответ уходит в обход, чтобы не спрашивать
дважды. Учётки после выбранной идут в помощницы для карточек, как раньше.
"""

from .client import FleetAccessDenied, FleetSessionExpired

OWN = 'own'
MAILINGS = 'mailings'


class Selection:
    """Итог выбора: основной клиент, его диспетчерские и помощницы.

    failures — [(имя, ошибка)] учёток, которые проверили и не взяли: по ним
    вызывающий решает, кого отметить протухшим и что сказать, если не подошёл
    никто (main тогда None)."""

    def __init__(self):
        self.main = None
        self.name = None
        self.account = ''
        self.parks = []
        self.helpers = []
        self.helper_names = []
        self.failures = []

    @property
    def spare(self):
        """Есть ли кем заменить основную, если она протухнет посреди обхода."""
        return bool(self.helpers)

    def expired(self, name):
        return any(failed == name and isinstance(error, FleetSessionExpired)
                   for failed, error in self.failures)


def choose(candidates, make_client):
    """candidates — [(имя, строка сессии)] в порядке старшинства; строки без кук
    пропускаются. make_client(строка) → клиент кабинета.

    Не годится учётка, которая на список диспетчерских ответила 401 (вход
    протух), 403 (нет прав даже на список) или пустотой. Сеть, 5xx и затянувшиеся
    отказы «помедленнее» — НЕ повод менять учётку: это кабинет моргнул, а не
    вход умер, и такая ошибка уходит наружу как есть — выгрузка встанет на
    подхват и попробует позже той же учёткой.
    """
    selection = Selection()
    usable = [(name, row) for name, row in candidates if row and row.get('cookies')]
    for index, (name, row) in enumerate(usable):
        client = make_client(row)
        try:
            parks = client.parks()
        except (FleetSessionExpired, FleetAccessDenied) as error:
            selection.failures.append((name, error))
            continue
        if not parks:
            selection.failures.append((name, FleetSessionExpired(
                'Кабинет не вернул учётке ни одной диспетчерской.')))
            continue
        selection.main = client
        selection.name = name
        selection.account = str(row.get('account') or '')
        selection.parks = parks
        rest = usable[index + 1:]
        selection.helpers = [make_client(extra) for _extra_name, extra in rest]
        selection.helper_names = [str(extra.get('account') or extra_name)
                                  for extra_name, extra in rest]
        break
    return selection
