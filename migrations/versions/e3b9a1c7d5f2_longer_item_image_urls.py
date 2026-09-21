"""list items: room for long product picture addresses (LoyaltyHub/Buyly image URLs)

Revision ID: e3b9a1c7d5f2
Revises: 1562f6e7e532
Create Date: 2026-09-21 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3b9a1c7d5f2'
down_revision = '1562f6e7e532'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('list_items', schema=None) as batch_op:
        batch_op.alter_column('ItemImage', existing_type=sa.String(length=255), type_=sa.String(length=1000), existing_nullable=True)


def downgrade():
    with op.batch_alter_table('list_items', schema=None) as batch_op:
        batch_op.alter_column('ItemImage', existing_type=sa.String(length=1000), type_=sa.String(length=255), existing_nullable=True)
