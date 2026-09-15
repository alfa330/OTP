import React from 'react';
import { Check, Plus, X } from 'lucide-react';
import { iosBtnGhost, iosGroupLabel, iosInput } from '../ui/ios';
import {
    QUIZ_MAX_OPTIONS, QUIZ_MAX_QUESTIONS, QUIZ_MIN_OPTIONS, QUIZ_MIN_QUESTIONS,
    dropOption, emptyQuestion,
} from '../wiki/questionQuiz';

/* Редактор теста в окне новости — ОДИН на обе формы: «Записать в базу знаний»
   во вкладке «Вопросы» и форма новости во вкладке «Новости». Две копии разошлись
   бы в правилах: одна пускала бы тест, который вторая не пускает. Правила —
   questionQuiz.js, те же, что у сервера (news/access.py: normalize_quiz).

   Верный вариант отмечается кружком слева — зелёным: здесь цвет несёт ровно
   один смысл, «это правильный ответ». */

function IconButton({ label, onClick, disabled }) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            aria-label={label}
            title={label}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-white hover:text-rose-500 active:scale-95 disabled:opacity-50"
        >
            <X size={14} />
        </button>
    );
}

export default function NewsQuizEditor({ quiz, onChange, disabled = false, label = null }) {
    const update = (index, next) => onChange(quiz.map((item, i) => (i === index ? next : item)));
    return (
        <section className="space-y-2.5">
            {label && <p className={iosGroupLabel}>{label}</p>}
            {quiz.map((item, index) => (
                <div key={index} className="space-y-2 rounded-xl bg-slate-50 p-3">
                    <div className="flex items-center gap-2">
                        <span className="w-4 shrink-0 text-right text-[12px] font-semibold tabular-nums text-slate-400">
                            {index + 1}
                        </span>
                        <input
                            value={item.prompt}
                            onChange={(e) => update(index, { ...item, prompt: e.target.value })}
                            disabled={disabled}
                            maxLength={300}
                            placeholder="Вопрос"
                            aria-label={`Вопрос ${index + 1}`}
                            className={`${iosInput} !bg-white`}
                        />
                        {quiz.length > QUIZ_MIN_QUESTIONS && (
                            <IconButton
                                label="Убрать вопрос"
                                disabled={disabled}
                                onClick={() => onChange(quiz.filter((_, i) => i !== index))}
                            />
                        )}
                    </div>
                    <div className="space-y-1.5 pl-6" role="radiogroup" aria-label={`Верный ответ на вопрос ${index + 1}`}>
                        {item.options.map((option, optionIndex) => {
                            const correct = item.correct === optionIndex;
                            return (
                                <div key={optionIndex} className="flex items-center gap-2">
                                    <button
                                        type="button"
                                        role="radio"
                                        aria-checked={correct}
                                        aria-label={`Вариант ${optionIndex + 1} — верный`}
                                        title="Верный вариант"
                                        disabled={disabled}
                                        onClick={() => update(index, { ...item, correct: optionIndex })}
                                        className={`grid h-5 w-5 shrink-0 place-items-center rounded-full ring-1 transition ${
                                            correct
                                                ? 'bg-emerald-500 text-white ring-emerald-500'
                                                : 'bg-white ring-slate-300 hover:ring-slate-400'
                                        }`}
                                    >
                                        {correct && <Check size={12} strokeWidth={3} />}
                                    </button>
                                    <input
                                        value={option}
                                        onChange={(e) => update(index, {
                                            ...item,
                                            options: item.options.map((value, i) => (i === optionIndex ? e.target.value : value)),
                                        })}
                                        disabled={disabled}
                                        maxLength={200}
                                        placeholder={`Вариант ${optionIndex + 1}`}
                                        aria-label={`Вариант ${optionIndex + 1}`}
                                        className={`${iosInput} !bg-white !py-2`}
                                    />
                                    {item.options.length > QUIZ_MIN_OPTIONS && (
                                        <IconButton
                                            label="Убрать вариант"
                                            disabled={disabled}
                                            onClick={() => update(index, dropOption(item, optionIndex))}
                                        />
                                    )}
                                </div>
                            );
                        })}
                        {item.options.length < QUIZ_MAX_OPTIONS && (
                            <button
                                type="button"
                                disabled={disabled}
                                onClick={() => update(index, { ...item, options: [...item.options, ''] })}
                                className={`${iosBtnGhost} -ml-2 !px-2 !py-1 text-[12.5px]`}
                            >
                                <Plus size={13} /> Вариант
                            </button>
                        )}
                    </div>
                </div>
            ))}
            {quiz.length < QUIZ_MAX_QUESTIONS && (
                <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onChange([...quiz, emptyQuestion()])}
                    className={iosBtnGhost}
                >
                    <Plus size={14} /> Вопрос
                </button>
            )}
        </section>
    );
}
