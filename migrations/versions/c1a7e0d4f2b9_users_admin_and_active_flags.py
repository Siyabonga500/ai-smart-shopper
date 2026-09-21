"""users: IsAdmin and IsActive flags (admin portal, account deactivation)

Revision ID: c1a7e0d4f2b9
Revises: b69bda71d54a
Create Date: 2026-09-21 01:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1a7e0d4f2b9'
down_revision = 'b69bda71d54a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('IsAdmin', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('IsActive', sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('IsActive')
        batch_op.drop_column('IsAdmin')
