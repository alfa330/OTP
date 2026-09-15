# -*- coding: utf-8 -*-
"""«Вопросы операторов»: вопрос, на который помощник не ответил, уходит супервайзеру.

Задача #321 (постановщик — Кастек Гаухар, механика — дополнение от 14.09.2026):

    вопрос оператора → помощник решает, может ли ответить сам → если нет,
    передаёт вопрос супервайзеру отдела → супервайзер даёт решение → вопрос и
    ответ фиксируются → подбирается статья, куда дописать сведения, или
    предлагается новая → после подтверждения готовятся правки → публикация →
    новость об изменении → тест по новой информации.

Здесь первая половина цепочки: кто передаёт, кому приходит и как ответ
возвращается в разговор. Вторая (статья, новость, тест) — routes_questions.py
и ai/knowledge.py.

РЕШЕНИЯ ПО ЗАДАЧЕ (15.09.2026), которых в постановке нет:
  * вопрос уходит АВТОМАТИЧЕСКИ на каждый отказ помощника, без кнопки, — и на
    ответ, признавший, что спрошенного в статьях нет (should_escalate);
  * ответ супервайзера приходит оператору СРАЗУ, статья оформляется следом —
    оператор на линии не ждёт публикации;
  * разбирают вопросы во вкладке «Вопросы» раздела «Вики», о новом будит колокол;
  * оператор передаёт вопрос и сам — кнопкой «Отправить супервайзеру» под ответом,
    который его не устроил (escalate_by_asker);
  * ответ можно выпустить отделу и одной новостью, без статьи: «Опубликовать как
    новость» открывает форму «Новостей» с заполненными полями (kb_status news).

КТО ПЕРЕДАЁТ. Только оператор и стажёр — так сказано в постановке. Помощника
спрашивают и супервайзер, и тренер, и бухгалтерия, но их отказ ушёл бы либо
самому спрашивающему, либо в отдел, где супервайзеров нет вовсе.

КТО РАЗБИРАЕТ. Тот же круг, что вправе адресовать отделу новость
(news/access.py: publish_ceiling и publish_departments). Это требование, а не
совпадение: разбор заканчивается новостью с тестом этому отделу, и вторая
лестница рядом с первой однажды разошлась бы — человек разобрал бы вопрос и
упёрся в отказ на публикации.

КОГО БУДИТ КОЛОКОЛ. Супервайзеров отдела — буквально по постановке. Главу отдела
только тогда, когда супервайзеров в отделе нет: иначе вопрос не увидел бы никто,
а цель задачи — «ни один вопрос не остаётся без решения». Правило живёт дважды —
в триггере (database.py: bell_notify_change) и в источнике колокола
(notifications/sources.py: wiki_questions), — и тест сверяет, что копии не
разошлись.

Отдел — снимок на момент вопроса: переведут оператора завтра, вопрос останется
у тех, кто его получил.
"""

from news import access as news_access

from . import access as wiki_access
from .ai import answer as ai_answer
from .ai import store as ai_store

# Кто передаёт вопрос супервайзеру при отказе помощника.
ESCALATING_ROLES = frozenset({'operator', 'trainee'})

# Вид реплики с ответом супервайзера в разговоре помощника. Реплика лежит в той
# же ленте, что и ответы модели: оператор спрашивал в этом чате, и ответ обязан
# прийти туда же, а не в отдельный раздел, о котором он не знает.
SUPERVISOR_KIND = 'supervisor'

# Потолок ответа. Ответ — справка для оператора, а не регламент: всё, что
# длиннее, пишется статьёй. Целиком он уходит и в модель на правку статьи.
MAX_ANSWER_LENGTH = 4000

# Текст отказа при передаче НЕ подменяется. О передаче говорит строка под
# пузырём (assistantThread.jsx), а сама реплика остаётся той, что выдал ответ:
# аналитика вики различает виды отказа по точному тексту (wiki/analytics.py:
# «unverified»), и подменённый текст молча перевёл бы их в другую графу.

# news — ответ ушёл отделу новостью без статьи («Опубликовать как новость»).
KB_STATUSES = ('published', 'skipped', 'news')

# Корзины вкладки. «Ждут статьи» — отвеченные, но ещё не оформленные в базу
# знаний: без своей корзины они растворились бы среди разобранных, и вторая
# половина цепочки держалась бы на памяти супервайзера.
BUCKETS = {
    'open': "q.status = 'open'",
    'answered': "q.status = 'answered' AND q.kb_status IS NULL",
    'done': "(q.status = 'dismissed' OR q.kb_status IS NOT NULL)",
}

