"""Dashboard (Home) - PDF view 1, Step 8.

    GET /dashboard   welcome, active budget, recently bought high-cost items, weather, shopping list,
                     budget status and potential savings   ("/" shows it too when signed in)

The weather card is filled in by the browser from ``/api/weather`` so a slow weather service never slows the page.
"""

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.services import dashboard as dashboard_service
from app.services.weather import place_label

bp = Blueprint("dashboard", __name__)


@bp.get("/dashboard")  # "/" (pages.home) shows the same page to a signed-in student
@login_required
def index():
    data = dashboard_service.build(current_user)
    has_position = current_user.Latitude is not None and current_user.Longitude is not None
    return render_template(
        "dashboard/index.html",
        data=data,
        has_saved_location=has_position,
        weather_place=place_label(current_user.ResidentialAddress, "Your area" if has_position else "Durban"),
    )
