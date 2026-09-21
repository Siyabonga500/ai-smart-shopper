"""Public static pages."""

from flask import Blueprint, render_template

bp = Blueprint("pages", __name__)


@bp.get("/terms")
def terms():
    return render_template("terms.html")
