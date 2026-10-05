# -*- coding: utf-8 -*-
"""Стороны звонка в карточке «ИИ-оценки» (call_qa.speaker_roles).

Стережём:
* оператор — тот голос, чьи реплики модель цитирует в обоснованиях, а не тот,
  кто больше говорит: в жалобе водитель говорит дольше, и раньше его реплики
  уезжали в пузыри «Оператор»;
* чей кусок цитаты — решает текст расшифровки, а метка голоса от модели
  («[S2]», «S2:», «С2:») — только когда кусок пересказан; кусок короче 8 знаков
  не улика ни так, ни так;
* ничья по кускам не отдаёт решение «кто больше говорит»: сначала
  самопредставление, потом число критериев, и только среди равных;
* без цитат решает самопредставление сотрудника, причём ответ на звонок
  («алло», «тыңдап тұрмын», «слушаю вас»), голое «звоню» водителя и «звоню из
  города …» уликой не считаются;
* у расшифровок до 05.10.2026 метка голоса восстанавливается по токенам и
  никогда не навешивается не на ту реплику;
* переписку это не трогает, а карточка звонка — и из журнала, и из АТС —
  получает стороны в review_payload, в том числе открытая из сохранённого прогона.
"""
import unittest
from contextlib import ExitStack, nullcontext
from unittest import mock

from call_qa import api, config
from call_qa import speaker_roles as sr

# Синтетическая жалоба: оператор (голос 2) представляется, водитель (голос 1)
# говорит втрое дольше. Реальных расшифровок здесь нет — репозиторий публичный.
OPERATOR_INTRO = "Здравствуйте, такси сервис, меня зовут Алия. Чем могу помочь?"
DRIVER_RANT = ("Слушайте, я уже третий день не могу выйти на линию, приложение "
               "пишет что фотоконтроль не пройден, а я его проходил вчера вечером "
               "два раза подряд и никто мне так и не ответил в поддержке")
OPERATOR_HOLD = "Понимаю вас, сейчас проверю ваш профиль, оставайтесь на линии"
DRIVER_MORE = ("И ещё бонус за прошлую неделю не начислили, хотя я отработал "
               "все сорок заказов по акции, как вы и обещали в рассылке")
OPERATOR_BYE = "Спасибо за ожидание, фотоконтроль подтверждён, хорошего дня"


def _line(spk, text, **extra):
    return {"spk": spk, "seg": [{"t": text}], **extra}


def complaint_lines():
    return [_line("1", "Алло."), _line("2", OPERATOR_INTRO), _line("1", DRIVER_RANT),
            _line("2", OPERATOR_HOLD), _line("1", DRIVER_MORE), _line("2", OPERATOR_BYE)]


def criterion(evidence, verdict="Correct", key="evidence"):
    return {"ai": verdict, key: evidence}


