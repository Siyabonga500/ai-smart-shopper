"""Public pages: the home page, About us (with Contact us) and the terms.

GET  /          visitors: the home page (products, how it works); signed-in students: their dashboard
GET  /about     About us and Contact us
POST /about     send a message (Admin > Messages)
GET  /terms
"""

from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import current_user

from app.extensions import db
from app.forms.contact import ContactForm
from app.models import ContactMessage, Store
from app.services import home as home_service

bp = Blueprint("pages", __name__)


@bp.get("/")
def home():
    if current_user.is_authenticated:
        from app.routes.dashboard import index as dashboard

        return dashboard()
    return render_template(
        "pages/home.html",
        sections=home_service.featured_products(),
        store_count=db.session.query(Store).count(),
    )


@bp.route("/about", methods=["GET", "POST"])
def about():
    form = ContactForm()
    if current_user.is_authenticated and not form.is_submitted():
        form.Name.data, form.Email.data = current_user.FullName, current_user.Email
    if form.validate_on_submit():
        if not form.Website.data:  # the trap field is empty: a person sent this
            db.session.add(
                ContactMessage(
                    Name=form.Name.data,
                    Email=form.Email.data,
                    Subject=form.Subject.data,
                    Body=form.Message.data,
                    UserId=current_user.UserId if current_user.is_authenticated else None,
                )
            )
            db.session.commit()
        flash("Thank you, your message has been sent. We will reply by email.", "success")
        return redirect(url_for("pages.about") + "#contact")
    status = 400 if form.is_submitted() else 200
    return render_template("pages/about.html", form=form, contact=_contact()), status


def _contact() -> dict:
    cfg = current_app.config
    return {
        "email": cfg.get("CONTACT_EMAIL"),
        "phone": cfg.get("CONTACT_PHONE"),
        "address": cfg.get("CONTACT_ADDRESS"),
        "hours": cfg.get("CONTACT_HOURS"),
    }


@bp.get("/terms")
def terms():
    return render_template("terms.html")
