# -*- coding: utf-8 -*-
"""Периметр ИИ-помощника вики: что помощнику разрешено читать за пользователя.

Отдельный модуль, а не ещё один флаг у visible_article_ids, ради одного
свойства: у помощника не должно быть способа посчитать периметр ШИРЕ личного.
Поэтому master_key здесь не параметр, а константа False — передать True снаружи
физически нельзя, и никакая правка вызывающего кода этого не изменит.

Второй смысл модуля — убрать раздвоение. Цепочку «субъекты → разделы → статьи»
до сих пор собирал локальный _perimeter внутри register() в routes_articles, то
есть достать её было нельзя, и помощнику пришлось бы написать вторую копию.
Именно на таком раздвоении сломалась исходная вика (см. шапку
tests/test_wiki_article_visibility.py). Теперь реализация одна на оба места.

Сверх библиотечного периметра ЧТЕНИЯ помощник обязан отбросить ещё три класса
статей. Право прочитать статью на экране и право отправить её текст во внешний
API — разные права, и второе строго уже:

  * не 'published' — черновик и архив не источник для ответа. У
    visible_article_ids параметра статуса нет вовсе, а can_see_drafts там
    выводится из can_publish и от master_key не зависит (wiki/articles.py:110),
    так что супервайзеру черновики видны — и в ответ помощника попадать они не
    должны;
  * strict_mode — чтение таких статей пишется в журнал поимённо. Ответ помощника
    журналом чтения не является, поэтому строгие статьи в индекс не берём вообще;
  * ai_opt_out — рубильник владельца «этот текст во внешний API не отправлять»,
    независимый от прав чтения (решение владельца 10.08.2026).

Про ai_opt_out на разделе выбрана СТРОГАЯ семантика: статья выпадает, если
помечен ХОТЯ БЫ ОДИН её раздел, а не все. Так рубильник ведёт себя предсказуемо
(«помечен — значит наружу не уходит»), и заодно не воспроизводится ловушка
all() по пустому множеству: у статьи без разделов all(...) истинно, и она молча
исчезла бы. На проде такая статья ровно одна и она содержательная —
«Классификатор авто» (id 36, 0 строк в wiki_article_sections), под неё сделаны
все существующие правила доступа.
"""

import hashlib

from . import articles as wiki_articles
from . import queries

# Статьи из личного периметра, которые вообще допустимы как источник для ИИ.
# Отдельным запросом, а не условиями внутри _VISIBLE_ARTICLES_SQL: тот запрос —
# единственный источник правды о ЧТЕНИИ, и дописывать в него условия помощника
# значило бы менять смысл чтения ради приставной функции.
_AI_ELIGIBLE_SQL = """
SELECT a.id
  FROM wiki_articles a
 WHERE a.id = ANY(%(candidates)s)
   AND a.status = 'published'
   AND NOT a.strict_mode
   AND NOT a.ai_opt_out
   AND NOT EXISTS (SELECT 1
                     FROM wiki_article_sections s
                     JOIN wiki_sections sec ON sec.id = s.section_id
                    WHERE s.article_id = a.id
                      AND sec.ai_opt_out)
"""


def read_perimeter(cursor, ctx, *, master_key=True, space_id=None):
    """Субъекты, разрешённые разделы и видимые статьи — за один проход.

    Ровно то, что раньше было локальным _perimeter в routes_articles: вынесено,
    чтобы витрина чтения и помощник считали периметр одной реализацией.

    master_key=False — личный периметр без мастер-ключа администратора вики
    (см. шапку wiki/articles.py).

    space_id СУЖАЕТ периметр до одного пространства — того, что выбрано
    переключателем в шапке. Сужать обязательно ЗДЕСЬ, до расчёта статей, а не
    отфильтровывать разделы во фронте: статья чужого пространства иначе
    доезжает до витрины и попадает в «Без раздела» и «Популярные» — её раздел
    отфильтрован, а сама она нет. Ровно это и случилось на первом прогоне.

    Границу пространства сужение НЕ заменяет: allowed_section_ids уже отсёк
    чужие пространства, и space_id выбирает из СВОИХ. Попросить чужое через
    параметр нельзя — его разделов в периметре просто нет.
    """
    # Субъекты уже посчитаны декоратором wiki_route и лежат в контексте:
    # выводить их второй раз из тех же полей значило бы завести второй
    # источник истины — ровно на таком раздвоении сломалась исходная вика
    # (см. шапку модуля).
    subjects = ctx['subjects']
    sections = queries.allowed_section_ids(cursor, ctx, subjects,
                                           master_key=master_key)
    visible = wiki_articles.visible_article_ids(cursor, ctx, subjects, sections,
                                               master_key=master_key)
    if space_id:
        # Сужать нужно ОБА множества, и статьи — по своим разделам, а не по
        # уже суженному списку: статью видно ещё и по авторству, гостевой
        # ссылке и личному правилу, мимо разделов. Сузишь только разделы —
        # и собственная статья автора из соседней вики останется на витрине.
        sections = queries.sections_of_space(cursor, sections, space_id)
        visible = queries.articles_of_space(cursor, visible, space_id)
    return subjects, sections, visible


def eligible_article_ids(cursor, candidates):
    """Из уже посчитанного периметра оставить пригодные для ИИ.

    Отдельная функция, чтобы её можно было проверить на синтетических данных
    без всей цепочки прав (tests/test_wiki_ai_perimeter.py).
    """
    candidates = sorted(int(x) for x in candidates)
    if not candidates:
        return frozenset()
    cursor.execute(_AI_ELIGIBLE_SQL, {'candidates': candidates})
    return frozenset(row[0] for row in cursor.fetchall())


def perimeter_hash(article_ids):
    """Устойчивый отпечаток периметра — для журнала и ответа «почему видит».

    Не кеш-ключ: кеша периметра нет намеренно, весь расчёт — около 1 мс
    серверного времени, а TTL отложил бы отзыв гостевого доступа до перезапуска.
    """
    payload = ','.join(str(x) for x in sorted(article_ids))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def assistant_perimeter(cursor, ctx):
    """Периметр ИИ-помощника. Мастер-ключ недоступен по построению.

    Возвращает словарь: article_ids (frozenset), section_ids (frozenset),
    hash (str) и read_count — сколько статей человек вправе прочитать глазами.
    Разница read_count и len(article_ids) — это и есть цена рубильника и
    строгого режима, её показываем в /ai/status, чтобы «помощник знает меньше,
    чем я вижу» не выглядело поломкой.
    """
    _subjects, sections, visible = read_perimeter(cursor, ctx, master_key=False)
    eligible = eligible_article_ids(cursor, visible)
    return {
        'article_ids': eligible,
        'section_ids': frozenset(sections),
        'read_count': len(visible),
        'hash': perimeter_hash(eligible),
    }
