"""Эндпоинты обязательного ознакомления.

Аналитика раздела переехала в wiki/routes_analytics.py: сводка, которая
здесь лежала, считала пять показателей, не сужалась по пространству и не
вызывалась с фронта ни разу. Держать отчёт по чтению и поиску внутри
модуля ознакомлений — та же ошибка, только дороже.
"""

from flask import jsonify, request

from . import ack as wiki_ack
from . import articles as wiki_articles
from . import queries
from .routes_structure import _int_or_none


def _body():
    return request.get_json(silent=True) or {}


def register(bp, wiki_route, db, log_ip):

    def _visible(cursor, ctx):
        subjects = ctx['subjects']
        sections = queries.allowed_section_ids(cursor, ctx, subjects)
        return wiki_articles.visible_article_ids(cursor, ctx, subjects, sections)

    # ── Мои ознакомления ─────────────────────────────────────────────────
    @wiki_route('/ack/my')
    def wiki_ack_my(cursor, ctx):
        return jsonify({"items": wiki_ack.my_assignments(
            cursor, ctx['user_id'], _visible(cursor, ctx))})

    @wiki_route('/articles/<int:article_id>/ack')
    def wiki_ack_state(cursor, ctx, article_id):
        if article_id not in _visible(cursor, ctx):
            return jsonify({"error": "Статья не найдена"}), 404
        assignment = wiki_ack.assignment_for(cursor, article_id, ctx['user_id'])
        if assignment:
            wiki_ack.mark_opened(cursor, article_id, ctx['user_id'])
        return jsonify({"assignment": assignment})

    @wiki_route('/articles/<int:article_id>/ack/read', methods=('POST',))
    def wiki_ack_read(cursor, ctx, article_id):
        """Отметка «дочитал». Решение принимает СЕРВЕР.

        Клиент сообщает только число раскрытых блоков; условие «все обязательные
        блоки раскрыты» сверяется здесь с blocks_total. В оригинале «дочитал»
        определялось на клиенте прокруткой окна — а окно в нашем каркасе не
        скроллится, и отметка ставилась бы в момент открытия статьи.
        """
        if article_id not in _visible(cursor, ctx):
            return jsonify({"error": "Статья не найдена"}), 404
        blocks = max(_int_or_none(_body().get('blocks_opened')) or 0, 0)
        state = wiki_ack.mark_read(cursor, article_id, ctx['user_id'], blocks)
        if state is None:
            return jsonify({"error": "Назначения нет"}), 404
        return jsonify(state)

    @wiki_route('/articles/<int:article_id>/ack/confirm', methods=('POST',))
    def wiki_ack_confirm(cursor, ctx, article_id):
        if article_id not in _visible(cursor, ctx):
            return jsonify({"error": "Статья не найдена"}), 404
        if not wiki_ack.acknowledge(cursor, article_id, ctx['user_id']):
            return jsonify({
                "error": "Подтвердить можно только после прочтения статьи целиком",
                "code": "WIKI_ACK_NOT_READ",
            }), 409
        queries.log_action(cursor, actor_id=ctx['user_id'], action='ack.confirm',
                           entity_type='article', entity_id=article_id,
                           ip_address=log_ip())
        return jsonify({"status": "acknowledged"})

    # ── Назначение и отчёт ───────────────────────────────────────────────
    # capability_from_role: назначение обязательного чтения — это про ЛЮДЕЙ
    # (ниже department_id раскрывается в весь состав отдела), а не про
    # содержимое раздела. Право выпускать, выписанное правилом на один раздел,
    # такую дверь открывать не должно — см. queries.load_capabilities.
    @wiki_route('/articles/<int:article_id>/ack/assign', methods=('POST',),
                capability='can_publish', capability_from_role=True)
    def wiki_ack_assign(cursor, ctx, article_id):
        if article_id not in _visible(cursor, ctx):
            return jsonify({"error": "Статья не найдена"}), 404

        data = _body()
        user_ids = [i for i in (_int_or_none(u) for u in (data.get('user_ids') or [])) if i]

        # Назначение на отдел раскрывается в людей здесь, а не на клиенте:
        # состав отдела меняется, и список должен браться на момент назначения.
        department_id = _int_or_none(data.get('department_id'))
        if department_id:
            cursor.execute(
                "SELECT id FROM users WHERE department_id = %s AND status = 'working'",
                (department_id,))
            user_ids += [row[0] for row in cursor.fetchall()]

        if not user_ids:
            return jsonify({"error": "Не выбран ни один сотрудник"}), 400

        created = wiki_ack.assign(cursor, article_id=article_id,
                                  user_ids=sorted(set(user_ids)),
                                  assigned_by=ctx['user_id'],
                                  due_at=data.get('due_at') or None)
        queries.log_action(cursor, actor_id=ctx['user_id'], action='ack.assign',
                           entity_type='article', entity_id=article_id,
                           details={'assigned': created, 'requested': len(set(user_ids))},
                           ip_address=log_ip())
        return jsonify({"assigned": created, "summary": wiki_ack.summary(cursor, article_id)})

    @wiki_route('/articles/<int:article_id>/ack/report', capability='can_publish',
                capability_from_role=True)
    def wiki_ack_report(cursor, ctx, article_id):
        if article_id not in _visible(cursor, ctx):
            return jsonify({"error": "Статья не найдена"}), 404
        return jsonify({
            "summary": wiki_ack.summary(cursor, article_id),
            "items": wiki_ack.report(cursor, article_id),
        })
