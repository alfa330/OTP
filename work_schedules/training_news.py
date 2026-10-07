# -*- coding: utf-8 -*-
"""Какое объявление Oktell стоит за интервалом «Тренинг» (задача #382).

Обязательное объявление, отправленное в Oktell, на время чтения снимает
оператора с линии в перерыв «Тренинг». В учёте часов это обычный интервал
статуса, и супервайзер подтверждает его, не зная, что человек делал. Здесь —
мост к разделу «Новости»: окна объявлений оператора за день.

Сопоставление окна с интервалом статуса делает интерфейс
(src/components/schedule/trainingNews.js): отрезки статусов он склеивает и
округляет сам, и вторая копия этих правил на сервере разошлась бы с экраном.
"""

# Колонку канала спрашиваем ОДИН раз на процесс, как это делает выдача
# объявлений агенту (oktell_guard/routes.py): день в «Графиках работы»
# открывают постоянно, а колонка, однажды появившись, уже не пропадёт.
_channel = {'ready': False}


def _channel_ready(cursor):
    if not _channel['ready']:
        from news import schema as news_schema
        _channel['ready'] = bool(news_schema.channel_ready(cursor))
    return _channel['ready']


def windows_for_day(cursor, *, operator_id, day):
    """Окна объявлений Oktell оператора, задевшие сутки `day`.

    Импорт пакета новостей ленивый: «Графики работы» обязаны открываться и
    там, где раздел новостей не развернулся. Нет колонки канала — объявлений
    в Oktell не было вовсе, отвечаем пустым списком, а не ошибкой.
    """
    if not _channel_ready(cursor):
        return []
    from news import queries as news_queries
    return news_queries.oktell_windows(cursor, user_id=int(operator_id), day=day)
