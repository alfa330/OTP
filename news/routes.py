# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Новости» (Flask Blueprint).

Собран фабрикой с внедрением зависимостей — как «Вики», «Обращения» и центр
уведомлений: импортировать bot_schedule2 отсюда нельзя, вышел бы цикл.

ДВЕ ДВЕРИ, И ОНИ РАЗНЫЕ. Это главное в модуле:

  ЧИТАТЬ (`/pending`, `/<id>/read`, лента `/feed` и прохождение теста и
  тренажёра `/<id>/quiz`, `/<id>/trainer`) — только аутентификация. Ни тумблера
  `departments.wiki_enabled`, ни QR-подтверждения сессии, которые стоят на
  роутах вики. Так требует постановка: «чтобы увидеть новость необязательно
  иметь доступ к чувствительным данным или к вики». Оператор отдела, которому
  вики не выдали, обязан увидеть окно — иначе объявление не доходит ровно до
  тех, ради кого оно пишется.

  ПИСАТЬ (всё остальное) — потолок должности news_access.publish_ceiling.
  Ниже супервайзера его нет ни у кого, и по этому же признаку прячется вкладка.
"""

import logging
import re
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request, send_file

from wiki.sanitize import sanitize_html

from . import access as news_access
from . import audit as news_audit
from . import photos as news_photos
from . import queries
from . import report_xlsx
from .schema import (DEFAULT_CONFIRM_DELAY_SECONDS, MAX_LOOSE_PHOTOS_PER_USER,
                     MAX_SPREAD_MINUTES, MIN_SPREAD_MINUTES,
                     MIN_WAVE_INTERVAL_MINUTES,
                     attempts_ready as schema_attempts_ready,
                     takedown_ready as schema_takedown_ready,
                     channel_ready as schema_channel_ready,
                     pass_ready as schema_pass_ready,
                     photos_ready as schema_photos_ready,
                     plan_ready as schema_plan_ready,
                     quiz_ready as schema_quiz_ready, schema_is_ready,
                     space_ready as schema_space_ready)

# Сколько имён «без SIP-номера» уезжает в форму. Остальные считаются числом:
# панель предупреждения читают глазами, и список на двести строк в ней — это не
# ответ, а новый вопрос.
SIP_MISSING_LIMIT = 50

# Отказ, который видит не-редактор. Одной строкой: текст показывают человеку,
# и «недостаточно прав» без объяснения отправляет его писать в поддержку.
_NOT_A_PUBLISHER = ('Новости публикуют супервайзер и выше', 403)


def build_news_blueprint(*, db, require_api_key, build_cors_preflight_response,
                         resolve_requester, gcs=None):
    """db — Database (ради _get_cursor); остальное — общие вещи из bot_schedule2.

    gcs=None по умолчанию НАМЕРЕННО: порядок деплоя не должен ронять старт
    портала. Без хранилища раздел работает как вчера, только фотографию не
    приложить — роут загрузки честно отвечает 503.

    Журнала действий у раздела нет намеренно: кто, когда и кому выпустил
    новость, записано в самой новости (author_id, published_at, адресаты), а
    журнал прочтений отвечает на второй вопрос — кто её увидел. Третья запись
    о том же была бы дублем.
    """

    bp = Blueprint('news', __name__, url_prefix='/api/news')

    # Схема разворачивается один раз при старте и никуда не девается, поэтому
    # спрашивать про неё базу на каждый запрос незачем — списком храним только
    # УСПЕХ. Отрицательный ответ не кешируем: он означает, что миграция ещё не
    # прошла, и следующий запрос обязан увидеть развёрнутую схему.
    schema_ready_once = []

    def _schema_ready(cursor):
        if schema_ready_once:
            return True
        if schema_is_ready(cursor):
            schema_ready_once.append(True)
            return True
        return False

    # Готовность таблицы кадров. Отдельно от _schema_ready намеренно: тот
    # отвечает за весь раздел, и «нет таблицы» превратилось бы в «раздел
    # разворачивается» у всех вошедших в портал. Здесь отсутствие таблицы
    # означает ровно «у новостей нет фотографий».
    #
    # Работает в одну сторону: False перепроверяется (таблица доедет следующим
    # деплоем), True запоминается навсегда — таблицы не исчезают.
    _photos_table = {'ready': False}

    def _photos_ready(cursor):
        if not _photos_table['ready']:
            _photos_table['ready'] = schema_photos_ready(cursor)
        return _photos_table['ready']

    # Готовность таблицы теста в окне — тем же приёмом и по той же причине.
    _quiz_table = {'ready': False}

    def _quiz_ready(cursor):
        if not _quiz_table['ready']:
            _quiz_table['ready'] = schema_quiz_ready(cursor)
        return _quiz_table['ready']

    # Канал доставки (решение владельца 18.09.2026) — тем же приёмом: без
    # колонки объявление уходит в портал, как до задачи.
    _channel_column = {'ready': False}

    def _channel_ready(cursor):
        if not _channel_column['ready']:
            _channel_column['ready'] = schema_channel_ready(cursor)
        return _channel_column['ready']

    # Колонки тренажёра и обязательности прохождения (задача #342) — тем же
    # приёмом: без них тест обязателен, как до задачи, а тренажёров нет.
    _pass_columns = {'ready': False}

    def _pass_ready(cursor):
        if not _pass_columns['ready']:
            _pass_columns['ready'] = schema_pass_ready(cursor)
        return _pass_columns['ready']

    # Тип, проходной балл, планировщик и волны (ТЗ #300) — одной защёлкой и с
    # тем же кэшем: её спрашивает /pending, то есть каждый вошедший в портал.
    _plan_columns = {'ready': False}

    def _plan_ready(cursor):
        if not _plan_columns['ready']:
            _plan_columns['ready'] = schema_plan_ready(cursor)
        return _plan_columns['ready']

    # Кто и когда снял с показа (ТЗ #300, п.16) — тем же кэшем: колонки читает
    # список редактора на каждое открытие вкладки.
    _takedown_columns = {'ready': False}

    def _takedown_ready(cursor):
        if not _takedown_columns['ready']:
            _takedown_columns['ready'] = schema_takedown_ready(cursor)
        return _takedown_columns['ready']

    # Таблица попыток (ТЗ #300, п.4.2) — тем же кэшем. Её спрашивает
    # подтверждение новости, то есть самый горячий пишущий роут раздела.
    _attempts_table = {'ready': False}

    def _attempts_ready(cursor):
        if not _attempts_table['ready']:
            _attempts_table['ready'] = schema_attempts_ready(cursor)
        return _attempts_table['ready']

    # Граница пространства (решение владельца 18.09.2026). Тем же приёмом и по
    # той же причине, что кадры и тест, но с одной особенностью: половина
    # ответа тут ЧУЖАЯ — таблицу wiki_space_departments приносит пакет вики.
    # Сорвись её миграция, раздел обязан работать как вчера, без границы, а не
    # отвечать пятисоткой каждому вошедшему в портал: ради этого пакет news/ и
    # вынесен из wiki/.
    _space_columns = {'ready': False}

    def _space_ready(cursor):
        if not _space_columns['ready']:
            _space_columns['ready'] = schema_space_ready(cursor)
        return _space_columns['ready']

    def _request_space(cursor):
        """Пространство, из которого пришёл запрос. None — не назвали.

        Из строки запроса и из тела — как у вики (routes_structure.request_space).
        Проверки «выдано ли человеку это пространство» здесь НЕТ намеренно: она
        стоит на ролях вики, а раздел о них не спрашивает вовсе. Границу держит
        не этот параметр, а справочник адресата (он сужен пересечением своего
        отдела с отделами пространства) и периметр списка — назвав чужой id,
        человек увидит не чужие новости, а пустой список.
        """
        if not _space_ready(cursor):
            return None
        raw = request.args.get('space_id')
        if raw is None:
            raw = (request.get_json(silent=True) or {}).get('space_id')
        return _int_or_none(raw)

    def _space_departments(cursor, space_id):
        """Отделы пространства для проверки адресата. None — не сужаем.

        Пустой список превращается в None НАМЕРЕННО: пространство, не назвавшее
        ни одного отдела, ничего про своих людей не сказало — ровно так же его
        читает и сторона показа (access.SPACE_MATCH_TEMPLATE). Оставь мы пустой
        список, в новом пространстве форма не предложила бы ни одного адресата,
        а выпущенная там новость всё равно дошла бы до всех: справочник говорил
        бы одно, а окно делало другое.
        """
        if space_id is None or not _space_ready(cursor):
            return None
        return queries.space_departments(cursor, space_id) or None

    def _channels_for_space(cursor, space_id):
        """Какие каналы предлагать в этом пространстве. Всегда хотя бы портал.

        Ответ считается по ОТДЕЛАМ пространства: программа «Ограничитель
        Перезвона» стоит только у СЗоВ, и в «Тез» выбирать не из чего
        (access.channels_for_departments). Список уходит форме и им же
        проверяется сохранение: правило, живущее только во фронте, держится до
        первого запроса мимо него.
        """
        if not _channel_ready(cursor):
            return [news_access.DEFAULT_CHANNEL]
        departments = _space_departments(cursor, space_id)
        if departments is None:
            # Пространство не назвали (старый бандл) или граница вики не
            # развёрнута: про отделы сказать нечего — остаётся портал.
            return [news_access.DEFAULT_CHANNEL]
        return news_access.channels_for_departments(
            queries.department_codes(cursor, departments))

    def _channel_from_request(cursor, payload, space_id, post=None):
        """(канал, отказ) из тела запроса.

        Ключа нет — прежний канал карточки или умолчание: правка заголовка не
        должна переносить объявление в другое место.
        """
        fallback = (post or {}).get('channel') or news_access.DEFAULT_CHANNEL
        if 'channel' not in payload:
            return fallback, None
        channel = news_access.normalize_channel(payload.get('channel'), default=fallback)
        if channel != news_access.DEFAULT_CHANNEL and not _channel_ready(cursor):
            # Прислали Oktell, а колонки нет: молча положить в портал нельзя —
            # автор отправил бы объявление не туда, куда собирался.
            return None, (jsonify({
                "error": "Выбор канала ещё разворачивается — сохраните без него "
                         "или загляните позже",
                "code": "NEWS_CHANNEL_NOT_READY"}), 503)
        if channel not in _channels_for_space(cursor, space_id):
            return None, (jsonify({
                "error": "В этом пространстве объявление уходит только в портал",
                "code": "NEWS_CHANNEL_UNAVAILABLE"}), 400)
        return channel, None

    def _get_post(cursor, post_id):
        """Карточка с колонками #342, пространством, каналом и планом (#300)."""
        return queries.get_post(cursor, post_id, with_pass=_pass_ready(cursor),
                                with_space=_space_ready(cursor),
                                with_channel=_channel_ready(cursor),
                                with_plan=_plan_ready(cursor),
                                with_takedown=_takedown_ready(cursor))

    def news_route(rule, methods=('GET',), publisher=False, rights=False,
                   defer_cursor=False):
        """Общий декоратор: preflight, авторизация, контекст, ошибки.

        publisher=True добавляет потолок должности. Флаг стоит НА ОБЪЯВЛЕНИИ
        роута, а не в теле обработчика, чтобы у каждой двери было видно, про
        чтение она или про выпуск.

        rights=True считает потолок и границу отдела, не требуя их наличия
        (это нужно ровно одному роуту — /access, который отвечает и тому, кто
        публиковать не вправе). publisher его подразумевает.

        Читающим роутам права НЕ считаются вовсе, и это не микрооптимизация:
        /pending дёргает КАЖДЫЙ вошедший в портал, каждая открытая вкладка на
        каждый тычок канала колокола и каждый возврат во вкладку. Расчёт
        потолка стоит двух лишних обращений к базе (проверка таблицы ролей вики
        плюс сам EXISTS), то есть больше половины запроса — ради ответа на
        вопрос, которого чтение не задаёт. Пул на портал — 40 соединений на
        всё, и его уже делит SSE аукциона.

        defer_cursor=True — контекст и права считаются в коротком курсоре, он
        ЗАКРЫВАЕТСЯ, и обработчик получает только ctx, а курсоры открывает сам.
        Нужен там, где между проверкой прав и записью в базу стоит ЧУЖАЯ СЕТЬ:
        пережатие кадра на 12 мегапикселей и заливка в бакет заняли бы слот
        пула на секунды, а слотов сорок на весь портал. Заодно это единственный
        способ снести блоб ПОСЛЕ фиксации транзакции, а не до неё.
        """
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status

                    with db._get_cursor() as cursor:
                        if not _schema_ready(cursor):
                            # Схемы нет — раздел просто пуст. Не 500: новости
                            # не должны уметь сломать вход в портал.
                            #
                            # Ответ несёт ВСЕ ключи, которых ждут витрины:
                            # окно читает items, вкладка — can_publish. Отдай мы
                            # один лишь items, вкладка прочла бы отсутствующий
                            # can_publish как «нет прав» и сказала бы человеку
                            # неправду — вместо «раздел разворачивается».
                            return jsonify({"items": [], "total": 0,
                                            "can_publish": False,
                                            "schema_ready": False})

                        ctx = queries.load_viewer_context(cursor, requester_id)
                        if not ctx:
                            return jsonify({"error": "Пользователь не найден"}), 404

                        # Значения по умолчанию, чтобы читающий роут не мог
                        # уронить запрос обращением к несчитанному праву.
                        ctx['ceiling'] = None
                        ctx['departments'] = None
                        if publisher or rights:
                            is_wiki_admin = queries.is_wiki_admin(
                                cursor, ctx['user_id'])
                            ctx['ceiling'] = news_access.publish_ceiling(
                                ctx['otp_role'], is_wiki_admin=is_wiki_admin)
                            ctx['departments'] = news_access.publish_departments(
                                ctx['otp_role'],
                                headed_department_ids=ctx['headed_department_ids'],
                                department_id=ctx['department_id'],
                                is_wiki_admin=is_wiki_admin)

                        if publisher and ctx['ceiling'] is None:
                            message, status = _NOT_A_PUBLISHER
                            return jsonify({"error": message,
                                            "code": "NEWS_FORBIDDEN"}), status

                        if not defer_cursor:
                            return handler(*args, cursor=cursor, ctx=ctx, **kwargs)

                    # Курсор ОТДАН обратно в пул, и только теперь обработчик
                    # уходит в сеть (пережатие кадра, заливка в бакет).
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001 — общего errorhandler нет
                    logging.exception('news: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Новости»",
                                    "detail": str(exc)[:200]}), 500

            return wrapper

        return decorator

    def _int_or_none(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _timestamp_or_none(value):
        """Срок показа из формы. Не разобрали — считаем, что срока нет.

        Разбираем ЗДЕСЬ, а не отдаём строку постгресу: невнятная дата уронила
        бы запрос пятисоткой, и автор увидел бы «внутренняя ошибка» вместо
        поля, которое надо поправить.

        ПОЛНОЧЬ СЧИТАЕМ КОНЦОМ ДНЯ. «Действует до 18.09» человек читает как «весь
        18-е», а поле `datetime-local`, в котором тронули только дату, отдаёт
        `18.09 00:00` — то есть НАЧАЛО дня. 18.09.2026 на этом и сгорели четыре
        объявления подряд (#7–#10): выпущенные вечером, они истекли за 22 часа до
        собственной публикации и не показались никому и нигде, молча. Кому нужна
        ровно полночь, ставит 00:01 — цена этого выбора несравнима с тишиной.
        """
        if not value:
            return None
        text = str(value).strip().replace('T', ' ')
        if not text:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                parsed = datetime.strptime(text[:len(fmt) + 4], fmt)
            except ValueError:
                continue
            if (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0):
                return parsed.replace(hour=23, minute=59, second=59)
            return parsed
        return None

    def _expiry_refusal(expires_at, publishing):
        """Отказ, если объявление выпускают уже просроченным.

        Такое объявление не показывается НИКОМУ и НИГДЕ — ни в портале, ни в
        окне агента, — и автор об этом не узнаёт: в списке оно «опубликовано».
        Молчать здесь нельзя: тишина неотличима от поломки, и 18.09.2026 её
        именно так и искали — в агенте, в канале и в правах.

        СРОК ПРИХОДИТ СТРОКОЙ. Оба места, откуда сюда попадают, берут его у
        queries.get_post, а тот отдаёт КАРТОЧКУ — то есть уже готовый JSON, где
        время сериализовано (`expires_at.isoformat()`). Сравнение строки с
        datetime роняло публикацию любого объявления, у которого заполнено
        «действует до»: 500 и «внутренняя ошибка» вместо выпуска. Поэтому
        значение приводится здесь, а не предполагается.
        """
        if not publishing or expires_at in (None, ''):
            return None
        moment = expires_at
        if not isinstance(moment, datetime):
            try:
                moment = datetime.fromisoformat(str(moment))
            except ValueError:
                # Дата, которую мы не понимаем, — не повод не выпускать
                # новость: срок показа проверяет ещё и сама выдача.
                return None
        if moment > datetime.now():
            return None
        return ("Срок показа уже истёк — объявление не увидит никто. "
                "Поправьте «действует до» или очистите поле.")

    def _may_read_post(ctx, post):
        """Вправе ли человек ОТКРЫТЬ карточку новости и её журнал.

        Периметр обязан совпадать со списком (queries.list_posts), а он у
        редактора звучит так: своё плюс чужое своего отдела, но только от
        авторов НЕ ВЫШЕ себя. Точечные пути это правило раньше не повторяли —
        и супервайзер, не видя черновик своего руководителя в списке, открывал
        его прямым обращением по id вместе с журналом «кто прочитал».

        Правило дублируется намеренно, но проверяется тестом
        (tests/test_news.py::NewsPerimeterTests): держать его одним SQL нельзя —
        список это выборка, а здесь уже готовая строка.
        """
        if post.get('author_id') == ctx['user_id']:
            return True
        if ctx['departments'] is None:
            return True
        if post.get('author_department_id') not in ctx['departments']:
            return False
        return (news_access.effective_role_level(post.get('author_role'))
                <= news_access.effective_role_level(ctx['otp_role']))

    def _may_edit(ctx, post):
        """Вправе ли человек править эту новость.

        Свою — всегда. Чужую — только без границы отдела (супер-админ,
        администратор вики): переписывать объявление коллеги того же уровня
        по чужому усмотрению нельзя, даже внутри одного отдела.
        """
        if post.get('author_id') == ctx['user_id']:
            return True
        return ctx['departments'] is None

    def _may_take_down(ctx, post):
        """Вправе ли человек СНЯТЬ новость с показа.

        Шире правки намеренно. Править чужой текст нельзя — это чужие слова.
        Но обязательное окно, которое видит весь отдел, обязано иметь тормоз у
        того, кто за отдел отвечает: иначе ошибочное объявление супервайзера
        снимается только им самим, а он может быть на смене, в отпуске или уже
        не работать. «Новость идёт вниз» — значит и снять её вправе тот, кто
        выше автора, а не только сам автор.
        """
        if _may_edit(ctx, post):
            return True
        if not _may_read_post(ctx, post):
            return False
        return (news_access.effective_role_level(ctx['otp_role'])
                > news_access.effective_role_level(post.get('author_role')))

    def _with_rights(ctx, post):
        """Дописывает в карточку признак «мне это править можно».

        Считает СЕРВЕР и отдаёт готовым — та же причина, что у действий в
        строке каталога вики: вторая формула во фронте разошлась бы с этой, и
        человек получал бы пункт меню, на который сервер отвечает 403.
        """
        post['can_edit'] = _may_edit(ctx, post)
        # Снять шире, чем править: обязательному окну нужен тормоз у того, кто
        # выше автора, — см. _may_take_down.
        post['can_take_down'] = _may_take_down(ctx, post)
        return post

    def _dress(cursor, ctx, post):
        """Карточка, готовая к отдаче: права плюс подписанные адреса кадров.

        Кадры берутся ОТДЕЛЬНЫМ запросом, а не подзапросом внутри get_post, и
        это не лишнее обращение из лени. get_post зовут семь мест, из них два —
        журнал прочтений и удаление — свой результат наружу не отдают. Положив
        bucket и blob_path в общий словарь, мы завели бы семь возможностей
        уронить путь в бакете в jsonify, и правильность держалась бы на том,
        что никто не забудет его выкинуть.
        """
        post = _with_rights(ctx, post)
        post['photos'] = (news_photos.sign_urls(gcs, queries.post_photos(cursor, post['id']))
                          if _photos_ready(cursor) else [])
        # Тест С ВЕРНЫМИ ответами — только карточке редактора: сюда приходят
        # после _may_read_post, а окну сотрудника ответы не уходят никогда.
        post['quiz'] = queries.post_quiz(cursor, post['id']) if _quiz_ready(cursor) else []
        return post

    def _quiz_from_request(cursor, payload):
        """(тест, отказ) из тела запроса. Нет ключа или пустой список — теста нет.

        Проверка — та же normalize_quiz, что у «Вопросов операторов»: форма
        новости, ИИ и разбор вопроса дают тест одного вида, а окно сотрудника
        сверяет ответы по одной таблице.
        """
        raw = payload.get('quiz')
        if not raw:
            return [], None
        if not _quiz_ready(cursor):
            return None, (jsonify({"error": "Тесты к новостям ещё разворачиваются — "
                                            "сохраните без теста или загляните позже",
                                   "code": "NEWS_QUIZ_NOT_READY"}), 503)
        quiz, problem = news_access.normalize_quiz(raw)
        if problem:
            return None, (jsonify({"error": problem, "code": "NEWS_QUIZ_INVALID"}), 400)
        return quiz, None

    def _passes_from_request(cursor, payload, post=None):
        """({'pass_required', 'trainer_key'}, отказ) из тела запроса.

        Ключа нет — берём прежнее значение карточки (post) или умолчание:
        правка одного поля не должна сносить остальные. Умолчание
        pass_required — ДА: так вели себя все тесты до задачи #342, и форма,
        не знающая про тумблер, не должна молча делать тест необязательным.
        """
        current = post or {}
        key = current.get('trainer_key')
        if 'trainer_key' in payload:
            key, problem = news_access.normalize_trainer_key(payload.get('trainer_key'))
            if problem:
                return None, (jsonify({"error": problem, "code": "NEWS_TRAINER_INVALID"}), 400)
        required = bool(payload.get('pass_required', current.get('pass_required', True)))
        if not _pass_ready(cursor) and (key or not required):
            # Прислали тренажёр или «необязательно», а колонок нет: молча
            # проглотить нельзя — автор выпустил бы объявление не таким, каким
            # его собрал.
            return None, (jsonify({"error": "Тренажёры к новостям ещё разворачиваются — "
                                            "сохраните без них или загляните позже",
                                   "code": "NEWS_PASS_NOT_READY"}), 503)
        return {'pass_required': required, 'trainer_key': key}, None

    def _moment_or_none(value):
        """Время запуска из формы. Не разобрали — запуск не взводим.

        СВОЙ разбор, а не _timestamp_or_none: тот считает полночь концом дня
        (иначе «действует до 18.09» сгорало бы в начале суток), а «запустить
        19.09 00:00» — это ровно полночь, и сдвинуть её на без секунды сутки
        означало бы выпустить объявление на день позже, чем просили.
        """
        if not value:
            return None
        text = str(value).strip().replace('T', ' ')
        if not text:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(text[:len(fmt) + 4], fmt)
            except ValueError:
                continue
        return None

    def _plan_from_request(cursor, payload, post=None, publishing=False, has_quiz=False):
        """(план, отказ): тип, проходной балл и режим запуска (ТЗ #300, п.4, 5, 8).

        Ключа нет — берём прежнее значение карточки, как у тренажёра и
        адресатов: правка одного поля не должна сносить остальные.

        has_quiz — будет ли у новости тест ПОСЛЕ этого запроса. Им проверяется
        критичный тип: критичная без теста — это важная, названная критичной.
        """
        current = post or {}
        if 'kind' in payload:
            kind = news_access.normalize_kind(payload.get('kind'))
        elif current.get('kind'):
            kind = news_access.normalize_kind(current['kind'])
        else:
            # Форма старого бандла про тип не знает. Выводим его из того, чем
            # новость собрана, — тем же правилом, что и бэкфилл схемы, иначе
            # объявление сменило бы тип от одного сохранения.
            kind = news_access.kind_of(
                is_mandatory=bool(payload.get('is_mandatory',
                                              current.get('is_mandatory', True))),
                pass_required=bool(payload.get('pass_required',
                                               current.get('pass_required', True))),
                has_quiz=has_quiz)
        kind_problem = news_access.kind_refusal(kind, has_quiz=has_quiz)
        if kind_problem:
            return None, (jsonify({"error": kind_problem,
                                   "code": "NEWS_KIND_NEEDS_QUIZ"}), 400)

        mode = news_access.normalize_publish_mode(
            payload.get('publish_mode', current.get('publish_mode')))
        scheduled_at = (_moment_or_none(payload.get('scheduled_at'))
                        if 'scheduled_at' in payload
                        else _moment_or_none(current.get('scheduled_at')))
        spread = _int_or_none(payload.get('spread_minutes',
                                          current.get('spread_minutes')))
        interval = _int_or_none(payload.get('wave_interval_minutes',
                                            current.get('wave_interval_minutes')))
        if mode == 'now':
            # Запуск «сразу» ничего не взводит: оставить здесь прежнюю дату
            # значило бы, что крон выпустит новость ВТОРОЙ раз. Этим же
            # переключением автор и отменяет запланированный запуск.
            scheduled_at, spread, interval = None, None, None
        elif mode == 'later':
            spread, interval = None, None
        problem = news_access.schedule_refusal(
            mode=mode, scheduled_at=scheduled_at, spread_minutes=spread,
            wave_interval_minutes=interval, publishing=publishing)
        if problem:
            return None, (jsonify({"error": problem, "code": "NEWS_SCHEDULE"}), 400)
        if (mode != 'now' or scheduled_at) and not _plan_ready(cursor):
            # Прислали планировщик, а колонок нет: молча проглотить нельзя —
            # автор решил бы, что запуск отложен, а объявление ушло бы сразу.
            return None, (jsonify({"error": "Планировщик публикаций ещё разворачивается — "
                                            "выпустите сразу или загляните позже",
                                   "code": "NEWS_PLAN_NOT_READY"}), 503)
        return {
            'kind': kind,
            'pass_score_percent': news_access.normalize_pass_score(
                payload.get('pass_score_percent', current.get('pass_score_percent'))),
            'publish_mode': mode,
            'scheduled_at': scheduled_at,
            'spread_minutes': spread,
            'wave_interval_minutes': interval,
        }, None

    def _quiz_refusal(result):
        """Ответ на непройденную попытку. Один на окно, ленту и клиент АТС.

        КАКИЕ вопросы неверны, не называем (решение владельца 21.09.2026):
        подсветка вернула бы подбор ответа переключением одного варианта. А вот
        СКОЛЬКО верных и сколько нужно — говорим: при проходном балле ниже ста
        «есть неверные ответы» не объясняет, почему тест не засчитан, хотя
        часть ответов верна (ТЗ #300, п.4).
        """
        detail = result if isinstance(result, dict) else {}
        total = int(detail.get('total') or 0)
        needed = int(detail.get('needed') or 0)
        correct = int(detail.get('correct') or 0)
        if total and needed and needed < total:
            message = ('Верных ответов %d из %d, нужно не меньше %d — '
                       'перечитайте новость' % (correct, total, needed))
        else:
            message = 'Есть неверные ответы — перечитайте новость'
        return {"error": message, "code": "NEWS_QUIZ_WRONG",
                "correct": correct, "total": total, "needed": needed}

    def _build_waves(cursor, post_id, plan, start_at):
        """Разложить адресатов по волнам. Только для режима растяжки.

        Считается В МОМЕНТ ВЫПУСКА, а не при взведении: между «запланировал в
        понедельник» и «вышло в среду» люди приходят и уходят, и волны обязаны
        быть про тех, кому объявление правда уходит сейчас.
        """
        user_ids = queries.audience_user_ids(cursor, post_id,
                                             with_space=_space_ready(cursor))
        queries.set_waves(cursor, post_id=post_id, plan=news_access.plan_waves(
            user_ids=user_ids, start_at=start_at,
            spread_minutes=plan.get('spread_minutes'),
            wave_interval_minutes=plan.get('wave_interval_minutes')))

    def _audit(cursor, ctx, action, post, details=None):
        """Одна дверь в журнал вики для всех роутов раздела (news/audit.py).

        Пространство вкладки — только запасное: у новости со своим
        пространством запись принадлежит ей, а не тому, куда человек смотрит.
        """
        news_audit.record(cursor, actor_id=ctx['user_id'], action=action, post=post,
                          fallback_space_id=_request_space(cursor), details=details)

    def _audit_launch(cursor, ctx, post_id, outcome, plan, created=False):
        """Запись о выпуске или взведённом запуске — по тому, что сделал _launch.

        created — новость создана этим же нажатием: отдельной строки «создана»
        у неё нет, и подробность говорит об этом, чтобы по журналу было видно,
        что до выпуска черновика не существовало.
        """
        post = _get_post(cursor, post_id)
        details = {'mode': plan.get('publish_mode'), 'kind': plan.get('kind')}
        if created:
            details['created'] = True
        if outcome == 'scheduled':
            details['scheduled_at'] = post.get('scheduled_at')
            _audit(cursor, ctx, 'news.schedule', post, details)
        else:
            _audit(cursor, ctx, 'news.publish', post, details)

    def _launch(cursor, post_id, plan, audience_max_role_level):
        """Выпуск по плану. 'scheduled' — запуск взведён, 'published' — ушло.

        Одна дверь на создание с публикацией и на кнопку «Опубликовать»: две
        копии этого выбора разъехались бы ровно в том, чего не видно, — в том,
        строятся ли волны.
        """
        moment = datetime.now()
        start = plan.get('scheduled_at')
        if plan.get('publish_mode') != 'now' and start and start > moment:
            queries.schedule_post(cursor, post_id=post_id, scheduled_at=start,
                                  audience_max_role_level=audience_max_role_level)
            return 'scheduled'
        # Растяжка «сразу» считается от ЭТОЙ минуты: первая волна открывается
        # немедленно, остальные догоняют по расписанию.
        if plan.get('publish_mode') == 'spread' and _plan_ready(cursor):
            _build_waves(cursor, post_id, plan, start_at=moment)
        queries.publish_post(cursor, post_id=post_id,
                             audience_max_role_level=audience_max_role_level)
        return 'published'

    def _set_photos_refusal(cursor, ctx, post_id, payload):
        """Привязка кадров, если форма их прислала. Строка отказа или None.

        Ключа нет — кадры не трогаем: тем же правилом живёт `audience`, и оно
        нужно, чтобы правка одного поля не сносила остальные.
        """
        if 'photos' not in payload:
            return None
        if not _photos_ready(cursor):
            # Кадры прислали, а таблицы нет: молча проглотить нельзя — автор
            # решит, что фотографии прикреплены, и опубликует объявление без них.
            return 'Фотографии ещё разворачиваются, попробуйте чуть позже'
        return queries.set_photos(cursor, post_id=post_id,
                                  photo_ids=payload.get('photos') or [],
                                  user_id=ctx['user_id'])

    def _rules_from_request(payload):
        """Адресаты из тела запроса, приведённые к виду таблицы."""
        rules = []
        for raw in (payload.get('audience') or []):
            if not isinstance(raw, dict):
                continue
            subject_type = str(raw.get('subject_type') or '').strip()
            rules.append({
                'subject_type': subject_type,
                'subject_id': (None if subject_type == 'otp_role'
                               else _int_or_none(raw.get('subject_id'))),
                'subject_role': (str(raw.get('subject_role') or '').strip().lower()
                                 if subject_type == 'otp_role' else None),
                'min_role_level': _int_or_none(raw.get('min_role_level')),
            })
        return rules

    def _audience_refusal(cursor, ctx, rules, space_id=None):
        """Отказ по адресатам либо None. Отделы адресатов достаются одним
        запросом — по одному на строку формы было бы десять обращений к базе.

        space_id — пространство, которому новость принадлежит: у создания это
        вкладка, из которой пишут, у правки и публикации — сама новость. Форма
        уже сужена тем же справочником, но правило, живущее только во фронте,
        держится до первого запроса мимо него."""
        return news_access.audience_refusal(
            rules,
            ceiling=ctx['ceiling'],
            departments=ctx['departments'],
            space_departments=_space_departments(cursor, space_id),
            subject_departments=queries.subject_departments(cursor, rules),
            target_roles=queries.roles_of_users(
                cursor, [r.get('subject_id') for r in rules
                         if r.get('subject_type') == 'user']),
        )

    # ── ЧТЕНИЕ: доступно каждому сотруднику ──────────────────────────────
    @news_route('/pending')
    def news_pending(cursor, ctx):
        """Новости, которые надо показать этому человеку прямо сейчас.

        Отметку о показе ставим ЗДЕСЬ же, а не отдельным запросом с фронта: она
        и есть точка отсчёта задержки кнопки «Прочитал», и лишний рейс на
        сервер ради неё означал бы окно, в котором кнопка не загорится никогда,
        если второй запрос не дошёл.
        """
        with_photos = _photos_ready(cursor)
        items = queries.pending_for_user(
            cursor, user_id=ctx['user_id'], otp_role=ctx['otp_role'],
            subjects=ctx['subjects'], with_photos=with_photos,
            with_quiz=_quiz_ready(cursor), with_pass=_pass_ready(cursor),
            # Окно стоит вне вики, и пространства у него нет: человеку
            # показывают всё, что адресовано ЕМУ. Границу здесь держит не
            # вкладка, а сама новость — объявление чужой компании под правило
            # «все операторы» больше не попадает (access.SPACE_MATCH_TEMPLATE).
            with_space=_space_ready(cursor),
            # ТОЛЬКО объявления портала: то, что автор отправил в Oktell,
            # показывает программа поверх клиента АТС, и второе окно здесь
            # означало бы два подтверждения одной новости.
            channel=(news_access.DEFAULT_CHANNEL if _channel_ready(cursor) else None))
        if with_photos:
            # Подписи берутся из процессного кэша и базу не трогают: обращений к
            # ней у этого роута столько же, сколько было до фотографий.
            for item in items:
                item['photos'] = news_photos.sign_urls(gcs, item['photos'])
        if items:
            # Только ПЕРВУЮ: окно показывает по одной, а отметка «открыл» — это
            # запись в журнал и точка отсчёта задержки. Поставив её всей
            # очереди, мы бы написали «открыл» про то, чего человек не видел.
            # Следующую отметит следующий /pending — окно его и запрашивает,
            # подтвердив текущую.
            queries.mark_shown(cursor, news_ids=[items[0]['id']],
                               user_id=ctx['user_id'])
        return jsonify({"items": items, "schema_ready": True})

    @news_route('/<int:post_id>/read', methods=('POST',))
    def news_read(cursor, ctx, post_id):
        """«Прочитал». Задержку и тест проверяет сервер (queries.confirm_read)."""
        payload = request.get_json(silent=True) or {}
        status, detail = queries.confirm_read(
            cursor, news_id=post_id, user_id=ctx['user_id'],
            otp_role=ctx['otp_role'], subjects=ctx['subjects'],
            answers=payload.get('answers'), with_quiz=_quiz_ready(cursor),
            with_pass=_pass_ready(cursor), with_space=_space_ready(cursor),
            with_plan=_plan_ready(cursor), with_attempts=_attempts_ready(cursor))
        if status == 'not_found':
            return jsonify({"error": "Новость не найдена"}), 404
        if status == 'trainer_pending':
            return jsonify({"error": "Сначала пройдите тренажёр",
                            "code": "NEWS_TRAINER_PENDING"}), 409
        if status == 'too_early':
            return jsonify({"error": "Кнопка станет активной чуть позже",
                            "code": "NEWS_TOO_EARLY",
                            "remaining_seconds": detail}), 409
        if status == 'quiz_wrong':
            # Какие именно вопросы неверны, НЕ называем (решение владельца
            # 21.09.2026): окно снимает весь выбор и просит пройти тест заново,
            # а подсказка «ошибка во втором» вернула бы подбор ответа
            # переключением одного варианта — при том что сеть у браузера
            # открыта любому.
            return jsonify(_quiz_refusal(detail)), 409
        return jsonify({"status": "ok"})

    @news_route('/<int:post_id>/quiz', methods=('POST',))
    def news_quiz_pass(cursor, ctx, post_id):
        """«Проверить» у теста (задача #342). Отметка в журнале, без подтверждения.

        Нужен необязательному тесту: он не держит «Прочитал», но пройти его
        человек вправе — в окне или позже во вкладке «Новости». Тот же гейт, что
        у /read: опубликована и адресована этому человеку (queries.pass_quiz).
        """
        if not (_quiz_ready(cursor) and _pass_ready(cursor)):
            return jsonify({"error": "Тесты ещё разворачиваются, попробуйте чуть позже",
                            "code": "NEWS_QUIZ_NOT_READY"}), 503
        payload = request.get_json(silent=True) or {}
        status, detail = queries.pass_quiz(
            cursor, news_id=post_id, user_id=ctx['user_id'], otp_role=ctx['otp_role'],
            subjects=ctx['subjects'], answers=payload.get('answers'),
            with_space=_space_ready(cursor), with_plan=_plan_ready(cursor),
            with_attempts=_attempts_ready(cursor))
        if status in ('not_found', 'no_quiz'):
            return jsonify({"error": "Теста у этой новости нет"}), 404
        if status == 'quiz_wrong':
            # Вопросы с ошибкой не называем — как и у /read.
            return jsonify(_quiz_refusal(detail)), 409
        return jsonify({"status": "ok"})

    @news_route('/<int:post_id>/trainer', methods=('POST',))
    def news_trainer_pass(cursor, ctx, post_id):
        """Тренажёр новости пройден до конца (задача #342).

        Зовёт окно, когда урок дошёл до финального шага. Итог присылает браузер
        — сценарии живут в коде фронта, и так же пишется статистика тренажёров
        вики; граница — та же, что у /read.
        """
        if not _pass_ready(cursor):
            return jsonify({"error": "Тренажёры ещё разворачиваются, попробуйте чуть позже",
                            "code": "NEWS_PASS_NOT_READY"}), 503
        status, _detail = queries.mark_trainer_passed(
            cursor, news_id=post_id, user_id=ctx['user_id'], otp_role=ctx['otp_role'],
            subjects=ctx['subjects'], with_space=_space_ready(cursor),
            with_plan=_plan_ready(cursor))
        if status in ('not_found', 'no_trainer'):
            return jsonify({"error": "Тренажёра у этой новости нет"}), 404
        return jsonify({"status": "ok"})

    @news_route('/feed')
    def news_feed(cursor, ctx):
        """Лента «мои новости» для вкладки «Новости» вики (решение владельца 17.09.2026).

        Вкладка открыта всем, но читателю — «только сами новости, которые ему
        были предназначены». Поэтому периметр тот же, что у окна, а права
        публикации роут не считает вовсе: чтение за них не платит.
        """
        limit = min(max(_int_or_none(request.args.get('limit')) or 20, 1), 50)
        offset = max(_int_or_none(request.args.get('offset')) or 0, 0)
        total, items = queries.feed_for_user(
            cursor, user_id=ctx['user_id'], otp_role=ctx['otp_role'],
            subjects=ctx['subjects'], limit=limit, offset=offset,
            with_photos=_photos_ready(cursor), with_quiz=_quiz_ready(cursor),
            with_pass=_pass_ready(cursor), with_space=_space_ready(cursor),
            # Волна режет и ленту: раньше своей волны человек не должен
            # увидеть объявление даже списком.
            with_plan=_plan_ready(cursor),
            # Лента живёт во вкладке вики, и вкладка всегда открыта В
            # пространстве: «Новости» в «Тез» — это новости Тез.
            space_id=_request_space(cursor))
        return jsonify({"items": items, "total": total, "schema_ready": True})

    @news_route('/feed/<int:post_id>')
    def news_feed_item(cursor, ctx, post_id):
        """Новость из ленты целиком: текст, кадры, тест без ответов, тренажёр."""
        with_photos = _photos_ready(cursor)
        post = queries.feed_post(
            cursor, news_id=post_id, user_id=ctx['user_id'], otp_role=ctx['otp_role'],
            subjects=ctx['subjects'], with_photos=with_photos,
            with_quiz=_quiz_ready(cursor), with_pass=_pass_ready(cursor),
            with_space=_space_ready(cursor), with_plan=_plan_ready(cursor))
        if post is None:
            return jsonify({"error": "Новость не найдена"}), 404
        post['photos'] = news_photos.sign_urls(gcs, post['photos']) if with_photos else []
        return jsonify(post)

    # ── ВЫПУСК: супервайзер и выше ───────────────────────────────────────
    @news_route('/access', rights=True)
    def news_access_info(cursor, ctx):
        """Что этот человек вправе делать в разделе + справочники адресата.

        Одним ответом, а не тремя: форма открывается сразу с заполненными
        списками, и «вход в раздел стоит один запрос» остаётся правдой.
        """
        if ctx['ceiling'] is None:
            return jsonify({"can_publish": False, "ceiling": None,
                            "subjects": {}, "people": [], "roles": []})
        # Отделы ПРОСТРАНСТВА, из которого открыта вкладка. Справочник сужается
        # ими так же, как у выдачи доступа в вике: предлагать в «Тез» отдел
        # СЗоВ значит показывать чужую оргструктуру и обещать правило, которое
        # сервер отвергнет (access.audience_refusal).
        space_id = _request_space(cursor)
        space_departments = _space_departments(cursor, space_id)
        return jsonify({
            "can_publish": True,
            # Куда можно отправить объявление из ЭТОГО пространства. Один
            # вариант — форма про выбор не спрашивает вовсе.
            "channels": _channels_for_space(cursor, space_id),
            "ceiling": ctx['ceiling'],
            "bounded": ctx['departments'] is not None,
            "default_confirm_delay_seconds": DEFAULT_CONFIRM_DELAY_SECONDS,
            # Пределы планировщика и теста — ОТ СЕРВЕРА (ТЗ #300, п.4 и п.8).
            # Форма рисует по ним поля и не держит своей копии чисел: копия
            # разъехалась бы, и автор получал бы отказ на значение, которое ему
            # только что предложили.
            "plan_ready": _plan_ready(cursor),
            "min_spread_minutes": MIN_SPREAD_MINUTES,
            "max_spread_minutes": MAX_SPREAD_MINUTES,
            "min_wave_interval_minutes": MIN_WAVE_INTERVAL_MINUTES,
            "subjects": queries.subject_catalog(cursor, ctx['departments'],
                                                space_department_ids=space_departments),
            "people": queries.targetable_people(
                cursor, max_role_level=ctx['ceiling'],
                department_ids=ctx['departments'],
                space_department_ids=space_departments),
            # Должность как адресат доступна только тому, у кого нет границы
            # отдела: правило на роль адресует людей по всей компании.
            "roles": (queries.targetable_roles(ctx['ceiling'])
                      if ctx['departments'] is None else []),
        })

    @news_route('/audience/oktell', methods=('POST',), publisher=True)
    def news_audience_oktell(cursor, ctx):
        """Кто из отмеченных адресатов не увидит объявление в Oktell.

        Канал Oktell рисует окно поверх клиента АТС, а в АТС человек без
        SIP-номера не работает вовсе — объявление до него просто не дойдёт.
        Форма спрашивает это ДО публикации и называет людей поимённо: «кому-то
        не дошло» выясняется иначе только через неделю и по жалобе.

        Правила приезжают НЕСОХРАНЁННЫМИ — их набирают прямо сейчас. Считает их
        тот же report_match, что и журнал (queries.audience_sip_check), а
        периметр проверяется тем же _audience_refusal, что у сохранения: иначе
        ручка стала бы способом перебрать чужих людей по id.
        """
        payload = request.get_json(silent=True) or {}
        rules = _rules_from_request(payload)
        if not rules:
            return jsonify({"checked": 0, "missing": [], "missing_count": 0})
        space_id = _request_space(cursor)
        refusal = _audience_refusal(cursor, ctx, rules, space_id=space_id)
        if refusal:
            return jsonify({"error": refusal, "code": "NEWS_AUDIENCE"}), 403
        checked, missing = queries.audience_sip_check(
            cursor, rules=rules, author_id=ctx['user_id'],
            audience_max_role_level=ctx['ceiling'],
            space_id=space_id, with_space=_space_ready(cursor))
        # Список режем, счётчик — нет: в панели читают первые имена, а решение
        # принимают по числу. Полсотни строк в ответе хватает и на «весь отдел».
        return jsonify({"checked": checked,
                        "missing": missing[:SIP_MISSING_LIMIT],
                        "missing_count": len(missing)})

    @news_route('/audience/preview', methods=('POST',), publisher=True)
    def news_audience_preview(cursor, ctx):
        """Расчёт рассылки ДО подтверждения публикации (ТЗ #300, п.8.4).

        «Позволит администратору оценить влияние обязательной новости на
        доступность операторов до её запуска»: сколько человек получит
        объявление, на сколько волн они разойдутся, по скольку в волне и когда
        дойдёт до последнего.

        Считается ТЕМИ ЖЕ функциями, что и настоящий выпуск: число получателей —
        правилами журнала, волны — access.wave_count. Вторая формула в форме
        обещала бы одно, а происходило бы другое.

        Правила приезжают несохранёнными — их набирают прямо сейчас; периметр
        проверяется тем же _audience_refusal, что у сохранения, иначе ручка
        стала бы способом пересчитать чужой отдел.
        """
        payload = request.get_json(silent=True) or {}
        rules = _rules_from_request(payload)
        space_id = _request_space(cursor)
        if rules:
            refusal = _audience_refusal(cursor, ctx, rules, space_id=space_id)
            if refusal:
                return jsonify({"error": refusal, "code": "NEWS_AUDIENCE"}), 403
        recipients = queries.audience_count_for_rules(
            cursor, rules=rules, author_id=ctx['user_id'],
            audience_max_role_level=ctx['ceiling'],
            space_id=space_id, with_space=_space_ready(cursor))
        start = _moment_or_none(payload.get('scheduled_at')) or datetime.now()
        preview = news_access.spread_preview(
            recipients=recipients, start_at=start,
            spread_minutes=_int_or_none(payload.get('spread_minutes')),
            wave_interval_minutes=_int_or_none(payload.get('wave_interval_minutes')))
        preview['starts_at'] = preview['starts_at'].isoformat()
        preview['ends_at'] = preview['ends_at'].isoformat()
        return jsonify(preview)

    @news_route('/posts', publisher=True)
    def news_posts(cursor, ctx):
        limit = min(max(_int_or_none(request.args.get('limit')) or 50, 1), 200)
        offset = max(_int_or_none(request.args.get('offset')) or 0, 0)
        status = request.args.get('status') or None
        total, items = queries.list_posts(
            cursor, viewer_id=ctx['user_id'],
            viewer_level=news_access.effective_role_level(ctx['otp_role']),
            departments=ctx['departments'], status=status,
            limit=limit, offset=offset, with_photos=_photos_ready(cursor),
            with_channel=_channel_ready(cursor), with_plan=_plan_ready(cursor),
            with_takedown=_takedown_ready(cursor),
            with_quiz=_quiz_ready(cursor), with_pass=_pass_ready(cursor),
            with_space=_space_ready(cursor), space_id=_request_space(cursor))
        return jsonify({"items": [_with_rights(ctx, item) for item in items],
                        "total": total})

    @news_route('/posts/<int:post_id>', publisher=True)
    def news_post_item(cursor, ctx, post_id):
        post = _get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_read_post(ctx, post):
            return jsonify({"error": "Эта новость не из вашего периметра"}), 403
        post['audience_size'] = queries.audience_size(
            cursor, post_id, with_space=_space_ready(cursor))
        return jsonify(_dress(cursor, ctx, post))

    @news_route('/posts', methods=('POST',), publisher=True)
    def news_post_create(cursor, ctx):
        payload = request.get_json(silent=True) or {}
        title = news_access.normalize_title(payload.get('title'))
        if not title:
            return jsonify({"error": "Укажите заголовок"}), 400
        body = sanitize_html(payload.get('body') or '')
        rules = _rules_from_request(payload)
        # Пространство новости. Вкладка называет его сама; вкладка со старым
        # бандлом не называет — тогда берём вику отдела автора, как при
        # разборе ничьих на старте (schema.backfill_space_ids). Оставить такую
        # новость ничьей значило бы показать её обеим компаниям разом.
        space_id = _request_space(cursor)
        if space_id is None and _space_ready(cursor):
            space_id = queries.space_of_department(cursor, ctx['department_id'])
        refusal = _audience_refusal(cursor, ctx, rules, space_id=space_id)
        if refusal:
            return jsonify({"error": refusal, "code": "NEWS_AUDIENCE"}), 403
        # Тест проверяется ДО первой записи: отказ после create_post оставил бы
        # в базе новость без теста, который автор считал приложенным.
        quiz, quiz_refusal = _quiz_from_request(cursor, payload)
        if quiz_refusal:
            return quiz_refusal
        passes, pass_refusal = _passes_from_request(cursor, payload)
        if pass_refusal:
            return pass_refusal
        # Канал — тоже ДО первой записи: отказ после create_post оставил бы в
        # базе черновик, который автор считает отменённым.
        channel, channel_refusal = _channel_from_request(cursor, payload, space_id)
        if channel_refusal:
            return channel_refusal
        # План (тип, проходной балл, режим запуска) — тоже до первой записи и по
        # той же причине. Тип решает за обязательность и за обязательность
        # прохождения: два тумблера отвечали на этот вопрос порознь и позволяли
        # собрать «необязательную новость с обязательным тестом».
        plan, plan_refusal = _plan_from_request(
            cursor, payload, publishing=bool(payload.get('publish')),
            has_quiz=bool(quiz))
        if plan_refusal:
            return plan_refusal
        mandatory, passes['pass_required'] = news_access.kind_flags(
            plan['kind'], pass_required=passes['pass_required'])

        post_id = queries.create_post(
            cursor, title=title, body=body, author_id=ctx['user_id'],
            author_department_id=ctx['department_id'],
            space_id=space_id, with_space=_space_ready(cursor),
            channel=channel, with_channel=_channel_ready(cursor),
            plan=plan, with_plan=_plan_ready(cursor),
            # С ОБЯЗАТЕЛЬНЫМ прохождением — всегда обязательна: у необязательной
            # крестик подтверждал бы прочтение без единого ответа. Необязательный
            # тест или тренажёр (#342) обязательность не навязывают.
            is_mandatory=mandatory or news_access.must_pass(
                pass_required=passes['pass_required'], has_quiz=bool(quiz),
                has_trainer=bool(passes['trainer_key'])),
            confirm_delay_seconds=news_access.normalize_delay(
                payload.get('confirm_delay_seconds', DEFAULT_CONFIRM_DELAY_SECONDS)),
            expires_at=_timestamp_or_none(payload.get('expires_at')),
            created_by=ctx['user_id'])
        queries.set_audience(cursor, post_id=post_id, rules=rules,
                             audience_max_role_level=ctx['ceiling'])
        # ДО публикации и в той же транзакции. Иначе между «новость есть» и
        # «кадры прикреплены» открывается окно, в котором объявление уже
        # всплыло у всего отдела без фотографий — а второй раз оно не всплывёт
        # никогда: очередь /pending человек получает один раз.
        photo_refusal = _set_photos_refusal(cursor, ctx, post_id, payload)
        if photo_refusal:
            return jsonify({"error": photo_refusal, "code": "NEWS_PHOTO_LIMIT"}), 400
        # Тест — тоже ДО публикации и в той же транзакции, по той же причине, что
        # кадры: окно, всплывшее у отдела без вопросов, второй раз не всплывёт.
        if quiz:
            queries.set_quiz(cursor, post_id=post_id, quiz=quiz)
        # Тренажёр и обязательность — тоже до публикации и по той же причине.
        if _pass_ready(cursor):
            queries.set_passes(cursor, post_id=post_id, **passes)
        # ЖУРНАЛ — одна строка на одно нажатие. «Опубликовать» у новой новости
        # это и создание, и выпуск, и две записи в одну секунду («создана»,
        # «опубликована») были бы шумом: пишем то, чем кончилось. Черновик —
        # «создана». Отказ выпуска (просроченный срок) черновик НЕ отменяет:
        # ответ 4xx здесь тоже фиксирует транзакцию, — значит и запись о нём.
        if not payload.get('publish'):
            _audit(cursor, ctx, 'news.create', _get_post(cursor, post_id),
                   {'kind': plan.get('kind')})
        else:
            refusal = _expiry_refusal(_get_post(cursor, post_id)['expires_at'], True)
            if refusal:
                _audit(cursor, ctx, 'news.create', _get_post(cursor, post_id),
                       {'kind': plan.get('kind')})
                return jsonify({"error": refusal, "code": "NEWS_EXPIRED"}), 400
            # Волны строятся ВНУТРИ _launch и ДО publish_post — в той же
            # транзакции и по той же причине, что кадры и тест: объявление,
            # всплывшее раньше своего расписания, второй раз не всплывёт.
            outcome = _launch(cursor, post_id, plan, audience_max_role_level=ctx['ceiling'])
            _audit_launch(cursor, ctx, post_id, outcome, plan, created=True)
        return jsonify(_dress(cursor, ctx, _get_post(cursor, post_id))), 201

    @news_route('/posts/<int:post_id>', methods=('PATCH',), publisher=True)
    def news_post_update(cursor, ctx, post_id):
        post = _get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_edit(ctx, post):
            return jsonify({"error": "Править новость может её автор"}), 403
        # Тест и кадры ДО правки — для журнала: колонками в карточке их нет, и
        # «что изменилось» по ним иначе не сказать (news/audit.py).
        before_quiz = queries.post_quiz(cursor, post_id) if _quiz_ready(cursor) else []
        before_photos = ([photo['id'] for photo in queries.post_photos(cursor, post_id)]
                         if _photos_ready(cursor) else [])

        # Правка ОПУБЛИКОВАННОЙ новости не сбрасывает подтверждения. Это
        # решение, а не недосмотр: правят обычно опечатку, а сброс показал бы
        # окно заново всему отделу. Нужно спросить заново — публикуется новая
        # новость; так же устроено и обязательное ознакомление в вике, где
        # новая редакция статьи создаёт новое назначение, а не переоткрывает
        # старое. Автору это сказано прямо в форме.
        payload = request.get_json(silent=True) or {}
        title = news_access.normalize_title(payload.get('title', post['title']))
        if not title:
            return jsonify({"error": "Укажите заголовок"}), 400
        body = (sanitize_html(payload['body']) if 'body' in payload else post['body'])

        rules = (_rules_from_request(payload) if 'audience' in payload
                 else [{'subject_type': r['subject_type'], 'subject_id': r['subject_id'],
                        'subject_role': r['subject_role'],
                        'min_role_level': r['min_role_level']}
                       for r in post['audience']])
        refusal = _audience_refusal(cursor, ctx, rules, space_id=post.get('space_id'))
        if refusal:
            return jsonify({"error": refusal, "code": "NEWS_AUDIENCE"}), 403

        # Обязательность у ВЫПУЩЕННОЙ новости не меняется. У необязательной
        # крестик пишет ту же отметку, что кнопка «Прочитал», — переключив
        # тумблер задним числом, автор превратил бы «закрыл, не читая» в
        # «подтвердил прочтение» у всех, кто уже успел её закрыть, и журнал
        # соврал бы ровно там, где к нему обращаются.
        #
        # Само значение считается ниже, из ТИПА новости (ТЗ #300, п.5): тумблер
        # «обязательно к прочтению» остался способом исполнения, а вопрос
        # автору задаётся один — насколько это важно.
        # Тест ВЫПУСКАВШЕЙСЯ новости не меняется: часть отдела уже ответила на эти
        # вопросы, и подменённый тест сделал бы журнал «Кто прочитал» журналом
        # другой новости. По published_at, а не по статусу — как у удаления: снятая
        # с показа новость свои ответы тоже уже собрала. Нужен другой тест —
        # публикуется новая новость.
        quiz = None
        if 'quiz' in payload:
            if post['published_at']:
                return jsonify({
                    "error": "Тест опубликованной новости не меняется — опубликуйте новую новость",
                    "code": "NEWS_QUIZ_LOCKED",
                }), 409
            quiz, quiz_refusal = _quiz_from_request(cursor, payload)
            if quiz_refusal:
                return quiz_refusal
        # Тест, который останется у новости: присланный в этой правке или прежний.
        # Считаем ЗДЕСЬ, до плана: по нему проверяется критичный тип, а он
        # обязан смотреть на то, чем новость станет, а не чем была.
        keeps_quiz = (bool(quiz) if quiz is not None
                      else bool(_quiz_ready(cursor)
                                and queries.quiz_answer_key(cursor, post_id)))
        # Тренажёр и обязательность прохождения выпускавшейся новости не
        # меняются по той же причине, что тест: часть отдела уже прошла или
        # подтвердила её на прежних условиях. Сравниваем со значением, а не с
        # наличием ключа: форма вправе прислать то, что и так стоит.
        passes, pass_refusal = _passes_from_request(cursor, payload, post)
        if pass_refusal:
            return pass_refusal
        plan, plan_refusal = _plan_from_request(
            cursor, payload, post, publishing=bool(payload.get('publish')),
            has_quiz=keeps_quiz)
        if plan_refusal:
            return plan_refusal
        # ТИП ВЫПУЩЕННОЙ НОВОСТИ НЕ МЕНЯЕТСЯ. Он решает и за обязательность, и
        # за обязательность прохождения, а обе у выпущенной заперты (ниже и в
        # NEWS_PASS_LOCKED). Отдельный отказ нужен, чтобы автор прочитал
        # «тип не меняется», а не «обязательность» — он менял тип.
        if post['published_at'] and plan['kind'] != post.get('kind'):
            return jsonify({
                "error": "Тип опубликованной новости не меняется — "
                         "опубликуйте новую новость",
                "code": "NEWS_KIND_LOCKED",
            }), 409
        # Проходной балл — под тем же замком, что и сам тест: часть отдела уже
        # сдала его по прежнему порогу, и журнал стал бы журналом другой новости.
        if post['published_at'] and (
                plan['pass_score_percent'] != post.get('pass_score_percent')):
            return jsonify({
                "error": "Проходной балл опубликованной новости не меняется — "
                         "опубликуйте новую новость",
                "code": "NEWS_SCORE_LOCKED",
            }), 409
        wants_mandatory, passes['pass_required'] = news_access.kind_flags(
            plan['kind'], pass_required=passes['pass_required'])
        if post['published_at'] and (
                passes['trainer_key'] != post['trainer_key']
                or passes['pass_required'] != post['pass_required']):
            return jsonify({
                "error": "Тренажёр и обязательность прохождения опубликованной новости "
                         "не меняются — опубликуйте новую новость",
                "code": "NEWS_PASS_LOCKED",
            }), 409
        # Канал ВЫПУЩЕННОЙ новости не меняется — по той же причине, что тест и
        # обязательность: объявление уже показано людям там, куда его отправили,
        # и половина отдела подтвердила его в другом окне. Нужен другой канал —
        # публикуется новая новость.
        channel, channel_refusal = _channel_from_request(
            cursor, payload, post.get('space_id'), post)
        if channel_refusal:
            return channel_refusal
        if post['published_at'] and channel != (post.get('channel')
                                                or news_access.DEFAULT_CHANNEL):
            return jsonify({
                "error": "Канал опубликованной новости не меняется — "
                         "опубликуйте новую новость",
                "code": "NEWS_CHANNEL_LOCKED",
            }), 409
        # Принесли тест или тренажёр с обязательным прохождением — новость
        # становится обязательной сама, как и при создании.
        if news_access.must_pass(
                pass_required=passes['pass_required'], has_quiz=bool(quiz),
                has_trainer='trainer_key' in payload and bool(passes['trainer_key'])):
            wants_mandatory = True
        if post['status'] == 'published' and wants_mandatory != bool(post['is_mandatory']):
            return jsonify({
                "error": "У опубликованной новости обязательность не меняется — "
                         "снимите её с показа и опубликуйте заново",
                "code": "NEWS_MANDATORY_LOCKED",
            }), 409
        # С обязательным прохождением — тем более: у необязательной новости
        # крестик и есть подтверждение, без единого ответа.
        if not wants_mandatory and news_access.must_pass(
                pass_required=passes['pass_required'], has_quiz=keeps_quiz,
                has_trainer=bool(passes['trainer_key'])):
            return jsonify({
                "error": "У новости с обязательным тестом или тренажёром "
                         "обязательность не снимается",
                "code": "NEWS_QUIZ_MANDATORY",
            }), 409

        queries.update_post(
            cursor, post_id=post_id, title=title, body=body,
            is_mandatory=wants_mandatory,
            confirm_delay_seconds=news_access.normalize_delay(
                payload.get('confirm_delay_seconds', post['confirm_delay_seconds'])),
            expires_at=_timestamp_or_none(
                payload.get('expires_at', post['expires_at'])),
            channel=channel, with_channel=_channel_ready(cursor),
            plan=plan, with_plan=_plan_ready(cursor))
        if 'audience' in payload:
            queries.set_audience(cursor, post_id=post_id, rules=rules,
                                 audience_max_role_level=ctx['ceiling'])
        # Пустой список у черновика — «убрать тест».
        if quiz is not None and _quiz_ready(cursor):
            queries.set_quiz(cursor, post_id=post_id, quiz=quiz)
        if _pass_ready(cursor):
            queries.set_passes(cursor, post_id=post_id, **passes)
        photo_refusal = _set_photos_refusal(cursor, ctx, post_id, payload)
        if photo_refusal:
            return jsonify({"error": photo_refusal, "code": "NEWS_PHOTO_LIMIT"}), 400

        # ЖУРНАЛ: что правка изменила на самом деле. Форма присылает все поля
        # разом, поэтому сравниваем карточки до и после, а не ключи запроса —
        # иначе каждая правка опечатки читалась бы как «поменяли всё».
        after = _get_post(cursor, post_id)
        fields = news_audit.changed_fields(post, after)
        if 'quiz' in payload and _quiz_ready(cursor) \
                and before_quiz != queries.post_quiz(cursor, post_id):
            fields.append('quiz')
        if 'photos' in payload and _photos_ready(cursor) and before_photos != [
                photo['id'] for photo in queries.post_photos(cursor, post_id)]:
            fields.append('photos')
        # Снятый взвод — отдельная запись «запуск отменён», а не безликое
        # «поле: запуск»: это решение, которое потом ищут по журналу.
        unscheduled = post.get('state') == 'scheduled' and after.get('state') != 'scheduled'
        if unscheduled:
            fields = [field for field in fields if field != 'schedule']
        if fields:
            _audit(cursor, ctx, 'news.update', after, {'fields': fields})
        if unscheduled:
            _audit(cursor, ctx, 'news.unschedule', after)
        return jsonify(_dress(cursor, ctx, after))

    @news_route('/posts/<int:post_id>/publish', methods=('POST',), publisher=True)
    def news_post_publish(cursor, ctx, post_id):
        post = _get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_edit(ctx, post):
            return jsonify({"error": "Публикует новость её автор"}), 403
        rules = [{'subject_type': r['subject_type'], 'subject_id': r['subject_id'],
                  'subject_role': r['subject_role'],
                  'min_role_level': r['min_role_level']} for r in post['audience']]
        refusal = _audience_refusal(cursor, ctx, rules, space_id=post.get('space_id'))
        if refusal:
            return jsonify({"error": refusal, "code": "NEWS_AUDIENCE"}), 403
        refusal = _expiry_refusal(post['expires_at'], True)
        if refusal:
            return jsonify({"error": refusal, "code": "NEWS_EXPIRED"}), 400
        # План берём У САМОЙ НОВОСТИ: кнопка «Опубликовать» тела не присылает, а
        # режим запуска автор выбрал в форме и сохранил вместе с остальным.
        plan, plan_refusal = _plan_from_request(cursor, {}, post, publishing=True,
                                                has_quiz=bool(_quiz_ready(cursor)
                                                              and queries.quiz_answer_key(
                                                                  cursor, post_id)))
        if plan_refusal:
            return plan_refusal
        outcome = _launch(cursor, post_id, plan, audience_max_role_level=ctx['ceiling'])
        _audit_launch(cursor, ctx, post_id, outcome, plan)
        return jsonify(_dress(cursor, ctx, _get_post(cursor, post_id)))

    @news_route('/posts/<int:post_id>/archive', methods=('POST',), publisher=True)
    def news_post_archive(cursor, ctx, post_id):
        post = _get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_take_down(ctx, post):
            return jsonify({"error": "Снять новость может её автор "
                                     "или руководитель выше него"}), 403
        # Снимают только то, что на показе (ТЗ #300, п.16). Черновик снимать
        # не с чего, а снятую второй раз — значит переписать имя и время
        # первого, кто её остановил. Условие живёт В САМОМ UPDATE
        # (queries.take_down): двое, нажавшие одновременно, иначе оба получили
        # бы «сняли», и в истории остался бы второй.
        if not queries.take_down(cursor, post_id=post_id, by=ctx['user_id'],
                                 with_takedown=_takedown_ready(cursor)):
            return jsonify({"error": "Новость уже не на показе — "
                                     "её сняли раньше или она ещё не выходила",
                            "code": "NEWS_NOT_ON_AIR"}), 409
        _audit(cursor, ctx, 'news.archive', post)
        return jsonify(_dress(cursor, ctx, _get_post(cursor, post_id)))

    # defer_cursor: между удалением строк и сносом блобов стоит чужая сеть, и
    # снести байты раньше, чем база подтвердила удаление, значило бы получить
    # новость с пустыми рамками, если транзакция не доедет.
    @news_route('/posts/<int:post_id>', methods=('DELETE',), publisher=True,
                defer_cursor=True)
    def news_post_delete(ctx, post_id):
        """Удалить новость насовсем — вместе с журналом прочтений и тестом.

        Решение владельца 21.09.2026: «в архиве должна быть кнопка удалить,
        чтобы можно было удалить новость навсегда, журнал его тоже удалится кто
        прочитал». До него удалялось только то, что ни разу не выходило к
        людям, и снятая новость оставалась в архиве навсегда.

        Порог остался ровно один и он про ПОКАЗ, а не про прошлое: объявление,
        висящее у людей на экране прямо сейчас, сначала снимают. Иначе
        обязательное окно исчезало бы у читающего его человека в середине
        текста, а «Прочитал» отвечало бы, что новости нет.
        """
        with db._get_cursor() as cursor:
            post = _get_post(cursor, post_id)
            if not post:
                return jsonify({"error": "Новость не найдена"}), 404
            if not _may_edit(ctx, post):
                return jsonify({"error": "Удалить новость может её автор"}), 403
            if post['status'] == 'published':
                return jsonify({"error": "Новость на показе — сначала снимите её",
                                "code": "NEWS_PUBLISHED"}), 409
            # ЗАПИСЬ — ДО удаления и в той же транзакции. После удаления от
            # новости не остаётся ничего: ни строки, ни журнала прочтений, ни
            # теста, — и этот вопрос владелец и задал: «чтобы при удалении
            # можно было увидеть, кто это сделал». Заодно фиксируем, ЧТО стёрто
            # вместе с ней: сколько человек успели подтвердить.
            addressed, confirmed = queries.audience_stats(
                cursor, [post_id], with_space=_space_ready(cursor)).get(post_id, (0, 0))
            _audit(cursor, ctx, 'news.delete', post, {
                'was_published': bool(post.get('published_at')),
                'addressed': addressed, 'confirmed': confirmed})
            refs = queries.delete_post(cursor, post_id,
                                       with_photos=_photos_ready(cursor))
        # ПОСЛЕ фиксации — тем же приёмом, что и снятие одного кадра.
        news_photos.drop_blobs(gcs, refs)
        return jsonify({"status": "deleted"})

    # ── ФОТОГРАФИИ ───────────────────────────────────────────────────────
    # Роута ОТДАЧИ кадра здесь нет, и это решение: тег <img> не шлёт
    # заголовков, значит такой роут пришлось бы авторизовать кукой, а на
    # мобильном её SameSite понижается до Lax и кросс-сайтовый запрос её не
    # приложит. Браузер идёт прямо в GCS по подписи; подробности и честная
    # оговорка про пересылку адреса — в шапке news/photos.py.
    @news_route('/quiz/draft', methods=('POST',), publisher=True, defer_cursor=True)
    def news_quiz_draft(ctx):
        """«Составить ИИ» в форме новости: тест по заголовку и тексту. Ничего не пишет.

        Курсор отдан в пул ДО вызова модели (defer_cursor): черновик теста — это
        десятки секунд чужой сети, а слотов в пуле сорок на весь портал. Модель и
        разбор — те же, что пишут новость с тестом во «Вопросах операторов»
        (wiki/ai/knowledge.py). Импорт ленивый: модуль ИИ вики тянет за собой
        поиск и векторы, и поднимать его ради каждого /pending незачем — а
        заодно так нет кольца импорта (knowledge сам читает news.access).
        """
        from wiki.ai import knowledge as ai_knowledge
        from wiki.ai import providers as ai_providers

        payload = request.get_json(silent=True) or {}
        title = news_access.normalize_title(payload.get('title'))
        # Потолок на разбор HTML: в модель всё равно уходит QUIZ_MAX_SOURCE знаков.
        body = str(payload.get('body') or '')[:200000]
        if not ai_knowledge.news_text(body).strip():
            return jsonify({"error": "Сначала напишите текст новости — тест составляется по нему",
                            "code": "NEWS_QUIZ_NO_TEXT"}), 400
        try:
            result = ai_knowledge.draft_quiz(title=title, body=body,
                                             generate_fn=ai_providers.generate_article)
        except ai_providers.ProviderError as exc:
            return jsonify({"error": "ИИ недоступен — добавьте вопросы вручную",
                            "detail": str(exc)[:300], "code": "NEWS_AI_UNAVAILABLE"}), 503
        return jsonify({'quiz': result['quiz'], 'warnings': result['warnings'],
                        'model': (result.get('meta') or {}).get('model')})

    @news_route('/photos', methods=('POST',), publisher=True, defer_cursor=True)
    def news_photo_add(ctx):
        """Приложить фотографию. Один файл на запрос.

        По одному, а не пачкой: очередь грузится последовательно, и осечка на
        третьем снимке не уносит два уже уехавших. Плюс только так осмысленна
        проверка content_length — глобального MAX_CONTENT_LENGTH в приложении нет.
        """
        if not gcs or not (gcs.get('bucket_name') and gcs['bucket_name']()):
            return jsonify({"error": "Хранилище фотографий не настроено",
                            "code": "NEWS_PHOTO_STORAGE_OFF"}), 503
        # `or 0` обязателен: при chunked заголовка нет, и None > int дал бы 500
        # вместо честного 413.
        if (request.content_length or 0) > news_photos.MAX_BYTES:
            return jsonify({"error": 'Файл больше %d МБ'
                                     % (news_photos.MAX_BYTES // (1024 * 1024)),
                            "code": "NEWS_PHOTO_TOO_LARGE"}), 413
        item = request.files.get('file')
        if item is None:
            return jsonify({"error": "Файл не выбран",
                            "code": "NEWS_PHOTO_REJECTED"}), 400

        # Потолок «ничьих» — ДО чтения байтов: тот, кто не собирается сохранять
        # новость, не должен уметь набить бакет циклом.
        with db._get_cursor() as cursor:
            if not _photos_ready(cursor):
                return jsonify({"error": "Фотографии ещё разворачиваются",
                                "code": "NEWS_PHOTOS_NOT_READY"}), 409
            stale = queries.sweep_loose_photos(cursor)
            if queries.count_loose_photos(cursor, ctx['user_id']) >= MAX_LOOSE_PHOTOS_PER_USER:
                news_photos.drop_blobs(gcs, stale)
                return jsonify({"error": "Слишком много неприкреплённых фотографий — "
                                         "сохраните новость или закройте форму",
                                "code": "NEWS_PHOTO_LOOSE_LIMIT"}), 409
        news_photos.drop_blobs(gcs, stale)      # после фиксации, а не внутри неё

        try:                                    # пережатие и заливка — ВНЕ курсора
            prepared = news_photos.prepare(item.read(), filename=item.filename,
                                           content_type=item.mimetype)
            bucket, blob_path = news_photos.upload(gcs, prepared)
        except news_photos.PhotoError as refusal:
            return jsonify({"error": refusal.message,
                            "code": refusal.code}), refusal.status
        except Exception:
            # Наружу — общая фраза: текст ошибки хранилища несёт полный адрес
            # объекта, а обёртка news_route кладёт detail прямо в тело ответа.
            logging.exception('news: фотография не уехала в бакет')
            return jsonify({"error": "Не удалось сохранить фотографию",
                            "code": "NEWS_PHOTO_UPLOAD_FAILED"}), 502

        try:
            with db._get_cursor() as cursor:
                row = queries.insert_loose_photo(
                    cursor, prepared=prepared, bucket=bucket, blob_path=blob_path,
                    uploaded_by=ctx['user_id'])
        except Exception:
            # Байты уже в бакете, а строки не будет — снимаем блоб, иначе он
            # останется сиротой навсегда: сборщика сирот в проекте нет.
            news_photos.drop_blobs(gcs, [(bucket, blob_path)])
            raise

        signed = news_photos.sign_urls(gcs, [row])
        if not signed:
            return jsonify({"error": "Фотография сохранена, но адрес не выдался",
                            "code": "NEWS_PHOTO_UNSIGNED"}), 502
        return jsonify({"photo": signed[0]}), 201

    @news_route('/photos/<uuid:photo_id>', methods=('DELETE',),
                publisher=True, defer_cursor=True)
    def news_photo_drop(ctx, photo_id):
        """Снять фотографию.

        Конвертер <uuid:…> отсеивает мусор ещё в маршрутизации: без него «abc»
        доехало бы до psycopg2 и вернулось пятисоткой вместо 404.
        """
        with db._get_cursor() as cursor:
            if not _photos_ready(cursor):
                return jsonify({"error": "Фотография не найдена"}), 404
            row = queries.photo_by_id(cursor, str(photo_id))
            if not row:
                return jsonify({"error": "Фотография не найдена"}), 404
            if row['news_id'] is None:
                # Ничья — только своя. Второго читателя у неё нет по построению.
                if row['uploaded_by'] != ctx['user_id']:
                    return jsonify({"error": "Фотография не найдена"}), 404
            else:
                post = _get_post(cursor, row['news_id'])
                if not post:
                    return jsonify({"error": "Новость не найдена"}), 404
                # _may_edit, а не _may_read_post: чужой текст не правят, и
                # фотография здесь — часть текста.
                if not _may_edit(ctx, post):
                    return jsonify({"error": "Править новость может её автор"}), 403
            refs = queries.drop_photo(cursor, str(photo_id))
            refs += queries.sweep_loose_photos(cursor)
        # ПОСЛЕ фиксации транзакции: снести блоб раньше, чем БД подтвердила
        # удаление строки, значило бы получить запись, ссылающуюся в пустоту.
        news_photos.drop_blobs(gcs, refs)
        return jsonify({"status": "deleted"})

    def _report_data(cursor, post):
        """Журнал новости: строки со статусом, сводка и аналитика ошибок.

        ОДИН сборщик на экран и на выгрузку в Excel (ТЗ #300, п.11–14). Два
        счёта одного журнала разошлись бы ровно там, где по ним принимают
        решение о человеке, — и разошлись бы молча.
        """
        rows = queries.read_report(cursor, post['id'], with_pass=_pass_ready(cursor),
                                   with_space=_space_ready(cursor),
                                   with_plan=_plan_ready(cursor),
                                   with_attempts=_attempts_ready(cursor))
        has_quiz = bool(_quiz_ready(cursor)
                        and queries.quiz_answer_key(cursor, post['id']))
        # «Нет смен после публикации» говорим только про того, чьи часы ведут
        # (queries.read_report: CTE hours). Учёт есть не у всех отделов и не у
        # каждого человека, и по одному «часов после публикации нет» мы
        # приписали бы прогул тому, чьи смены просто нигде не считают.
        for row in rows:
            row['status'] = news_access.person_status(
                has_quiz=has_quiz, shown_at=row['shown_at'],
                confirmed_at=row['confirmed_at'], quiz_passed_at=row['quiz_passed_at'],
                attempts=row['attempts'], worked_after=row['worked_after'],
                attendance_known=row['attendance_tracked'])
        summary = news_access.report_summary(
            rows, has_quiz=has_quiz, has_trainer=bool(post.get('trainer_key')))
        questions = (queries.question_stats(cursor, post['id'])
                     if has_quiz and _attempts_ready(cursor) else [])
        return rows, summary, questions

    @news_route('/posts/<int:post_id>/attempts/<int:user_id>', publisher=True)
    def news_post_person_attempts(cursor, ctx, post_id, user_id):
        """Попытки одного сотрудника с ответами — разбор, как «Ответы» в опросах.

        Верные ответы здесь есть, и это правильно: сюда приходят только через
        _may_read_post, то есть редактор и те, кто выше автора. Сотруднику
        сервер не называет даже, КАКОЙ вопрос он завалил (решение владельца
        21.09.2026), — но руководитель разбирает ошибки именно по вопросам.
        """
        post = _get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_read_post(ctx, post):
            return jsonify({"error": "Эта новость не из вашего периметра"}), 403
        if not (_quiz_ready(cursor) and _attempts_ready(cursor)):
            return jsonify({"attempts": [], "quiz": []})
        return jsonify({
            "attempts": queries.person_attempts(cursor, post_id, user_id),
            "quiz": queries.post_quiz(cursor, post_id),
        })

    @news_route('/posts/<int:post_id>/report.xlsx', publisher=True, defer_cursor=True)
    def news_post_report_export(ctx, post_id):
        """Журнал ознакомления файлом (ТЗ #300, п.14).

        defer_cursor: сборка книги — работа процессора, а слотов пула сорок на
        весь портал. Курсор закрывается до того, как openpyxl начнёт считать.

        Охват тот же, что у журнала на экране: те же строки, тот же статус, та
        же сводка. Файл, расходящийся с экраном, хуже отсутствующего файла.
        """
        with db._get_cursor() as cursor:
            post = _get_post(cursor, post_id)
            if not post:
                return jsonify({"error": "Новость не найдена"}), 404
            if not _may_read_post(ctx, post):
                return jsonify({"error": "Эта новость не из вашего периметра"}), 403
            rows, _summary, _questions = _report_data(cursor, post)
        stream = report_xlsx.build(post, rows)
        # Имя файла — заголовком новости: в папке «Загрузки» пять одинаковых
        # «Ознакомление.xlsx» не различить. Чистим то, чем давятся файловые
        # системы, и режем: длинный заголовок делает имя нечитаемым.
        safe = re.sub(r'[\\/:*?"<>|]+', ' ', post['title']).strip()[:60] or 'новость'
        return send_file(
            stream,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='Ознакомление — %s.xlsx' % safe)

    @news_route('/posts/<int:post_id>/report', publisher=True)
    def news_post_report(cursor, ctx, post_id):
        """Кто прочитал, кто нет. Ради этого журнала раздел и делали."""
        post = _get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_read_post(ctx, post):
            return jsonify({"error": "Эта новость не из вашего периметра"}), 403
        rows, summary, questions = _report_data(cursor, post)
        return jsonify({
            "items": rows,
            # Знаменатель — только НЫНЕШНИЕ адресаты: «из скольких» отвечает на
            # вопрос «сколько человек это касается сейчас». Подтвердившие,
            # которых уже нет в периметре, в списке остаются (in_audience=false)
            # и посчитаны отдельно (access.report_summary).
            **summary,
            # Прежнее имя счётчика: вкладка со старым бандлом читает его.
            "total": summary['assigned'],
            # ТЗ #300, п.12: где чаще ошибаются.
            "questions": questions,
            # ТЗ #300, п.8.5: режим публикации, плановое и фактическое время
            # начала, период и интервал. Номер волны каждого сотрудника уже в
            # строках — считается там же, где и всё остальное про человека.
            "plan": {
                "kind": post.get('kind'),
                "pass_score_percent": post.get('pass_score_percent'),
                "publish_mode": post.get('publish_mode'),
                "scheduled_at": post.get('scheduled_at'),
                "published_at": post.get('published_at'),
                "spread_minutes": post.get('spread_minutes'),
                "wave_interval_minutes": post.get('wave_interval_minutes'),
                "waves": (news_access.wave_count(post.get('spread_minutes'),
                                                 post.get('wave_interval_minutes'))
                          if post.get('publish_mode') == 'spread' else 0),
            },
        })

    return bp
