"""Справка из вики для оценщика: статьи, на которые ссылаются критерии шкалы и
указания направления.

Зачем. Часть критериев проверяет то, что оператор сказал по существу, а факты
живут в вики: какие акции действуют в каком парке, условия, сроки. Без них ИИ
не может решить, должен ли был оператор озвучить акцию: на переписке 61480
(23.10.2026) он честно писал «наличие акции у парка QAZAQ установить
невозможно», хотя статья «Все акции» прямо говорит, что у QAZAQ акции для
новичков есть.

Как. Человек кладёт ссылку на статью (как её копируют из вики:
?view=wiki&article=<слаг>) в описание критерия в шкале или в «Указания для ИИ»
направления. При загрузке шкалы (criteria.load_direction) ссылки находятся, а
текст статей уходит в системный промпт отдельным блоком (evaluator.build_system).
Статья берётся ТЕКУЩАЯ: правка в вики доходит до следующей оценки сама, а
prompt_hash отпечатка меняется вместе с текстом — прежние оценки честно
становятся «устаревшими».

Статьи выбирает человек, а не поиск: в вики две опубликованные статьи «Все
акции» с разным содержимым, и поиск по смыслу отдал бы модели обе.

Периметр — тот же, что у ИИ-помощника вики (wiki/ai/index.py): опубликована,
не «строгий режим», не отключена от ИИ ни сама, ни её раздел. Черновик или
закрытая от ИИ статья в промпт не попадает, даже если на неё сослались.
"""
from __future__ import annotations

import logging
import re

# Ссылка в тексте: полный адрес портала или относительный «?…article=…». Что
# из найденного — статья вики, решает тот же разбор, что у самой вики
# (wiki.links.slug_from_href): свой хост и путь, параметр article.
_LINK = re.compile(r"(?:https?://[^\s<>\"«»]+|\?[^\s<>\"«»]*article=[^\s<>\"«»]+)")
_TRAILING = ".,;:!?)]}»\"'"

# Потолки текста в промпте. «Все акции» — около 17 тыс. знаков; потолок на статью
# с запасом на рост таблицы, общий — чтобы десяток ссылок не раздул промпт.
ARTICLE_LIMIT = 30000
TOTAL_LIMIT = 60000

_ARTICLES_SQL = """
SELECT a.slug, a.title, a.updated_at, coalesce(a.content, ''), coalesce(a.content_plain, '')
  FROM wiki_articles a
 WHERE a.slug = ANY(%s)
   AND a.status = 'published' AND NOT a.strict_mode AND NOT a.ai_opt_out
   AND NOT EXISTS (SELECT 1
                     FROM wiki_article_sections s
                     JOIN wiki_sections sec ON sec.id = s.section_id
                    WHERE s.article_id = a.id AND sec.ai_opt_out)
"""


def referenced_slugs(text) -> list[str]:
    """Слаги статей вики, на которые ссылается текст, в порядке появления."""
    from wiki import links as wiki_links
    out = []
    for match in _LINK.finditer(str(text or "")):
        slug = wiki_links.slug_from_href(match.group(0).rstrip(_TRAILING))
        if slug and slug not in out:
            out.append(slug)
    return out


def article_text(html, fallback_text="") -> str:
    """Статья — текстом для модели: заголовки строками «## …», таблицы — строками
    «поле: значение» (та же разборка, что у ИИ-помощника вики), иначе таблица
    акций сплющилась бы в одну строку без границ ячеек."""
    from wiki.ai.chunker import parse_blocks
    lines = []
    for block in parse_blocks(html):
        line = (f"{'#' * min(int(block['level'] or 1), 4)} {block['text']}"
                if block["kind"] == "heading" else block["text"])
        if not lines or lines[-1] != line:
            lines.append(line)
    text = "\n".join(lines).strip()
    return text or " ".join(str(fallback_text or "").split())


def load(cur, descriptions, guidance) -> list[dict]:
    """Статьи, на которые ссылаются описания критериев и указания направления.

    descriptions — [(idx критерия, описание)]. Возвращает список в порядке первой
    ссылки: {slug, title, updated_at (ISO), text, criterion_idxs, in_guidance}.
    Ссылок нет — запроса нет. Сбой чтения вики оценку не останавливает: модель
    просто останется без справки, а причина — в логе."""
    refs = {}
    for idx, description in descriptions:
        for slug in referenced_slugs(description):
            refs.setdefault(slug, {"criterion_idxs": [], "in_guidance": False})
            refs[slug]["criterion_idxs"].append(int(idx))
    for slug in referenced_slugs(guidance):
        refs.setdefault(slug, {"criterion_idxs": [], "in_guidance": False})
        refs[slug]["in_guidance"] = True
    if not refs:
        return []
    try:
        cur.execute(_ARTICLES_SQL, (list(refs),))
        rows = {row[0]: row for row in cur.fetchall()}
    except Exception:
        logging.exception("ai-qa: статьи вики %s для оценки не прочитаны", list(refs))
        return []
    out, total = [], 0
    for slug, ref in refs.items():
        row = rows.get(slug)
        if row is None:
            continue
        text = article_text(row[3], row[4])
        if len(text) > ARTICLE_LIMIT:
            text = text[:ARTICLE_LIMIT] + "\n…(статья обрезана)"
        if total + len(text) > TOTAL_LIMIT:
            logging.warning("ai-qa: статья вики «%s» не вошла в справку — потолок %s знаков",
                            row[1], TOTAL_LIMIT)
            continue
        total += len(text)
        updated = row[2]
        out.append({"slug": slug, "title": row[1],
                    "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
                    "text": text, **ref})
    return out
