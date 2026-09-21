"""notifications table (step 17)

Revision ID: cf410da88970
Revises: c1a7e0d4f2b9
Create Date: 2026-09-21 01:46:10.459718

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cf410da88970'
down_revision = 'c1a7e0d4f2b9'
branch_labels = None
depends_on = None


def upgrade():
    
    op.create_table('notifications',
    sa.Column('NotificationId', sa.String(length=30), nullable=False),
    sa.Column('UserId', sa.String(length=30), nullable=False),
    sa.Column('Title', sa.String(length=120), nullable=False),
    sa.Column('Message', sa.Text(), nullable=False),
    sa.Column('Type', sa.String(length=40), nullable=False),
    sa.Column('IsRead', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('CreatedOn', sa.DateTime(timezone=True), nullable=False),
    sa.Column('DedupeKey', sa.String(length=150), nullable=True),
    sa.Column('Url', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['UserId'], ['users.UserId'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('NotificationId'),
    sa.UniqueConstraint('UserId', 'DedupeKey', name='uq_notifications_user_dedupe')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index('ix_notifications_user_read_created', ['UserId', 'IsRead', 'CreatedOn'], unique=False)

    


def downgrade():
    
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index('ix_notifications_user_read_created')

    op.drop_table('notifications')
    
