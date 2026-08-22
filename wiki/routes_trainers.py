"""Эндпоинты тренажёров: где вставлены, кто проходил и выгрузка в Excel.

Гейтов здесь ДВА, и это главное, что стоит понимать про модуль.

Запись попытки открыта любому, кто вошёл в раздел: проходит тренажёр читатель,
и требовать от него прав редактора значило бы не учитывать ровно тех, ради кого
тренажёр и сделан.

Статистика — только редакторам вики, тем же, кому открыта сама вкладка
«Тренажёры». «Кто проходил» — персональные данные: имя, отдел, группа и то,
сколько раз человек ошибся. Гейт стоит на сервере, а не только во фронте, потому
что гард в интерфейсе прячет вкладку, но не защищает прямой запрос.
"""

from io import BytesIO

from flask import jsonify, request, send_file

from . import access as wiki_access
from . import articles as wiki_articles
from . import queries
from . import trainer_report
from . import trainers as wiki_trainers
from .routes_structure import _int_or_none

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Потолок ленты попыток за один запрос. Ограничение не про производительность
# базы (индекс по (trainer_key, started_at) отдаёт страницу мгновенно), а про
# браузер: таблица на десять тысяч строк рисуется в интерфейсе секундами.
MAX_PAGE = 200


def _body():
    return request.get_json(silent=True) or {}


def _period():
    """Диапазон дат из строки запроса. Пустое значение = «за всё время».

    Формат не разбираем: и Postgres, и выгрузка принимают ISO-дату как есть, а
    самодельный парсер добавил бы второе место, где формат может разойтись.
    """
    def one(name):
        raw = str(request.args.get(name) or '').strip()
        return raw[:10] or None
    return one('since'), one('until')


