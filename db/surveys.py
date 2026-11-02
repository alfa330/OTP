"""База данных: опросы.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    datetime,
    defaultdict,
    json,
    logging,
    math,
    role_has_min,
)


class SurveysMixin:
    @staticmethod
    def _survey_dt_to_iso(value):
        return value.isoformat() if hasattr(value, 'isoformat') else value

    @classmethod
    def survey_test_status(cls, starts_at, ends_at, now=None):
        moment = now or datetime.now()
        if starts_at is not None and moment < starts_at:
            return cls.SURVEY_TEST_STATUS_SCHEDULED
        if ends_at is not None and moment >= ends_at:
            return cls.SURVEY_TEST_STATUS_FINISHED
        return cls.SURVEY_TEST_STATUS_ACTIVE

    @staticmethod
    def survey_question_points(question):
        """Вес вопроса в тесте. Некорректное/пустое значение → 1 балл."""
        try:
            points = float((question or {}).get('points'))
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(points) or points <= 0:
            return 1.0
        return round(points, 2)

    @staticmethod
    def survey_answer_is_correct(question_type, correct_options, selected_options, answer_text=''):
        """Ответ верен полностью: отмечены ровно все правильные варианты."""
        expected = [str(item) for item in (correct_options or [])]
        selected = [str(item) for item in (selected_options or [])]
        if str(answer_text or '').strip():
            return False
        if question_type == 'single':
            return len(expected) == 1 and len(selected) == 1 and selected[0] == expected[0]
        if question_type == 'multiple':
            return len(expected) > 0 and set(selected) == set(expected)
        return False

    @classmethod
    def survey_answer_score_ratio(cls, question_type, correct_options, selected_options,
                                  answer_text='', partial_credit=False):
        """
        Доля балла за ответ (0..1).

        По умолчанию вопрос зачитывается целиком. При включённом частичном
        зачёте (только для вопросов с несколькими ответами) балл начисляется
        пропорционально угаданным правильным вариантам, но любой лишний —
        неправильный — вариант обнуляет вопрос. Иначе «отметить всё» гарантировало
        бы часть балла, ничего не зная.
        """
        if cls.survey_answer_is_correct(question_type, correct_options, selected_options, answer_text):
            return 1.0
        if not partial_credit or question_type != 'multiple':
            return 0.0
        if str(answer_text or '').strip():
            return 0.0

        expected = {str(item) for item in (correct_options or [])}
        selected = {str(item) for item in (selected_options or [])}
        if not expected or not selected:
            return 0.0
        if selected - expected:
            return 0.0
        return len(selected & expected) / len(expected)

    @classmethod
    def score_survey_test_attempt(cls, questions, answers_by_question):
        """
        Итог попытки: набранные баллы, максимум и процент 0..100.
        Вопросы без вариантов (рейтинг) в тесте не участвуют.
        """
        earned = 0.0
        max_points = 0.0
        total_questions = 0
        answered_questions = 0
        correct_answers = 0
        per_question = {}

        for question in questions or []:
            question_id = int(question.get('id') or 0)
            question_type = str(question.get('type') or '')
            if question_type not in ('single', 'multiple'):
                continue
            points = cls.survey_question_points(question)
            partial_credit = bool(question.get('partial_credit')) and question_type == 'multiple'
            total_questions += 1
            max_points += points

            answer = (answers_by_question or {}).get(question_id) or {}
            selected_options = list(answer.get('selected_options') or [])
            answer_text = str(answer.get('answer_text') or '')
            has_answer = bool(selected_options or answer_text or answer.get('rating_value') is not None)
            if has_answer:
                answered_questions += 1

            is_correct = cls.survey_answer_is_correct(
                question_type,
                question.get('correct_options'),
                selected_options,
                answer_text
            )
            ratio = cls.survey_answer_score_ratio(
                question_type,
                question.get('correct_options'),
                selected_options,
                answer_text,
                partial_credit=partial_credit
            )
            question_earned = round(points * ratio, 2)
            if is_correct:
                correct_answers += 1
            earned += question_earned
            per_question[question_id] = {
                'is_correct': bool(is_correct),
                'is_partially_correct': bool(not is_correct and question_earned > 0),
                'points': points,
                'earned_points': question_earned
            }

        earned = round(earned, 2)
        score_percent = round((earned / max_points) * 100, 1) if max_points > 0 else 0.0
        return {
            'total_questions': int(total_questions),
            'answered_questions': int(answered_questions),
            'correct_answers': int(correct_answers),
            'earned_points': earned,
            'max_points': round(max_points, 2),
            'score_percent': float(score_percent),
            'per_question': per_question
        }

    @staticmethod
    def _survey_bool(value, default=False):
        if value is None:
            return bool(default)
        if isinstance(value, str):
            text = value.strip().lower()
            if text == '':
                return bool(default)
            return text in ('1', 'true', 'yes', 'on')
        return bool(value)

    @staticmethod
    def _survey_int_id_list(values):
        normalized = []
        for value in values or []:
            try:
                parsed = int(value)
            except Exception:
                continue
            if parsed > 0 and parsed not in normalized:
                normalized.append(parsed)
        return normalized

    @staticmethod
    def _is_survey_operator_dismissed_status(value):
        return str(value or '').strip().lower() in ('fired', 'dismissal', 'dismissed')

    def filter_active_survey_operator_ids(self, operator_ids):
        normalized_ids = []
        for op_id in operator_ids or []:
            try:
                parsed = int(op_id)
            except Exception:
                continue
            if parsed > 0 and parsed not in normalized_ids:
                normalized_ids.append(parsed)
        if not normalized_ids:
            return []

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id
                FROM users
                WHERE id = ANY(%s)
                  AND NOT (
                      LOWER(TRIM(COALESCE(status, ''))) IN ('fired', 'dismissal', 'dismissed')
                  )
            """, (normalized_ids,))
            active_ids = {int(row[0]) for row in cursor.fetchall()}

        return [op_id for op_id in normalized_ids if op_id in active_ids]

    def create_survey(self, title, description, created_by, assignment, questions, operator_ids,
                      repeat_from_survey_id=None, is_test=False, test_config=None):
        created_by_id = int(created_by) if created_by is not None else None
        definition = self._normalize_survey_definition(
            title=title,
            description=description,
            assignment=assignment,
            questions=questions,
            operator_ids=operator_ids,
            is_test=is_test,
            test_config=test_config
        )
        title_norm = definition['title']
        description_norm = definition['description']
        is_test_norm = definition['is_test']
        direction_ids_norm = definition['direction_ids']
        group_ids_norm = definition['group_ids']
        tenure_weeks_min = definition['tenure_weeks_min']
        tenure_weeks_max = definition['tenure_weeks_max']
        operator_ids_norm = definition['operator_ids']
        normalized_questions = definition['questions']

        with self._get_cursor() as cursor:
            repeat_root_id_to_use = None
            repeat_iteration_to_use = 1
            if repeat_from_survey_id is not None:
                try:
                    repeat_from_survey_id = int(repeat_from_survey_id)
                except Exception:
                    raise ValueError("SURVEY_REPEAT_SOURCE_NOT_FOUND")

                cursor.execute("""
                    SELECT id, COALESCE(repeat_root_id, id), COALESCE(repeat_iteration, 1)
                    FROM surveys
                    WHERE id = %s
                """, (repeat_from_survey_id,))
                repeat_source_row = cursor.fetchone()
                if not repeat_source_row:
                    raise ValueError("SURVEY_REPEAT_SOURCE_NOT_FOUND")

                repeat_root_id_to_use = int(repeat_source_row[1])
                cursor.execute("""
                    SELECT COALESCE(MAX(repeat_iteration), 1)
                    FROM surveys
                    WHERE repeat_root_id = %s OR id = %s
                """, (repeat_root_id_to_use, repeat_root_id_to_use))
                max_iteration_row = cursor.fetchone()
                current_max_iteration = int(max_iteration_row[0] or 1) if max_iteration_row else 1
                repeat_iteration_to_use = max(1, current_max_iteration + 1)

            cursor.execute("""
                INSERT INTO surveys (
                    title,
                    description,
                    created_by,
                    direction_ids,
                    group_ids,
                    tenure_weeks_min,
                    tenure_weeks_max,
                    repeat_root_id,
                    repeat_iteration,
                    is_test,
                    starts_at,
                    ends_at,
                    single_attempt,
                    affects_quality,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s::jsonb,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                )
                RETURNING id, created_at
            """, (
                title_norm,
                description_norm,
                created_by_id,
                json.dumps(direction_ids_norm),
                json.dumps(group_ids_norm),
                tenure_weeks_min,
                tenure_weeks_max,
                repeat_root_id_to_use,
                repeat_iteration_to_use,
                is_test_norm,
                definition['starts_at'],
                definition['ends_at'],
                definition['single_attempt'],
                definition['affects_quality']
            ))
            survey_id, created_at = cursor.fetchone()

            if repeat_root_id_to_use is None:
                repeat_root_id_to_use = int(survey_id)
                cursor.execute("""
                    UPDATE surveys
                    SET repeat_root_id = %s
                    WHERE id = %s
                """, (repeat_root_id_to_use, survey_id))
                repeat_iteration_to_use = 1

            for question in normalized_questions:
                cursor.execute("""
                    INSERT INTO survey_questions (
                        survey_id,
                        position,
                        question_text,
                        question_type,
                        is_required,
                        allow_other,
                        options_json,
                        correct_options_json,
                        points,
                        partial_credit
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                """, (
                    survey_id,
                    question['position'],
                    question['text'],
                    question['type'],
                    question['required'],
                    question['allow_other'],
                    json.dumps(question['options'], ensure_ascii=False),
                    json.dumps(question['correct_options'], ensure_ascii=False),
                    question['points'],
                    question['partial_credit']
                ))

            for operator_id in operator_ids_norm:
                cursor.execute("""
                    INSERT INTO survey_assignments (
                        survey_id,
                        operator_id,
                        assigned_by,
                        status,
                        assigned_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        'assigned',
                        (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    )
                    ON CONFLICT (survey_id, operator_id) DO UPDATE
                    SET
                        assigned_by = EXCLUDED.assigned_by,
                        status = CASE
                            WHEN survey_assignments.status = 'completed' THEN survey_assignments.status
                            ELSE 'assigned'
                        END,
                        completed_at = CASE
                            WHEN survey_assignments.status = 'completed' THEN survey_assignments.completed_at
                            ELSE NULL
                        END,
                        assigned_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                """, (survey_id, operator_id, created_by_id))

            return {
                'id': int(survey_id),
                'created_at': self._survey_dt_to_iso(created_at),
                'repeat_root_id': int(repeat_root_id_to_use),
                'repeat_iteration': int(repeat_iteration_to_use),
                'is_test': bool(is_test_norm)
            }

    def update_survey(self, survey_id, requester_id, requester_role, title, description, assignment,
                      questions, operator_ids, is_test=False, test_config=None):
        survey_id = int(survey_id)
        requester_id = int(requester_id)
        role = self._normalize_survey_role(requester_role)

        definition = self._normalize_survey_definition(
            title=title,
            description=description,
            assignment=assignment,
            questions=questions,
            operator_ids=operator_ids,
            is_test=is_test,
            test_config=test_config,
            keep_question_ids=True
        )
        title_norm = definition['title']
        description_norm = definition['description']
        is_test_norm = definition['is_test']
        direction_ids_norm = definition['direction_ids']
        group_ids_norm = definition['group_ids']
        tenure_weeks_min = definition['tenure_weeks_min']
        tenure_weeks_max = definition['tenure_weeks_max']
        operator_ids_norm = definition['operator_ids']
        normalized_questions = definition['questions']

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, created_by
                FROM surveys
                WHERE id = %s
            """, (survey_id,))
            survey_row = cursor.fetchone()
            if not survey_row:
                raise ValueError("SURVEY_NOT_FOUND")

            created_by = survey_row[1]
            if (not role_has_min(role, 'admin')) and (created_by is None or int(created_by) != requester_id):
                raise PermissionError("SURVEY_FORBIDDEN")

            cursor.execute("SELECT id FROM survey_questions WHERE survey_id = %s", (survey_id,))
            existing_question_ids = {int(row[0]) for row in cursor.fetchall()}
            cursor.execute("UPDATE survey_questions SET position = -id WHERE survey_id = %s", (survey_id,))

            kept_question_ids = set()
            for question in normalized_questions:
                question_id = question.get('id')
                if question_id is not None and question_id in existing_question_ids:
                    cursor.execute("""
                        UPDATE survey_questions
                        SET
                            position = %s,
                            question_text = %s,
                            question_type = %s,
                            is_required = %s,
                            allow_other = %s,
                            options_json = %s::jsonb,
                            correct_options_json = %s::jsonb,
                            points = %s,
                            partial_credit = %s
                        WHERE id = %s AND survey_id = %s
                    """, (
                        question['position'],
                        question['text'],
                        question['type'],
                        question['required'],
                        question['allow_other'],
                        json.dumps(question['options'], ensure_ascii=False),
                        json.dumps(question['correct_options'], ensure_ascii=False),
                        question['points'],
                        question['partial_credit'],
                        question_id,
                        survey_id
                    ))
                    kept_question_ids.add(question_id)
                else:
                    cursor.execute("""
                        INSERT INTO survey_questions (
                            survey_id,
                            position,
                            question_text,
                            question_type,
                            is_required,
                            allow_other,
                            options_json,
                            correct_options_json,
                            points,
                            partial_credit
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                        RETURNING id
                    """, (
                        survey_id,
                        question['position'],
                        question['text'],
                        question['type'],
                        question['required'],
                        question['allow_other'],
                        json.dumps(question['options'], ensure_ascii=False),
                        json.dumps(question['correct_options'], ensure_ascii=False),
                        question['points'],
                        question['partial_credit']
                    ))
                    kept_question_ids.add(int(cursor.fetchone()[0]))

            removed_question_ids = list(existing_question_ids - kept_question_ids)
            if removed_question_ids:
                cursor.execute("""
                    DELETE FROM survey_questions
                    WHERE survey_id = %s AND id = ANY(%s)
                """, (survey_id, removed_question_ids))

            cursor.execute("""
                UPDATE surveys
                SET
                    title = %s,
                    description = %s,
                    direction_ids = %s::jsonb,
                    group_ids = %s::jsonb,
                    tenure_weeks_min = %s,
                    tenure_weeks_max = %s,
                    is_test = %s,
                    starts_at = %s,
                    ends_at = %s,
                    single_attempt = %s,
                    affects_quality = %s,
                    updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                WHERE id = %s
            """, (
                title_norm,
                description_norm,
                json.dumps(direction_ids_norm),
                json.dumps(group_ids_norm),
                tenure_weeks_min,
                tenure_weeks_max,
                is_test_norm,
                definition['starts_at'],
                definition['ends_at'],
                definition['single_attempt'],
                definition['affects_quality'],
                survey_id
            ))

            cursor.execute("SELECT operator_id FROM survey_assignments WHERE survey_id = %s", (survey_id,))
            existing_operator_ids = {int(row[0]) for row in cursor.fetchall()}
            target_operator_ids = set(operator_ids_norm)
            removed_operator_ids = list(existing_operator_ids - target_operator_ids)
            if removed_operator_ids:
                # Сначала убираем оценку «Тестирование знаний» — иначе после
                # SET NULL осталась бы запись журнала, которую уже не отличить
                # от прослушанного звонка.
                cursor.execute("""
                    DELETE FROM calls
                    WHERE survey_response_id IN (
                        SELECT id FROM survey_responses
                        WHERE survey_id = %s AND operator_id = ANY(%s)
                    )
                """, (survey_id, removed_operator_ids))
                cursor.execute("""
                    DELETE FROM survey_attempt_drafts
                    WHERE survey_id = %s AND operator_id = ANY(%s)
                """, (survey_id, removed_operator_ids))
                cursor.execute("""
                    DELETE FROM survey_responses
                    WHERE survey_id = %s AND operator_id = ANY(%s)
                """, (survey_id, removed_operator_ids))
                cursor.execute("""
                    DELETE FROM survey_assignments
                    WHERE survey_id = %s AND operator_id = ANY(%s)
                """, (survey_id, removed_operator_ids))

            for operator_id in operator_ids_norm:
                cursor.execute("""
                    INSERT INTO survey_assignments (
                        survey_id,
                        operator_id,
                        assigned_by,
                        status,
                        assigned_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        'assigned',
                        (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    )
                    ON CONFLICT (survey_id, operator_id) DO UPDATE
                    SET
                        assigned_by = EXCLUDED.assigned_by,
                        status = CASE
                            WHEN survey_assignments.status = 'completed' THEN survey_assignments.status
                            ELSE 'assigned'
                        END,
                        completed_at = CASE
                            WHEN survey_assignments.status = 'completed' THEN survey_assignments.completed_at
                            ELSE NULL
                        END
                """, (survey_id, operator_id, requester_id))

            # Правка вопросов или баллов меняет правила подсчёта, поэтому уже
            # сданные попытки пересчитываются — иначе статистика показывала бы
            # старый процент рядом с новыми баллами, а в качестве оператора
            # осталась бы оценка по прежней шкале.
            if is_test_norm:
                rescored = self._rescore_survey_responses_tx(cursor, survey_id)
            else:
                # Режим теста выключили — оценки «Тестирование знаний» по нему
                # больше не имеют смысла и уходят из журнала.
                cursor.execute("""
                    DELETE FROM calls
                    WHERE survey_response_id IN (
                        SELECT id FROM survey_responses WHERE survey_id = %s
                    )
                """, (survey_id,))
                rescored = 0

            return {
                'id': survey_id,
                'updated_at': self._survey_dt_to_iso(datetime.now()),
                'rescored_responses': int(rescored)
            }

    def _rescore_survey_responses_tx(self, cursor, survey_id):
        """Пересчитывает сохранённые итоги попыток теста по текущим вопросам."""
        survey_id = int(survey_id)
        cursor.execute("""
            SELECT
                COALESCE(is_test, FALSE),
                COALESCE(affects_quality, FALSE),
                title,
                created_by
            FROM surveys
            WHERE id = %s
        """, (survey_id,))
        survey_row = cursor.fetchone()
        if not survey_row or not bool(survey_row[0]):
            return 0
        affects_quality = bool(survey_row[1])
        survey_title = str(survey_row[2] or '')
        survey_created_by = survey_row[3]

        cursor.execute("""
            SELECT id, question_type, correct_options_json, points, partial_credit
            FROM survey_questions
            WHERE survey_id = %s
            ORDER BY position, id
        """, (survey_id,))
        questions = [
            {
                'id': int(row[0]),
                'type': row[1],
                'correct_options': [str(item) for item in (row[2] if isinstance(row[2], list) else [])],
                'points': self.survey_question_points({'points': row[3]}),
                'partial_credit': bool(row[4])
            }
            for row in cursor.fetchall()
        ]
        if not questions:
            return 0

        cursor.execute("""
            SELECT id, operator_id
            FROM survey_responses
            WHERE survey_id = %s
        """, (survey_id,))
        responses = [(int(row[0]), int(row[1])) for row in cursor.fetchall()]
        if not responses:
            return 0

        cursor.execute("""
            SELECT response_id, question_id, selected_options_json, answer_text
            FROM survey_answers
            WHERE response_id = ANY(%s)
        """, ([response_id for response_id, _ in responses],))
        answers_by_response = defaultdict(dict)
        for row in cursor.fetchall():
            answers_by_response[int(row[0])][int(row[1])] = {
                'selected_options': [str(item) for item in (row[2] if isinstance(row[2], list) else [])],
                'answer_text': row[3] or ''
            }

        for response_id, operator_id in responses:
            score = self.score_survey_test_attempt(questions, answers_by_response.get(response_id, {}))
            cursor.execute("""
                UPDATE survey_responses
                SET
                    earned_points = %s,
                    max_points = %s,
                    score_percent = %s
                WHERE id = %s
            """, (score['earned_points'], score['max_points'], score['score_percent'], response_id))

            if affects_quality:
                self._upsert_knowledge_test_evaluation_tx(
                    cursor,
                    survey_id=survey_id,
                    survey_title=survey_title,
                    survey_created_by=survey_created_by,
                    operator_id=operator_id,
                    response_id=response_id,
                    score_percent=score['score_percent'],
                    touch_timestamps=False
                )
            else:
                # Учёт в качестве выключили — оценка из журнала уходит.
                cursor.execute("DELETE FROM calls WHERE survey_response_id = %s", (response_id,))

        return len(responses)

    def delete_survey(self, survey_id, requester_id, requester_role):
        survey_id = int(survey_id)
        requester_id = int(requester_id)
        role = self._normalize_survey_role(requester_role)

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, created_by
                FROM surveys
                WHERE id = %s
            """, (survey_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("SURVEY_NOT_FOUND")

            created_by = row[1]
            if (not role_has_min(role, 'admin')) and (created_by is None or int(created_by) != requester_id):
                raise PermissionError("SURVEY_FORBIDDEN")

            # Оценки «Тестирование знаний» уходят вместе с тестом: иначе после
            # SET NULL они превратились бы в неотличимые записи журнала.
            cursor.execute("""
                DELETE FROM calls
                WHERE survey_response_id IN (
                    SELECT id FROM survey_responses WHERE survey_id = %s
                )
            """, (survey_id,))
            cursor.execute("DELETE FROM surveys WHERE id = %s", (survey_id,))
            return True

    def _build_survey_question_stats(self, questions, answers_for_survey, respondents_total=0):
        try:
            survey_respondents_total = int(respondents_total or 0)
        except Exception:
            survey_respondents_total = 0
        survey_respondents_total = max(0, survey_respondents_total)

        answers_by_question = defaultdict(list)
        for answer in answers_for_survey:
            try:
                question_id = int(answer.get('question_id'))
            except Exception:
                continue
            answers_by_question[question_id].append(answer)

        stats = []
        for question in questions:
            question_id = int(question['id'])
            qtype = question.get('type')
            question_text = str(question.get('text') or '')
            correct_options = [str(item) for item in (question.get('correct_options') or [])]
            question_answers = answers_by_question.get(question_id, [])
            answered_count = 0

            if qtype == 'rating':
                values = []
                distribution = {str(i): 0 for i in range(1, 6)}
                for answer in question_answers:
                    try:
                        value = int(answer.get('rating_value'))
                    except Exception:
                        continue
                    if 1 <= value <= 5:
                        answered_count += 1
                        values.append(value)
                        distribution[str(value)] += 1

                sorted_values = sorted(values)
                median_rating = None
                if sorted_values:
                    middle = len(sorted_values) // 2
                    if len(sorted_values) % 2:
                        median_rating = float(sorted_values[middle])
                    else:
                        median_rating = round((sorted_values[middle - 1] + sorted_values[middle]) / 2, 2)

                detailed_distribution = []
                for score in range(1, 6):
                    count = int(distribution[str(score)])
                    detailed_distribution.append({
                        'value': score,
                        'count': count,
                        'percent_of_answers': round((count / answered_count) * 100, 1) if answered_count > 0 else 0.0,
                        'percent_of_respondents': round((count / survey_respondents_total) * 100, 1) if survey_respondents_total > 0 else 0.0
                    })

                stats.append({
                    'question_id': question_id,
                    'text': question_text,
                    'type': qtype,
                    'correct_options': correct_options,
                    'respondents_total': survey_respondents_total,
                    'question_respondents_total': answered_count,
                    'survey_respondents_total': survey_respondents_total,
                    'responses_with_answer': answered_count,
                    'skipped_count': max(0, survey_respondents_total - answered_count),
                    'response_rate': round((answered_count / survey_respondents_total) * 100, 1) if survey_respondents_total > 0 else 0.0,
                    'average_rating': round(sum(values) / len(values), 2) if values else None,
                    'median_rating': median_rating,
                    'min_rating': min(values) if values else None,
                    'max_rating': max(values) if values else None,
                    'ratings_distribution': distribution,
                    'ratings_distribution_detailed': detailed_distribution
                })
                continue

            option_counts = defaultdict(int)
            other_count = 0
            selections_total = 0

            for answer in question_answers:
                selected_values = []
                for selected in answer.get('selected_options') or []:
                    selected_text = str(selected or '').strip()
                    if selected_text:
                        selected_values.append(selected_text)

                answer_text = str(answer.get('answer_text') or '').strip()
                if selected_values or answer_text:
                    answered_count += 1

                for selected_value in selected_values:
                    option_counts[selected_value] += 1
                    selections_total += 1
                if answer_text:
                    other_count += 1

            options_stats = []
            for option in question.get('options') or []:
                count = int(option_counts.get(option, 0))
                options_stats.append({
                    'option': option,
                    'count': count,
                    'percent': round((count / answered_count) * 100, 1) if answered_count > 0 else 0.0,
                    'percent_of_answers': round((count / answered_count) * 100, 1) if answered_count > 0 else 0.0,
                    'percent_of_respondents': round((count / survey_respondents_total) * 100, 1) if survey_respondents_total > 0 else 0.0,
                    'percent_of_selections': round((count / selections_total) * 100, 1) if selections_total > 0 else 0.0
                })
            if question.get('allow_other'):
                options_stats.append({
                    'option': 'Другое',
                    'count': int(other_count),
                    'is_other': True,
                    'percent': round((other_count / answered_count) * 100, 1) if answered_count > 0 else 0.0,
                    'percent_of_answers': round((other_count / answered_count) * 100, 1) if answered_count > 0 else 0.0,
                    'percent_of_respondents': round((other_count / survey_respondents_total) * 100, 1) if survey_respondents_total > 0 else 0.0,
                    'percent_of_selections': None
                })

            top_options = [
                option for option in sorted(
                    [item for item in options_stats if int(item.get('count') or 0) > 0],
                    key=lambda item: (-int(item.get('count') or 0), str(item.get('option') or ''))
                )
            ][:3]

            stats.append({
                'question_id': question_id,
                'text': question_text,
                'type': qtype,
                'correct_options': correct_options,
                'respondents_total': survey_respondents_total,
                'question_respondents_total': answered_count,
                'survey_respondents_total': survey_respondents_total,
                'responses_with_answer': answered_count,
                'skipped_count': max(0, survey_respondents_total - answered_count),
                'response_rate': round((answered_count / survey_respondents_total) * 100, 1) if survey_respondents_total > 0 else 0.0,
                'selections_total': int(selections_total),
                'options': options_stats,
                'top_options': top_options
            })

        return stats

    def _serialize_survey_list(self, surveys_rows, questions_rows, assignments_rows, responses_rows, answers_rows):
        now = datetime.now()
        questions_by_survey = defaultdict(list)
        question_positions_by_survey = defaultdict(dict)
        for row in questions_rows:
            survey_id = int(row[1])
            question_id = int(row[0])
            position = int(row[2])
            options = row[7] if isinstance(row[7], list) else []
            correct_options = row[8] if len(row) > 8 and isinstance(row[8], list) else []
            question_obj = {
                'id': question_id,
                'position': position,
                'text': row[3],
                'type': row[4],
                'required': bool(row[5]),
                'allow_other': bool(row[6]),
                'options': [str(item) for item in options],
                'correct_options': [str(item) for item in correct_options],
                'points': self.survey_question_points({'points': row[9] if len(row) > 9 else None}),
                'partial_credit': bool(row[10]) if len(row) > 10 else False
            }
            questions_by_survey[survey_id].append(question_obj)
            question_positions_by_survey[survey_id][question_id] = position

        assignments_by_survey = defaultdict(list)
        for row in assignments_rows:
            operator_status = row[7] if len(row) > 7 else None
            operator_department_id = row[8] if len(row) > 8 else None
            assignments_by_survey[int(row[1])].append({
                'id': int(row[0]),
                'operator_id': int(row[2]),
                'operator_name': row[3] or f"#{row[2]}",
                'status': row[4],
                'operator_status': operator_status or '',
                'is_operator_dismissed': self._is_survey_operator_dismissed_status(operator_status),
                'department_id': int(operator_department_id) if operator_department_id is not None else None,
                'assigned_at': self._survey_dt_to_iso(row[5]),
                'completed_at': self._survey_dt_to_iso(row[6]),
                'started_at': self._survey_dt_to_iso(row[9]) if len(row) > 9 else None
            })

        responses_by_survey = defaultdict(list)
        for row in responses_rows:
            responses_by_survey[int(row[1])].append({
                'id': int(row[0]),
                'operator_id': int(row[2]),
                'submitted_at': self._survey_dt_to_iso(row[3]),
                'started_at': self._survey_dt_to_iso(row[4]) if len(row) > 4 else None,
                'earned_points': float(row[5]) if len(row) > 5 and row[5] is not None else None,
                'max_points': float(row[6]) if len(row) > 6 and row[6] is not None else None,
                'score_percent': float(row[7]) if len(row) > 7 and row[7] is not None else None,
                'is_auto_submitted': bool(row[8]) if len(row) > 8 else False,
                'quality_call_id': int(row[9]) if len(row) > 9 and row[9] is not None else None
            })

        answers_by_survey = defaultdict(list)
        answers_by_response = defaultdict(list)
        for row in answers_rows:
            response_id = None
            if len(row) >= 6:
                response_id = int(row[0])
                survey_id = int(row[1])
                question_id = int(row[2])
                selected_options = row[3] if isinstance(row[3], list) else []
                answer_text = row[4] or ''
                rating_value = row[5]
            else:
                survey_id = int(row[0])
                question_id = int(row[1])
                selected_options = row[2] if isinstance(row[2], list) else []
                answer_text = row[3] or ''
                rating_value = row[4]

            answer_obj = {
                'question_id': question_id,
                'selected_options': [str(item) for item in selected_options],
                'answer_text': answer_text,
                'rating_value': rating_value
            }
            answers_by_survey[survey_id].append(answer_obj)
            if response_id is not None:
                answers_by_response[response_id].append(answer_obj)

        serialized = []
        for row in surveys_rows:
            survey_id = int(row[0])
            repeat_root_id = survey_id
            repeat_iteration = 1
            is_test = False
            if len(row) > 12:
                try:
                    repeat_root_id = int(row[12]) if row[12] is not None else survey_id
                except Exception:
                    repeat_root_id = survey_id
            if len(row) > 13:
                try:
                    parsed_repeat_iteration = int(row[13]) if row[13] is not None else 1
                    repeat_iteration = parsed_repeat_iteration if parsed_repeat_iteration >= 1 else 1
                except Exception:
                    repeat_iteration = 1
            if len(row) > 14:
                is_test = bool(row[14])

            group_ids = row[15] if len(row) > 15 and isinstance(row[15], list) else []
            starts_at = row[16] if len(row) > 16 else None
            ends_at = row[17] if len(row) > 17 else None
            single_attempt = bool(row[18]) if len(row) > 18 and row[18] is not None else True
            affects_quality = bool(row[19]) if len(row) > 19 else False
            archived_at = row[20] if len(row) > 20 else None
            test_status = self.survey_test_status(starts_at, ends_at, now=now) if is_test else None

            direction_ids = row[6] if isinstance(row[6], list) else []
            questions = sorted(questions_by_survey.get(survey_id, []), key=lambda item: (item['position'], item['id']))
            assignments = assignments_by_survey.get(survey_id, [])
            responses = responses_by_survey.get(survey_id, [])
            question_positions = question_positions_by_survey.get(survey_id, {})
            test_question_meta_by_id = {}
            test_total_questions = 0
            test_max_points = 0.0
            if is_test:
                for question in questions:
                    question_id = int(question.get('id') or 0)
                    question_type = str(question.get('type') or '')
                    correct_options = [str(item) for item in (question.get('correct_options') or [])]
                    test_question_meta_by_id[question_id] = {
                        'type': question_type,
                        'correct_options': correct_options,
                        'points': self.survey_question_points(question)
                    }
                    if question_type in ('single', 'multiple'):
                        test_total_questions += 1
                        test_max_points += self.survey_question_points(question)

            assigned_count = len(assignments)
            completed_count = sum(1 for assignment in assignments if assignment.get('status') == 'completed')
            pending_count = max(0, assigned_count - completed_count)
            completion_rate = round((completed_count / assigned_count) * 100, 1) if assigned_count > 0 else 0.0

            normalized_direction_ids = []
            for value in direction_ids:
                try:
                    parsed = int(value)
                except Exception:
                    continue
                if parsed not in normalized_direction_ids:
                    normalized_direction_ids.append(parsed)

            latest_response_by_operator = {}
            for response in responses:
                operator_id = int(response.get('operator_id'))
                if operator_id not in latest_response_by_operator:
                    latest_response_by_operator[operator_id] = response

            responses_detailed = []
            for assignment in assignments:
                operator_id = int(assignment.get('operator_id'))
                operator_response = latest_response_by_operator.get(operator_id)
                response_id = int(operator_response['id']) if operator_response else None
                response_answers = list(answers_by_response.get(response_id, [])) if response_id is not None else []
                response_answers.sort(
                    key=lambda item: (
                        question_positions.get(int(item.get('question_id') or 0), 10 ** 9),
                        int(item.get('question_id') or 0)
                    )
                )

                answers_by_question = {}
                normalized_answers = []
                # Итог считает та же функция, что и при отправке попытки, —
                # иначе правила частичного зачёта разъехались бы со статистикой.
                attempt_score = self.score_survey_test_attempt(
                    questions,
                    {int(answer.get('question_id')): answer for answer in response_answers}
                ) if is_test else None
                test_answered_questions = int((attempt_score or {}).get('answered_questions') or 0)
                test_correct_answers = int((attempt_score or {}).get('correct_answers') or 0)
                test_earned_points = float((attempt_score or {}).get('earned_points') or 0.0)

                for answer in response_answers:
                    question_id = int(answer.get('question_id'))
                    selected_options = list(answer.get('selected_options') or [])
                    answer_text = str(answer.get('answer_text') or '')
                    answer_payload = {
                        'question_id': question_id,
                        'selected_options': selected_options,
                        'answer_text': answer_text,
                        'rating_value': answer.get('rating_value')
                    }
                    if is_test:
                        question_meta = test_question_meta_by_id.get(question_id) or {}
                        per_question = (attempt_score or {}).get('per_question', {}).get(question_id) or {}
                        answer_payload['is_correct'] = bool(per_question.get('is_correct'))
                        answer_payload['is_partially_correct'] = bool(per_question.get('is_partially_correct'))
                        answer_payload['expected_options'] = list(question_meta.get('correct_options') or [])
                        answer_payload['points'] = float(per_question.get('points') or question_meta.get('points') or 1.0)
                        answer_payload['earned_points'] = float(per_question.get('earned_points') or 0.0)

                    answers_by_question[str(question_id)] = answer_payload
                    normalized_answers.append(answer_payload)

                test_summary = None
                if is_test:
                    # Сохранённый при отправке итог — источник истины: он же ушёл
                    # в оценку качества. Пересчёт нужен только для старых попыток,
                    # записанных до появления баллов.
                    stored_percent = (operator_response or {}).get('score_percent')
                    stored_earned = (operator_response or {}).get('earned_points')
                    stored_max = (operator_response or {}).get('max_points')
                    if response_id is None:
                        score_percent = None
                        earned_points = None
                        max_points = round(float(test_max_points), 2)
                    elif stored_percent is not None:
                        score_percent = float(stored_percent)
                        earned_points = float(stored_earned) if stored_earned is not None else round(test_earned_points, 2)
                        max_points = float(stored_max) if stored_max is not None else round(float(test_max_points), 2)
                    else:
                        earned_points = round(test_earned_points, 2)
                        max_points = round(float(test_max_points), 2)
                        score_percent = round((earned_points / max_points) * 100, 1) if max_points > 0 else 0.0
                    test_summary = {
                        'total_questions': int(test_total_questions),
                        'answered_questions': int(test_answered_questions),
                        'correct_answers': int(test_correct_answers),
                        'earned_points': earned_points,
                        'max_points': max_points,
                        'score_percent': float(score_percent) if score_percent is not None else None,
                        'is_auto_submitted': bool((operator_response or {}).get('is_auto_submitted')),
                        'quality_call_id': (operator_response or {}).get('quality_call_id'),
                        'sent_to_quality': (operator_response or {}).get('quality_call_id') is not None
                    }

                responses_detailed.append({
                    'operator_id': operator_id,
                    'operator_name': assignment.get('operator_name') or f"#{operator_id}",
                    'operator_status': assignment.get('operator_status') or '',
                    'is_operator_dismissed': bool(assignment.get('is_operator_dismissed')),
                    'department_id': assignment.get('department_id'),
                    'status': assignment.get('status') or 'assigned',
                    'assigned_at': assignment.get('assigned_at'),
                    'completed_at': assignment.get('completed_at'),
                    'started_at': (operator_response or {}).get('started_at') or assignment.get('started_at'),
                    'response_id': response_id,
                    'submitted_at': operator_response.get('submitted_at') if operator_response else None,
                    'repeat_iteration': int(repeat_iteration),
                    'repeat_root_id': int(repeat_root_id),
                    'repeat_survey_id': int(survey_id),
                    'answers': normalized_answers,
                    'answers_by_question': answers_by_question,
                    'test_summary': test_summary
                })

            serialized.append({
                'id': survey_id,
                'title': row[1],
                'description': row[2] or '',
                'created_at': self._survey_dt_to_iso(row[3]),
                'updated_at': self._survey_dt_to_iso(row[4]),
                'archived_at': self._survey_dt_to_iso(archived_at),
                'is_archived': archived_at is not None,
                'created_by': {
                    'id': row[9],
                    'name': row[10] or 'Система',
                    'role': row[11] or 'unknown'
                },
                'repeat': {
                    'root_id': int(repeat_root_id),
                    'iteration': int(repeat_iteration),
                    'is_repeat': int(repeat_iteration) > 1
                },
                'is_test': bool(is_test),
                'test': {
                    'status': test_status,
                    'starts_at': self._survey_dt_to_iso(starts_at),
                    'ends_at': self._survey_dt_to_iso(ends_at),
                    'single_attempt': bool(single_attempt),
                    'affects_quality': bool(affects_quality),
                    'max_points': round(float(test_max_points), 2),
                    'questions_count': int(test_total_questions)
                } if is_test else None,
                'assignment': {
                    'direction_ids': normalized_direction_ids,
                    'group_ids': self._survey_int_id_list(group_ids),
                    'tenure_weeks_min': row[7],
                    'tenure_weeks_max': row[8],
                    'operator_ids': [int(assignment['operator_id']) for assignment in assignments],
                    'operators': assignments
                },
                'questions': questions,
                'statistics': {
                    'assigned_count': assigned_count,
                    'completed_count': completed_count,
                    'pending_count': pending_count,
                    'responses_count': len(responses),
                    'completion_rate': completion_rate,
                    'question_stats': self._build_survey_question_stats(
                        questions,
                        answers_by_survey.get(survey_id, []),
                        respondents_total=len(responses)
                    ),
                    'responses_detailed': responses_detailed
                }
            })

        grouped_repetition_rows = defaultdict(list)
        for survey in serialized:
            repeat_info = survey.get('repeat') or {}
            try:
                root_id = int(repeat_info.get('root_id') or survey.get('id'))
            except Exception:
                root_id = int(survey.get('id'))
            repeat_iteration = int(repeat_info.get('iteration') or 1)

            for response_row in (survey.get('statistics') or {}).get('responses_detailed') or []:
                row_copy = dict(response_row)
                row_copy['repeat_iteration'] = int(repeat_iteration)
                row_copy['repeat_root_id'] = int(root_id)
                row_copy['repeat_survey_id'] = int(survey.get('id'))
                row_copy['repeat_survey_title'] = survey.get('title') or f"#{survey.get('id')}"
                grouped_repetition_rows[root_id].append(row_copy)

        for root_id, rows in grouped_repetition_rows.items():
            rows.sort(key=lambda item: (
                int(item.get('repeat_iteration') or 1),
                str(item.get('operator_name') or '').lower(),
                int(item.get('operator_id') or 0)
            ))

        for survey in serialized:
            repeat_info = survey.get('repeat') or {}
            try:
                root_id = int(repeat_info.get('root_id') or survey.get('id'))
            except Exception:
                root_id = int(survey.get('id'))
            survey.setdefault('statistics', {})['responses_detailed_all_repetitions'] = list(grouped_repetition_rows.get(root_id, []))

        return serialized

    def get_surveys_for_management(self, requester_id, requester_role, scope_department_id=None,
                                   survey_ids=None):
        """Полные карточки опросов со статистикой.

        survey_ids сужает выборку до конкретных опросов — так работает открытие
        одной карточки: список страниц отдаёт лёгкие строки (get_surveys_page),
        а вопросы, ответы и статистику подтягивает уже выбранный опрос. Раньше
        этот метод всегда выгружал ВСЕ опросы со всеми ответами всех операторов
        ради списка из десятка заголовков.
        """
        requester_id = int(requester_id)
        role = self._normalize_survey_role(requester_role)

        if not (role_has_min(role, 'admin') or role in ('sv', 'trainer')):
            return []

        requested_ids = self._survey_int_id_list(survey_ids) if survey_ids is not None else None
        if requested_ids is not None and not requested_ids:
            return []
        id_filter = " AND s.id = ANY(%s)" if requested_ids is not None else ""

        with self._get_cursor() as cursor:
            if role_has_min(role, 'admin') or role == 'trainer':
                params = [requested_ids] if requested_ids is not None else []
                cursor.execute(f"""
                    SELECT
                        {self._SURVEY_LIST_COLUMNS}
                    FROM surveys s
                    LEFT JOIN users creator ON creator.id = s.created_by
                    WHERE TRUE{id_filter}
                    ORDER BY s.created_at DESC, s.id DESC
                """, tuple(params))
            else:
                if scope_department_id is not None:
                    params = [requester_id, int(scope_department_id)]
                    if requested_ids is not None:
                        params.append(requested_ids)
                    cursor.execute(f"""
                    SELECT DISTINCT
                        {self._SURVEY_LIST_COLUMNS}
                    FROM surveys s
                    LEFT JOIN users creator ON creator.id = s.created_by
                    LEFT JOIN survey_assignments sa ON sa.survey_id = s.id
                    LEFT JOIN users op ON op.id = sa.operator_id
                    WHERE (s.created_by = %s OR op.department_id = %s){id_filter}
                    ORDER BY s.created_at DESC, s.id DESC
                """, tuple(params))
                else:
                    params = [requester_id, requester_id]
                    if requested_ids is not None:
                        params.append(requested_ids)
                    cursor.execute(f"""
                    SELECT DISTINCT
                        {self._SURVEY_LIST_COLUMNS}
                    FROM surveys s
                    LEFT JOIN users creator ON creator.id = s.created_by
                    LEFT JOIN survey_assignments sa ON sa.survey_id = s.id
                    LEFT JOIN users op ON op.id = sa.operator_id
                    WHERE (s.created_by = %s OR op.supervisor_id = %s){id_filter}
                    ORDER BY s.created_at DESC, s.id DESC
                """, tuple(params))

            surveys_rows = cursor.fetchall()
            if not surveys_rows:
                return []

            survey_ids = [int(row[0]) for row in surveys_rows]
            visible_operator_ids = None
            if role == 'sv':
                visible_operator_ids = set(self._get_visible_operator_ids_for_requester_tx(
                    cursor,
                    requester_id,
                    role,
                    scope_department_id=scope_department_id
                ))

            cursor.execute(f"""
                SELECT
                    {self._SURVEY_QUESTION_COLUMNS}
                FROM survey_questions q
                WHERE q.survey_id = ANY(%s)
                ORDER BY q.survey_id, q.position, q.id
            """, (survey_ids,))
            questions_rows = cursor.fetchall()

            cursor.execute("""
                SELECT
                    sa.id,
                    sa.survey_id,
                    sa.operator_id,
                    u.name,
                    sa.status,
                    sa.assigned_at,
                    sa.completed_at,
                    u.status,
                    u.department_id,
                    sa.started_at
                FROM survey_assignments sa
                LEFT JOIN users u ON u.id = sa.operator_id
                WHERE sa.survey_id = ANY(%s)
                ORDER BY sa.survey_id, u.name, sa.id
            """, (survey_ids,))
            assignments_rows = cursor.fetchall()

            if visible_operator_ids is not None:
                assignments_rows = [row for row in assignments_rows if int(row[2]) in visible_operator_ids]

            cursor.execute("""
                SELECT
                    sr.id,
                    sr.survey_id,
                    sr.operator_id,
                    sr.submitted_at,
                    sr.started_at,
                    sr.earned_points,
                    sr.max_points,
                    sr.score_percent,
                    sr.is_auto_submitted,
                    (SELECT c.id FROM calls c WHERE c.survey_response_id = sr.id LIMIT 1)
                FROM survey_responses sr
                WHERE sr.survey_id = ANY(%s)
                ORDER BY sr.submitted_at DESC, sr.id DESC
            """, (survey_ids,))
            responses_rows = cursor.fetchall()

            if visible_operator_ids is not None:
                responses_rows = [row for row in responses_rows if int(row[2]) in visible_operator_ids]

            response_ids = [int(row[0]) for row in responses_rows]
            answers_rows = []
            if response_ids:
                cursor.execute("""
                    SELECT
                        a.response_id,
                        r.survey_id,
                        a.question_id,
                        a.selected_options_json,
                        a.answer_text,
                        a.rating_value
                    FROM survey_answers a
                    JOIN survey_responses r ON r.id = a.response_id
                    WHERE a.response_id = ANY(%s)
                """, (response_ids,))
                answers_rows = cursor.fetchall()

            return self._serialize_survey_list(
                surveys_rows=surveys_rows,
                questions_rows=questions_rows,
                assignments_rows=assignments_rows,
                responses_rows=responses_rows,
                answers_rows=answers_rows
            )

    # ── Страница списка, карточка и архив ─────────────────────────────────
    #
    # Раздел «Опросы» раньше жил одним запросом: /api/surveys выгружал ВСЕ
    # опросы со всеми вопросами, назначениями, ответами и статистикой — ради
    # списка заголовков слева. С ростом архива это и стало главным тормозом.
    #
    # Теперь их два: страница лёгких строк (заголовок + счётчики) и карточка
    # одного опроса. Счётчики считает сама база агрегатом, а не Python по
    # выгруженным назначениям.

    @staticmethod
    def _survey_assignment_visible_sql(role, scope_department_id, alias='sa'):
        """Какие назначения этот пользователь вправе видеть в счётчиках.

        Повторяет _get_visible_operator_ids_for_requester_tx, но предикатом:
        считать «пройдено N из M» в SQL можно только там же, где идёт агрегат,
        а тащить сюда список из тысяч id было бы тем же самым, от чего уходим.
        """
        if role_has_min(role, 'admin') or role == 'trainer':
            return 'TRUE', []

        if role == 'sv':
            scope_filter = ''
            params = []
            if scope_department_id is not None:
                scope_filter = ' AND vu.department_id = %s'
                params.append(int(scope_department_id))
            return (
                f"""EXISTS (
                    SELECT 1 FROM users vu
                     WHERE vu.id = {alias}.operator_id
                       AND LOWER(TRIM(COALESCE(vu.role, ''))) = 'operator'
                       AND COALESCE(NULLIF(LOWER(TRIM(COALESCE(vu.status, 'working'))), ''), 'working')
                           NOT IN ('fired', 'dismissal'){scope_filter}
                )""",
                params,
            )

        return 'FALSE', []

    @staticmethod
    def _survey_visible_sql(role, requester_id, scope_department_id):
        """Какие опросы вообще попадают в список — дословно прежние правила."""
        if role_has_min(role, 'admin') or role == 'trainer':
            return 'TRUE', []

        if role == 'sv':
            if scope_department_id is not None:
                return (
                    """(s.created_by = %s OR EXISTS (
                        SELECT 1 FROM survey_assignments sa2
                          JOIN users op2 ON op2.id = sa2.operator_id
                         WHERE sa2.survey_id = s.id AND op2.department_id = %s
                    ))""",
                    [int(requester_id), int(scope_department_id)],
                )
            return (
                """(s.created_by = %s OR EXISTS (
                    SELECT 1 FROM survey_assignments sa2
                      JOIN users op2 ON op2.id = sa2.operator_id
                     WHERE sa2.survey_id = s.id AND op2.supervisor_id = %s
                ))""",
                [int(requester_id), int(requester_id)],
            )

        return 'FALSE', []

    @staticmethod
    def _survey_search_pattern(search):
        text = str(search or '').strip()
        if not text:
            return None
        # Экранируем спецсимволы LIKE: без этого «%» в запросе означал бы
        # «что угодно», а не сам символ процента.
        escaped = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        return f'%{escaped}%'

    def _survey_page_envelope(self, items, total, page, page_size):
        total = max(0, int(total or 0))
        pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        return {
            'items': items,
            'total': total,
            'page': page,
            'page_size': page_size,
            'pages': pages,
        }

    def get_surveys_page(self, requester_id, requester_role, scope_department_id=None,
                         department_id=None, archived=False, search='', page=1, page_size=None):
        """Страница списка для руководителя: заголовок и счётчики, без ответов."""
        requester_id = int(requester_id)
        role = self._normalize_survey_role(requester_role)
        page, page_size = self._normalize_survey_page(
            page, page_size if page_size is not None else self.SURVEY_PAGE_SIZE_DEFAULT
        )

        if not (role_has_min(role, 'admin') or role in ('sv', 'trainer')):
            return self._survey_page_envelope([], 0, page, page_size)

        visible_sql, visible_params = self._survey_visible_sql(role, requester_id, scope_department_id)
        assignment_sql, assignment_params = self._survey_assignment_visible_sql(role, scope_department_id)

        join_conditions = [assignment_sql]
        join_params = list(assignment_params)
        # Фильтр по отделу в шапке раздела — это срез состава, а не прав:
        # опрос остаётся видимым, но «пройдено N из M» считается по людям
        # выбранного отдела, и опросы без таких людей из списка уходят.
        department_id = int(department_id) if department_id not in (None, '') else None
        if department_id is not None:
            join_conditions.append("""EXISTS (
                SELECT 1 FROM users du WHERE du.id = sa.operator_id AND du.department_id = %s
            )""")
            join_params.append(department_id)

        where_conditions = [visible_sql]
        where_params = list(visible_params)
        where_conditions.append('s.archived_at IS NOT NULL' if archived else 's.archived_at IS NULL')
        pattern = self._survey_search_pattern(search)
        if pattern:
            where_conditions.append("(s.title ILIKE %s OR COALESCE(s.description, '') ILIKE %s)")
            where_params.extend([pattern, pattern])

        having_sql = 'HAVING COUNT(sa.id) > 0' if department_id is not None else ''

        sql = f"""
            SELECT
                s.id,
                s.title,
                s.is_test,
                s.created_at,
                s.archived_at,
                s.starts_at,
                s.ends_at,
                s.single_attempt,
                s.affects_quality,
                s.repeat_root_id,
                s.repeat_iteration,
                COUNT(sa.id) AS assigned_count,
                COUNT(sa.id) FILTER (WHERE sa.status = 'completed') AS completed_count,
                COUNT(*) OVER () AS total_rows
            FROM surveys s
            LEFT JOIN survey_assignments sa
                   ON sa.survey_id = s.id AND {' AND '.join(join_conditions)}
            WHERE {' AND '.join(where_conditions)}
            GROUP BY s.id
            {having_sql}
            ORDER BY s.created_at DESC, s.id DESC
            LIMIT %s OFFSET %s
        """
        params = tuple(join_params + where_params + [page_size, (page - 1) * page_size])

        now = datetime.now()
        with self._get_cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        total = int(rows[0][13]) if rows else 0
        items = [self._serialize_survey_list_row(row, now=now) for row in rows]
        return self._survey_page_envelope(items, total, page, page_size)

    def _serialize_survey_list_row(self, row, now=None):
        survey_id = int(row[0])
        is_test = bool(row[2])
        starts_at = row[5]
        ends_at = row[6]
        assigned_count = int(row[11] or 0)
        completed_count = int(row[12] or 0)
        pending_count = max(0, assigned_count - completed_count)
        try:
            repeat_root_id = int(row[9]) if row[9] is not None else survey_id
        except Exception:
            repeat_root_id = survey_id
        try:
            repeat_iteration = max(1, int(row[10] or 1))
        except Exception:
            repeat_iteration = 1

        return {
            'id': survey_id,
            'title': row[1],
            'is_test': is_test,
            'created_at': self._survey_dt_to_iso(row[3]),
            'archived_at': self._survey_dt_to_iso(row[4]),
            'is_archived': row[4] is not None,
            'repeat': {
                'root_id': repeat_root_id,
                'iteration': repeat_iteration,
                'is_repeat': repeat_iteration > 1,
            },
            'test': {
                'status': self.survey_test_status(starts_at, ends_at, now=now),
                'starts_at': self._survey_dt_to_iso(starts_at),
                'ends_at': self._survey_dt_to_iso(ends_at),
                'single_attempt': bool(row[7]) if row[7] is not None else True,
                'affects_quality': bool(row[8]),
            } if is_test else None,
            'statistics': {
                'assigned_count': assigned_count,
                'completed_count': completed_count,
                'pending_count': pending_count,
                'completion_rate': round((completed_count / assigned_count) * 100, 1) if assigned_count else 0.0,
            },
        }

    def get_operator_surveys_page(self, operator_id, archived=False, search='', page=1, page_size=None):
        """Страница списка для оператора: назначенные ему опросы без вопросов."""
        operator_id = int(operator_id)
        page, page_size = self._normalize_survey_page(
            page, page_size if page_size is not None else self.SURVEY_PAGE_SIZE_DEFAULT
        )

        where_conditions = [
            'sa.operator_id = %s',
            """(NOT COALESCE(s.is_test, FALSE)
                OR s.starts_at IS NULL
                OR s.starts_at <= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))""",
            's.archived_at IS NOT NULL' if archived else 's.archived_at IS NULL',
        ]
        params = [operator_id]
        pattern = self._survey_search_pattern(search)
        if pattern:
            where_conditions.append("(s.title ILIKE %s OR COALESCE(s.description, '') ILIKE %s)")
            params.extend([pattern, pattern])

        # Колонки 0..13 — те же, что у управленческой страницы: их читает общий
        # _serialize_survey_list_row. Своё у оператора идёт следом, с 14-й.
        sql = f"""
            SELECT
                s.id,
                s.title,
                s.is_test,
                s.created_at,
                s.archived_at,
                s.starts_at,
                s.ends_at,
                s.single_attempt,
                s.affects_quality,
                s.repeat_root_id,
                s.repeat_iteration,
                1 AS assigned_count,
                CASE WHEN sa.status = 'completed' THEN 1 ELSE 0 END AS completed_count,
                COUNT(*) OVER () AS total_rows,
                sa.status,
                sa.completed_at
            FROM survey_assignments sa
            JOIN surveys s ON s.id = sa.survey_id
            WHERE {' AND '.join(where_conditions)}
            ORDER BY
                CASE WHEN sa.status = 'completed' THEN 1 ELSE 0 END,
                sa.assigned_at DESC,
                sa.id DESC
            LIMIT %s OFFSET %s
        """
        params.extend([page_size, (page - 1) * page_size])

        now = datetime.now()
        with self._get_cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()

        total = int(rows[0][13]) if rows else 0
        items = []
        for row in rows:
            item = self._serialize_survey_list_row(row, now=now)
            status = str(row[14] or 'assigned')
            is_completed = status == 'completed'
            single_attempt = bool(row[7]) if row[7] is not None else True
            test_status = (item.get('test') or {}).get('status')
            # Пройти можно только внутри окна теста; повторная попытка —
            # только если тест это разрешает. То же правило, что в карточке.
            can_submit = not is_completed
            if item['is_test']:
                can_submit = (
                    test_status == self.SURVEY_TEST_STATUS_ACTIVE
                    and (not is_completed or not single_attempt)
                )
            # Архив закрыт на ответы — сервер вернул бы SURVEY_ARCHIVED.
            # Показывать форму, которую нельзя отправить, нечестно.
            if item['is_archived']:
                can_submit = False
            item['my_assignment'] = {
                'status': status,
                'completed_at': self._survey_dt_to_iso(row[15]),
                'can_submit': bool(can_submit),
            }
            items.append(item)
        return self._survey_page_envelope(items, total, page, page_size)

    def get_survey_detail_for_requester(self, survey_id, requester_id, requester_role,
                                        scope_department_id=None):
        """Карточка одного опроса: вопросы, ответы и статистика.

        Руководителю отдаём и «соседей» по повторению: вкладка ответов сводит
        все прогоны одного опроса, и без вопросов соседнего прогона ответ
        не к чему привязать. Раньше эти данные приезжали в общем списке —
        то есть вместе со всеми остальными опросами разом.
        """
        survey_id = int(survey_id)
        role = self._normalize_survey_role(requester_role)

        if role == 'operator':
            surveys = self.get_surveys_for_operator(
                requester_id, survey_ids=[survey_id], include_archived=True
            )
            if not surveys:
                return None
            return {'survey': surveys[0], 'repetitions': []}

        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(repeat_root_id, id) FROM surveys WHERE id = %s",
                (survey_id,)
            )
            root_row = cursor.fetchone()
            if not root_row:
                return None
            root_id = int(root_row[0])
            cursor.execute(
                "SELECT id FROM surveys WHERE COALESCE(repeat_root_id, id) = %s",
                (root_id,)
            )
            family_ids = [int(row[0]) for row in cursor.fetchall()]

        surveys = self.get_surveys_for_management(
            requester_id,
            requester_role,
            scope_department_id=scope_department_id,
            survey_ids=family_ids or [survey_id]
        )
        selected = next((item for item in surveys if int(item.get('id')) == survey_id), None)
        if selected is None:
            return None

        # created_at нужен карточке запуска на вкладке «Ответы»: без даты
        # девять повторений одного опроса различались бы только номером.
        repetitions = [{
            'id': int(item.get('id')),
            'title': item.get('title'),
            'iteration': int((item.get('repeat') or {}).get('iteration') or 1),
            'created_at': item.get('created_at'),
            'questions': item.get('questions') or [],
        } for item in surveys if int(item.get('id')) != survey_id]

        return {'survey': selected, 'repetitions': repetitions}

    def count_pending_surveys(self, requester_id, requester_role, scope_department_id=None):
        """Число для бейджа раздела — одним COUNT вместо выгрузки всех опросов.

        Архивные не считаются: в этом и смысл архива.
        """
        requester_id = int(requester_id)
        role = self._normalize_survey_role(requester_role)

        if role == 'operator':
            sql = """
                SELECT COUNT(*)
                  FROM survey_assignments sa
                  JOIN surveys s ON s.id = sa.survey_id
                 WHERE sa.operator_id = %s
                   AND COALESCE(sa.status, '') <> 'completed'
                   AND s.archived_at IS NULL
                   AND (NOT COALESCE(s.is_test, FALSE)
                        OR ((s.starts_at IS NULL OR s.starts_at <= %s)
                            AND (s.ends_at IS NULL OR s.ends_at > %s)))
            """
            now = datetime.now()
            with self._get_cursor() as cursor:
                cursor.execute(sql, (requester_id, now, now))
                row = cursor.fetchone()
            return int(row[0] or 0) if row else 0

        if not (role_has_min(role, 'admin') or role in ('sv', 'trainer')):
            return 0

        visible_sql, visible_params = self._survey_visible_sql(role, requester_id, scope_department_id)
        assignment_sql, assignment_params = self._survey_assignment_visible_sql(role, scope_department_id)
        sql = f"""
            SELECT COUNT(*)
              FROM survey_assignments sa
              JOIN surveys s ON s.id = sa.survey_id
             WHERE COALESCE(sa.status, '') <> 'completed'
               AND s.archived_at IS NULL
               AND {assignment_sql}
               AND {visible_sql}
        """
        with self._get_cursor() as cursor:
            cursor.execute(sql, tuple(assignment_params + visible_params))
            row = cursor.fetchone()
        return int(row[0] or 0) if row else 0

    def archive_stale_surveys(self, older_than_days=None, now=None):
        """Уводит в архив опросы и закрытые тесты старше двух недель.

        Тест архивируется, только когда его окно уже НЕ идёт: закончилось или
        не было задано вовсе. Идущий тест не трогаем, даже если он создан
        месяц назад, — люди сейчас его проходят.

        Раньше тесты не архивировались совсем: считалось, что окно закроет их
        само. На проде оказалось иначе — у четырёх тестов окна не было вовсе
        (39–144 дня), и они вечно висели «активными», держа назначения в
        бейдже раздела и в колоколе у операторов.

        Возвращает число заархивированных — само уведомление владельцу уходит
        отдельным шагом, только после успешной отправки (см.
        collect_survey_archive_notifications).
        """
        threshold_days = int(older_than_days if older_than_days is not None else self.SURVEY_ARCHIVE_AFTER_DAYS)
        moment = now or datetime.now()
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE surveys
                   SET archived_at = %s
                 WHERE archived_at IS NULL
                   AND created_at <= %s - (%s * INTERVAL '1 day')
                   AND (NOT COALESCE(is_test, FALSE) OR ends_at IS NULL OR ends_at <= %s)
                RETURNING id
            """, (moment, moment, threshold_days, moment))
            return len(cursor.fetchall())

    def collect_survey_archive_notifications(self, limit=100):
        """Опросы, уехавшие в архив, о которых владельцу ещё не сообщили."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT s.id, s.title, s.archived_at, s.created_at, u.id, u.name, u.telegram_id,
                       (SELECT COUNT(*) FROM survey_assignments sa WHERE sa.survey_id = s.id),
                       (SELECT COUNT(*) FROM survey_assignments sa
                         WHERE sa.survey_id = s.id AND sa.status = 'completed')
                  FROM surveys s
                  JOIN users u ON u.id = s.created_by
                 WHERE s.archived_at IS NOT NULL
                   AND s.archive_notified_at IS NULL
                   AND u.telegram_id IS NOT NULL
                 ORDER BY s.archived_at ASC, s.id ASC
                 LIMIT %s
            """, (int(limit),))
            return [{
                'id': int(row[0]),
                'title': row[1] or f'Опрос #{row[0]}',
                'archived_at': row[2],
                'created_at': row[3],
                'owner_id': int(row[4]),
                'owner_name': row[5] or '',
                'chat_id': row[6],
                'assigned_count': int(row[7] or 0),
                'completed_count': int(row[8] or 0),
            } for row in cursor.fetchall()]

    def mark_survey_archive_notified(self, survey_id, now=None):
        with self._get_cursor() as cursor:
            cursor.execute(
                'UPDATE surveys SET archive_notified_at = %s WHERE id = %s AND archive_notified_at IS NULL',
                (now or datetime.now(), int(survey_id))
            )

    def get_surveys_for_operator(self, operator_id, survey_ids=None, include_archived=False):
        operator_id = int(operator_id)
        now = datetime.now()

        requested_ids = self._survey_int_id_list(survey_ids) if survey_ids is not None else None
        if requested_ids is not None and not requested_ids:
            return []
        id_filter = " AND s.id = ANY(%s)" if requested_ids is not None else ""
        # Архивный опрос оператору не показываем в списке, но открыть уже
        # пройденный по прямой ссылке он должен: иначе «мой результат»
        # исчезал бы вместе с уходом опроса в архив.
        archive_filter = "" if include_archived else " AND s.archived_at IS NULL"

        with self._get_cursor() as cursor:
            # Запланированный тест оператору не отдаём вообще: он не должен
            # знать ни вопросов, ни вариантов до времени запуска.
            operator_params = [operator_id]
            if requested_ids is not None:
                operator_params.append(requested_ids)
            cursor.execute(f"""
                SELECT
                    s.id,
                    s.title,
                    s.description,
                    s.created_at,
                    s.updated_at,
                    s.created_by,
                    s.direction_ids,
                    s.tenure_weeks_min,
                    s.tenure_weeks_max,
                    creator.id,
                    creator.name,
                    creator.role,
                    sa.id,
                    sa.status,
                    sa.assigned_at,
                    sa.completed_at,
                    sr.id,
                    sr.submitted_at,
                    s.repeat_root_id,
                    s.repeat_iteration,
                    s.is_test,
                    s.starts_at,
                    s.ends_at,
                    s.single_attempt,
                    s.affects_quality,
                    sa.started_at,
                    sr.score_percent,
                    sr.earned_points,
                    sr.max_points,
                    sr.is_auto_submitted,
                    sd.answers_json,
                    s.archived_at
                FROM survey_assignments sa
                JOIN surveys s ON s.id = sa.survey_id
                LEFT JOIN users creator ON creator.id = s.created_by
                LEFT JOIN survey_responses sr
                    ON sr.survey_id = sa.survey_id
                   AND sr.operator_id = sa.operator_id
                LEFT JOIN survey_attempt_drafts sd
                    ON sd.survey_id = sa.survey_id
                   AND sd.operator_id = sa.operator_id
                WHERE sa.operator_id = %s
                  AND (
                      NOT COALESCE(s.is_test, FALSE)
                      OR s.starts_at IS NULL
                      OR s.starts_at <= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                  ){archive_filter}{id_filter}
                ORDER BY
                    CASE WHEN sa.status = 'completed' THEN 1 ELSE 0 END,
                    sa.assigned_at DESC,
                    sa.id DESC
            """, tuple(operator_params))
            surveys_rows = cursor.fetchall()
            if not surveys_rows:
                return []

            survey_ids = [int(row[0]) for row in surveys_rows]
            cursor.execute(f"""
                SELECT
                    {self._SURVEY_QUESTION_COLUMNS}
                FROM survey_questions q
                WHERE q.survey_id = ANY(%s)
                ORDER BY q.survey_id, q.position, q.id
            """, (survey_ids,))
            question_rows = cursor.fetchall()

            questions_by_survey = defaultdict(list)
            question_positions_by_survey = defaultdict(dict)
            for row in question_rows:
                survey_id = int(row[1])
                question_id = int(row[0])
                position = int(row[2])
                options = row[7] if isinstance(row[7], list) else []
                correct_options = row[8] if len(row) > 8 and isinstance(row[8], list) else []
                questions_by_survey[survey_id].append({
                    'id': question_id,
                    'position': position,
                    'text': row[3],
                    'type': row[4],
                    'required': bool(row[5]),
                    'allow_other': bool(row[6]),
                    'options': [str(item) for item in options],
                    'correct_options': [str(item) for item in correct_options],
                    'points': self.survey_question_points({'points': row[9] if len(row) > 9 else None}),
                    'partial_credit': bool(row[10]) if len(row) > 10 else False
                })
                question_positions_by_survey[survey_id][question_id] = position

            response_ids = [int(row[16]) for row in surveys_rows if row[16] is not None]
            answers_by_response = defaultdict(list)
            if response_ids:
                cursor.execute("""
                    SELECT
                        a.response_id,
                        a.question_id,
                        a.selected_options_json,
                        a.answer_text,
                        a.rating_value
                    FROM survey_answers a
                    WHERE a.response_id = ANY(%s)
                """, (response_ids,))
                for answer_row in cursor.fetchall():
                    selected_options = answer_row[2] if isinstance(answer_row[2], list) else []
                    answers_by_response[int(answer_row[0])].append({
                        'question_id': int(answer_row[1]),
                        'selected_options': [str(item) for item in selected_options],
                        'answer_text': answer_row[3] or '',
                        'rating_value': answer_row[4]
                    })

            result = []
            for row in surveys_rows:
                survey_id = int(row[0])
                repeat_root_id = survey_id
                repeat_iteration = 1
                is_test = bool(row[20]) if len(row) > 20 else False
                if len(row) > 18:
                    try:
                        repeat_root_id = int(row[18]) if row[18] is not None else survey_id
                    except Exception:
                        repeat_root_id = survey_id
                if len(row) > 19:
                    try:
                        parsed_repeat_iteration = int(row[19]) if row[19] is not None else 1
                        repeat_iteration = parsed_repeat_iteration if parsed_repeat_iteration >= 1 else 1
                    except Exception:
                        repeat_iteration = 1

                starts_at = row[21] if len(row) > 21 else None
                ends_at = row[22] if len(row) > 22 else None
                single_attempt = bool(row[23]) if len(row) > 23 and row[23] is not None else True
                affects_quality = bool(row[24]) if len(row) > 24 else False
                archived_at = row[31] if len(row) > 31 else None
                is_archived = archived_at is not None

                questions_source = sorted(
                    questions_by_survey.get(survey_id, []),
                    key=lambda item: (item.get('position', 0), item.get('id', 0))
                )
                question_positions = question_positions_by_survey.get(survey_id, {})
                direction_ids = row[6] if isinstance(row[6], list) else []
                normalized_direction_ids = []
                for value in direction_ids:
                    try:
                        parsed = int(value)
                    except Exception:
                        continue
                    if parsed not in normalized_direction_ids:
                        normalized_direction_ids.append(parsed)

                assignment_status = str(row[13] or 'assigned')
                # Правильные ответы отдаём только когда попытка уже закрыта:
                # при разрешённом повторе внутри окна это была бы подсказка.
                reveal_correct_options = (
                    is_test
                    and assignment_status == 'completed'
                    and (
                        single_attempt
                        or self.survey_test_status(starts_at, ends_at, now=now) == self.SURVEY_TEST_STATUS_FINISHED
                    )
                )
                questions = []
                for question in questions_source:
                    question_copy = {
                        'id': int(question.get('id')),
                        'position': int(question.get('position') or 0),
                        'text': str(question.get('text') or ''),
                        'type': str(question.get('type') or ''),
                        'required': bool(question.get('required')),
                        'allow_other': bool(question.get('allow_other')),
                        'options': list(question.get('options') or [])
                    }
                    if is_test:
                        question_copy['points'] = self.survey_question_points(question)
                        question_copy['partial_credit'] = bool(question.get('partial_credit'))
                    if reveal_correct_options:
                        question_copy['correct_options'] = list(question.get('correct_options') or [])
                    questions.append(question_copy)

                response_id = int(row[16]) if row[16] is not None else None
                my_answers = list(answers_by_response.get(response_id, [])) if response_id is not None else []
                my_answers.sort(
                    key=lambda item: (
                        question_positions.get(int(item.get('question_id') or 0), 10 ** 9),
                        int(item.get('question_id') or 0)
                    )
                )

                my_answers_by_question = {}
                question_meta_by_id = {int(item.get('id')): item for item in questions_source}
                attempt_score = None
                if is_test:
                    attempt_score = self.score_survey_test_attempt(
                        questions_source,
                        {int(answer.get('question_id')): answer for answer in my_answers}
                    )
                for answer in my_answers:
                    question_id = int(answer.get('question_id'))
                    answer_selected_options = list(answer.get('selected_options') or [])
                    answer_text_value = str(answer.get('answer_text') or '')
                    answer_payload = {
                        'question_id': question_id,
                        'selected_options': answer_selected_options,
                        'answer_text': answer_text_value,
                        'rating_value': answer.get('rating_value')
                    }
                    if is_test:
                        question_meta = question_meta_by_id.get(question_id) or {}
                        per_question = (attempt_score or {}).get('per_question', {}).get(question_id) or {}
                        answer_payload['is_correct'] = bool(per_question.get('is_correct'))
                        answer_payload['is_partially_correct'] = bool(per_question.get('is_partially_correct'))
                        answer_payload['points'] = float(per_question.get('points') or self.survey_question_points(question_meta))
                        answer_payload['earned_points'] = float(per_question.get('earned_points') or 0.0)
                        if reveal_correct_options:
                            answer_payload['expected_options'] = list(question_meta.get('correct_options') or [])

                    my_answers_by_question[str(question_id)] = {
                        **answer_payload
                    }

                test_status = self.survey_test_status(starts_at, ends_at, now=now) if is_test else None
                draft_answers = self.survey_draft_answers_for_questions(
                    row[30] if len(row) > 30 else None,
                    questions_source
                )

                my_response = None
                if response_id is not None:
                    my_response = {
                        'id': response_id,
                        'submitted_at': self._survey_dt_to_iso(row[17]),
                        'answers': my_answers,
                        'answers_by_question': my_answers_by_question
                    }
                    if is_test:
                        stored_percent = row[26] if len(row) > 26 else None
                        stored_earned = row[27] if len(row) > 27 else None
                        stored_max = row[28] if len(row) > 28 else None
                        my_response['test_summary'] = {
                            'total_questions': int((attempt_score or {}).get('total_questions') or 0),
                            'answered_questions': int((attempt_score or {}).get('answered_questions') or 0),
                            'correct_answers': int((attempt_score or {}).get('correct_answers') or 0),
                            'earned_points': float(stored_earned) if stored_earned is not None else float((attempt_score or {}).get('earned_points') or 0.0),
                            'max_points': float(stored_max) if stored_max is not None else float((attempt_score or {}).get('max_points') or 0.0),
                            'score_percent': float(stored_percent) if stored_percent is not None else float((attempt_score or {}).get('score_percent') or 0.0),
                            'is_auto_submitted': bool(row[29]) if len(row) > 29 else False
                        }

                # Пройти можно только внутри окна теста. Повторная попытка —
                # только если тест это разрешает. Архивный опрос закрыт совсем:
                # его можно открыть по ссылке и посмотреть свой результат,
                # но отправить ответы уже нельзя.
                can_submit = assignment_status != 'completed'
                if is_test:
                    can_submit = (
                        test_status == self.SURVEY_TEST_STATUS_ACTIVE
                        and (assignment_status != 'completed' or not single_attempt)
                    )
                if is_archived:
                    can_submit = False

                result.append({
                    'id': survey_id,
                    'title': row[1],
                    'description': row[2] or '',
                    'created_at': self._survey_dt_to_iso(row[3]),
                    'updated_at': self._survey_dt_to_iso(row[4]),
                    'archived_at': self._survey_dt_to_iso(archived_at),
                    'is_archived': is_archived,
                    'created_by': {
                        'id': row[9],
                        'name': row[10] or 'Система',
                        'role': row[11] or 'unknown'
                    },
                    'repeat': {
                        'root_id': int(repeat_root_id),
                        'iteration': int(repeat_iteration),
                        'is_repeat': int(repeat_iteration) > 1
                    },
                    'is_test': bool(is_test),
                    'test': {
                        'status': test_status,
                        'starts_at': self._survey_dt_to_iso(starts_at),
                        'ends_at': self._survey_dt_to_iso(ends_at),
                        'single_attempt': bool(single_attempt),
                        'affects_quality': bool(affects_quality),
                        'max_points': float((attempt_score or {}).get('max_points') or 0.0),
                        'questions_count': int((attempt_score or {}).get('total_questions') or 0)
                    } if is_test else None,
                    'assignment': {
                        'direction_ids': normalized_direction_ids,
                        'tenure_weeks_min': row[7],
                        'tenure_weeks_max': row[8],
                        'operator_ids': [operator_id]
                    },
                    'questions': questions,
                    'my_assignment': {
                        'id': int(row[12]),
                        'status': assignment_status,
                        'assigned_at': self._survey_dt_to_iso(row[14]),
                        'completed_at': self._survey_dt_to_iso(row[15]),
                        'started_at': self._survey_dt_to_iso(row[25]) if len(row) > 25 else None,
                        'response_id': response_id,
                        'submitted_at': self._survey_dt_to_iso(row[17]),
                        'can_submit': can_submit,
                        'draft_answers': draft_answers
                    },
                    'my_response': my_response,
                    'statistics': {
                        'assigned_count': 1,
                        'completed_count': 1 if assignment_status == 'completed' else 0,
                        'pending_count': 0 if assignment_status == 'completed' else 1,
                        'responses_count': 1 if response_id is not None else 0,
                        'completion_rate': 100.0 if assignment_status == 'completed' else 0.0,
                        'question_stats': []
                    }
                })

            return result

    def survey_draft_answers_for_questions(self, draft_answers, questions):
        """
        Черновик начатой попытки без вариантов, которых в вопросе уже нет.

        Автор может править опрос, пока его проходят: тогда сохранённый выбор
        ссылается на исчезнувшую формулировку. Такой выбор — не ответ, и отдавать
        его оператору нельзя: вопрос выглядел бы отвеченным (галочка в бейдже,
        полный прогресс), а отправка упиралась бы в отказ по варианту, которого
        на экране уже нет. Пустой вопрос честнее — его видно и можно ответить.
        """
        if not isinstance(draft_answers, list):
            return []

        options_by_question = {}
        for question in questions or []:
            try:
                question_id = int(question.get('id'))
            except (TypeError, ValueError):
                continue
            options_by_question[question_id] = {str(item) for item in (question.get('options') or [])}

        cleaned = []
        seen_question_ids = set()
        for raw_answer in draft_answers:
            if not isinstance(raw_answer, dict):
                continue
            try:
                question_id = int(raw_answer.get('question_id'))
            except (TypeError, ValueError):
                continue
            if question_id in seen_question_ids or question_id not in options_by_question:
                continue
            seen_question_ids.add(question_id)
            allowed_options = options_by_question[question_id]
            selected = []
            for item in (raw_answer.get('selected_options') or []):
                text = str(item or '').strip()
                if text and text in allowed_options and text not in selected:
                    selected.append(text)
            if not selected:
                continue
            cleaned.append({'question_id': question_id, 'selected_options': selected})

        return cleaned

    def submit_survey_response(self, survey_id, operator_id, answers, auto_submitted=False):
        survey_id = int(survey_id)
        operator_id = int(operator_id)

        if not isinstance(answers, list):
            raise ValueError("SURVEY_ANSWERS_REQUIRED")

        with self._get_cursor() as cursor:
            return self._submit_survey_response_tx(
                cursor,
                survey_id=survey_id,
                operator_id=operator_id,
                answers=answers,
                auto_submitted=auto_submitted
            )

    def _submit_survey_response_tx(self, cursor, *, survey_id, operator_id, answers, auto_submitted=False):
        """
        Отправка попытки. Автоотправка по окончании окна теста идёт тем же путём,
        поэтому подсчёт баллов и передача в качество не могут разойтись
        с обычной отправкой; отличие одно — незаполненные вопросы не блокируют.
        """
        cursor.execute("""
            SELECT
                id,
                COALESCE(is_test, FALSE),
                starts_at,
                ends_at,
                COALESCE(single_attempt, TRUE),
                COALESCE(affects_quality, FALSE),
                title,
                created_by,
                archived_at
            FROM surveys
            WHERE id = %s
        """, (survey_id,))
        survey_row = cursor.fetchone()
        if not survey_row:
            raise ValueError("SURVEY_NOT_FOUND")
        # Архив — это закрытая дверь, а не только скрытая строка в списке:
        # иначе ответ мог бы прийти по уже сохранённой вкладке.
        if survey_row[8] is not None:
            raise ValueError("SURVEY_ARCHIVED")
        is_test = bool(survey_row[1])
        starts_at = survey_row[2]
        ends_at = survey_row[3]
        single_attempt = bool(survey_row[4])
        affects_quality = bool(survey_row[5])
        survey_title = str(survey_row[6] or '')
        survey_created_by = survey_row[7]

        if is_test and not auto_submitted:
            test_status = self.survey_test_status(starts_at, ends_at)
            if test_status == self.SURVEY_TEST_STATUS_SCHEDULED:
                raise ValueError("SURVEY_TEST_NOT_STARTED")
            if test_status == self.SURVEY_TEST_STATUS_FINISHED:
                raise ValueError("SURVEY_TEST_FINISHED")

        cursor.execute("""
            SELECT id, status
            FROM survey_assignments
            WHERE survey_id = %s AND operator_id = %s
        """, (survey_id, operator_id))
        assignment_row = cursor.fetchone()
        if not assignment_row:
            raise PermissionError("SURVEY_NOT_ASSIGNED")

        assignment_id = int(assignment_row[0])
        assignment_status = str(assignment_row[1] or 'assigned')
        # Опрос всегда одноразовый; у теста повтор разрешает переключатель
        # «одна попытка».
        if assignment_status == 'completed' and (not is_test or single_attempt):
            raise ValueError("SURVEY_ALREADY_COMPLETED")

        cursor.execute("""
            SELECT
                id,
                question_text,
                question_type,
                is_required,
                allow_other,
                options_json,
                correct_options_json,
                points,
                partial_credit
            FROM survey_questions
            WHERE survey_id = %s
            ORDER BY position, id
        """, (survey_id,))
        question_rows = cursor.fetchall()
        if not question_rows:
            raise ValueError("SURVEY_HAS_NO_QUESTIONS")

        questions_by_id = {}
        for row in question_rows:
            options = row[5] if isinstance(row[5], list) else []
            correct_options = row[6] if len(row) > 6 and isinstance(row[6], list) else []
            questions_by_id[int(row[0])] = {
                'id': int(row[0]),
                # Номер вопроса — тот же, что видит оператор в форме: вопросы
                # идут по position, id, и бейдж рисует порядковый номер.
                'number': len(questions_by_id) + 1,
                'text': row[1],
                'type': row[2],
                'required': bool(row[3]),
                'allow_other': bool(row[4]),
                'options': [str(item) for item in options],
                'correct_options': [str(item) for item in correct_options],
                'points': self.survey_question_points({'points': row[7] if len(row) > 7 else None}),
                'partial_credit': bool(row[8]) if len(row) > 8 else False
            }

        answers_by_question = {}
        for raw_answer in answers:
            if not isinstance(raw_answer, dict):
                continue
            raw_question_id = raw_answer.get('question_id')
            try:
                question_id = int(raw_question_id)
            except Exception:
                continue
            if question_id in questions_by_id:
                answers_by_question[question_id] = raw_answer

        normalized_answers = []
        for question in questions_by_id.values():
            raw_answer = answers_by_question.get(question['id']) or {}
            qtype = question['type']
            if is_test and qtype not in ('single', 'multiple'):
                raise ValueError(f"SURVEY_TEST_RATING_NOT_ALLOWED_{question['id']}")

            answer_text = str(raw_answer.get('answer_text') or '').strip()
            if is_test:
                answer_text = ''
            if answer_text and not question.get('allow_other'):
                answer_text = ''

            if qtype == 'rating':
                rating_value = raw_answer.get('rating_value')
                if rating_value in (None, ''):
                    if question['required'] and not auto_submitted:
                        raise ValueError(f"SURVEY_REQUIRED_QUESTION_{question['id']}")
                    continue
                try:
                    rating_value = int(rating_value)
                except Exception:
                    raise ValueError(f"SURVEY_INVALID_RATING_{question['id']}")
                if rating_value < 1 or rating_value > 5:
                    raise ValueError(f"SURVEY_INVALID_RATING_{question['id']}")

                normalized_answers.append({
                    'question_id': question['id'],
                    'selected_options': [],
                    'answer_text': None,
                    'rating_value': rating_value
                })
                continue

            raw_selected_options = raw_answer.get('selected_options')
            if isinstance(raw_selected_options, list):
                selected_options = [str(item or '').strip() for item in raw_selected_options if str(item or '').strip()]
            elif raw_selected_options is None:
                selected_options = []
            else:
                one = str(raw_selected_options).strip()
                selected_options = [one] if one else []

            selected_unique = []
            for selected in selected_options:
                if selected not in selected_unique:
                    selected_unique.append(selected)

            allowed_options = set(question.get('options') or [])
            invalid_selected = [item for item in selected_unique if item not in allowed_options]
            if invalid_selected and not question.get('allow_other'):
                # Варианта в вопросе больше нет: автор переписал формулировку,
                # пока оператор проходил опрос. Живому оператору называем номер
                # вопроса — иначе он упирается в «недопустимый вариант» и не
                # может понять, где именно. Автоотправку по окончании окна это
                # ронять не должно: иначе попытка пропала бы целиком, поэтому
                # исчезнувший выбор просто не засчитывается.
                if not auto_submitted:
                    raise ValueError(f"SURVEY_OPTION_CHANGED_{question['number']}")
                selected_unique = [item for item in selected_unique if item in allowed_options]
                invalid_selected = []

            valid_selected = [item for item in selected_unique if item in allowed_options]
            if qtype == 'single' and len(valid_selected) > 1:
                raise ValueError(f"SURVEY_TOO_MANY_OPTIONS_{question['id']}")

            if qtype == 'single' and answer_text:
                valid_selected = []
                invalid_selected = []

            if (
                question['required']
                and not auto_submitted
                and not valid_selected
                and not answer_text
                and not (question.get('allow_other') and invalid_selected)
            ):
                raise ValueError(f"SURVEY_REQUIRED_QUESTION_{question['id']}")

            if question.get('allow_other') and invalid_selected and not answer_text:
                answer_text = ', '.join(invalid_selected)

            if answer_text and len(answer_text) > self.SURVEY_OTHER_ANSWER_MAX_LENGTH:
                raise ValueError(f"SURVEY_OTHER_TEXT_TOO_LONG_{question['id']}")

            if not valid_selected and not answer_text:
                continue

            normalized_answers.append({
                'question_id': question['id'],
                'selected_options': valid_selected,
                'answer_text': answer_text or None,
                'rating_value': None
            })

        # Автоотправка сохраняет даже пустую попытку: «не ответил» — это
        # результат 0, а не отсутствие попытки.
        if not normalized_answers and not auto_submitted:
            raise ValueError("SURVEY_EMPTY_RESPONSE")

        attempt_score = None
        if is_test:
            attempt_score = self.score_survey_test_attempt(
                list(questions_by_id.values()),
                {answer['question_id']: answer for answer in normalized_answers}
            )

        cursor.execute("""
            SELECT started_at
            FROM survey_attempt_drafts
            WHERE survey_id = %s AND operator_id = %s
        """, (survey_id, operator_id))
        draft_row = cursor.fetchone()
        attempt_started_at = draft_row[0] if draft_row else None

        cursor.execute("""
            SELECT id
            FROM survey_responses
            WHERE survey_id = %s AND operator_id = %s
        """, (survey_id, operator_id))
        response_row = cursor.fetchone()

        if response_row:
            response_id = int(response_row[0])
            cursor.execute("DELETE FROM survey_answers WHERE response_id = %s", (response_id,))
            cursor.execute("""
                UPDATE survey_responses
                SET
                    assignment_id = %s,
                    submitted_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    started_at = COALESCE(%s, started_at),
                    earned_points = %s,
                    max_points = %s,
                    score_percent = %s,
                    is_auto_submitted = %s
                WHERE id = %s
            """, (
                assignment_id,
                attempt_started_at,
                (attempt_score or {}).get('earned_points'),
                (attempt_score or {}).get('max_points'),
                (attempt_score or {}).get('score_percent'),
                bool(auto_submitted),
                response_id
            ))
        else:
            cursor.execute("""
                INSERT INTO survey_responses (
                    survey_id,
                    operator_id,
                    assignment_id,
                    submitted_at,
                    started_at,
                    earned_points,
                    max_points,
                    score_percent,
                    is_auto_submitted
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                RETURNING id
            """, (
                survey_id,
                operator_id,
                assignment_id,
                attempt_started_at,
                (attempt_score or {}).get('earned_points'),
                (attempt_score or {}).get('max_points'),
                (attempt_score or {}).get('score_percent'),
                bool(auto_submitted)
            ))
            response_id = int(cursor.fetchone()[0])

        for answer in normalized_answers:
            cursor.execute("""
                INSERT INTO survey_answers (
                    response_id,
                    question_id,
                    answer_text,
                    selected_options_json,
                    rating_value,
                    created_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s,
                    (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                )
            """, (
                response_id,
                answer['question_id'],
                answer['answer_text'],
                json.dumps(answer['selected_options'], ensure_ascii=False),
                answer['rating_value']
            ))

        cursor.execute("""
            UPDATE survey_assignments
            SET
                status = 'completed',
                completed_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                started_at = COALESCE(started_at, %s)
            WHERE id = %s
        """, (attempt_started_at, assignment_id))

        # Черновик больше не нужен: попытка закрыта.
        cursor.execute("""
            DELETE FROM survey_attempt_drafts
            WHERE survey_id = %s AND operator_id = %s
        """, (survey_id, operator_id))

        quality_call_id = None
        if is_test and affects_quality:
            quality_call_id = self._upsert_knowledge_test_evaluation_tx(
                cursor,
                survey_id=survey_id,
                survey_title=survey_title,
                survey_created_by=survey_created_by,
                operator_id=operator_id,
                response_id=response_id,
                score_percent=float((attempt_score or {}).get('score_percent') or 0.0)
            )

        cursor.execute("SELECT completed_at FROM survey_assignments WHERE id = %s", (assignment_id,))
        completed_row = cursor.fetchone()
        completed_at = completed_row[0] if completed_row else None

        return {
            'survey_id': survey_id,
            'assignment_id': assignment_id,
            'response_id': response_id,
            'completed_at': self._survey_dt_to_iso(completed_at),
            'is_test': bool(is_test),
            'auto_submitted': bool(auto_submitted),
            'test_summary': {
                'total_questions': int((attempt_score or {}).get('total_questions') or 0),
                'answered_questions': int((attempt_score or {}).get('answered_questions') or 0),
                'correct_answers': int((attempt_score or {}).get('correct_answers') or 0),
                'earned_points': float((attempt_score or {}).get('earned_points') or 0.0),
                'max_points': float((attempt_score or {}).get('max_points') or 0.0),
                'score_percent': float((attempt_score or {}).get('score_percent') or 0.0),
                'sent_to_quality': quality_call_id is not None,
                'quality_call_id': quality_call_id
            } if is_test else None
        }

    def save_survey_attempt_draft(self, survey_id, operator_id, answers):
        """
        Промежуточное сохранение начатой попытки теста. Нужно, чтобы по
        окончании окна автоотправка сохранила уже выбранные ответы.
        """
        survey_id = int(survey_id)
        operator_id = int(operator_id)
        if not isinstance(answers, list):
            raise ValueError("SURVEY_ANSWERS_REQUIRED")

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT COALESCE(is_test, FALSE), starts_at, ends_at, COALESCE(single_attempt, TRUE)
                FROM surveys
                WHERE id = %s
            """, (survey_id,))
            survey_row = cursor.fetchone()
            if not survey_row:
                raise ValueError("SURVEY_NOT_FOUND")
            if not bool(survey_row[0]):
                raise ValueError("SURVEY_NOT_A_TEST")

            test_status = self.survey_test_status(survey_row[1], survey_row[2])
            if test_status == self.SURVEY_TEST_STATUS_SCHEDULED:
                raise ValueError("SURVEY_TEST_NOT_STARTED")
            if test_status == self.SURVEY_TEST_STATUS_FINISHED:
                raise ValueError("SURVEY_TEST_FINISHED")

            cursor.execute("""
                SELECT id, status
                FROM survey_assignments
                WHERE survey_id = %s AND operator_id = %s
            """, (survey_id, operator_id))
            assignment_row = cursor.fetchone()
            if not assignment_row:
                raise PermissionError("SURVEY_NOT_ASSIGNED")
            if str(assignment_row[1] or '') == 'completed' and bool(survey_row[3]):
                raise ValueError("SURVEY_ALREADY_COMPLETED")

            allowed_question_ids = set()
            cursor.execute("SELECT id FROM survey_questions WHERE survey_id = %s", (survey_id,))
            for row in cursor.fetchall():
                allowed_question_ids.add(int(row[0]))

            draft_answers = []
            seen_question_ids = set()
            for raw_answer in answers:
                if not isinstance(raw_answer, dict):
                    continue
                try:
                    question_id = int(raw_answer.get('question_id'))
                except Exception:
                    continue
                if question_id not in allowed_question_ids or question_id in seen_question_ids:
                    continue
                seen_question_ids.add(question_id)
                raw_selected = raw_answer.get('selected_options')
                selected = []
                if isinstance(raw_selected, list):
                    for item in raw_selected:
                        text = str(item or '').strip()
                        if text and text not in selected:
                            selected.append(text)
                draft_answers.append({
                    'question_id': question_id,
                    'selected_options': selected
                })

            cursor.execute("""
                INSERT INTO survey_attempt_drafts (survey_id, operator_id, answers_json, started_at, updated_at)
                VALUES (
                    %s,
                    %s,
                    %s::jsonb,
                    (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                )
                ON CONFLICT (survey_id, operator_id) DO UPDATE
                SET
                    answers_json = EXCLUDED.answers_json,
                    updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                RETURNING started_at
            """, (survey_id, operator_id, json.dumps(draft_answers, ensure_ascii=False)))
            started_at = cursor.fetchone()[0]

            cursor.execute("""
                UPDATE survey_assignments
                SET
                    status = CASE WHEN status = 'assigned' THEN 'in_progress' ELSE status END,
                    started_at = COALESCE(started_at, %s)
                WHERE id = %s
            """, (started_at, int(assignment_row[0])))

            return {
                'survey_id': survey_id,
                'started_at': self._survey_dt_to_iso(started_at),
                'answers_count': len(draft_answers)
            }

    def autoclose_due_survey_tests(self):
        """
        Тесты, у которых истекло время: начатые попытки отправляются
        автоматически с уже выбранными ответами, чтобы результат не потерялся.
        Не начатые попытки остаются незавершёнными — оператор к тесту не заходил.
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT d.survey_id, d.operator_id
                FROM survey_attempt_drafts d
                JOIN surveys s ON s.id = d.survey_id
                WHERE COALESCE(s.is_test, FALSE)
                  AND s.ends_at IS NOT NULL
                  AND s.ends_at <= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                ORDER BY d.survey_id, d.operator_id
            """)
            pending = [(int(row[0]), int(row[1])) for row in cursor.fetchall()]

        submitted = 0
        failed = 0
        for survey_id, operator_id in pending:
            try:
                with self._get_cursor() as cursor:
                    cursor.execute("""
                        SELECT answers_json
                        FROM survey_attempt_drafts
                        WHERE survey_id = %s AND operator_id = %s
                    """, (survey_id, operator_id))
                    draft_row = cursor.fetchone()
                    if not draft_row:
                        continue
                    draft_answers = draft_row[0] if isinstance(draft_row[0], list) else []
                    self._submit_survey_response_tx(
                        cursor,
                        survey_id=survey_id,
                        operator_id=operator_id,
                        answers=draft_answers,
                        auto_submitted=True
                    )
                submitted += 1
            except ValueError as value_error:
                # Попытка уже закрыта — черновик убираем, иначе job будет
                # безуспешно возвращаться к нему каждую минуту.
                failed += 1
                if str(value_error) == 'SURVEY_ALREADY_COMPLETED':
                    try:
                        with self._get_cursor() as cursor:
                            cursor.execute("""
                                DELETE FROM survey_attempt_drafts
                                WHERE survey_id = %s AND operator_id = %s
                            """, (survey_id, operator_id))
                    except Exception:
                        logging.exception(
                            "Cleanup of stale survey draft %s/%s failed", survey_id, operator_id
                        )
                else:
                    logging.exception(
                        "Auto-submit of survey test %s for operator %s failed", survey_id, operator_id
                    )
            except Exception:
                failed += 1
                logging.exception(
                    "Auto-submit of survey test %s for operator %s failed", survey_id, operator_id
                )

        return {'submitted': submitted, 'failed': failed, 'candidates': len(pending)}
