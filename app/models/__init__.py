"""SQLAlchemy models, taken from the Budget Shopper Views Plan PDF (pages 1-3, 18-22).

Importing every model here guarantees they are registered on ``db.metadata``
before Flask-Migrate inspects it.
"""

from app.models.admin import AuditLog, CategoryMapping, IntegrationSetting
from app.models.budget import Budget
from app.models.catalogue import CatalogueProduct, ProductPicture
from app.models.contact import ContactMessage
from app.models.course import Answer, Course, CourseAttempt, Question
from app.models.list_item import ListItem
from app.models.mock_override import MockProductOverride, MockRetailerOverride
from app.models.notification import Notification
from app.models.preference import Preference
from app.models.shopping_list import ShoppingList
from app.models.store import Store
from app.models.sub_budget import SubBudget
from app.models.user import User, load_user
from app.utils.ids import generate_id

__all__ = [
    "User",
    "Budget",
    "SubBudget",
    "ShoppingList",
    "ListItem",
    "MockProductOverride",
    "MockRetailerOverride",
    "Preference",
    "Store",
    "CatalogueProduct",
    "ProductPicture",
    "ContactMessage",
    "Course",
    "Question",
    "Answer",
    "CourseAttempt",
    "Notification",
    "CategoryMapping",
    "IntegrationSetting",
    "AuditLog",
    "load_user",
    "generate_id",
]
