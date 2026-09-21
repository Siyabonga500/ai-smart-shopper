"""Flask-WTF forms. Server-side validation lives here; the browser-side checks
in static/js/register.js mirror the same rules for instant feedback."""

from app.forms.auth import LoginForm, RegistrationForm
from app.forms.profile import ChangePasswordForm, PreferenceForm, ProfileForm

__all__ = ["LoginForm", "RegistrationForm", "ProfileForm", "ChangePasswordForm", "PreferenceForm"]
