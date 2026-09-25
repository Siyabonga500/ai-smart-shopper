"""list items: "Purchased" tick on the active shopping list (IsCollected)

Revision ID: a7c3e9d2b5f1
Revises: f4a2c8b1d7e3
Create Date: 2026-09-25 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c3e9d2b5f1'
down_revision = 'f4a2c8b1d7e3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('list_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('IsCollected', sa.Boolean(), server_default=sa.text('0'), nullable=False))


def downgrade():
    with op.batch_alter_table('list_items', schema=None) as batch_op:
        batch_op.drop_column('IsCollected')
