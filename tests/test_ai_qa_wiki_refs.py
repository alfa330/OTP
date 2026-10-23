"""Справка из вики для оценщика: статьи по ссылкам из описания критерия и указаний
направления уходят в системный промпт (переписка 61480, 23.10.2026: ИИ не знал,
есть ли у парка QAZAQ акция, хотя статья «Все акции» это говорит)."""
from datetime import datetime
import unittest
from unittest.mock import patch

from call_qa.evaluation import criteria as criteria_mod
from call_qa.evaluation import evaluator, wiki_refs

LINK = "https://alfa330.github.io/OTP/?view=wiki&article=все-акции"
TABLE_HTML = ("<h2>Актуальные акции 2026</h2><table><tr><th>Парк</th><th>Название</th></tr>"
              "<tr><td>Все таксопарки, кроме Eki Dongelek и Tenge</td><td>Быстрый старт</td></tr>"
              "</table><p>Архив ниже.</p>")


class _Cursor:
    def __init__(self, answers):
        self.answers = answers
        self.executed = []
        self._rows = []

    def execute(self, sql, params=None):
        if params is not None and sql.count("%s") != len(params):
            raise AssertionError("число %s не совпадает с параметрами")
        self.executed.append((" ".join(sql.split()), params))
        self._rows = []
        for marker, rows in self.answers:
            if marker in sql:
                if isinstance(rows, Exception):
                    raise rows
                self._rows = list(rows)
                break

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _Connection:
    def __init__(self, answers):
        self.cur = _Cursor(answers)

    def cursor(self):
        return self.cur

    def close(self):
        pass


def _article_row(slug="все-акции", title="Все акции", html=TABLE_HTML):
    return (slug, title, datetime(2026, 10, 6, 17, 46), html, "")


class LinksTests(unittest.TestCase):
    def test_links_as_people_paste_them(self):
        text = (f"Какие акции действуют — статья «Все акции»: {LINK}.\n"
                "Ещё: ?view=wiki&article=vse-akcii, внешняя https://example.com/?article=x "
                "и чужой портал https://evil.example/OTP/?view=wiki&article=secret")
        self.assertEqual(wiki_refs.referenced_slugs(text), ["все-акции", "vse-akcii"])

    def test_percent_encoded_link_from_the_address_bar(self):
        encoded = "https://alfa330.github.io/OTP/?view=wiki&article=%D0%B2%D1%81%D0%B5-%D0%B0%D0%BA%D1%86%D0%B8%D0%B8"
        self.assertEqual(wiki_refs.referenced_slugs(encoded), ["все-акции"])

    def test_other_portal_sections_are_not_articles(self):
        self.assertEqual(wiki_refs.referenced_slugs(
            "https://alfa330.github.io/OTP/?view=ai_qa&eval=wz_episode-61480"), [])
        self.assertEqual(wiki_refs.referenced_slugs(None), [])


class ArticleTextTests(unittest.TestCase):
    def test_table_rows_keep_their_cells(self):
        text = wiki_refs.article_text(TABLE_HTML)
        self.assertIn("## Актуальные акции 2026", text)
        self.assertIn("Все таксопарки, кроме Eki Dongelek и Tenge", text)
        self.assertIn("Быстрый старт", text)
        # Строка таблицы — одной строкой, а не кашей из всей таблицы.
        row = next(line for line in text.splitlines() if "Eki Dongelek" in line)
        self.assertIn("Быстрый старт", row)

    def test_empty_body_falls_back_to_plain_text(self):
        self.assertEqual(wiki_refs.article_text("", "  только   текст "), "только текст")


class LoadTests(unittest.TestCase):
    def test_no_links_no_query(self):
        cur = _Cursor([])
        self.assertEqual(wiki_refs.load(cur, [(0, "Корректно\t15\tбез ссылок")], ""), [])
        self.assertEqual(cur.executed, [])

    def test_article_is_loaded_for_its_criteria_and_guidance(self):
        cur = _Cursor([("FROM wiki_articles", [_article_row()])])
        out = wiki_refs.load(cur, [(5, f"Корректно\t15\t…\n{LINK}"), (6, "без ссылки"),
                                   (7, f"тоже {LINK}")],
                             f"Указания: {LINK}; черновик ?view=wiki&article=draft-only")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["criterion_idxs"], [5, 7])
        self.assertTrue(out[0]["in_guidance"])
        self.assertEqual(out[0]["updated_at"], "2026-10-06T17:46:00")
        sql, params = cur.executed[0]
        self.assertEqual(params, (["все-акции", "draft-only"],))
        # Периметр ИИ-помощника вики: опубликована, не строгий режим, не закрыта от ИИ.
        for condition in ("a.status = 'published'", "NOT a.strict_mode", "NOT a.ai_opt_out",
                          "sec.ai_opt_out"):
            self.assertIn(condition, sql)

    def test_long_article_is_cut_and_total_is_bounded(self):
        big = "<p>" + "а" * (wiki_refs.ARTICLE_LIMIT + 10) + "</p>"
        rows = [_article_row("one", "Один", big), _article_row("two", "Два", big),
                _article_row("three", "Три", big)]
        cur = _Cursor([("FROM wiki_articles", rows)])
        out = wiki_refs.load(cur, [(0, "?view=wiki&article=one ?view=wiki&article=two "
                                       "?view=wiki&article=three")], "")
        self.assertTrue(out[0]["text"].endswith("…(статья обрезана)"))
        self.assertLessEqual(sum(len(item["text"]) for item in out), wiki_refs.TOTAL_LIMIT)
        self.assertEqual([item["slug"] for item in out], ["one"])

    def test_wiki_failure_does_not_stop_the_evaluation(self):
        cur = _Cursor([("FROM wiki_articles", RuntimeError("wiki down"))])
        self.assertEqual(wiki_refs.load(cur, [(0, LINK)], ""), [])


