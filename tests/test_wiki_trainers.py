# -*- coding: utf-8 -*-
"""Тренажёры в вики: кнопка в тексте статьи и список «где вставлен».

Что здесь важно проверить и почему.

1. КНОПКА ДОЛЖНА ПЕРЕЖИТЬ САНИТАЙЗЕР. В базу она уезжает пустым div'ом с
   четырьмя data-атрибутами. Вырежи санитайзер любой из них — статья
   сохранится, а при чтении кнопка станет безымянным блоком и тренажёр не
   откроется. Причём молча: ошибки не будет ни у автора, ни у читателя.

2. И ПРИ ЭТОМ ОСТАТЬСЯ БЕЗОПАСНОЙ. Раз в тексте статьи появился элемент, по
   которому читалка навешивает обработчик, соблазн «дописать обработчик прямо в
   разметке» становится реальным. Тест фиксирует: onclick и javascript: из
   кнопки вырезаются, как из любого другого узла.

3. ТИП СТАТЬИ ОБЯЗАН ПРОЙТИ CHECK. Ограничение на article_type объявлено внутри
   CREATE TABLE, то есть на проде лежит со старым списком значений. Без
   пересборки сохранение статьи-тренажёра падало бы 500-й ошибкой.

4. «ГДЕ ВСТАВЛЕН» НЕ ДОЛЖЕН ВРАТЬ. Одна статья с двумя одинаковыми кнопками —
   это одна статья в списке, а не две.
"""

import re
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from wiki import articles as wiki_articles  # noqa: E402
from wiki import perimeter as wiki_perimeter  # noqa: E402
from wiki import queries  # noqa: E402
from wiki import schema as wiki_schema  # noqa: E402
from wiki.access import collect_subjects  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402
from wiki.sanitize import sanitize_html, to_plain_text  # noqa: E402

# Ровно то, что отдаёт редактор (снято с реального сохранения в браузере).
BUTTON_HTML = (
    '<div data-wiki-trainer="sapar-site-avr" data-label="Пройти тренажёр по Сапару" '
    'data-width="60" data-align="right" class="wiki-trainer-embed" '
    'style="width: 60%; margin-left: auto;">'
    '<span class="wiki-trainer-embed__label">Пройти тренажёр по Сапару</span></div>'
)


class SanitizeTrainerButtonTest(unittest.TestCase):
    def setUp(self):
        try:
            import nh3  # noqa: F401
        except ImportError:  # pragma: no cover — окружение без зависимости
            self.skipTest('nh3 не установлен')

    def test_all_four_attributes_survive(self):
        out = sanitize_html(BUTTON_HTML)
        self.assertIn('data-wiki-trainer="sapar-site-avr"', out)
        self.assertIn('data-label="Пройти тренажёр по Сапару"', out)
        self.assertIn('data-width="60"', out)
        self.assertIn('data-align="right"', out)

    def test_class_and_width_survive(self):
        """Без класса кнопка теряет вид, без ширины — заданный автором размер."""
        out = sanitize_html(BUTTON_HTML)
        self.assertIn('wiki-trainer-embed', out)
        self.assertIn('width: 60%', out)
        # Выравнивание держится на полях, и оба поля в белом списке CSS.
        self.assertIn('margin-left: auto', out)

    def test_center_alignment_keeps_both_margins(self):
        html = ('<div data-wiki-trainer="taxi-pro-avr" data-align="center" data-width="100" '
                'style="width: 100%; margin-left: auto; margin-right: auto">'
                '<span>Тренажёр</span></div>')
        out = sanitize_html(html)
        self.assertIn('margin-left: auto', out)
        self.assertIn('margin-right: auto', out)

    def test_handler_on_the_button_is_stripped(self):
        """Обработчик навешивает читалка; в тексте статьи его быть не может."""
        html = ('<div data-wiki-trainer="taxi-pro-avr" onclick="alert(1)" '
                'onmouseover="steal()">Тренажёр</div>')
        out = sanitize_html(html)
        self.assertIn('data-wiki-trainer', out)
        self.assertNotIn('onclick', out.lower())
        self.assertNotIn('onmouseover', out.lower())

    def test_button_cannot_be_a_link_to_javascript(self):
        html = ('<a href="javascript:alert(1)" data-wiki-trainer="taxi-pro-avr">'
                'Тренажёр</a>')
        self.assertNotIn('javascript', sanitize_html(html).lower())

    def test_button_cannot_cover_the_portal(self):
        """position/z-index не в белом списке CSS, fixed-класс отбрасывается."""
        html = ('<div data-wiki-trainer="taxi-pro-avr" class="fixed z-50" '
                'style="position: fixed; z-index: 99; width: 60%">Тренажёр</div>')
        out = sanitize_html(html)
        self.assertNotIn('position', out)
        self.assertNotIn('z-index', out)
        self.assertNotIn('fixed', out)
        self.assertIn('width: 60%', out)

    def test_label_reaches_plain_text(self):
        """Подпись кнопки — часть текста статьи, значит её находит поиск."""
        plain = to_plain_text('<p>Инструкция.</p>' + BUTTON_HTML)
        self.assertIn('Пройти тренажёр по Сапару', plain)


