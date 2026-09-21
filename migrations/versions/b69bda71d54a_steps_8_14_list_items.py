"""steps 8-14: list item category, store position and purchase columns

Revision ID: b69bda71d54a
Revises: d6526d03a521
Create Date: 2026-09-20 22:06:30.189603

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b69bda71d54a'
down_revision = 'd6526d03a521'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('list_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('Category', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('StoreLatitude', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('StoreLongitude', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('IsPurchased', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('PurchasedDate', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table('list_items', schema=None) as batch_op:
        batch_op.drop_column('PurchasedDate')
        batch_op.drop_column('IsPurchased')
        batch_op.drop_column('StoreLongitude')
        batch_op.drop_column('StoreLatitude')
        batch_op.drop_column('Category')
