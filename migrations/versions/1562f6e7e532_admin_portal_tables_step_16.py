"""admin portal tables (step 16)

Revision ID: 1562f6e7e532
Revises: cf410da88970
Create Date: 2026-09-21 01:59:29.166601

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1562f6e7e532'
down_revision = 'cf410da88970'
branch_labels = None
depends_on = None


def upgrade():
    
    op.create_table('audit_logs',
    sa.Column('AuditId', sa.String(length=30), nullable=False),
    sa.Column('AdminId', sa.String(length=30), nullable=False),
    sa.Column('AdminEmail', sa.String(length=100), nullable=False),
    sa.Column('Action', sa.String(length=60), nullable=False),
    sa.Column('Target', sa.String(length=200), nullable=True),
    sa.Column('Detail', sa.JSON(), nullable=True),
    sa.Column('CreatedOn', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('AuditId')
    )
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.create_index('ix_audit_logs_admin', ['AdminId', 'CreatedOn'], unique=False)
        batch_op.create_index('ix_audit_logs_created', ['CreatedOn'], unique=False)

    op.create_table('category_mappings',
    sa.Column('MappingId', sa.String(length=30), nullable=False),
    sa.Column('RawCategory', sa.String(length=150), nullable=False),
    sa.Column('RawKey', sa.String(length=150), nullable=False),
    sa.Column('MappedCategory', sa.String(length=30), nullable=False),
    sa.Column('CreatedOn', sa.DateTime(timezone=True), nullable=False),
    sa.Column('UpdatedOn', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('MappingId'),
    sa.UniqueConstraint('RawKey')
    )
    op.create_table('integration_settings',
    sa.Column('Source', sa.String(length=20), nullable=False),
    sa.Column('IsActive', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('ApiKeyEncrypted', sa.Text(), nullable=True),
    sa.Column('LastSyncOn', sa.DateTime(timezone=True), nullable=True),
    sa.Column('LastSyncStatus', sa.String(length=10), nullable=True),
    sa.Column('LastSyncMessage', sa.String(length=300), nullable=True),
    sa.Column('UpdatedOn', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('Source')
    )
    


def downgrade():
    
    op.drop_table('integration_settings')
    op.drop_table('category_mappings')
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_logs_created')
        batch_op.drop_index('ix_audit_logs_admin')

    op.drop_table('audit_logs')
    
