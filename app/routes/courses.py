"""Short courses for students: learn to budget, save and shop smart, then take a 10-question quiz.

GET  /courses                          the three courses, with your best score
GET  /courses/<slug>                   the lesson and its quiz
POST /courses/<slug>                   hand the quiz in (every question must be answered)
GET  /courses/<slug>/result/<attempt>  your score, with the right answer and why for each question
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.services import courses as course_service

bp = Blueprint("courses", __name__, url_prefix="/courses")


@bp.get("", strict_slashes=False)
@login_required
def index():
    course_service.ensure_courses()
    return render_template("courses/index.html", cards=course_service.cards_for(current_user.UserId))


@bp.route("/<slug>", methods=["GET", "POST"])
@login_required
def course(slug):
    course_service.ensure_courses()
    item = course_service.get_by_slug(slug) or abort(404)
    missing = []
    if request.method == "POST":
        attempt, missing = course_service.grade(item, current_user, request.form)
        if attempt is not None:
            db.session.commit()
            return redirect(url_for("courses.result", slug=slug, attempt_id=attempt.AttemptId))
        flash(f"Answer every question before you hand in. Still open: question {', '.join(missing)}.", "warning")
    return (
        render_template("courses/course.html", course=item, picked=request.form, missing=set(missing)),
        400 if missing else 200,
    )


@bp.get("/<slug>/result/<attempt_id>")
@login_required
def result(slug, attempt_id):
    attempt = course_service.attempt_for(current_user.UserId, attempt_id)
    if attempt is None or attempt.course.Slug != slug:
        abort(404)
    return render_template("courses/result.html", course=attempt.course, attempt=attempt, choices=attempt.Choices or {})
