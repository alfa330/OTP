"""Оценщик видит телефоны и офисы парка из справочника «Парки» (владелец 16.11.2026,
«Да, подключи»).

Звонок imported_call-15650: оператор продиктовал номер техподдержки «Ноль такси»
708 267 54 87. В статье «Номера телефонов» у парка один номер, а в карточке парка
справочника «Парки» — два, и этот среди них. Оценщик читал только статьи и штрафовал
за верный номер. Теперь, когда план ищет контакт парка, оценщик получает его телефоны
и офисы — и только их: полная карточка (комиссия, акции, описание) на повторах боевых
прогонов ослабляла проверку неозвученной акции."""
import json
import unittest
from unittest.mock import patch

from call_qa.evaluation import wiki_lookup

CRITERIA = [{"idx": 15, "criterion_id": "c15", "name": "КО_Достоверность", "description": "d",
             "eval_source": "transcript", "is_critical": True},
            {"idx": 17, "criterion_id": "c17", "name": "КО_Альтернативное решение",
             "description": "d", "eval_source": "transcript", "is_critical": True}]
TRANSCRIPT = "[S2] Қазір сізге техподдержкаға номер берем. Ноль таксидің. 708. 267. 54 87."


def _park(park_id, slug, name, **extra):
    park = {"id": park_id, "slug": slug, "name": name, "description": "", "city": None,
            "address": None, "website": "", "commission": None, "status": "active",
            "position": park_id, "head_office": None, "head_office_id": None}
    park.update(extra)
    return park


PARKS = [_park(1, "itaxi", "iTaxi", commission=2.0),
         _park(16, "nol-taksi", "Ноль такси", commission=0.0, website="nol.kz",
               description="Ноль Такси — 0% комиссии парка.\nКомиссия при выводе 5,5%."),
         _park(17, "itaxi-vip", "iTaxi VIP", commission=2.0),
         _park(19, "centr-registraciy-regiony", "Центр Регистраций (Регионы)", commission=2.0)]
PHONES = {16: {None: [{"phone": "+7 700 921 42 22", "note": ""},
                      {"phone": "+7 708 267 54 87", "note": ""}]},
          1: {None: [{"phone": "+7 707 000 00 01", "note": ""}]}}
OFFICES = {1: [{"name": "Офис Алматы №1", "city": "Алматы",
                "phones": [{"phone": "+7 707 505 08 80", "note": ""}]}]}
NOL_QUERY = {"criterion_idx": 17, "query": "Номера телефонов техподдержка таксопарк Ноль такси",
             "reason": "сверить номер"}


class _Cursor:
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.executed = []
        self._rows = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self._rows = []
        for marker, rows in self.answers:
            if marker in sql:
                if isinstance(rows, Exception):
                    raise rows
                self._rows = list(rows)
                break

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def close(self):
        pass


class _Connection:
    def __init__(self, answers=()):
        self.cur = _Cursor(answers)
        self.closed = 0

    def cursor(self):
        return self.cur

    def close(self):
        self.closed += 1


def _spaces(rows):
    """Ответ на запрос пространств отдела — и только на него (у периметра свой SQL)."""
    return ("SELECT DISTINCT sp.id, sp.features", rows)


def _directory(parks=PARKS, offices=OFFICES, phones=PHONES):
    return (patch("wiki.parks.list_parks", return_value=parks),
            patch("wiki.offices.offices_by_park", return_value=offices),
            patch("wiki.offices.phones_by_park", return_value=phones))


