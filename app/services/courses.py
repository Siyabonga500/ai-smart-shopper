"""Short courses: loading the built-in ones, grading a quiz, and the admin's question and answer edits.

Rules kept for every question: at least two answers, at most six, and exactly one correct answer.
Callers commit (together with the audit row for admin changes).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from app.data.courses import COURSES
from app.extensions import db
from app.models import Answer, Course, CourseAttempt, Question, User

MIN_ANSWERS, MAX_ANSWERS = 2, 6


class CourseError(ValueError):
    """A change that is refused; the message is safe to show."""


# ---------------------------------------------------------------------------------------------- seeding
def add_missing_courses() -> int:
    """Add the built-in courses that are not in the database yet (never touches one an admin edited)."""
    have = set(db.session.scalars(select(Course.Slug)))
    added = 0
    for position, seed in enumerate(COURSES, 1):
        if seed.slug in have:
            continue
        course = Course(
            Slug=seed.slug,
            Title=seed.title,
            Summary=seed.summary,
            Content=seed.content,
            Minutes=seed.minutes,
            Icon=seed.icon,
            Position=position,
        )
        for q_pos, q in enumerate(seed.questions, 1):
            question = Question(Text=q.text, Explanation=q.explanation, Position=q_pos)
            for a_pos, text in enumerate(q.answers, 1):
                question.answers.append(Answer(Text=text, IsCorrect=(a_pos - 1 == q.correct), Position=a_pos))
            course.questions.append(question)
        db.session.add(course)
        added += 1
    return added


def ensure_courses() -> None:
    """Load the built-in courses the first time anyone opens the Courses pages."""
    if not db.session.scalar(select(func.count()).select_from(Course)):
        add_missing_courses()
        db.session.commit()


# ---------------------------------------------------------------------------------------------- students
@dataclass
class CourseCard:
    course: Course
    question_count: int
    best: CourseAttempt | None
    attempts: int


def published() -> list[Course]:
    return list(db.session.scalars(select(Course).where(Course.IsPublished.is_(True)).order_by(Course.Position)))


def cards_for(user_id: str) -> list[CourseCard]:
    cards = []
    for course in published():
        mine = [a for a in course.attempts if a.UserId == user_id]
        best = max(mine, key=lambda a: (a.percent, a.CreatedOn), default=None)
        cards.append(CourseCard(course, len(course.questions), best, len(mine)))
    return cards


def get_by_slug(slug: str, published_only: bool = True) -> Course | None:
    query = select(Course).where(Course.Slug == slug)
    if published_only:
        query = query.where(Course.IsPublished.is_(True))
    return db.session.scalar(query)


def grade(course: Course, user: User, form) -> tuple[CourseAttempt, list[str]]:
    """Mark the submitted quiz (``q_<question id>`` = answer id). Returns the saved attempt, or the unanswered questions."""
    choices, missing = {}, []
    score = 0
    for number, question in enumerate(course.questions, 1):
        picked = form.get(f"q_{question.QuestionId}")
        if not picked or picked not in {a.AnswerId for a in question.answers}:
            missing.append(str(number))
            continue
        choices[question.QuestionId] = picked
        if question.correct is not None and question.correct.AnswerId == picked:
            score += 1
    if missing:
        return None, missing
    attempt = CourseAttempt(
        CourseId=course.CourseId, UserId=user.UserId, Score=score, Total=len(course.questions), Choices=choices
    )
    db.session.add(attempt)
    return attempt, []


def attempt_for(user_id: str, attempt_id: str) -> CourseAttempt | None:
    attempt = db.session.get(CourseAttempt, attempt_id)
    return attempt if attempt is not None and attempt.UserId == user_id else None


# ---------------------------------------------------------------------------------------------- admin
def all_courses() -> list[Course]:
    return list(db.session.scalars(select(Course).order_by(Course.Position, Course.Title)))


def attempts_of(course: Course) -> list[CourseAttempt]:
    return list(
        db.session.scalars(
            select(CourseAttempt)
            .where(CourseAttempt.CourseId == course.CourseId)
            .order_by(CourseAttempt.CreatedOn.desc())
        )
    )


def attempt_stats(course: Course) -> dict:
    attempts = course.attempts
    people = {a.UserId for a in attempts}
    return {
        "attempts": len(attempts),
        "people": len(people),
        "average": round(sum(a.percent for a in attempts) / len(attempts)) if attempts else 0,
        "passed": len({a.UserId for a in attempts if a.passed}),
    }


def _clean(text: str | None, limit: int, what: str) -> str:
    value = " ".join((text or "").split())
    if not value:
        raise CourseError(f"Enter the {what}.")
    if len(value) > limit:
        raise CourseError(f"The {what} can be at most {limit} characters.")
    return value


def add_question(
    course: Course, text: str, answers: list[str], correct: int | None, explanation: str | None
) -> Question:
    """A new question at the end of the course. ``answers`` are the typed answers (blanks skipped), ``correct`` the
    index of the right one among those typed."""
    text = _clean(text, 300, "question")
    typed = [(i, " ".join((a or "").split())) for i, a in enumerate(answers)]
    typed = [(i, a) for i, a in typed if a]
    if len(typed) < MIN_ANSWERS:
        raise CourseError(f"Give at least {MIN_ANSWERS} answers (four is best).")
    if len(typed) > MAX_ANSWERS:
        raise CourseError(f"A question can have at most {MAX_ANSWERS} answers.")
    if any(len(a) > 200 for _, a in typed):
        raise CourseError("Each answer can be at most 200 characters.")
    if correct is None or correct not in {i for i, _ in typed}:
        raise CourseError("Choose which answer is correct (it must not be empty).")
    explanation = " ".join((explanation or "").split())[:300] or None
    position = max((q.Position for q in course.questions), default=0) + 1
    question = Question(Text=text, Explanation=explanation, Position=position)
    for a_pos, (index, answer) in enumerate(typed, 1):
        question.answers.append(Answer(Text=answer, IsCorrect=index == correct, Position=a_pos))
    course.questions.append(question)
    return question


def add_answer(question: Question, text: str, correct: bool) -> Answer:
    text = _clean(text, 200, "answer")
    if len(question.answers) >= MAX_ANSWERS:
        raise CourseError(f"A question can have at most {MAX_ANSWERS} answers.")
    if correct:
        for answer in question.answers:
            answer.IsCorrect = False  # exactly one correct answer
    answer = Answer(
        Text=text, IsCorrect=bool(correct), Position=max((a.Position for a in question.answers), default=0) + 1
    )
    question.answers.append(answer)
    return answer


def mark_correct(answer: Answer) -> None:
    for other in answer.question.answers:
        other.IsCorrect = other.AnswerId == answer.AnswerId


def delete_answer(answer: Answer) -> None:
    question = answer.question
    if answer.IsCorrect:
        raise CourseError("This is the correct answer. Mark another answer as correct first, then remove this one.")
    if len(question.answers) <= MIN_ANSWERS:
        raise CourseError(f"A question needs at least {MIN_ANSWERS} answers.")
    question.answers.remove(answer)
    db.session.delete(answer)


def delete_question(question: Question) -> None:
    course = question.course
    course.questions.remove(question)
    db.session.delete(question)
    for position, remaining in enumerate(course.questions, 1):
        remaining.Position = position
