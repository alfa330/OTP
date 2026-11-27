import React from 'react';
import ResourceFteView from './ResourceFteView';

/*
  Расчет ресурсов · Основа ОП — тонкая обёртка над общим разделом (IC-1, #470).

  Решение владельца 27.11.2026: «переиспользуй раздел расчет ресурсов — линия СЗоВ, не
  нужно придумывать и делать все по новому, и распределения смен тоже по тому же
  алгоритму». Поэтому здесь, как у чата, нет своей витрины: всё живёт в ResourceFteView,
  а направление выбирается пропом. Различия (вкладки, вводные, подписи, ручки
  `/api/resource_fte/op`) — в OP_DIRECTION того же файла; для линии СЗоВ ни одно условие
  не меняется.

  Модель — из файла постановщика (руководство ОП, «Erlang_C_.xlsx»): входящие по
  Erlang C под SL 80/20 плюс нагрузка своих исходящих, пул общий. Считает сервер —
  resource_fte/op_osnova.py. Смены строит тот же генератор, что у линии, планы ложатся
  в тот же склад с прогоном аукциона 'op_osnova', а сам аукцион руководители отдела
  запускают сами, как у СЗоВ.
*/
const ResourceOpFteView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader, initialDashboardView, onOpenShiftAuction }) => (
  <ResourceFteView
    direction="op"
    user={user}
    showToast={showToast}
    apiBaseUrl={apiBaseUrl}
    withAccessTokenHeader={withAccessTokenHeader}
    initialDashboardView={initialDashboardView}
    onOpenShiftAuction={onOpenShiftAuction}
  />
);

export default ResourceOpFteView;
