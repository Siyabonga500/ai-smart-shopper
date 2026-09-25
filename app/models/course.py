"""Short courses on saving and budgeting (Courses page), each with a multiple-choice quiz.

``Course`` 1-n ``Question`` 1-n ``Answer`` (exactly one answer per question is correct). ``CourseAttempt`` is one
student taking one course's quiz: the score and which answer was picked for each question. Admins manage the
questions and answers and see the attempts under Admin > Courses.
"""

from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true

from app.extensions import db
from app.utils.dates import utcnow
from app.utils.ids import ANSWER_PREFIX, ATTEMPT_PREFIX, COURSE_PREFIX, QUESTION_PREFIX, generate_id


class Course(db.Model):
    __tablename__ = "courses"

    CourseId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(COURSE_PREFIX))
    Slug = db.Column(db.String(80), nullable=False, unique=True)
    Title = db.Column(db.String(120), nullable=False)
    Summary = db.Column(db.String(300), nullable=False)
    Content = db.Column(db.Text, nullable=False)  # the lesson: paragraphs separated by blank lines, "- " for bullets
    Minutes = db.Column(db.Integer, nullable=False, default=10)
    Icon = db.Column(db.String(40), nullable=False, default="mortarboard")
    Position = db.Column(db.Integer, nullable=False, default=0)
    IsPublished = db.Column(db.Boolean, nullable=False, default=True, server_default=sa_true())
    CreatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    questions = db.relationship(
        "Question", backref="course", order_by="Question.Position", cascade="all, delete-orphan", lazy=True
    )
    attempts = db.relationship("CourseAttempt", backref="course", cascade="all, delete-orphan", lazy=True)

    @property
    def lesson_blocks(self) -> list[dict]:
        """The lesson as ``[{"text": "...", "bullets": [...]}, ...]``: blank lines split blocks, "- " starts a bullet."""
        blocks = []
        for chunk in (self.Content or "").replace("\r\n", "\n").split("\n\n"):
            lines = [line.strip() for line in chunk.strip().split("\n") if line.strip()]
            if not lines:
                continue
            text = " ".join(line for line in lines if not line.startswith("- "))
            bullets = [line[2:].strip() for line in lines if line.startswith("- ")]
            blocks.append({"text": text, "bullets": bullets})
        return blocks


class Question(db.Model):
    __tablename__ = "course_questions"

    QuestionId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(QUESTION_PREFIX))
    CourseId = db.Column(
        db.String(30), db.ForeignKey("courses.CourseId", ondelete="CASCADE"), nullable=False, index=True
    )
    Text = db.Column(db.String(300), nullable=False)
    Explanation = db.Column(db.String(300), nullable=True)  # shown after the quiz
    Position = db.Column(db.Integer, nullable=False, default=0)

    answers = db.relationship(
        "Answer", backref="question", order_by="Answer.Position", cascade="all, delete-orphan", lazy=True
    )

    @property
    def correct(self):
        return next((a for a in self.answers if a.IsCorrect), None)


class Answer(db.Model):
    __tablename__ = "course_answers"

    AnswerId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(ANSWER_PREFIX))
    QuestionId = db.Column(
        db.String(30), db.ForeignKey("course_questions.QuestionId", ondelete="CASCADE"), nullable=False, index=True
    )
    Text = db.Column(db.String(200), nullable=False)
    IsCorrect = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())
    Position = db.Column(db.Integer, nullable=False, default=0)


class CourseAttempt(db.Model):
    __tablename__ = "course_attempts"

    AttemptId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(ATTEMPT_PREFIX))
    CourseId = db.Column(
        db.String(30), db.ForeignKey("courses.CourseId", ondelete="CASCADE"), nullable=False, index=True
    )
    UserId = db.Column(db.String(30), db.ForeignKey("users.UserId", ondelete="CASCADE"), nullable=False, index=True)
    Score = db.Column(db.Integer, nullable=False)
    Total = db.Column(db.Integer, nullable=False)
    Choices = db.Column(db.JSON, nullable=True)  # {question id: answer id picked}
    CreatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User", backref=db.backref("course_attempts", cascade="all, delete-orphan", lazy=True))

    @property
    def percent(self) -> int:
        return round(self.Score / self.Total * 100) if self.Total else 0

    @property
    def passed(self) -> bool:
        return self.percent >= 70