class ArticleTypeTest(unittest.TestCase):
    def test_trainer_is_a_known_type(self):
        self.assertIn('trainer', wiki_schema.ARTICLE_TYPES)

    def test_check_is_rebuilt_with_every_type(self):
        sql = wiki_schema._article_type_check_statement()
        for value in wiki_schema.ARTICLE_TYPES:
            self.assertIn("'%s'" % value, sql,
                          'тип %s не попал в пересобранный CHECK' % value)

    def test_stale_check_is_dropped_before_the_new_one(self):
        """На проде ограничение лежит с автоматическим именем и старым списком."""
        sql = wiki_schema._article_type_check_statement()
        self.assertIn('DROP CONSTRAINT', sql)
        self.assertIn('wiki_articles_type_chk', sql)
        # Условие «пересобирать или нет» проверяет ТЕКСТ определения: иначе
        # ограничение создалось бы один раз и навсегда заморозило список.
        self.assertIn("LIKE '%''trainer''%'", sql)

    def test_ddl_check_is_widened_at_startup(self):
        """В CREATE TABLE список исторический — новый тип добавляет миграция."""
        ddl = '\n'.join(s for s in wiki_schema._STATEMENTS if isinstance(s, str))
        match = re.search(r"article_type\s+VARCHAR\([^)]*\).*?CHECK \(article_type IN \((.*?)\)\)",
                          ddl, re.S)
        self.assertIsNotNone(match)
        historic = set(re.findall(r"'([a-z_]+)'", match.group(1)))
        self.assertTrue(set(wiki_schema.ARTICLE_TYPES) - historic,
                        'если DDL уже полон, миграция ниже стала бы бессмысленной '
                        'заглушкой — проверьте, что она всё ещё нужна')


class TrainerKeyPatternTest(unittest.TestCase):
    """Регулярка, которой ключи вынимаются в самом Постгресе."""

    def pattern(self):
        match = re.search(r"regexp_matches\(a\.content,\s*'([^']+)'", wiki_articles._TRAINER_USAGE_SQL)
        self.assertIsNotNone(match, 'не нашли регулярку в запросе')
        return re.compile(match.group(1))

    def test_key_is_found_in_the_real_button(self):
        self.assertEqual(self.pattern().findall(BUTTON_HTML), ['sapar-site-avr'])

    def test_two_buttons_give_two_matches(self):
        html = BUTTON_HTML + '<p>и ещё</p>' + BUTTON_HTML.replace(
            'sapar-site-avr', 'taxi-pro-avr')
        self.assertEqual(self.pattern().findall(html), ['sapar-site-avr', 'taxi-pro-avr'])

    def test_empty_and_broken_attribute_is_ignored(self):
        self.assertEqual(self.pattern().findall('<div data-wiki-trainer="">x</div>'), [])
        self.assertEqual(self.pattern().findall('<div data-wiki-trainer>x</div>'), [])

    def test_quote_cannot_escape_the_value(self):
        """Ключ — только буквы, цифры, дефис и подчёркивание."""
        self.assertEqual(
            self.pattern().findall('<div data-wiki-trainer="a<b>c">x</div>'), [])