# Очереди — старые сверху: вопрос, который ждёт дольше всех, и есть самый
# срочный. Разобранные — наоборот, свежие сверху, как в любом журнале.
_ORDER = {
    'open': 'q.created_at, q.id',
    'answered': 'q.resolved_at, q.id',
    'done': 'COALESCE(q.kb_at, q.resolved_at) DESC, q.id DESC',
}

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"


def should_escalate(otp_role, kind, text=''):
    """Передаётся ли ответ помощника супервайзеру.

    Два случая: полный отказ (kind no_answer) и ответ, который начинается с
    признания «нет информации о …», а дальше даёт смежное (ai_answer.admits_missing).
    Второй — решение 15.09.2026: оператор спросил комиссию Яндекса и получил таблицу
    комиссий парков; ответа на заданный вопрос нет, а к супервайзеру вопрос не ушёл.
    Смежные данные оператору остаются, под ответом — строка о передаче.
    """
    if wiki_access.normalize_role(otp_role) not in ESCALATING_ROLES:
        return False
    if kind == 'no_answer':
        return True
    return kind == 'answer' and ai_answer.admits_missing(text)


def reviewer_scope(ctx):
    """(вправе ли разбирать вопросы, отделы). Отделы None — без границы.

    Признак администратора вики тот же, что у /ping (grant_ceiling): по нему
    фронт решает, показывать ли вкладку, и вторая формула дала бы вкладку, на
    которую сервер отвечает 403.
    """
    is_wiki_admin = (bool(ctx.get('wiki_roles'))
                     and bool((ctx.get('capabilities') or {}).get('can_manage_access')))
    ceiling = news_access.publish_ceiling(ctx.get('otp_role'), is_wiki_admin=is_wiki_admin)
    if ceiling is None:
        return False, []
    return True, news_access.publish_departments(
        ctx.get('otp_role'),
        headed_department_ids=ctx.get('headed_department_ids') or (),
        department_id=ctx.get('department_id'),
        is_wiki_admin=is_wiki_admin)


_TABLE_READY = []


def table_ready(cursor):
    """Развёрнута ли таблица вопросов. «Да» запоминается на весь процесс.

    Код приезжает раньше, чем DDL успевает отработать на старте (так уже было с
    фотографиями новостей), а спрашивает о таблице самый горячий путь раздела —
    ответ помощника. Отсутствие таблицы обязано значить «вопросы пока не
    передаются», а не пятисотку вместо ответа.
    """
    if _TABLE_READY:
        return True
    cursor.execute("SELECT to_regclass('public.wiki_operator_questions') IS NOT NULL")
    row = cursor.fetchone()
    if row and row[0]:
        _TABLE_READY.append(True)
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# СТОРОНА ОПЕРАТОРА
# ─────────────────────────────────────────────────────────────────────────────

def escalate(cursor, *, asker_id, department_id, space_id, chat_id,
             question_message_id, refusal_message_id, question, requested_by_asker=False):
    """Передать вопрос отделу. Возвращает {'id', 'status'}."""
    cursor.execute(
        """
        INSERT INTO wiki_operator_questions
               (asker_id, department_id, space_id, chat_id, question_message_id,
                refusal_message_id, question, requested_by_asker)
        VALUES (%(asker)s, %(department)s, %(space)s, %(chat)s,
                %(question_message)s, %(refusal_message)s, %(question)s, %(requested)s)
        RETURNING id
        """,
        {'asker': asker_id, 'department': department_id, 'space': space_id,
         'chat': chat_id, 'question_message': question_message_id,
         'refusal_message': refusal_message_id, 'question': question,
         'requested': bool(requested_by_asker)},
    )
    return {'id': int(cursor.fetchone()[0]), 'status': 'open'}


# Что оператор вправе передать сам: ответ, уточняющий вопрос и отказ, если тот
# почему-то не ушёл автоматически. Ответ супервайзера — нет: с ним возвращаются к
# самому супервайзеру, а не заводят второй вопрос. Копия — во фронте
# (assistantThread.jsx: ESCALATABLE_KINDS), тест сверяет.
ASKER_ESCALATION_KINDS = ('answer', 'clarify', 'no_answer')


def may_escalate_by_hand(otp_role):
    """Положена ли кнопка «Отправить супервайзеру» — та же лестница, что у передачи."""
    return wiki_access.normalize_role(otp_role) in ESCALATING_ROLES


