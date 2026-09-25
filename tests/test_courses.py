"""Money courses: three built-in courses with 10 four-answer questions, the quiz, and the admin pages."""

import pytest
from sqlalchemy import select

from app.data.courses import COURSES
from app.extensions import db
from app.models import Answer, AuditLog, Course, CourseAttempt, Question
from app.services import courses as course_service


def test_three_courses_each_with_ten_questions_of_four_answers_and_one_correct():
    assert len(COURSES) == 3
    for course in COURSES:
        assert len(course.questions) == 10, course.slug
        for q in course.questions:
            assert len(q.answers) == 4 and len(set(q.answers)) == 4, q.text
            assert 0 <= q.correct <= 3
    corrects = [q.correct for c in COURSES for q in c.questions]
    assert len(set(corrects)) == 4  # the right answer is not always in the same place


@pytest.fixture
def student(client, make_user, login):
    user = make_user(first="Lindi", last="Zulu", email="lindi@dut4life.ac.za")
    login(user)
    client.user = user
    return client


def answers_for(course, right=10):
    """Form data answering the first ``right`` questions correctly and the rest wrongly."""
    data = {}
    for index, q in enumerate(course.questions):
        pick = q.correct if index < right else next(a for a in q.answers if not a.IsCorrect)
        data[f"q_{q.QuestionId}"] = pick.AnswerId
    return data


def test_courses_page_loads_the_courses_on_first_visit(student):
    html = student.get("/courses").get_data(as_text=True)
    assert "Budgeting basics on R1 750" in html and "Smart saving for students" in html and "Shop smart" in html
    assert db.session.query(Course).count() == 3 and db.session.query(Question).count() == 30
    assert db.session.query(Answer).count() == 120
    assert db.session.query(Answer).filter(Answer.IsCorrect.is_(True)).count() == 30


def test_taking_a_quiz_scores_it_and_shows_the_right_answers(student):
    student.get("/courses")
    course = course_service.get_by_slug("smart-saving")
    page = student.get("/courses/smart-saving").get_data(as_text=True)
    assert "Pay yourself first" in page and page.count('type="radio"') == 40

    response = student.post("/courses/smart-saving", data=answers_for(course, right=8))
    assert response.status_code == 302
    attempt = db.session.scalar(select(CourseAttempt))
    assert (attempt.Score, attempt.Total, attempt.passed) == (8, 10, True)
    result = student.get(response.headers["Location"]).get_data(as_text=True)
    assert "8/10" in result and "you passed" in result and result.count("Correct answer:") == 2

    student.post("/courses/smart-saving", data=answers_for(course, right=5))
    listing = student.get("/courses").get_data(as_text=True)
    assert "Best: 8/10 (80%)" in listing


def test_every_question_must_be_answered(student):
    student.get("/courses")
    course = course_service.get_by_slug("budgeting-basics")
    data = answers_for(course)
    data.pop(f"q_{course.questions[2].QuestionId}")
    response = student.post("/courses/budgeting-basics", data=data)
    assert response.status_code == 400 and "Still open: question 3" in response.get_data(as_text=True)
    assert db.session.query(CourseAttempt).count() == 0


def test_a_student_cannot_open_another_students_result(student, make_user, login, client):
    student.get("/courses")
    course = course_service.get_by_slug("shop-smart")
    url = student.post("/courses/shop-smart", data=answers_for(course)).headers["Location"]
    login(make_user())
    assert client.get(url).status_code == 404


@pytest.fixture
def boss(client, make_user, login):
    course_service.ensure_courses()
    admin = make_user(email="boss@example.com", IsAdmin=True)
    login(admin)
    return client


def test_admin_sees_courses_and_who_attempted_them(boss, make_user):
    course = course_service.get_by_slug("smart-saving")
    lindi = make_user(first="Lindi", last="Zulu", email="lindi@dut4life.ac.za")
    db.session.add(CourseAttempt(CourseId=course.CourseId, UserId=lindi.UserId, Score=9, Total=10))
    db.session.commit()
    listing = boss.get("/admin/courses").get_data(as_text=True)
    assert 'data-stat="attempts-smart-saving">1<' in listing
    page = boss.get(f"/admin/courses/{course.CourseId}/attempts").get_data(as_text=True)
    assert "Lindi Zulu" in page and "9/10 (90%)" in page and "Passed" in page


def test_admin_adds_and_removes_questions_and_answers(boss):
    course = course_service.get_by_slug("budgeting-basics")
    url = f"/admin/courses/{course.CourseId}/questions"
    bad = boss.post(url, data={"text": "What is 10% of R1 750?", "answer_0": "R175", "answer_1": "R17.50"})
    assert bad.status_code == 400 and "Choose which answer is correct" in bad.get_data(as_text=True)

    ok = boss.post(
        url,
        data={
            "text": "What is 10% of R1 750?",
            "answer_0": "R17.50",
            "answer_1": "R175",
            "answer_2": "R350",
            "answer_3": "R1 575",
            "correct": "1",
            "explanation": "1 750 / 10",
        },
    )
    assert ok.status_code == 302
    db.session.expire_all()
    question = course.questions[-1]
    assert len(course.questions) == 11 and question.correct.Text == "R175" and len(question.answers) == 4

    wrong = next(a for a in question.answers if not a.IsCorrect)
    boss.post(f"/admin/answers/{wrong.AnswerId}/delete")
    boss.post(f"/admin/questions/{question.QuestionId}/answers", data={"text": "R200", "correct": ""})
    db.session.expire_all()
    assert [a.Text for a in question.answers] == ["R175", "R350", "R1 575", "R200"]

    refused = boss.post(f"/admin/answers/{question.correct.AnswerId}/delete", follow_redirects=True)
    assert "Mark another answer as correct first" in refused.get_data(as_text=True)
    new_right = next(a for a in question.answers if a.Text == "R200")
    boss.post(f"/admin/answers/{new_right.AnswerId}/correct")
    db.session.expire_all()
    assert question.correct.Text == "R200" and sum(a.IsCorrect for a in question.answers) == 1

    boss.post(f"/admin/questions/{question.QuestionId}/delete")
    db.session.expire_all()
    assert len(course.questions) == 10 and [q.Position for q in course.questions] == list(range(1, 11))
    actions = set(db.session.scalars(select(AuditLog.Action)))
    assert {"question.create", "question.delete", "answer.create", "answer.delete", "answer.mark_correct"} <= actions


def test_students_cannot_open_the_admin_course_pages(student):
    assert student.get("/admin/courses").status_code == 403


def test_seed_courses_command_is_safe_to_repeat(app):
    runner = app.test_cli_runner()
    assert "3 added" in runner.invoke(args=["seed-courses"]).output
    assert "0 added" in runner.invoke(args=["seed-courses"]).output
    assert db.session.query(Question).count() == 30