class EvidenceTests(unittest.TestCase):
    def test_operator_is_who_the_model_quotes_not_who_talks_more(self):
        lines = complaint_lines()
        self.assertEqual(sr.talk_share(lines).most_common(1)[0][0], "1")
        crits = [criterion("такси сервис, меня зовут Алия"),
                 criterion("сейчас проверю ваш профиль, оставайтесь на линии"),
                 criterion("фотоконтроль подтверждён, хорошего дня")]
        self.assertEqual(sr.resolve(lines, crits), {"operator": ["2"], "method": "evidence"})

    def test_run_rows_use_evidence_quote_key(self):
        lines = complaint_lines()
        crits = [criterion("сейчас проверю ваш профиль", key="evidence_quote")]
        self.assertEqual(sr.evidence_votes(crits, lines), {"2": 1})

    def test_every_fragment_of_an_exchange_votes_for_its_voice(self):
        # Модель цитирует обмен: вопрос оператора и ответ водителя как контекст.
        lines = complaint_lines()
        crits = [criterion(f"{OPERATOR_HOLD} [S1] {DRIVER_MORE}"),
                 criterion(f"[S1] {DRIVER_RANT} [S2] {OPERATOR_HOLD}"),
                 criterion(f"[S2] {OPERATOR_BYE}")]
        self.assertEqual(sr.evidence_votes(crits, lines), {"2": 3, "1": 2})
        self.assertEqual(sr.resolve(lines, crits)["operator"], ["2"])

    def test_quote_marks_in_every_form_the_model_writes(self):
        # Дословная цитата с меткой в начале находится в расшифровке, только если
        # метку отрезать: иначе «s2» попадает в искомый текст, и голос теряется.
        lines = complaint_lines()
        for quote, voice in ((f"S2: {OPERATOR_HOLD}", "2"),
                             (f"S1 (клиент): «{DRIVER_MORE}»", "1"),
                             (f"С2: {OPERATOR_BYE}", "2"),
                             (f"[ S2 ] {OPERATOR_BYE}", "2")):
            with self.subTest(quote=quote[:20]):
                self.assertEqual(sr.evidence_votes([criterion(quote)], lines), {voice: 1})
        # Время — не метка голоса, в том числе с пробелом, как его пишет распознавание.
        for text in ("С 9:00 до 18:00 офис работает", "С9:30 приезжайте", "С 9: 30 до 18:00 работаем"):
            with self.subTest(text=text):
                self.assertEqual(sr._quote_fragments(text), [(None, text)])

    def test_model_mark_counts_only_when_the_text_is_paraphrased(self):
        lines = complaint_lines()
        # Пересказ, которого нет в расшифровке, — верим метке модели.
        paraphrase = criterion("[S2] Оператор пообещал перезвонить после проверки профиля")
        self.assertEqual(sr.evidence_votes([paraphrase], lines), {"2": 1})
        self.assertEqual(sr.evidence_votes([criterion("S2: Оператор пообещал перезвонить")], lines),
                         {"2": 1})
        # Текст нашёлся у другого голоса — верим расшифровке, а не метке.
        mislabeled = criterion(f"[S1] {OPERATOR_HOLD}")
        self.assertEqual(sr.evidence_votes([mislabeled], lines), {"2": 1})
        # Без метки пересказ — не улика; метка голоса, которого нет, — тоже.
        self.assertEqual(sr.evidence_votes([criterion("Оператор пообещал перезвонить позже"),
                                            criterion("[S7] Оператор пообещал перезвонить")],
                                           lines), {})

    def test_short_marked_fragment_is_not_evidence(self):
        # «[S2] Да. Иә.», «[S1] Жарайды.» — короткие реплики есть у обеих сторон,
        # и метка модели их уликой не делает.
        lines = complaint_lines()
        for quote in ("[S1] Да, да.", "[S2] Жарайды.", "S2: Иә, иә.", "[S2]"):
            with self.subTest(quote=quote):
                self.assertEqual(sr.evidence_votes([criterion(quote)], lines), {})

    def test_piece_length_boundary(self):
        lines = [_line("1", "Спасибо, хорошего дня"), _line("2", "Понятно")]
        self.assertEqual(sr.evidence_votes([criterion("спасибо")], lines), {})        # 7 знаков
        self.assertEqual(sr.evidence_votes([criterion("хорошего")], lines), {"1": 1})  # 8 знаков

    def test_matching_ignores_case(self):
        # Модель пишет цитату с заглавной, хотя в расшифровке это середина фразы.
        lines = complaint_lines()
        self.assertEqual(sr.evidence_votes([criterion("Сейчас проверю ваш профиль")], lines), {"2": 1})
        self.assertEqual(sr.evidence_votes([criterion("ФОТОКОНТРОЛЬ ПОДТВЕРЖДЁН")], lines), {"2": 1})

    def test_gap_pieces_vote_separately(self):
        lines = complaint_lines()
        for gap in ("...", "…"):
            with self.subTest(gap=gap):
                crits = [criterion(f"я уже третий день не могу выйти на линию {gap} "
                                   "сейчас проверю ваш профиль")]
                self.assertEqual(sr.evidence_votes(crits, lines), {"1": 1, "2": 1})

    def test_short_and_shared_pieces_are_not_evidence(self):
        lines = [_line("1", "Алло, здравствуйте."), _line("2", "Алло, здравствуйте."),
                 _line("1", "Да")]
        crits = [criterion("Алло, здравствуйте."), criterion("Да"), criterion("")]
        self.assertEqual(sr.evidence_votes(crits, lines), {})

    def test_quote_stitched_from_one_voice_across_the_other(self):
        # Модель склеивает реплики одного голоса, пропуская ответ собеседника.
        lines = [_line("1", "Добрый день."), _line("2", "Да."),
                 _line("1", "Меня зовут Алия, звоню из таксопарка.")]
        crits = [criterion("Добрый день. Меня зовут Алия, звоню из таксопарка.")]
        self.assertEqual(sr.evidence_votes(crits, lines), {"1": 1})

    def test_piece_matches_whole_words_only(self):
        lines = [_line("1", "Проверяем профиль водителя"), _line("2", "Хорошо")]
        self.assertEqual(sr.evidence_votes([criterion("еряем профиль водит")], lines), {})


