# -*- coding: utf-8 -*-
"""Проводка раздела «Воронка ОП»: меню, гарды, роуты, схема.

Тест читает исходники как ТЕКСТ. Это не каприз: в проекте уже случалось, что
предикат доступа возвращал true, бэкенд отдавал данные, раздел открывался прямым
адресом — а пункта в меню не было, и снаружи это выглядело как «доступ не
выдаётся». Гейт доступа и гейт видимости живут в разных местах, и связывает их
только такая проверка.

Ловушки, которые здесь закреплены:

* пункт меню объявлен РОВНО ОДИН раз и в общей части меню — как «Касания»,
  «Обращения», «Посылки». Дубль сломал бы счётчики соседних тестов, а объявление
  только в админской ветке спрятало бы раздел от главы отдела продаж
  (у него `isAdminLikeRole` ложно);
* раздел записан и в `SIDEBAR_SECTION_DEPARTMENTS`, и обёрнут `SidebarDeptScope` —
  эти два множества сверяются в обе стороны, и одна половина без другой падает;
* есть строка обхода в гарде видимости: у отдела продаж свой allowlist, и без
  обхода главу и СВ выбрасывало бы из раздела сразу после входа;
* есть строка допуска по URL — иначе Ctrl-клик по пункту открывал бы новую
  вкладку не туда.
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JSX = os.path.join(ROOT, 'src', 'App.jsx')
BOT = os.path.join(ROOT, 'bot_schedule2.py')
DATABASE = os.path.join(ROOT, 'database.py')

VIEW = 'op_funnel'


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


class SidebarWiringTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = read(APP_JSX)

    def test_компонент_подключён_лениво(self):
        self.assertIn("lazyWithRetry(() => import('./components/op_funnel/OpFunnelView'))",
                      self.app)

    def test_пункт_меню_объявлен_ровно_один_раз(self):
        # Больше одного — сломаются счётчики соседних разделов; ноль — раздел
        # открывается только прямым адресом.
        found = self.app.count("handleSidebarViewNavigation(e, '%s')" % VIEW)
        self.assertEqual(found, 1, 'пункт меню должен быть объявлен один раз')

    def test_подпись_пункта_на_месте(self):
        self.assertIn('<span className="sidebar-text">Воронка ОП</span>', self.app)

    def test_раздел_привязан_к_отделу_продаж(self):
        self.assertIn("%s: ['op']," % VIEW, self.app)

    def test_пункт_обёрнут_фильтром_отдела(self):
        # `SIDEBAR_SECTION_DEPARTMENTS` и `SidebarDeptScope` сверяются в обе
        # стороны: одна половина без другой роняет tests/sidebar_department_filter.
        self.assertIn('<SidebarDeptScope section="%s"' % VIEW, self.app)

    def test_предикат_доступа_объявлен_и_использован(self):
        self.assertIn('const canAccessOpFunnelSectionForUser = (userLike) => {', self.app)
        self.assertIn('const canAccessOpFunnelSection = canAccessOpFunnelSectionForUser(user);',
                      self.app)

    def test_гард_видимости_пропускает_раздел(self):
        # Без этой строки главу и СВ отдела продаж выбрасывает в «Учет
        # сотрудников» сразу после открытия: у ОП есть DEPARTMENT_VIEW_ALLOWLIST.
        self.assertIn("if (view === '%s' && canAccessOpFunnelSection) return;" % VIEW, self.app)

    def test_раздел_открывается_прямым_адресом(self):
        self.assertIn("(requestedViewFromUrl !== '%s' || canAccessOpFunnelSection)" % VIEW,
                      self.app)

    def test_экран_рендерится(self):
        self.assertIn('{view === "%s" && canAccessOpFunnelSection && (' % VIEW, self.app)
        self.assertIn('<OpFunnelView', self.app)

    def test_экрану_передан_тост_пропсом(self):
        # window.showToast в основном бандле никто не присваивает — тост через
        # него ушёл бы в никуда.
        block = self.app.split('{view === "%s" && canAccessOpFunnelSection && (' % VIEW, 1)[1]
        block = block[:800]
        for prop in ('apiBaseUrl={API_BASE_URL}', 'withAccessTokenHeader={withAccessTokenHeader}',
                     'showToast={showToast}'):
            self.assertIn(prop, block, prop)

    def test_флаг_попал_в_зависимости_меню(self):
        # Иначе пункт не перерисуется при смене пользователя и роли.
        tree = self.app.split('const sidebarTree = useMemo(', 1)
        self.assertEqual(len(tree), 2, 'не нашлось объявление sidebarTree')
        self.assertIn('canAccessOpFunnelSection,', tree[1])

    def test_пункт_объявлен_в_общей_части_меню(self):
        """Не внутри админской ветки: у главы отдела isAdminLikeRole ложно.

        Проверяем так же, как это делает тест «Бота опозданий»: считаем, что
        пункт стоит рядом с «Касаниями», у которых аудитория та же.
        """
        touches = self.app.index("handleSidebarViewNavigation(e, 'touches')")
        funnel = self.app.index("handleSidebarViewNavigation(e, '%s')" % VIEW)
        self.assertLess(touches, funnel, 'пункт ожидается сразу после «Касаний»')
        between = self.app[touches:funnel]
        self.assertNotIn('{isAdminLikeRole && (', between,
                         'между «Касаниями» и «Воронкой ОП» открылась ролевая ветка — '
                         'пункт уедет из общей части меню')


class BackendWiringTests(unittest.TestCase):

    def test_blueprint_зарегистрирован(self):
        bot = read(BOT)
        self.assertIn('from op_funnel.routes import build_op_funnel_blueprint', bot)
        self.assertIn('app.register_blueprint(build_op_funnel_blueprint(', bot)
        # Регистрация обёрнута try/except: упавший раздел не должен ронять портал.
        block = bot.split('from op_funnel.routes import build_op_funnel_blueprint', 1)[0]
        self.assertTrue(block.rstrip().endswith('try:'),
                        'регистрация должна стоять внутри try/except, как у соседей')

    def test_схема_разворачивается_при_старте(self):
        database = read(DATABASE)
        self.assertIn('self._init_op_funnel_schema_tx(cursor)', database)
        self.assertIn('def _init_op_funnel_schema_tx(self, cursor):', database)
        # SAVEPOINT — чтобы падение схемы раздела не уронило инициализацию базы.
        method = database.split('def _init_op_funnel_schema_tx(self, cursor):', 1)[1][:1500]
        self.assertIn('SAVEPOINT op_funnel_schema', method)
        self.assertIn('ROLLBACK TO SAVEPOINT op_funnel_schema', method)
        self.assertIn('RELEASE SAVEPOINT op_funnel_schema', method)

    def test_ночная_выгрузка_заведена(self):
        bot = read(BOT)
        self.assertIn('async def op_funnel_sync_job():', bot)
        self.assertIn("id='op_funnel_sync'", bot)
        # Свой пул на одно место: ночной обход не должен занимать общий.
        self.assertIn("op_funnel_pool = ThreadPoolExecutor(max_workers=1", bot)

    def test_все_роуты_под_гейтом_доступа(self):
        """Каждая ручка раздела обязана проходить общий каркас с проверкой прав.

        Проверяем, что роуты объявлены ТОЛЬКО через свой декоратор: обычный
        `@bp.route` в обход каркаса означал бы ручку без проверки доступа.
        """
        routes = read(os.path.join(ROOT, 'op_funnel', 'routes.py'))
        inside_factory = routes.split('def build_op_funnel_blueprint(', 1)[1]
        # Единственный прямой @bp.route — внутри самого декоратора funnel_route.
        direct = re.findall(r'@bp\.route\(', inside_factory)
        self.assertEqual(len(direct), 1,
                         'роуты должны объявляться только через funnel_route')
        self.assertIn('access.can_open_section(ctx)', inside_factory)
        self.assertIn('OP_FUNNEL_SECTION_CLOSED', inside_factory)
        self.assertIn('schema.schema_is_ready(cursor)', inside_factory)


class DirectionScopeTests(unittest.TestCase):
    """Сужение по направлению делает роут, а не фронт."""

    def test_каждая_ручка_с_направлением_проверяет_право(self):
        routes = read(os.path.join(ROOT, 'op_funnel', 'routes.py'))
        self.assertIn('access.can_see_direction(ctx, code)', routes)
        # Неизвестный код — отказ, а не «покажем всё».
        self.assertIn('OP_FUNNEL_BAD_DIRECTION', routes)
        self.assertIn('OP_FUNNEL_DIRECTION_FORBIDDEN', routes)

    def test_перечитывание_зафиксированного_только_руководителю(self):
        routes = read(os.path.join(ROOT, 'op_funnel', 'routes.py'))
        self.assertIn('OP_FUNNEL_FORCE_FORBIDDEN', routes)


if __name__ == '__main__':
    unittest.main()
