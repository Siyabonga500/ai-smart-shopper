"""The "Contact us" form on the About us page."""

from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired, Length

from app.forms.validators import email_address, normalise_text


class ContactForm(FlaskForm):
    Name = StringField(
        "Your name",
        filters=[normalise_text],
        validators=[DataRequired("Enter your name."), Length(max=100, message="At most 100 characters.")],
    )
    Email = StringField(
        "Your email",
        filters=[lambda v: v.strip().lower() if isinstance(v, str) else v],
        validators=[DataRequired("Enter your email address so we can reply."), Length(max=100), email_address],
    )
    Subject = StringField(
        "Subject",
        filters=[normalise_text],
        validators=[DataRequired("Say what it is about."), Length(max=150, message="At most 150 characters.")],
    )
    Message = TextAreaField(
        "Message",
        filters=[normalise_text],
        validators=[
            DataRequired("Write your message."),
            Length(min=10, max=3000, message="Between 10 and 3 000 characters."),
        ],
    )
    Website = StringField("Leave this empty")  # a hidden trap field: people never fill it in, spam robots do