class FallbackTests(unittest.TestCase):
    def test_tie_in_evidence_falls_back_to_introduction(self):
        lines = complaint_lines()
        crits = [criterion("сейчас проверю ваш профиль"),
                 criterion("я уже третий день не могу выйти на линию")]
        self.assertEqual(sr.resolve(lines, crits), {"operator": ["2"], "method": "introduction"})

    def test_tie_from_a_stitched_quote_is_broken_by_criteria(self):
        # Модель цитирует оператора в трёх критериях, водителя — в двух, но одна
        # цитата водителя склеена через «…» и весит вдвое: по кускам ничья 3:3.
        # Самопредставления нет, а водитель говорит чуть больше — раньше карточка
        # отдавала ему синие пузыри.
        lines = [_line("1", "Маған сіздерден пропущенный тұр, қайта звондап тұрмын."),
                 _line("2", "Бұл таксопарктың техникалық көмек көрсететін орталығы."),
                 _line("1", "Иә, түсінбедім, түсінбедім, қайталаңызшы."),
                 _line("2", "Сізге ешкім звондаған жоқ, қате болуы мүмкін."),
                 _line("1", "А-а, солай ма. Жақсы. Сау болыңыздар бәріңіз."),
                 _line("2", "Сау болыңыз, жақсы күн.")]
        crits = [criterion("Бұл таксопарктың техникалық көмек көрсететін орталығы."),
                 criterion("Сізге ешкім звондаған жоқ, қате болуы мүмкін."),
                 criterion("Сау болыңыз, жақсы күн."),
                 criterion("Иә, түсінбедім, түсінбедім."),
                 criterion("Иә, түсінбедім, түсінбедім ... Сау болыңыздар бәріңіз.")]
        self.assertEqual(sr.evidence_votes(crits, lines), {"1": 3, "2": 3})
        self.assertEqual(sr.talk_share(lines).most_common(1)[0][0], "1")
        self.assertEqual(sr.resolve(lines, crits), {"operator": ["2"], "method": "evidence_criteria"})

    def test_introduction_outranks_the_criteria_count(self):
        # По кускам ничья 2:2 (цитата оператора склеена через «…»), по критериям
        # впереди водитель 2:1, а представился оператор. Счёт по критериям первым
        # отдал бы пузыри водителю — так было бы на звонке 7122 из замера.
        lines = [_line("1", "Тест такси сервисі, есімім Алия. Қандай көмек көрсете аламын?"),
                 _line("2", "Сәлеметсіз бе, мен Семейде такси айдаймын, бонус түспей қалды."),
                 _line("1", "Қазір тексеріп көрейін, күте тұрыңыз."),
                 _line("2", "Жарайды, күтемін, рахмет сізге.")]
        crits = [criterion("Тест такси сервисі, есімім Алия ... Қазір тексеріп көрейін, күте тұрыңыз."),
                 criterion("Мен Семейде такси айдаймын, бонус түспей қалды."),
                 criterion("Жарайды, күтемін, рахмет сізге.")]
        self.assertEqual(sr.evidence_votes(crits, lines), {"1": 2, "2": 2})
        self.assertEqual(sr.resolve(lines, crits), {"operator": ["1"], "method": "introduction"})

    def test_tie_narrows_the_choice_to_the_tied_voices(self):
        # Диаризация разбила оператора на голоса 2 и 3, модель цитирует их поровну,
        # а водитель (голос 1) не процитирован, зато говорит больше всех.
        lines = [_line("1", DRIVER_RANT), _line("2", OPERATOR_HOLD), _line("1", DRIVER_MORE),
                 _line("3", "Фотоконтроль подтверждён, хорошего дня")]
        crits = [criterion(OPERATOR_HOLD), criterion("Фотоконтроль подтверждён, хорошего дня")]
        self.assertEqual(sr.talk_share(lines).most_common(1)[0][0], "1")
        self.assertEqual(sr.resolve(lines, crits), {"operator": ["2"], "method": "talk_share"})

    def test_answering_the_phone_is_not_an_introduction(self):
        # Исходящий: клиент снимает трубку и говорит «тыңдап тұрмын» / «слушаю вас»,
        # а представляется оператор.
        lines = [_line("1", "Алло, тыңдап тұрмын."), _line("2", "Сәлеметсіз бе."),
                 _line("1", "Да, слушаю вас."),
                 _line("2", "Мен сізге Яндекс тіркеу орталығынан хабарласып тұрмын.")]
        self.assertEqual(sr.introduction_votes(lines), {"2": 1})
        self.assertEqual(sr.resolve(lines, []), {"operator": ["2"], "method": "introduction"})

    def test_calling_verb_counts_only_next_to_the_company(self):
        # Водитель сам звонит в поддержку и повторяет «звондап жатырмын».
        driver_calls = [_line("1", "Сәлеметсіз бе, қандай көмек көрсете аламын?"),
                        _line("2", "Кешеден бері звондап жатырмын, ешкім көтермейді."),
                        _line("2", "Үшінші рет хабарласып тұрмын.")]
        self.assertEqual(sr.introduction_votes(driver_calls), {"1": 1})
        for phrase in ("Меня зовут Алия, звоню с таксопарка «Тенге».",
                       "Сізге Яндекс Такси таксоплатформасынан звондап отырмын.",
                       "Яндекстің тіркелу бөлімінен хабарласып тұрған едім.",
                       "Центр-регистрация, қалай көмектесе аламын?",
                       "Звоню вам из отдела регистрации.",
                       "Тест такси сервисі, есімім Алия."):
            with self.subTest(phrase=phrase):
                lines = [_line("1", "Алло."), _line("2", phrase)]
                self.assertEqual(sr.introduction_votes(lines), {"2": 1})

    def test_driver_naming_his_city_is_not_an_introduction(self):
        for phrase in ("Здравствуйте, я звоню из города Тараз, приложение не открывается.",
                       "Звоню вам из г. Шымкент по регистрации."):
            with self.subTest(phrase=phrase):
                lines = [_line("2", "Алло, слушаю."), _line("1", phrase)]
                self.assertEqual(sr.introduction_votes(lines), {})

    def test_introduction_is_only_looked_for_at_the_start(self):
        lines = [_line("1", "Алло.")] + [_line("2" if i % 2 else "1", f"реплика {i}") for i in range(6)]
        lines.append(_line("2", "Звоню из таксопарка"))      # восьмая реплика — ещё начало
        self.assertEqual(sr.introduction_votes(lines), {"2": 1})
        lines.insert(1, _line("1", "Ещё реплика"))           # теперь девятая — уже нет
        self.assertEqual(sr.introduction_votes(lines), {})

    def test_last_resort_is_talk_share(self):
        lines = [_line("1", "Короткая реплика."), _line("2", DRIVER_RANT)]
        self.assertEqual(sr.resolve(lines, None), {"operator": ["2"], "method": "talk_share"})

    def test_no_voice_labels_means_no_decision(self):
        self.assertIsNone(sr.resolve([{"speaker": "operator", "seg": [{"t": "x"}]}], []))
        self.assertIsNone(sr.resolve([], []))
        self.assertEqual(sr.resolve([_line(None, "текст без голоса")], []),
                         {"operator": [], "method": "unknown"})