def escalate_by_asker(cursor, *, asker_id, department_id, space_id, message_id):
    """«Отправить супервайзеру»: оператора не устроил ответ помощника.

    Возвращает (передача, отказ). Отказ — 'not_found' (реплики нет, разговор
    чужой или удалён) или 'not_answer' (это не ответ помощника). Повторное нажатие
    по той же реплике отдаёт уже созданную передачу: второй вопрос с тем же текстом
    в очереди отдела был бы дублем.

    Вопрос — последняя реплика оператора ПЕРЕД этим ответом, а не последняя в
    разговоре: кнопку жмут и под старым ответом, когда ниже спросили уже другое.
    Пространство — разговора, где дан ответ; у старых разговоров его нет, и тогда
    берётся то, что открыто сейчас.
    """
    cursor.execute(
        """
        SELECT m.chat_id, m.seq, m.role, m.kind, c.space_id
          FROM wiki_ai_messages m
          JOIN wiki_ai_chats c ON c.id = m.chat_id
         WHERE m.id = %(message)s AND c.user_id = %(asker)s AND c.deleted_at IS NULL
        """,
        {'message': message_id, 'asker': asker_id},
    )
    row = cursor.fetchone()
    if not row:
        return None, 'not_found'
    chat_id, seq, role, kind, chat_space_id = row
    if role != 'assistant' or kind not in ASKER_ESCALATION_KINDS:
        return None, 'not_answer'
    cursor.execute(
        'SELECT id, status FROM wiki_operator_questions WHERE refusal_message_id = %s '
        'ORDER BY id LIMIT 1',
        (message_id,),
    )
    existing = cursor.fetchone()
    if existing:
        return {'id': int(existing[0]), 'status': existing[1]}, None
    cursor.execute(
        """
        SELECT id, text FROM wiki_ai_messages
         WHERE chat_id = %s AND role = 'user' AND seq < %s
         ORDER BY seq DESC LIMIT 1
        """,
        (chat_id, seq),
    )
    asked = cursor.fetchone()
    if not asked:
        return None, 'not_answer'
    return escalate(cursor, asker_id=asker_id, department_id=department_id,
                    space_id=chat_space_id or space_id, chat_id=chat_id,
                    question_message_id=asked[0], refusal_message_id=message_id,
                    question=asked[1], requested_by_asker=True), None


_MARK_KEYS = ('id', 'status', 'refusal_message_id', 'answer_message_id',
              'resolved_by_name')


def chat_marks(cursor, chat_id):
    """Переданные вопросы одного разговора — для пометок в ленте."""
    cursor.execute(
        """
        SELECT q.id, q.status, q.refusal_message_id, q.answer_message_id, u.name
          FROM wiki_operator_questions q
          LEFT JOIN users u ON u.id = q.resolved_by
         WHERE q.chat_id = %s
        """,
        (chat_id,),
    )
    return [dict(zip(_MARK_KEYS, row)) for row in cursor.fetchall()]


def decorate_messages(messages, marks):
    """Пометить реплики разговора: какой отказ передан и чей это ответ.

    Отказу — состояние передачи (ждёт, отвечен, закрыт без ответа), ответу —
    имя супервайзера. Считается при чтении, а не хранится в реплике: статус
    вопроса меняется после того, как реплика записана.
    """
    by_refusal = {mark['refusal_message_id']: mark for mark in marks
                  if mark.get('refusal_message_id')}
    by_answer = {mark['answer_message_id']: mark for mark in marks
                 if mark.get('answer_message_id')}
    for message in messages:
        mark = by_refusal.get(message.get('id'))
        if mark:
            message['escalation'] = {'id': mark['id'], 'status': mark['status']}
        mark = by_answer.get(message.get('id'))
        if mark:
            message['supervisor_name'] = mark.get('resolved_by_name') or ''
    return messages


def mark_answers_seen(cursor, *, asker_id, chat_id):
    """Оператор открыл разговор — уведомление об ответе гаснет.

    Гасит открытие ЧАТА, а не взгляд на колокол: «супервайзер ответил» снимается
    тем, что ответ прочитан, иначе строка пропадала бы у того, кто лишь
    пролистал уведомления.
    """
    cursor.execute(
        """
        UPDATE wiki_operator_questions
           SET asker_seen_at = {now}
         WHERE chat_id = %s AND asker_id = %s
           AND status = 'answered' AND asker_seen_at IS NULL
        """.format(now=_NOW),
        (chat_id, asker_id),
    )
    return cursor.rowcount or 0


# ─────────────────────────────────────────────────────────────────────────────
# СТОРОНА СУПЕРВАЙЗЕРА
# ─────────────────────────────────────────────────────────────────────────────

