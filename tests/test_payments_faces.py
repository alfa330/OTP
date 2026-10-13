# -*- coding: utf-8 -*-
"""Фото людей на карточках досок «Оплаты счетов».

Просьба владельца 13.10.2026: «сделай, чтобы аватарки в разделе отображались корректно».
На карточках досок стояли серые кружки с инициалами: раздел отдавал о людях только имена,
а фото из профиля — нет (в «Задачах» на тех же местах стоят фото).

Что проверяется:
* чьи фото нужны карточке и что уходит наружу (payments/people.py) — в том числе правило
  п. 9 ТЗ: кому имена бухгалтерии и финансового отдела не положены, тот не получает и фото;
* выборка мест хранения фото одной командой (queries.avatar_refs);
* проводка: GET /api/payments/boards/<код> отдаёт ссылки одним списком на ответ (`photos`),
  карточки называют людей по номерам; каждый человек подписывается один раз, доска
  переживает отказ подписи и молчит без подписчика;
* монолит действительно передаёт разделу свой подписчик ссылок.

База не нужна: запросы подменены unittest.mock, как в test_payments_closing_wiring.py.
"""

import os
import unittest
from unittest import mock

from flask import Flask

from payments import access, people, privacy, queries as real_queries, routes, workflow as wf

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
INITIATOR, MANAGER, APPROVER, ACCOUNTANT = 10, 11, 12, 30
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def request_row(request_id, **overrides):
    row = {
        'id': request_id, 'status': 'active', 'stage': 'approval', 'request_kind': 'purchase',
        'payment_method': 'invoice', 'amount': 15000, 'expense_name': 'Картриджи',
        'initiator_id': INITIATOR, 'initiator_name': 'Иванова Айгерим',
    }
    row.update(overrides)
    return row


def subtask(kind, state, **overrides):
    """Подзадача, как её отдаёт queries.subtasks_for_requests (название и роль — из workflow)."""
    definition = wf.subtask(kind)
    item = {
        'kind': kind, 'title': definition['title'], 'state': state, 'outcome': None, 'status': 'new',
        'role_code': definition['role'], 'role_label': wf.ROLE_LABELS[definition['role']],
        'assignee_id': None, 'assignee_name': None, 'done_by': None, 'done_by_name': None, 'done_at': None,
    }
    item.update(overrides)
    return item