class AssignTests(unittest.TestCase):
    def test_labels_follow_the_decision(self):
        lines = [_line("1", "a", speaker="operator"), _line("2", "b", speaker="client"),
                 _line(None, "c", speaker="client"), {"speaker": "operator", "seg": []}]
        out = sr.assign(lines, {"operator": ["2"], "method": "evidence"})
        self.assertEqual([line["speaker"] for line in out], ["client", "operator", "client", "operator"])
        # Исходные реплики не мутируются: их держит кэш транскрипта.
        self.assertEqual(lines[0]["speaker"], "operator")

    def test_no_roles_keeps_lines(self):
        lines = [_line("1", "a", speaker="operator")]
        self.assertEqual(sr.assign(lines, None), lines)


class SpeakerIdRecoveryTests(unittest.TestCase):
    TOKENS = [{"text": "Ал", "speaker": "1"}, {"text": "ло", "speaker": "1"},
              {"text": " Здравствуйте", "speaker": "2"}, {"text": " Да", "speaker": "1"}]

    def test_old_segments_get_voice_labels_from_tokens(self):
        old = [{"speaker": "client", "seg": [{"t": "Алло"}]},
               {"speaker": "operator", "seg": [{"t": " Здравствуйте"}]},
               {"speaker": "client", "seg": [{"t": " Да"}]}]
        out = sr.with_speaker_ids(old, self.TOKENS)
        self.assertEqual([line["spk"] for line in out], ["1", "2", "1"])
        self.assertEqual([line["speaker"] for line in out], ["client", "operator", "client"])
        self.assertNotIn("spk", old[0])

    def test_mismatched_cut_is_left_alone(self):
        old = [{"speaker": "client", "seg": [{"t": "Алло Здравствуйте Да"}]}]
        self.assertEqual(sr.with_speaker_ids(old, self.TOKENS), old)

    def test_contradicting_old_labels_are_left_alone(self):
        # «Оператор» на двух разных голосах — нарезка не та, метки не навешиваем.
        old = [{"speaker": "operator", "seg": []}, {"speaker": "client", "seg": []},
               {"speaker": "operator", "seg": []}]
        tokens = [{"speaker": "1"}, {"speaker": "2"}, {"speaker": "3"}]
        self.assertEqual(sr.with_speaker_ids(old, tokens), old)

    def test_segments_with_labels_and_chats_pass_through(self):
        new = [_line("1", "a", speaker="operator")]
        self.assertEqual(sr.with_speaker_ids(new, []), new)
        chat = [{"speaker": "operator", "seg": [{"t": "Оператор (Анна): Здравствуйте"}]}]
        self.assertEqual(sr.with_speaker_ids(chat, []), chat)
        self.assertEqual(sr.with_speaker_ids(None, self.TOKENS), [])


