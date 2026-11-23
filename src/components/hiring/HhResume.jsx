import React, { useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { iosGroupLabel } from '../ui/ios';
import { hhMoney, hhPeriod, hhSpan, longTime, plural } from './hiringMeta';

/*
 * Вкладка «Резюме» карточки: сопроводительное письмо и полное резюме отклика hh (владелец 23.11.2026: «надо бы сделать
 * так что бы все резюме и письмо человека который обратился отобразились сразу в карточке»). Мост открывает новый отклик
 * и приносит резюме без контактов (телефон — в самой карточке); подписи кодам hh даёт сервер (hiring/hh.clean_resume).
 * Несколько откликов одного человека — письмо каждого с вакансией, резюме — последнего. Длинные тексты свёрнуты до
 * нескольких строк, «Показать полностью» — по запросу: стена текста в карточке — шум.
 */

const Section = ({ title, right, children }) => (
    <section className="space-y-1.5">
        <div className="flex items-end justify-between gap-2">
            <div className={iosGroupLabel}>{title}</div>
            {right}
        </div>
        <div className="rounded-2xl bg-slate-50 px-4 py-2.5 ring-1 ring-slate-200/60">{children}</div>
    </section>
);

const Clamp = ({ text, lines = 6 }) => {
    const [open, setOpen] = useState(false);
    const long = text.length > 70 * lines || text.split('\n').length > lines;
    const clamp = long && !open ? { display: '-webkit-box', WebkitLineClamp: lines, WebkitBoxOrient: 'vertical', overflow: 'hidden' } : null;
    return (
        <div>
            <div className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-800" style={clamp || undefined}>{text}</div>
            {long && (
                <button type="button" onClick={() => setOpen((value) => !value)} className="mt-1 text-[12.5px] font-medium text-blue-700 hover:underline">
                    {open ? 'Свернуть' : 'Показать полностью'}
                </button>
            )}
        </div>
    );
};

const Fact = ({ label, children }) => (children ? (
    <div className="flex items-start justify-between gap-3 py-1 text-[13px]">
        <span className="shrink-0 text-slate-500">{label}</span>
        <span className="min-w-0 text-right text-slate-900">{children}</span>
    </div>
) : null);

const list = (values) => (values || []).filter(Boolean).join(', ');

const School = ({ item }) => (
    <div className="py-1.5 text-[13px]">
        <div className="text-slate-900">{[item.year, item.name].filter(Boolean).join(' — ')}</div>
        {(item.organization || item.result) && <div className="text-[12.5px] text-slate-600">{list([item.organization, item.result])}</div>}
    </div>
);

export const HhResume = ({ responses }) => {
    const items = responses || [];
    const latest = items.find((item) => item.resume) || null;
    const resume = latest?.resume || null;
    const letters = items.filter((item) => item.letter);
    const headline = resume ? [
        resume.age ? `${resume.age} ${plural(resume.age, 'год', 'года', 'лет')}` : null,
        resume.gender,
        [resume.area, resume.metro ? `м. ${resume.metro}` : null].filter(Boolean).join(', '),
        resume.citizenship?.length ? `гражданство: ${list(resume.citizenship)}` : null,
    ].filter(Boolean).join(' · ') : '';
    const extra = resume ? [
        resume.driver_license?.length ? `права: ${list(resume.driver_license)}` : null,
        resume.has_vehicle ? 'есть автомобиль' : null,
        resume.medical_book ? 'есть медкнижка' : null,
    ].filter(Boolean).join(' · ') : '';

    return (
        <div className="space-y-4">
            {letters.map((item) => (
                <Section key={item.topic_id} title="Сопроводительное письмо"
                         right={<span className="truncate text-[12px] text-slate-500">{[item.vacancy_name, longTime(item.responded_at)].filter(Boolean).join(' · ')}</span>}>
                    {/* Письмо — первое, что читает рекрутер: сворачивается только длинное. */}
                    <Clamp text={item.letter} lines={12} />
                </Section>
            ))}
            {resume && (
                <>
                    <Section title={letters.length ? 'Резюме' : 'Резюме · без сопроводительного письма'}
                             right={latest.resume_url ? (
                                 <a href={latest.resume_url} target="_blank" rel="noopener noreferrer"
                                    className="inline-flex shrink-0 items-center gap-1 text-[12.5px] text-blue-700 hover:underline">
                                     на hh <ExternalLink size={12} />
                                 </a>
                             ) : null}>
                        <div className="pb-1">
                            {resume.title && <div className="text-[15px] font-semibold text-slate-900">{resume.title}</div>}
                            {resume.salary && <div className="text-[14px] tabular-nums text-slate-900">{hhMoney(resume.salary)}</div>}
                            {headline && <div className="mt-0.5 text-[12.5px] text-slate-600">{headline}</div>}
                        </div>
                        <Fact label="Занятость">{list(resume.employment)}</Fact>
                        <Fact label="График">{list(resume.schedule)}</Fact>
                        <Fact label="Формат работы">{list(resume.formats)}</Fact>
                        <Fact label="Переезд">{resume.relocation}</Fact>
                        <Fact label="Командировки">{resume.business_trips}</Fact>
                        <Fact label="Время в пути">{resume.travel_time}</Fact>
                        <Fact label="Дополнительно">{extra}</Fact>
                    </Section>

                    <Section title={resume.experience?.length ? `Опыт работы${resume.experience_total ? ` · ${hhSpan(resume.experience_total)}` : ''}` : 'Опыт работы'}>
                        {resume.experience?.length ? (
                            <div className="divide-y divide-slate-200/70">
                                {resume.experience.map((job, index) => (
                                    <div key={`${job.start}-${index}`} className="py-2">
                                        <div className="flex items-baseline justify-between gap-3 text-[12px] tabular-nums text-slate-500">
                                            <span>{hhPeriod(job.start, job.end)}</span>
                                            {job.span && <span className="shrink-0">{hhSpan(job.span)}</span>}
                                        </div>
                                        {job.position && <div className="text-[13.5px] font-medium text-slate-900">{job.position}</div>}
                                        <div className="text-[12.5px] text-slate-600">{list([job.company, job.area])}</div>
                                        {job.description && <div className="mt-1"><Clamp text={job.description} lines={4} /></div>}
                                    </div>
                                ))}
                            </div>
                        ) : <div className="py-1 text-[13px] text-slate-500">Опыта работы в резюме нет</div>}
                    </Section>

                    {(resume.education?.length > 0 || resume.education_level) && (
                        <Section title={`Образование${resume.education_level ? ` · ${resume.education_level}` : ''}`}>
                            {resume.education?.length ? resume.education.map((item, index) => <School key={index} item={item} />)
                                : <div className="py-1 text-[13px] text-slate-500">Учебные заведения не указаны</div>}
                        </Section>
                    )}
                    {resume.courses?.length > 0 && (
                        <Section title="Курсы и повышение квалификации">
                            {resume.courses.map((item, index) => <School key={index} item={item} />)}
                        </Section>
                    )}
                    {resume.key_skills?.length > 0 && (
                        <Section title="Навыки">
                            <div className="flex flex-wrap gap-1.5 py-1">
                                {resume.key_skills.map((skill) => (
                                    <span key={skill} className="rounded-lg bg-white px-2 py-0.5 text-[12.5px] text-slate-700 ring-1 ring-slate-200/80">{skill}</span>
                                ))}
                            </div>
                        </Section>
                    )}
                    {resume.languages?.length > 0 && (
                        <Section title="Языки">
                            {resume.languages.map((item) => <Fact key={item.title} label={item.title}>{item.level || '—'}</Fact>)}
                        </Section>
                    )}
                    {resume.about && (
                        <Section title="О себе">
                            <Clamp text={resume.about} />
                        </Section>
                    )}
                </>
            )}
        </div>
    );
};

export default HhResume;