_ROW_SQL = """
SELECT q.id, q.question, q.status, q.created_at, q.space_id, q.chat_id,
       q.asker_id, asker.name, q.department_id, d.name,
       q.answer, q.resolved_at, q.resolved_by, resolver.name,
       q.kb_status, q.kb_at, q.kb_article_id, a.title, a.slug, q.kb_news_id,
       q.answer_message_id, sp.name, q.requested_by_asker, reply.text
  FROM wiki_operator_questions q
  LEFT JOIN users asker ON asker.id = q.asker_id
  LEFT JOIN users resolver ON resolver.id = q.resolved_by
  LEFT JOIN departments d ON d.id = q.department_id
  LEFT JOIN wiki_articles a ON a.id = q.kb_article_id
  LEFT JOIN wiki_spaces sp ON sp.id = q.space_id
  LEFT JOIN wiki_ai_messages reply ON reply.id = q.refusal_message_id
"""

_ROW_KEYS = ('id', 'question', 'status', 'created_at', 'space_id', 'chat_id',
             'asker_id', 'asker_name', 'department_id', 'department_name',
             'answer', 'resolved_at', 'resolved_by', 'resolved_by_name',
             'kb_status', 'kb_at', 'kb_article_id', 'kb_article_title',
             'kb_article_slug', 'kb_news_id', 'answer_message_id', 'space_name',
             'requested_by_asker', 'assistant_text')


def _row(row):
    item = dict(zip(_ROW_KEYS, row))
    for key in ('created_at', 'resolved_at', 'kb_at'):
        item[key] = item[key].isoformat() if item[key] else None
    return item


def _scope(departments):
    """Граница отдела разбирающего. Вопрос без отдела видит только тот, у кого
    границы нет: отнести его к чьему-то отделу было бы догадкой."""
    if departments is None:
        return 'TRUE', {}
    return ('q.department_id = ANY(%(departments)s)',
            {'departments': sorted({int(value) for value in departments}) or [-1]})


def _space_scope(space_id, reachable_spaces):
    """Граница вики: вопрос виден в том пространстве, где его задали.

    Граница отдела отвечает на «чей вопрос», а не на «в какой вике он живёт».
    Без этой вопрос оператора Тез КЦ, заданный в «Тез», показывался в
    «Таксопарках» каждому, кто разбирает оба отдела (жалоба исполнителя
    15.09.2026), — хотя статью по нему подбирают в вике вопроса
    (routes_questions: wiki_questions_targets), а не в той, что на экране.

    Два исключения, оба затем, чтобы вопрос не потерялся:
      * пространство не записано (NULL) — отнести вопрос к вике было бы догадкой;
      * пространство разбирающему не открыто: оператору выдали чужую вику
        гостем, а его супервайзер туда не ходит. Такой вопрос виден в любой
        вике разбирающего — иначе его не увидел бы никто.

    space_id None — открытых вик нет вовсе, сужать нечем.
    """
    if not space_id:
        return 'TRUE', {}
    reachable = sorted({int(value) for value in reachable_spaces or ()}) or [-1]
    return ('(q.space_id = %(space)s OR q.space_id IS NULL'
            ' OR NOT (q.space_id = ANY(%(reachable_spaces)s)))',
            {'space': int(space_id), 'reachable_spaces': reachable})