class WhoIsOnTheCardTests(unittest.TestCase):
    """people.py: чьи лица у карточки и что из них уходит наружу."""

    def test_initiator_and_the_person_of_the_stage(self):
        focus = subtask('manager_approval', 'open', assignee_id=MANAGER, assignee_name='Петров Данияр')
        self.assertEqual(people.face_ids(request_row(1), focus), {INITIATOR, MANAGER})
        photos = {INITIATOR: 'u-init', MANAGER: 'u-manager'}
        self.assertEqual(people.card_photos(request_row(1), focus, photos),
                         {'initiator': INITIATOR, 'assignee': MANAGER, 'done_by': None})

    def test_finished_stage_shows_who_closed_it(self):
        focus = subtask('approval', 'done', done_by=APPROVER, done_by_name='Карабек Ержан')
        self.assertEqual(people.face_ids(request_row(1), focus), {INITIATOR, APPROVER})
        # У инициатора фото нет — его номер карточке не нужен: она нарисует инициалы.
        self.assertEqual(people.card_photos(request_row(1), focus, {APPROVER: 'u-approver'}),
                         {'initiator': None, 'assignee': None, 'done_by': APPROVER})

    def test_task_of_a_department_has_no_face(self):
        """Задача подразделения сотруднику не назначена — лица у этапа нет, только инициатор."""
        focus = subtask('invoice_payment', 'open')
        self.assertEqual(people.face_ids(request_row(1), focus), {INITIATOR})
        self.assertEqual(people.card_photos(request_row(1), focus, {INITIATOR: 'u-init'}),
                         {'initiator': INITIATOR, 'assignee': None, 'done_by': None})

    def test_department_people_stay_hidden_from_outsiders(self):
        """П. 9 ТЗ: инициатор не должен знать, кто именно в бухгалтерии провёл платёж, — фото
        выдало бы человека не хуже фамилии."""
        for kind in privacy.DEPARTMENT_KINDS:
            focus = subtask(kind, 'done', assignee_id=ACCOUNTANT, done_by=ACCOUNTANT, done_by_name='Сотрудник отдела')
            photos = {INITIATOR: 'u-init', ACCOUNTANT: 'u-accountant'}
            # Своим и администратору — как есть.
            self.assertEqual(people.card_photos(request_row(1), focus, photos)['done_by'], ACCOUNTANT, kind)
            # Остальным — ни в выборку, ни в карточку (даже если ссылка откуда-то нашлась).
            self.assertEqual(people.face_ids(request_row(1), focus, hide_people=True), {INITIATOR}, kind)
            self.assertEqual(people.card_photos(request_row(1), focus, photos, hide_people=True),
                             {'initiator': INITIATOR, 'assignee': None, 'done_by': None}, kind)

    def test_approvers_are_known_to_everyone(self):
        """Согласующие — люди заявки, их знают все участники: скрытие имён подразделений их не касается."""
        for kind in wf.APPROVAL_KINDS:
            self.assertNotIn(kind, privacy.DEPARTMENT_KINDS)
            focus = subtask(kind, 'open', assignee_id=MANAGER, assignee_name='Петров Данияр')
            self.assertEqual(people.card_photos(request_row(1), focus, {MANAGER: 'u-manager'}, hide_people=True)['assignee'],
                             MANAGER, kind)

    def test_hidden_names_never_keep_a_photo(self):
        """Когда privacy уже заменил человека подразделением (стол, карточка заявки), номеров людей
        в подзадаче нет — и фото взять не с кого."""
        focus = subtask('invoice_payment', 'done', done_by=ACCOUNTANT, done_by_name='Сотрудник отдела')
        privacy.hide_in_subtask(focus, 'invoice')
        self.assertEqual(focus['done_by_name'], 'Бухгалтерия')
        self.assertEqual(people.face_ids(request_row(1), focus), {INITIATOR})
        self.assertIsNone(people.card_photos(request_row(1), focus, {ACCOUNTANT: 'u-accountant'})['done_by'])

    def test_no_stage_and_odd_values(self):
        self.assertEqual(people.face_ids(request_row(1), None), {INITIATOR})
        self.assertEqual(people.face_ids({}, None), set())
        self.assertEqual(people.face_ids(None, None), set())
        self.assertEqual(people.stage_people({'kind': 'approval', 'assignee_id': '11', 'done_by': 'нет'}),
                         {'assignee': 11, 'done_by': None})
        self.assertEqual(people.card_photos(request_row(1), None, None),
                         {'initiator': None, 'assignee': None, 'done_by': None})


class AvatarRefsQueryTests(unittest.TestCase):
    """queries.avatar_refs: одна выборка на весь список людей."""

    def test_nobody_asked_no_query(self):
        cursor = mock.MagicMock()
        self.assertEqual(real_queries.avatar_refs(cursor, []), {})
        self.assertEqual(real_queries.avatar_refs(cursor, {None}), {})
        cursor.execute.assert_not_called()

    def test_one_query_for_everybody(self):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = [(INITIATOR, 'bucket', 'avatars/10.jpg'), (MANAGER, 'bucket', 'avatars/11.png')]
        refs = real_queries.avatar_refs(cursor, [MANAGER, INITIATOR, MANAGER, None, '12'])
        self.assertEqual(refs, {INITIATOR: ('bucket', 'avatars/10.jpg'), MANAGER: ('bucket', 'avatars/11.png')})
        cursor.execute.assert_called_once()
        sql, params = cursor.execute.call_args[0]
        self.assertEqual(params, ([INITIATOR, MANAGER, 12],))
        self.assertIn('FROM users', sql)
        self.assertIn('id = ANY(%s)', sql)
        # У кого фото нет, тот в ответ не попадает — карточка нарисует инициалы.
        self.assertIn('avatar_bucket IS NOT NULL', sql)
        self.assertIn('avatar_blob_path IS NOT NULL', sql)