def register(bp, wiki_route, db, log_ip):

    def _visible(cursor, ctx):
        subjects = ctx['subjects']
        sections = queries.allowed_section_ids(cursor, ctx, subjects)
        return wiki_articles.visible_article_ids(cursor, ctx, subjects, sections)

    def _departments(ctx):
        """Границы, внутри которых человеку можно показывать людей поимённо.

        None — без границы. Правило то же, что у выдачи доступа
        (_grant_departments в routes_structure.py), и повторено оно намеренно:
        там это замыкание внутри register(), импортировать его нельзя, а
        разъехаться правила не должны — обе границы про одно и то же.

        Без неё супервайзер СЗоВ с правом публикации видел бы поимённый состав
        отдела продаж: способность редактора границ отдела не знает.
        """
        if wiki_access.normalize_role(ctx['otp_role']) == 'super_admin':
            return None
        if bool(ctx['wiki_roles']) and ctx['capabilities'].get('can_manage_access'):
            return None
        own = set(ctx.get('headed_department_ids') or [])
        if ctx.get('department_id'):
            own.add(ctx['department_id'])
        return sorted(own)

    def _is_editor(ctx):
        caps = ctx['capabilities']
        return bool(caps.get('can_create') or caps.get('can_edit') or caps.get('can_publish'))

    def _forbidden():
        return jsonify({
            "error": "Статистика тренажёров доступна редакторам вики",
            "code": "WIKI_EDITOR_ONLY",
        }), 403

    # ── Запись попытки ───────────────────────────────────────────────────

    @wiki_route('/trainers/runs', methods=('POST',))
    def wiki_trainer_run_start(cursor, ctx):
        """Человек сел за тренажёр. Возвращает id попытки.

        Статью проверяем по периметру: id приходит из браузера, и без проверки
        в статистику можно было бы приписать чужую статью. Не прошло проверку —
        пишем попытку БЕЗ статьи, а не отказываем: терять учебный сеанс из-за
        того, что не сошёлся идентификатор, незачем.
        """
        payload = _body()
        key = str(payload.get('key') or '').strip()
        if not key:
            return jsonify({"error": "Не указан тренажёр"}), 400

        article_id = _int_or_none(payload.get('article_id'))
        if article_id is not None and article_id not in _visible(cursor, ctx):
            article_id = None

        run_id = wiki_trainers.start_run(
            cursor,
            trainer_key=key,
            user_id=ctx['user_id'],
            article_id=article_id,
            source=str(payload.get('source') or 'article'),
            stages_total=payload.get('stages_total'),
        )
        return jsonify({"run_id": run_id})

    @wiki_route('/trainers/runs/<int:run_id>', methods=('POST',))
    def wiki_trainer_run_finish(cursor, ctx, run_id):
        """Урок закрыт: прошёл до конца или бросил на полпути.

        POST, а не PATCH: закрытую вкладку досылает `fetch(keepalive)`, и с ним
        браузеры надёжно переживают выгрузку страницы именно на POST. А это и
        есть последний шанс записать брошенную попытку.
        """
        payload = _body()
        ok = wiki_trainers.finish_run(
            cursor,
            run_id=run_id,
            user_id=ctx['user_id'],
            status=str(payload.get('status') or 'abandoned'),
            stages_done=payload.get('stages_done'),
            errors=payload.get('errors'),
            hints=payload.get('hints'),
            restarts=payload.get('restarts'),
            duration_ms=payload.get('duration_ms'),
        )
        # 200 и в случае «строка не нашлась»: попытку мог закрыть предыдущий
        # маячок, и ошибка здесь превратилась бы в красный тост поверх статьи
        # ровно в тот момент, когда человек её закрывает.
        return jsonify({"saved": bool(ok)})

    # ── Статистика ───────────────────────────────────────────────────────

    @wiki_route('/trainers/stats')
    def wiki_trainer_stats(cursor, ctx):
        """Сводка по всем тренажёрам — для карточек вкладки."""
        if not _is_editor(ctx):
            return _forbidden()
        return jsonify({"stats": wiki_trainers.summary(cursor)})

    @wiki_route('/trainers/<key>/stats')
    def wiki_trainer_stats_one(cursor, ctx, key):
        """Подробности по одному тренажёру: итоги, по статьям, по людям, лента."""
        if not _is_editor(ctx):
            return _forbidden()

        since, until = _period()
        depts = _departments(ctx)
        limit = min(max(_int_or_none(request.args.get('limit')) or 50, 1), MAX_PAGE)
        offset = max(_int_or_none(request.args.get('offset')) or 0, 0)

        return jsonify({
            "key": key,
            "since": since,
            "until": until,
            # Границу отдела возвращаем явно: без неё «прошло 3 человека» на
            # экране супервайзера и «прошло 40» у директора выглядят как
            # расхождение в данных, а не как разный охват.
            "scoped": depts is not None,
            "totals": wiki_trainers.totals(cursor, key, since=since, until=until,
                                           departments=depts),
            "articles": wiki_trainers.by_article(cursor, key, _visible(cursor, ctx),
                                                 since=since, until=until,
                                                 departments=depts),
            "people": wiki_trainers.by_person(cursor, key, since=since, until=until,
                                              departments=depts),
            "runs": wiki_trainers.runs(cursor, key, since=since, until=until,
                                       departments=depts, limit=limit, offset=offset),
        })

    @wiki_route('/trainers/<key>/export')
    def wiki_trainer_export(cursor, ctx, key):
        """Выгрузка статистики одного тренажёра.

        Название тренажёра приходит параметром: сценарии живут во фронте, и
        сервер знает про них только ключ. Подставлять ключ вместо названия в
        шапку файла нельзя — файл уходит наружу, и «sapar-site-avr» в нём
        читается как недоделка.

        Лента попыток берётся ЦЕЛИКОМ, без постраничного потолка: файл для того
        и нужен, чтобы в нём было всё. Верхняя граница всё же есть — иначе один
        запрос мог бы собрать книгу на сотню мегабайт.
        """
        if not _is_editor(ctx):
            return _forbidden()

        since, until = _period()
        depts = _departments(ctx)
        totals = wiki_trainers.totals(cursor, key, since=since, until=until,
                                      departments=depts)
        rows = wiki_trainers.runs(cursor, key, since=since, until=until,
                                  departments=depts, limit=50000, offset=0)

        cursor.execute('SELECT name FROM users WHERE id = %s', (ctx['user_id'],))
        row = cursor.fetchone()

        stream = trainer_report.build_workbook(
            trainer={
                'key': key,
                'title': str(request.args.get('title') or '').strip()[:120] or key,
                'app': str(request.args.get('app') or '').strip()[:60],
            },
            totals=totals,
            runs=rows['items'],
            people=wiki_trainers.by_person(cursor, key, since=since, until=until,
                                           departments=depts),
            articles=wiki_trainers.by_article(cursor, key, _visible(cursor, ctx),
                                              since=since, until=until,
                                              departments=depts),
            since=since,
            until=until,
            requested_by=(row[0] if row else '') or '',
        )
        return send_file(
            BytesIO(stream.getvalue()),
            mimetype=XLSX_MIME,
            as_attachment=True,
            download_name=trainer_report.report_filename(key),
        )