class ParkCardsForTests(unittest.TestCase):
    def _cards(self, queries, *, spaces=((11, {"parks": True}),), parks=PARKS, offices=OFFICES,
               phones=PHONES, department="op"):
        cur = _Cursor([_spaces(list(spaces))])
        listing, office_reader, phone_reader = _directory(parks, offices, phones)
        with listing as list_mock, office_reader as offices_mock, phone_reader as phones_mock:
            cards = wiki_lookup.park_cards_for(cur, department, queries)
        return cards, cur, list_mock, offices_mock, phones_mock

    def test_search_for_a_number_brings_the_parks_phones(self):
        cards, cur, listing, offices, phones = self._cards([NOL_QUERY])
        self.assertEqual([card["name"] for card in cards], ["Ноль такси"])
        card = cards[0]
        self.assertEqual((card["park_id"], card["slug"], card["space_id"], card["criterion_idx"]),
                         (16, "nol-taksi", 11, 17))
        self.assertEqual(card["text"], "Таксопарк «Ноль такси».\n"
                                       "Телефон парка Ноль такси: +7 700 921 42 22.\n"
                                       "Телефон парка Ноль такси: +7 708 267 54 87.")
        # Пространство — по коду отдела, парки, офисы и номера — только этого пространства.
        self.assertEqual(cur.executed[0][1], {"code": "op"})
        self.assertEqual(listing.call_args.kwargs["space_id"], 11)
        self.assertEqual((offices.call_args.args[1], offices.call_args.kwargs["space_id"]), ([16], 11))
        self.assertEqual((phones.call_args.args[1], phones.call_args.kwargs["space_id"]), ([16], 11))

    def test_only_contacts_no_commission_description_site_or_promotions(self):
        # Полная карточка ослабляла проверку неозвученной акции (повторы 15441 и др.).
        cards, *_ = self._cards([NOL_QUERY])
        text = cards[0]["text"]
        for absent in ("Комиссия", "0%", "5,5%", "Описание", "Сайт", "nol.kz", "Акция"):
            self.assertNotIn(absent, text)

    def test_offices_with_their_numbers(self):
        cards, *_ = self._cards([{"criterion_idx": 17, "query": "адрес офиса iTaxi в Алматы"}])
        self.assertIn("Телефон парка iTaxi в офисе «Офис Алматы №1» (Алматы): +7 707 505 08 80.",
                      cards[0]["text"])

    def test_department_code_is_matched_like_the_article_perimeter(self):
        cards, cur, *_ = self._cards([NOL_QUERY], department=" OP ")
        self.assertEqual(cur.executed[0][1], {"code": "op"})
        self.assertEqual(len(cards), 1)

    def test_query_without_a_contact_brings_no_card(self):
        # Комиссия, акции, регистрация через WhatsApp-шаблон — не поиск контакта парка.
        queries = [{"criterion_idx": 15, "query": "комиссия таксопарка Ай такси"},
                   {"criterion_idx": 5, "query": "действующие акции для новых водителей таксопарк Qazaq"},
                   {"criterion_idx": 15, "query": "регистрация водителя через WhatsApp шаблон Ноль такси"}]
        cards, cur, listing, offices, _ = self._cards(queries)
        self.assertEqual(cards, [])
        self.assertEqual(cur.executed, [])
        listing.assert_not_called()

    def test_contact_words(self):
        for text in ("Номера телефонов техподдержка Ноль такси", "контакты для аренды iTaxi",
                     "адрес офиса таксопарка Qazaq", "куда позвонить водителю iTaxi",
                     "служба поддержки водителей iTaxi", "как связаться с парком Qazaq",
                     "горячая линия iTaxi", "колл-центр Ноль такси",
                     # План пишет запросы как заголовки — с заглавной.
                     "Адрес iTaxi", "ТЕЛЕФОН НОЛЬ ТАКСИ"):
            self.assertTrue(wiki_lookup.CONTACT_INTENT.search(text), text)
        for text in ("комиссия таксопарка iTaxi", "регистрация через WhatsApp iTaxi",
                     "презентация свойство связка выгода iTaxi", "акции Ноль такси",
                     "перезвонить водителю позже"):
            self.assertFalse(wiki_lookup.CONTACT_INTENT.search(text), text)

    def test_parks_in_query_order_each_once_under_the_cap(self):
        queries = [{"criterion_idx": 5, "query": "телефон таксопарка Ай такси"},
                   {"criterion_idx": 17, "query": "номер техподдержки Ноль такси"},
                   {"criterion_idx": 15, "query": "офисы Ноль такси и iTaxi VIP"}]
        with patch.object(wiki_lookup, "MAX_PARK_CARDS", 2):
            cards, *_ = self._cards(queries)
        self.assertEqual([(card["name"], card["criterion_idx"]) for card in cards],
                         [("iTaxi", 5), ("Ноль такси", 17)])

    def test_two_parks_with_nested_names_are_two_cards(self):
        cards, *_ = self._cards([{"criterion_idx": 15, "query": "телефоны iTaxi и iTaxi VIP"}])
        self.assertEqual(sorted(card["name"] for card in cards), ["iTaxi", "iTaxi VIP"])

    def test_digits_and_the_greeting_name_no_park(self):
        queries = [{"criterion_idx": 17, "query": "номер ноль семь ноль восемь двести"},
                   {"criterion_idx": 0, "query": "адрес центр регистрации водителей"}]
        cards, _cur, _listing, offices, _ = self._cards(queries)
        self.assertEqual(cards, [])
        offices.assert_not_called()

    def test_space_without_the_parks_tab_is_not_read(self):
        cards, _cur, listing, *_ = self._cards([NOL_QUERY], spaces=((12, {"parks": False}),))
        self.assertEqual(cards, [])
        listing.assert_not_called()

    def test_tab_flag_absent_means_enabled(self):
        # wiki.schema.space_features: чего нет в JSONB — то включено (как у вкладки).
        cards, *_ = self._cards([NOL_QUERY], spaces=((11, None),))
        self.assertEqual(len(cards), 1)

    def test_no_queries_no_department_no_database(self):
        for queries, department in (([], "op"), ([NOL_QUERY], None), ([{"query": ""}], "op")):
            cur = _Cursor()
            self.assertEqual(wiki_lookup.park_cards_for(cur, department, queries), [])
            self.assertEqual(cur.executed, [])

    def test_long_office_list_is_clipped_at_a_line(self):
        offices = {1: [{"name": f"Офис №{i}", "city": "Алматы",
                        "phones": [{"phone": f"+7 707 000 {i:02d} 00", "note": ""}]}
                       for i in range(60)]}
        with patch.object(wiki_lookup, "PARK_CARD_CHARS", 600):
            cards, *_ = self._cards([{"criterion_idx": 17, "query": "телефон iTaxi"}],
                                    offices=offices)
        text = cards[0]["text"]
        self.assertLessEqual(len(text), 602)
        self.assertTrue(text.endswith("\n…"))
        self.assertIn("Телефон парка iTaxi: +7 707 000 00 01.", text)


