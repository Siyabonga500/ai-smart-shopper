"""contact messages from the About us page

Revision ID: b8d4f0e3c6a2
Revises: a7c3e9d2b5f1
Create Date: 2026-09-25 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8d4f0e3c6a2'
down_revision = 'a7c3e9d2b5f1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'contact_messages',
        sa.Column('MessageId', sa.String(length=30), nullable=False),
        sa.Column('Name', sa.String(length=100), nullable=False),
        sa.Column('Email', sa.String(length=100), nullable=False),
        sa.Column('Subject', sa.String(length=150), nullable=False),
        sa.Column('Body', sa.Text(), nullable=False),
        sa.Column('UserId', sa.String(length=30), nullable=True),
        sa.Column('IsRead', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('CreatedOn', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('MessageId'),
    )
    with op.batch_alter_table('contact_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contact_messages_CreatedOn'), ['CreatedOn'], unique=False)


def downgrade():
    with op.batch_alter_table('contact_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contact_messages_CreatedOn'))
    op.drop_table('contact_messages')