class BoardCardsCarryPhotosTests(unittest.TestCase):
    """GET /api/payments/boards/<код>: ссылки на фото в карточках."""

    def board(self, code, pairs, *, refs=None, signer='default', sees_people=None, viewer=ADMIN):
        queries = mock.MagicMock()
        queries.load_access_context.return_value = {'user_id': viewer, 'name': 'Смотрящий', 'role': 'admin'}
        queries.roles_of_user.return_value = set()
        queries.is_manager_somewhere.return_value = False
        queries.board_requests.return_value = pairs
        queries.role_member_ids.return_value = {}
        queries.duplicate_request_ids.return_value = set()
        queries.avatar_refs.return_value = refs or {}
        patches = [mock.patch.object(routes, 'queries', queries)]
        if sees_people is not None:
            patches.append(mock.patch.object(routes.access, 'sees_department_people', return_value=sees_people))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.signed = []

        def sign(bucket, blob_path):
            self.signed.append((bucket, blob_path))
            return 'https://photos.example/%s/%s' % (bucket, blob_path)

        deps = {}
        if signer == 'default':
            deps['avatar_url'] = sign
        elif signer is not None:
            deps['avatar_url'] = signer
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (viewer, None, None), **deps))
        response = app.test_client().get('/api/payments/boards/%s' % code)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.photos = body['photos']
        self.raw = response.get_data(as_text=True)
        cards = {card['id']: card for column in body['columns'] for card in column['cards']}
        return cards, queries

    def photo(self, key):
        """Ссылка на фото по номеру из карточки — как её находит интерфейс."""
        return None if key is None else self.photos.get(str(key))

    def approval_pairs(self):
        waiting = [subtask('manager_approval', 'open', assignee_id=MANAGER, assignee_name='Петров Данияр')]
        approved = [subtask('manager_approval', 'done', assignee_id=MANAGER, assignee_name='Петров Данияр',
                            done_by=MANAGER, done_by_name='Петров Данияр', outcome='approved'),
                    subtask('approval', 'done', done_by=APPROVER, done_by_name='Карабек Ержан', outcome='approved')]
        return [(request_row(1), waiting), (request_row(2, stage='payment'), approved)]

    def test_cards_get_the_photos_of_their_people(self):
        refs = {INITIATOR: ('bucket', 'avatars/10.jpg'), MANAGER: ('bucket', 'avatars/11.png')}
        cards, queries = self.board('approval', self.approval_pairs(), refs=refs)
        self.assertEqual(set(cards), {1, 2})
        # Ссылки — одним списком на ответ, по номеру человека.
        self.assertEqual(self.photos, {str(INITIATOR): 'https://photos.example/bucket/avatars/10.jpg',
                                       str(MANAGER): 'https://photos.example/bucket/avatars/11.png'})
        # Заявка у руководителя: инициатор и согласующий — с фото.
        self.assertEqual(self.photo(cards[1]['initiator_photo']), 'https://photos.example/bucket/avatars/10.jpg')
        self.assertEqual(self.photo(cards[1]['subtask']['assignee_photo']), 'https://photos.example/bucket/avatars/11.png')
        self.assertIsNone(cards[1]['subtask']['done_by_photo'])
        # Согласованная: у того, кто согласовал, фото в профиле нет — номера нет, имя на месте.
        self.assertEqual(cards[2]['subtask']['done_by_name'], 'Карабек Ержан')
        self.assertIsNone(cards[2]['subtask']['done_by_photo'])
        self.assertEqual(cards[2]['initiator_photo'], cards[1]['initiator_photo'])
        # Инициатор стоит на обеих карточках, а его ссылка в ответе одна: не по ссылке на карточку.
        self.assertEqual(self.raw.count('avatars/10.jpg'), 1)
        # Одна выборка на всю доску — по всем людям карточек сразу.
        queries.avatar_refs.assert_called_once()
        self.assertEqual(queries.avatar_refs.call_args[0][1], {INITIATOR, MANAGER, APPROVER})
        # Подпись — по разу на человека, а не на карточку: инициатор стоит на обеих.
        self.assertEqual(sorted(self.signed), [('bucket', 'avatars/10.jpg'), ('bucket', 'avatars/11.png')])

    def test_board_survives_a_failed_signature(self):
        def broken(bucket, blob_path):
            raise RuntimeError('хранилище не отвечает')

        refs = {INITIATOR: ('bucket', 'avatars/10.jpg')}
        with self.assertLogs(level='ERROR'):
            cards, _queries = self.board('approval', self.approval_pairs(), refs=refs, signer=broken)
        self.assertEqual(set(cards), {1, 2})
        self.assertEqual(self.photos, {})
        self.assertIsNone(cards[1]['initiator_photo'])
        self.assertEqual(cards[1]['initiator_name'], 'Иванова Айгерим')

    def test_signer_may_answer_nothing(self):
        refs = {INITIATOR: ('bucket', 'avatars/10.jpg')}
        cards, _queries = self.board('approval', self.approval_pairs(), refs=refs, signer=lambda bucket, blob: None)
        self.assertEqual(self.photos, {})
        self.assertIsNone(cards[1]['initiator_photo'])

    def test_without_a_signer_nothing_is_read(self):
        """Тесты и стенды без монолита: фото просто нет, лишней выборки тоже."""
        cards, queries = self.board('approval', self.approval_pairs(), signer=None)
        queries.avatar_refs.assert_not_called()
        self.assertEqual(self.photos, {})
        self.assertIn('initiator_photo', cards[1])
        self.assertIsNone(cards[1]['initiator_photo'])
        self.assertIsNone(cards[1]['subtask']['assignee_photo'])

    def test_accountant_photo_goes_only_to_those_who_see_names(self):
        paid = [subtask('manager_approval', 'done', done_by=MANAGER, done_by_name='Петров Данияр', outcome='approved'),
                subtask('invoice_payment', 'done', done_by=ACCOUNTANT, done_by_name='Сотрудник отдела')]
        pairs = [(request_row(3, stage='receiving', paid_on='2026-10-13', paid_amount=15000), paid)]
        refs = {INITIATOR: ('bucket', 'avatars/10.jpg'), ACCOUNTANT: ('bucket', 'avatars/30.jpg')}
        # Администратор и сама бухгалтерия видят, кто оплатил, — и его фото.
        cards, queries = self.board('accounting', pairs, refs=refs)
        self.assertEqual(cards[3]['subtask']['kind'], 'invoice_payment')
        self.assertEqual(self.photo(cards[3]['subtask']['done_by_photo']), 'https://photos.example/bucket/avatars/30.jpg')
        self.assertEqual(queries.avatar_refs.call_args[0][1], {INITIATOR, ACCOUNTANT})
        # Тот, кому имена подразделения не положены, не получает ни фото, ни номера человека, ни даже
        # выборки по нему. (Выборка здесь подменена и отдаёт бухгалтера всё равно — как если бы
        # его фото попало в список по другой карточке: в эту карточку номер попасть не должен.)
        cards, queries = self.board('accounting', pairs, refs=refs, sees_people=False)
        self.assertIsNone(cards[3]['subtask']['done_by_photo'])
        self.assertIsNone(cards[3]['subtask']['assignee_photo'])
        self.assertEqual(self.photo(cards[3]['initiator_photo']), 'https://photos.example/bucket/avatars/10.jpg')
        self.assertEqual(queries.avatar_refs.call_args[0][1], {INITIATOR})


class MonolithWiringTests(unittest.TestCase):
    """Монолит отдаёт разделу свой подписчик ссылок — тот же, что у фото в «Задачах»."""

    def test_blueprint_receives_the_signer(self):
        with open(os.path.join(ROOT, 'bot_schedule2.py'), encoding='utf-8') as handle:
            source = handle.read()
        start = source.index('app.register_blueprint(build_payments_blueprint(')
        call = source[start:source.index('logging.info("Раздел «Оплата счетов»: Blueprint подключён', start)]
        self.assertIn('avatar_url=_build_avatar_signed_url,', call)
        self.assertLess(source.index('def _build_avatar_signed_url('), start, 'подписчик объявлен раньше подключения раздела')

    def test_desk_rows_do_not_carry_photos(self):
        """Строки «Моих задач» лиц не рисуют — и ссылок на фото в них нет: лишнего наружу не отдаём."""
        with open(os.path.join(ROOT, 'payments', 'routes.py'), encoding='utf-8') as handle:
            source = handle.read()
        desk = source[source.index("@payments_route('/desk')"):source.index("@payments_route('/history')")]
        self.assertNotIn('_with_faces', desk)
        self.assertNotIn('avatar', desk)


if __name__ == '__main__':
    unittest.main()
