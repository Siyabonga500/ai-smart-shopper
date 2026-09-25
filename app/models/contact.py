"""Messages sent from the "Contact us" form on the About us page. Admins read them under Admin > Messages."""

from sqlalchemy import false as sa_false

from app.extensions import db
from app.utils.dates import utcnow
from app.utils.ids import CONTACT_PREFIX, generate_id


class ContactMessage(db.Model):
    __tablename__ = "contact_messages"

    MessageId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(CONTACT_PREFIX))
    Name = db.Column(db.String(100), nullable=False)
    Email = db.Column(db.String(100), nullable=False)
    Subject = db.Column(db.String(150), nullable=False)
    Body = db.Column(db.Text, nullable=False)
    UserId = db.Column(db.String(30), nullable=True)  # the signed-in student, if any (no foreign key: keep the message)
    IsRead = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())
    CreatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