def list_questions(cursor, *, departments, bucket, limit, offset,
                   space_id=None, reachable_spaces=()):
    """(строки корзины, счётчики всех корзин) — двумя запросами на любую корзину.

    Счётчики — под той же границей вики, что и строки: число на корзине, за
    которым список пуст, звало бы искать то, чего в этой вике нет.
    """
    where, params = _scope(departments)
    space_where, space_params = _space_scope(space_id, reachable_spaces)
    where = '%s AND %s' % (where, space_where)
    params.update(space_params)
    params.update({'limit': limit, 'offset': offset})
    cursor.execute(
        _ROW_SQL
        + ' WHERE ' + where + ' AND ' + BUCKETS[bucket]
        + ' ORDER BY ' + _ORDER[bucket]
        + ' LIMIT %(limit)s OFFSET %(offset)s',
        params,
    )
    items = [_row(row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT count(*) FILTER (WHERE {open}),
               count(*) FILTER (WHERE {answered}),
               count(*) FILTER (WHERE {done})
          FROM wiki_operator_questions q
         WHERE {where}
        """.format(where=where, **BUCKETS),
        params,
    )
    open_count, answered_count, done_count = cursor.fetchone()
    return items, {'open': int(open_count or 0), 'answered': int(answered_count or 0),
                   'done': int(done_count or 0)}


def get_question(cursor, question_id, *, departments):
    # Границы вики здесь нет намеренно: карточку открывают из колокола, находясь
    # в другой вике, и вкладка сама переключает шапку на вику вопроса.
    where, params = _scope(departments)
    params['id'] = question_id
    cursor.execute(_ROW_SQL + ' WHERE q.id = %(id)s AND ' + where, params)
    row = cursor.fetchone()
    return _row(row) if row else None


def answer_question(cursor, *, question_id, reviewer_id, answer):
    """Записать ответ и вернуть его в разговор оператора. None — уже разобран.

    Условие status = 'open' стоит в самом UPDATE: два супервайзера отдела видят
    одну очередь, и отвечает один — второй получит отказ, а не второй ответ в
    чате оператора.
    """
    cursor.execute(
        """
        UPDATE wiki_operator_questions
           SET status = 'answered', answer = %(answer)s,
               resolved_by = %(by)s, resolved_at = {now}, updated_at = {now}
         WHERE id = %(id)s AND status = 'open'
        RETURNING chat_id, asker_id
        """.format(now=_NOW),
        {'answer': answer, 'by': reviewer_id, 'id': question_id},
    )
    row = cursor.fetchone()
    if not row:
        return None
    chat_id, asker_id = row
    if chat_id is None:
        return {'message_id': None}

    # Удалённый оператором разговор возвращается в список. Удаление — это
    # «убрать с глаз», а не «не хочу ответа»: вопрос в нём ушёл супервайзеру, и
    # ответ, упавший в скрытый чат, до оператора не дошёл бы никогда.
    cursor.execute(
        """
        UPDATE wiki_ai_chats SET deleted_at = NULL, deleted_by = NULL
         WHERE id = %s AND user_id = %s AND deleted_at IS NOT NULL
        """,
        (chat_id, asker_id),
    )
    stored = ai_store.append_message(cursor, chat_id, role='assistant',
                                     kind=SUPERVISOR_KIND, text=answer)
    ai_store.touch_chat(cursor, asker_id, chat_id)
    cursor.execute(
        'UPDATE wiki_operator_questions SET answer_message_id = %s WHERE id = %s',
        (stored['id'], question_id),
    )
    return {'message_id': stored['id']}


def dismiss_question(cursor, *, question_id, reviewer_id):
    """Закрыть без ответа — «привет», «кто ты» и прочее, что вопросом не было."""
    cursor.execute(
        """
        UPDATE wiki_operator_questions
           SET status = 'dismissed', resolved_by = %(by)s,
               resolved_at = {now}, updated_at = {now}
         WHERE id = %(id)s AND status = 'open'
        RETURNING id
        """.format(now=_NOW),
        {'by': reviewer_id, 'id': question_id},
    )
    return cursor.fetchone() is not None


def finish_knowledge(cursor, *, question_id, status, by, article_id=None, news_id=None):
    """Закрыть вторую половину цепочки: опубликовано в базу или не для базы."""
    if status not in KB_STATUSES:
        raise ValueError('неизвестный исход: %r' % (status,))
    cursor.execute(
        """
        UPDATE wiki_operator_questions
           SET kb_status = %(status)s, kb_article_id = %(article)s,
               kb_news_id = %(news)s, kb_by = %(by)s,
               kb_at = {now}, updated_at = {now}
         WHERE id = %(id)s AND status = 'answered' AND kb_status IS NULL
        RETURNING id
        """.format(now=_NOW),
        {'status': status, 'article': article_id, 'news': news_id, 'by': by,
         'id': question_id},
    )
    return cursor.fetchone() is not None


def attach_article_source(cursor, *, message_id, article_id, title, slug):
    """Под ответом супервайзера в чате оператора появляется статья-источник.

    Отдельной реплики «ответ добавлен в базу» нет намеренно: это вторая строка
    про то же самое. Источник под уже полученным ответом говорит ровно то, что
    нужно, — где это теперь записано, — и открывает статью тем же чипом, что у
    ответов модели.
    """
    if not message_id or not article_id:
        return
    cursor.execute(
        """
        INSERT INTO wiki_ai_message_sources
               (message_id, ord, article_id, title, slug, quote_ok)
        VALUES (%(message)s,
                (SELECT COALESCE(max(ord) + 1, 0) FROM wiki_ai_message_sources
                  WHERE message_id = %(message)s),
                %(article)s, %(title)s, %(slug)s, TRUE)
        """,
        {'message': message_id, 'article': article_id,
         'title': (title or '')[:255], 'slug': (slug or '')[:255]},
    )