class PromptTests(unittest.TestCase):
    crits = [{"idx": 5, "name": "Информация по акциям", "description": "d", "is_critical": False},
             {"idx": 6, "name": "Грамотность", "description": "d", "is_critical": False}]
    wiki = [{"slug": "все-акции", "title": "Все акции", "updated_at": "2026-10-06T17:46:00",
             "text": "## Актуальные акции 2026\nБыстрый старт", "criterion_idxs": [5],
             "in_guidance": False}]

    def test_without_links_prompt_is_unchanged(self):
        self.assertEqual(evaluator.build_system(self.crits, "wz_episode", "op"),
                         evaluator.build_system(self.crits, "wz_episode", "op", wiki=[]))
        self.assertEqual(evaluator.build_system(self.crits, "wz_episode", "op", guidance="g"),
                         evaluator.build_system(self.crits, "wz_episode", "op", guidance="g",
                                                wiki=None))

    def test_article_goes_after_guidance_with_its_criterion_and_date(self):
        prompt = evaluator.build_system(self.crits, "wz_episode", "op", guidance="Указание",
                                        wiki=self.wiki)
        self.assertLess(prompt.index("Указание"), prompt.index("СПРАВКА ИЗ ВИКИ"))
        self.assertIn("[статья «Все акции»; к критерию 5; обновлена 06.10.2026]\n"
                      "## Актуальные акции 2026\nБыстрый старт", prompt)

    def test_article_of_a_criterion_outside_this_call_is_left_out(self):
        prompt = evaluator.build_system(self.crits[1:], "wz_episode", "op", wiki=self.wiki)
        self.assertNotIn("СПРАВКА ИЗ ВИКИ", prompt)
        guidance_article = [{**self.wiki[0], "criterion_idxs": [], "in_guidance": True}]
        prompt = evaluator.build_system(self.crits[1:], "wz_episode", "op", wiki=guidance_article)
        self.assertIn("[статья «Все акции»; к указаниям направления; обновлена 06.10.2026]", prompt)

    def test_evaluation_body_and_fingerprint_see_the_same_articles(self):
        direction = {"id": 71, "guidance": "", "wiki": self.wiki}
        body = evaluator.build_eval_body("[22.10] Оператор: Вы стали партнёром", direction,
                                         self.crits, use_rag=False, model="glm-5.3-flash",
                                         subject_kind="wz_episode", department="op")
        self.assertIn("СПРАВКА ИЗ ВИКИ", str(body))


class LoadDirectionTests(unittest.TestCase):
    def test_scale_brings_the_articles_its_criteria_link_to(self):
        raw = [{"name": "Приветствие", "value": "Корректно\t5\tпоздоровался", "weight": 5},
               {"name": "Информация по акциям", "value": f"Корректно\t15\t…\nАкции: {LINK}",
                "weight": 15}]
        conn = _Connection([("FROM directions", [(71, "Верификатор", raw, None)]),
                            ("qa_direction_guidance", [("",)]),
                            ("FROM wiki_articles", [_article_row()])])
        with patch.object(criteria_mod.config, "connect_ro", return_value=conn):
            direction = criteria_mod.load_direction(71)
        self.assertEqual([item["slug"] for item in direction["wiki"]], ["все-акции"])
        self.assertEqual(direction["wiki"][0]["criterion_idxs"], [1])
        # Шкала та же: в scale_hash ссылка входит как часть описания, а текст статьи — нет.
        with patch.object(criteria_mod.config, "connect_ro", return_value=_Connection([
                ("FROM directions", [(71, "Верификатор", raw, None)]),
                ("qa_direction_guidance", [("",)]),
                ("FROM wiki_articles", [_article_row(html="<p>другой текст</p>")])])):
            again = criteria_mod.load_direction(71)
        self.assertEqual(direction["scale_hash"], again["scale_hash"])
        self.assertNotEqual(direction["wiki"][0]["text"], again["wiki"][0]["text"])


if __name__ == "__main__":
    unittest.main()