class RenderTests(unittest.TestCase):
    CARDS = [{"park_id": 16, "name": "Ноль такси", "slug": "nol-taksi", "space_id": 11,
              "criterion_idx": 17, "query": "q", "text": "Таксопарк «Ноль такси».\nТелефон…"}]

    def test_cards_follow_the_articles_with_running_numbers(self):
        text = wiki_lookup.render_parks(self.CARDS, start=3)
        self.assertEqual(text, "[3; справочник «Парки»: телефоны и офисы таксопарка «Ноль такси»]\n"
                               "Таксопарк «Ноль такси».\nТелефон…")
        self.assertEqual(wiki_lookup.render_parks([]), "")

    def test_card_source_has_no_article_link(self):
        self.assertEqual(wiki_lookup.park_sources(self.CARDS), [{
            "kind": "park", "park_id": 16, "article_id": None, "title": "Ноль такси", "slug": "",
            "updated_at": None, "headings": [], "criterion_idxs": [17], "stale": False}])


class PrepareTests(unittest.TestCase):
    ARTICLE = {"chunk_id": 7569, "article_id": 8, "title": "Номера телефонов", "slug": "callcenter",
               "heading_path": "Номера тех.поддержки", "updated_at": "2026-10-29T17:14:39",
               "text": "Таксопарк: Ноль такси; Номер WhatsApp: 77009214222", "criterion_idx": 17,
               "query": "q", "stale_note": ""}
    LABEL = "справочник «Парки»: телефоны и офисы таксопарка «Ноль такси»]"

    def _prepare(self, *, search_chunks, listing_error=None):
        first = _Connection([("FROM wiki_space_departments", [(8, "Номера телефонов")])])
        second = _Connection([_spaces([(11, {"parks": True})])])
        pool = iter([first, second])
        listing, office_reader, phone_reader = _directory()
        if listing_error:
            listing = patch("wiki.parks.list_parks", side_effect=listing_error)
        with patch.object(wiki_lookup, "plan",
                          return_value={"queries": [dict(NOL_QUERY)], "meta": {}}), \
             patch.object(wiki_lookup, "embed_queries",
                          return_value={"vectors": [None], "errors": [], "degraded": False}), \
             patch.object(wiki_lookup, "search", return_value={"chunks": search_chunks}), \
             listing, office_reader, phone_reader:
            out = wiki_lookup.prepare(criteria=CRITERIA, transcript=TRANSCRIPT,
                                      subject_kind="imported_call", department="op",
                                      model="glm", connect=lambda: next(pool))
        self.assertEqual((first.closed, second.closed), (1, 1))
        return out

    def test_card_goes_after_the_article_excerpt(self):
        out = self._prepare(search_chunks=[self.ARTICLE])
        text = out["wiki_text"]
        article = text.index("[1; к критерию 17; статья «Номера телефонов»")
        card = text.index("[2; " + self.LABEL)
        self.assertLess(article, card)
        self.assertIn("Телефон парка Ноль такси: +7 708 267 54 87.", text[card:])
        self.assertEqual(out["trace"]["status"], "ok")
        self.assertEqual([park["name"] for park in out["trace"]["parks"]], ["Ноль такси"])
        self.assertEqual([(item.get("kind"), item["title"]) for item in out["sources"]],
                         [(None, "Номера телефонов"), ("park", "Ноль такси")])
        self.assertEqual(out["trace"]["version"], "wiki-search-v2")
        json.dumps(out, ensure_ascii=False)  # уходит в payload прогона и манифест пакета

    def test_only_a_card_is_still_a_finding(self):
        out = self._prepare(search_chunks=[])
        self.assertEqual(out["trace"]["status"], "ok")
        self.assertTrue(out["wiki_text"].startswith("[1; " + self.LABEL))

    def test_directory_failure_keeps_the_articles(self):
        out = self._prepare(search_chunks=[self.ARTICLE], listing_error=RuntimeError("parks down"))
        self.assertIn("статья «Номера телефонов»", out["wiki_text"])
        self.assertNotIn("справочник «Парки»", out["wiki_text"])
        self.assertEqual(out["trace"]["status"], "ok")
        self.assertEqual(out["trace"]["parks"], [])
        self.assertTrue(out["trace"]["errors"][-1].startswith("parks: RuntimeError: parks down"))

    def test_empty_trace_has_the_parks_slot(self):
        self.assertEqual(wiki_lookup.empty_trace("skipped")["parks"], [])


if __name__ == "__main__":
    unittest.main()
