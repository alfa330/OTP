# -*- coding: utf-8 -*-
"""Ручка справочника «Классификатора авто» для экрана (wiki/routes_classifier.py).

Ответ — на настоящем справочнике (wiki/data/classifier-data.json). Сама ручка —
на тестовом Flask с упрощённым wiki_route: курсор и контекст подставлены, периметр
чтения подменён. Без базы.
"""

import unittest
from unittest.mock import MagicMock, patch

from wiki import classifier
from wiki import classifier_source as source
from wiki import routes_classifier as routes

try:
    from flask import Blueprint, Flask
except ImportError:  # pragma: no cover
    Flask = None

DATA = classifier.read_data()
DIGEST = source.fingerprint(DATA)


class PayloadTest(unittest.TestCase):
    def payload(self, city_id):
        return routes.payload(DATA, city_id, digest=DIGEST, origin='file')

    def test_almaty(self):
        body = self.payload('almaty')
        self.assertEqual(body['fingerprint'], DIGEST)
        self.assertEqual(body['source']['updated'], DATA['source']['updated'])
        self.assertEqual(len(body['cities']), len(DATA['cities']))
        self.assertEqual(len(body['cars']), len(DATA['cars']))
        tariffs = body['city']['tariffs']
        self.assertEqual(list(tariffs), ['econom', 'intercity', 'comfort', 'comfort_plus', 'business',
                                         'electro', 'ultima'])
        intercity = tariffs['intercity']
        self.assertEqual((intercity['kind'], intercity['other']), ('list', [1999]))
        self.assertIn('LADA (ВАЗ)', intercity['brands'])
        self.assertEqual(intercity['count'], sum(1 for row in intercity['rows'] if row[1] != 0))
        self.assertTrue(any('минивен' in note for note in intercity['notes']))
        self.assertEqual(body['city']['url'], source.city_url('almaty'))

    def test_city_with_a_rule_only(self):
        tariffs = self.payload('aksay')['city']['tariffs']
        self.assertEqual(list(tariffs), ['econom'])
        self.assertEqual((tariffs['econom']['kind'], tariffs['econom']['any']), ('any', 1988))
        self.assertNotIn('rows', tariffs['econom'])

    def test_unknown_city(self):
        self.assertIsNone(routes.city_payload(DATA, 'petropavlovsk-2'))

    def test_cities_list_their_tariffs(self):
        cities = {city['id']: city for city in self.payload('almaty')['cities']}
        self.assertEqual(cities['aksay']['tariffs'], ['econom'])
        self.assertEqual(cities['aktau']['tariffs'], ['econom', 'intercity', 'comfort', 'comfort_plus'])

    def test_upcoming_rules_are_served(self):
        data = dict(DATA, cities=[dict(city, tariffs=dict(city['tariffs'])) for city in DATA['cities']])
        aksay = next(city for city in data['cities'] if city['id'] == 'aksay')
        aksay['tariffs']['econom'] = dict(aksay['tariffs']['econom'],
                                          next={'any': 2000, 'from': '2027-01-01'})
        econom = routes.city_payload(data, 'aksay')['tariffs']['econom']
        self.assertEqual(econom['next'], {'notes': [], 'kind': 'any', 'any': 2000, 'from': '2027-01-01'})


@unittest.skipIf(Flask is None, 'Flask не установлен')
class RouteTest(unittest.TestCase):
    def setUp(self):
        self.cursor = MagicMock()
        self.cursor.fetchone.return_value = (36,)
        app, bp = Flask(__name__), Blueprint('wiki', __name__)

        def wiki_route(rule, methods=('GET',), **_extra):
            def decorator(handler):
                @bp.route(rule, methods=list(methods), endpoint=handler.__name__)
                def wrapper(**kwargs):
                    return handler(cursor=self.cursor, ctx={'user_id': 1}, **kwargs)
                return wrapper
            return decorator

        routes.register(bp, wiki_route, db=None, log_ip=lambda: None)
        app.register_blueprint(bp, url_prefix='/api/wiki')
        self.client = app.test_client()
        self.patches = [
            patch.object(routes.perimeter, 'read_perimeter', return_value=(set(), set(), {36, 40})),
            patch.object(routes.wiki_classifier, 'current',
                         return_value=(None, DATA, DIGEST, 'snapshot', {'checked_at': '2026-11-16T05:40:00'})),
        ]
        self.perimeter, self.current = [item.start() for item in self.patches]

    def tearDown(self):
        for item in self.patches:
            item.stop()

    def test_city_from_the_query(self):
        response = self.client.get('/api/wiki/classifier?city=aktau')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual((body['city']['id'], body['source']['origin'], body['source']['checked_at']),
                         ('aktau', 'snapshot', '2026-11-16T05:40:00'))
        self.assertEqual(response.headers['ETag'], '"%s:aktau"' % DIGEST[:16])
        self.cursor.execute.assert_called_once_with(
            'SELECT id FROM wiki_articles WHERE slug = %s', ('klassifikator-avto',))

    def test_default_and_unknown_city_are_almaty(self):
        self.assertEqual(self.client.get('/api/wiki/classifier').get_json()['city']['id'], 'almaty')
        self.assertEqual(self.client.get('/api/wiki/classifier?city=nowhere').get_json()['city']['id'],
                         'almaty')

    def test_not_modified(self):
        etag = self.client.get('/api/wiki/classifier?city=semey').headers['ETag']
        again = self.client.get('/api/wiki/classifier?city=semey', headers={'If-None-Match': etag})
        self.assertEqual((again.status_code, again.data), (304, b''))
        other = self.client.get('/api/wiki/classifier?city=aksay', headers={'If-None-Match': etag})
        self.assertEqual(other.status_code, 200)

    def test_hidden_article_is_404(self):
        self.perimeter.return_value = (set(), set(), {40})
        response = self.client.get('/api/wiki/classifier?city=almaty')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['code'], 'WIKI_CLASSIFIER_HIDDEN')
        self.current.assert_not_called()                     # данные не читались вовсе

    def test_missing_article_is_404(self):
        self.cursor.fetchone.return_value = None
        self.assertEqual(self.client.get('/api/wiki/classifier').status_code, 404)

    def test_book_without_data_is_503(self):
        self.current.return_value = (None, None, None, None, None)
        response = self.client.get('/api/wiki/classifier')
        self.assertEqual((response.status_code, response.get_json()['code']),
                         (503, 'WIKI_CLASSIFIER_EMPTY'))


class RegistrationTest(unittest.TestCase):
    def test_the_route_is_registered_in_the_wiki_blueprint(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / 'wiki' / 'routes.py').read_text(encoding='utf-8')
        self.assertIn('routes_classifier.register(bp, wiki_route, db, _ip)', text)


if __name__ == '__main__':
    unittest.main()