class TrainerUsagesTest(unittest.TestCase):
    def cursor_with(self, rows):
        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        return cursor

    def test_articles_are_grouped_by_trainer(self):
        cursor = self.cursor_with([
            (1, 'podpisanie', 'Подписание', 'published', 'taxi-pro-avr'),
            (2, 'zapas', 'Запасной способ', 'draft', 'sapar-site-avr'),
            (1, 'podpisanie', 'Подписание', 'published', 'sapar-site-avr'),
        ])
        usages = wiki_articles.trainer_usages(cursor, {1, 2})
        self.assertEqual([a['id'] for a in usages['taxi-pro-avr']], [1])
        self.assertEqual([a['id'] for a in usages['sapar-site-avr']], [2, 1])
        self.assertEqual(usages['sapar-site-avr'][0]['status'], 'draft')

    def test_same_button_twice_in_one_article_counts_once(self):
        """Кнопка в начале и в конце длинной инструкции — это одна статья."""
        cursor = self.cursor_with([
            (1, 'podpisanie', 'Подписание', 'published', 'taxi-pro-avr'),
            (1, 'podpisanie', 'Подписание', 'published', 'taxi-pro-avr'),
        ])
        usages = wiki_articles.trainer_usages(cursor, {1})
        self.assertEqual(len(usages['taxi-pro-avr']), 1,
                         '«используется в 2 статьях» при одной статье')

    def test_empty_perimeter_asks_the_database_nothing(self):
        cursor = MagicMock()
        self.assertEqual(wiki_articles.trainer_usages(cursor, set()), {})
        cursor.execute.assert_not_called()

    def test_query_stays_inside_the_perimeter(self):
        cursor = self.cursor_with([])
        wiki_articles.trainer_usages(cursor, {5, 6})
        sql, params = cursor.execute.call_args[0]
        self.assertIn('a.id = ANY(%(ids)s)', ' '.join(sql.split()))
        self.assertEqual(sorted(params['ids']), [5, 6])
        # Тела статей в питон не тянем: в проде 81 % их объёма — base64-картинки.
        self.assertNotIn('a.content,', ' '.join(sql.split()).split('FROM')[0])


def make_context(**caps):
    """Контекст доступа с заданными способностями роли вики."""
    role = {'id': 5, 'code': 'wiki_role', 'can_read': True, 'can_create': False,
            'can_edit': False, 'can_delete': False, 'can_publish': False,
            'can_approve': False, 'can_manage_users': False,
            'can_manage_structure': False, 'can_manage_access': False}
    role.update(caps)
    return {
        'user_id': 42, 'otp_role': 'operator', 'department_id': None,
        'direction_id': None, 'headed_department_ids': [], 'group_ids': [],
        'wiki_roles': [role], 'access_mode': 'auto',
    }


@unittest.skipIf(Flask is None, 'flask не установлен')
class TrainersRouteTest(unittest.TestCase):
    """/trainers — инструмент редактора, и гейт стоит на сервере."""

    def build(self, context):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = [
            (1, 'podpisanie', 'Подписание', 'published', 'taxi-pro-avr'),
        ]
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def fake_perimeter(_cursor, _ctx, **_kwargs):
            return collect_subjects(user_id=42, otp_role='operator'), {3}, {1, 2}

        patches = [
            (queries, 'load_access_context', lambda _c, _u: dict(context)),
            # Курсор здесь один на все запросы; расчёт способностей ходит в базу
            # первым и без подмены разобрал бы чужие строки как выписанные права.
            (queries, 'granted_rule_rights', lambda _c, _s, _u: ({}, [])),
            (queries, 'log_action', lambda *a, **k: None),
            (wiki_perimeter, 'read_perimeter', fake_perimeter),
        ]
        for module, name, replacement in patches:
            original = getattr(module, name)
            setattr(module, name, replacement)
            self.addCleanup(setattr, module, name, original)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (42, None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
            gcs={'signed_url': lambda *a, **k: 'https://x'},
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def test_reader_is_refused(self):
        client = self.build(make_context())
        response = client.get('/api/wiki/trainers')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'WIKI_EDITOR_ONLY')

    def test_editor_gets_usages(self):
        client = self.build(make_context(can_create=True))
        data = client.get('/api/wiki/trainers').get_json()
        self.assertEqual([a['slug'] for a in data['usages']['taxi-pro-avr']],
                         ['podpisanie'])

    def test_publisher_counts_as_editor(self):
        """Гейт тот же, что у каталога: любая из трёх способностей открывает."""
        client = self.build(make_context(can_publish=True))
        self.assertEqual(client.get('/api/wiki/trainers').status_code, 200)


if __name__ == '__main__':
    unittest.main()