class ApiWiringTests(unittest.TestCase):
    CRITS = [criterion("такси сервис, меня зовут Алия"),
             criterion("[S2] Понимаю вас, сейчас проверю ваш профиль")]
    SIDES = ["client", "operator", "client", "operator", "client", "operator"]

    def test_new_transcript_lines_carry_voice_and_provisional_side(self):
        tokens = []
        for spk, text in (("1", "Алло."), ("2", " " + OPERATOR_INTRO), ("1", " " + DRIVER_RANT),
                          ("2", " " + OPERATOR_HOLD), ("1", " " + DRIVER_MORE)):
            tokens.append({"text": text, "speaker": spk, "confidence": 0.95,
                           "start_time_ms": 0, "end_time_ms": 10})
        lines = api._lines_from_tokens(tokens)
        self.assertEqual([line["spk"] for line in lines], ["1", "2", "1", "2", "1"])
        # Оценки ещё нет; водитель говорит больше, но представляется голос 2.
        self.assertEqual([line["speaker"] for line in lines],
                         ["client", "operator", "client", "operator", "client"])

    def _payload(self, kind):
        # Подписи прежней эвристики: водитель (голос 1) говорит больше — «оператор».
        lines = [{**line, "speaker": "operator" if line["spk"] == "1" else "client"}
                 for line in complaint_lines()]
        return {"id": 1, "subject_kind": kind, "transcript": lines, "criteria": list(self.CRITS)}

    def test_card_sides_follow_the_evaluation(self):
        for kind in config.AUDIO_SUBJECT_KINDS:
            with self.subTest(kind=kind):
                payload = api._attach_speaker_roles(self._payload(kind))
                self.assertEqual(payload["speaker_roles"], {"operator": ["2"], "method": "evidence"})
                self.assertEqual([line["speaker"] for line in payload["transcript"]], self.SIDES)

    def test_chat_card_is_untouched(self):
        for kind in config.CHAT_SUBJECT_KINDS:
            with self.subTest(kind=kind):
                payload = self._payload(kind)
                before = [line["speaker"] for line in payload["transcript"]]
                api._attach_speaker_roles(payload)
                self.assertNotIn("speaker_roles", payload)
                self.assertEqual([line["speaker"] for line in payload["transcript"]], before)

    def test_review_payload_applies_sides(self):
        for kind in config.AUDIO_SUBJECT_KINDS:
            with self.subTest(kind=kind), \
                    mock.patch.object(api, "_evaluate_and_cache", return_value=self._payload(kind)), \
                    mock.patch.object(api, "_attach_human_review", side_effect=lambda p, reviewer_id=None: p), \
                    mock.patch.object(api, "_attach_ai_review", side_effect=lambda p: p), \
                    mock.patch.object(api.runtime_store, "distributed_call_lock",
                                      return_value=nullcontext(True)):
                payload = api.review_payload(1, subject_kind=kind)
                self.assertEqual([line["speaker"] for line in payload["transcript"]], self.SIDES)

    def test_cached_transcript_gets_voice_labels(self):
        record = {"id": 5, "transcript_hash": "h" * 64, "text": "[S1] Алло\n[S2] Здравствуйте",
                  "segments": [{"speaker": "client", "seg": [{"t": "Алло"}]},
                               {"speaker": "operator", "seg": [{"t": " Здравствуйте"}]}],
                  "tokens": [{"text": "Алло", "speaker": "1"},
                             {"text": " Здравствуйте", "speaker": "2"}]}
        subject = {"id": 9, "audio_path": "bucket/a.mp3", "kind": config.SUBJECT_IMPORTED_CALL}
        with mock.patch.object(api, "_audio_object_fingerprint", return_value="f" * 64), \
                mock.patch.object(api.runtime_store, "asr_config", return_value={}), \
                mock.patch.object(api.runtime_store, "get_transcript", return_value=record):
            source = api._resolve_call_source(subject, "model")
        self.assertEqual([line["spk"] for line in source["lines"]], ["1", "2"])

    def test_card_from_saved_run_with_old_transcript(self):
        # Самый частый путь: карточка открывается из сохранённого прогона, а его
        # расшифровка записана до 05.10.2026 — в репликах только подпись старой
        # эвристики, где водитель (голос 1) «оператор». Проходим настоящую ветку
        # кэша _evaluate_and_cache; заглушены только база, хранилище и модель.
        old_lines = [{"speaker": "operator" if line["spk"] == "1" else "client", "seg": line["seg"]}
                     for line in complaint_lines()]
        tokens = [{"text": line["seg"][0]["t"], "speaker": line["spk"]} for line in complaint_lines()]
        record = {"id": 5, "transcript_hash": "h" * 64, "text": "[S1] …", "segments": old_lines,
                  "tokens": tokens, "languages": {}, "mean_conf": 0.9, "low_conf_spans": []}
        snapshot = {"id": 1, "content_hash": "c", "knowledge_revision": 1}
        conn = mock.MagicMock()
        conn.__enter__.return_value = conn
        for kind in config.AUDIO_SUBJECT_KINDS:
            subject = {"id": 9, "kind": kind, "audio_path": "bucket/a.mp3", "direction_id": 73,
                       "operator": "Алия", "datetime": None}
            run = {"id": "run-1", "is_latest": True, "transcript_cache_id": 5,
                   "evaluation_fingerprint": "fp",
                   "payload": {"id": 9, "subject_kind": kind, "criteria": list(self.CRITS)}}
            patches = [
                mock.patch.object(api.subjects_mod, "load", return_value=subject),
                mock.patch.object(api.subjects_mod, "require_evaluable"),
                mock.patch.object(api.subjects_mod, "eligibility", return_value={"detail": None}),
                mock.patch.object(api, "_audio_object_fingerprint", return_value="f" * 64),
                mock.patch.object(api.runtime_store, "asr_config", return_value={}),
                mock.patch.object(api.runtime_store, "get_transcript", return_value=record),
                mock.patch.object(api.runtime_store, "get_transcript_by_id", return_value=record),
                mock.patch.object(api.runtime_store, "get_cached_evaluation", return_value=run),
                mock.patch.object(api.criteria_mod, "load_direction",
                                  return_value={"id": 73, "criteria": [], "scale_hash": "s"}),
                mock.patch.object(api.cc, "apply_to_direction"),
                mock.patch.object(api, "_direction_department_code", return_value="op"),
                mock.patch.object(api.config, "connect_rw", return_value=conn),
                mock.patch("call_qa.rag.knowledge.ensure_knowledge_context",
                           return_value={"scale_revision_id": 1, "snapshot": snapshot}),
                mock.patch.object(api, "_rag_rollout",
                                  return_value={"rag_enabled": False, "shadow_enabled": False}),
                mock.patch.object(api, "_evaluation_identity",
                                  return_value=("fp", {"call_end_party": "unknown"}, {})),
                mock.patch.object(api, "_hydrate_cached_card_binding", return_value=True),
                mock.patch.object(api, "_normalise_legacy_ai_verdicts", return_value=False),
                mock.patch.object(api, "_cache_get", return_value={"cached": True}),
                mock.patch.object(api, "_signed_url", return_value=None),
                mock.patch.object(api, "_attach_human_review", side_effect=lambda p, reviewer_id=None: p),
                mock.patch.object(api, "_attach_ai_review", side_effect=lambda p: p),
                mock.patch.object(api.evaluator, "evaluate", side_effect=AssertionError("без переоценки")),
                mock.patch.object(api.runtime_store, "distributed_call_lock",
                                  return_value=nullcontext(True)),
            ]
            with self.subTest(kind=kind), ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                payload = api.review_payload(9, subject_kind=kind)
                self.assertTrue(payload["_cached"])
                self.assertEqual(payload["speaker_roles"], {"operator": ["2"], "method": "evidence"})
                self.assertEqual([line["speaker"] for line in payload["transcript"]], self.SIDES)


if __name__ == "__main__":
    unittest.main()
