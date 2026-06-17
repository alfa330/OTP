import React, { useState } from 'react';
import FaIcon from '../common/FaIcon';

const SalaryComingSoon = ({ departmentName = '' }) => {
  const assetBaseUrl = import.meta.env.BASE_URL || '/';
  const [hasCatImage, setHasCatImage] = useState(true);
  const departmentLabel = departmentName ? ` для отдела ${departmentName}` : '';

  return (
    <div className="relative overflow-hidden rounded-2xl bg-[#f8fafc] px-5 py-7 sm:px-8 sm:py-9">
      <div className="grid items-center gap-7 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full bg-white px-3 py-1.5 text-xs font-semibold text-teal-700 shadow-sm ring-1 ring-teal-100">
            <FaIcon className="fas fa-sparkles text-amber-500" />
            В разработке
          </div>
          <h3 className="text-4xl font-black text-slate-950 sm:text-5xl">
            COMING SOON
          </h3>
          <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600 sm:text-lg">
            Зарплатный калькулятор{departmentLabel} уже готовится. Сейчас доступная формула
            настроена только для СЗОВ, а для этого отдела появится отдельный расчет.
          </p>
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            <div className="flex items-center gap-2 rounded-xl bg-white/80 px-3 py-2 text-sm font-medium text-slate-700">
              <FaIcon className="fas fa-lightbulb text-rose-500" />
              Формула
            </div>
            <div className="flex items-center gap-2 rounded-xl bg-white/80 px-3 py-2 text-sm font-medium text-slate-700">
              <FaIcon className="fas fa-chart-line text-teal-600" />
              KPI
            </div>
            <div className="flex items-center gap-2 rounded-xl bg-white/80 px-3 py-2 text-sm font-medium text-slate-700">
              <FaIcon className="fas fa-gift text-amber-500" />
              Бонусы
            </div>
          </div>
        </div>

        <div className="mx-auto w-full max-w-[330px] lg:max-w-[360px]">
          {hasCatImage ? (
            <img
              src={`${assetBaseUrl}assets/salary-coming-soon-cat.png`}
              alt="Котик с калькулятором"
              className="h-auto w-full rounded-2xl object-cover shadow-sm"
              loading="lazy"
              onError={() => setHasCatImage(false)}
            />
          ) : (
            <div className="grid aspect-square place-items-center rounded-2xl bg-white shadow-sm ring-1 ring-slate-200">
              <div className="text-center text-slate-500">
                <FaIcon className="fas fa-images text-5xl text-teal-500" />
                <div className="mt-3 text-sm font-semibold">Котик скоро появится</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default SalaryComingSoon;
