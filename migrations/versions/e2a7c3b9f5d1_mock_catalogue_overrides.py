"""admin edits and deletions of the built-in (mock) catalogue

Revision ID: e2a7c3b9f5d1
Revises: d1f6b2a8e4c9
Create Date: 2026-09-26 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2a7c3b9f5d1'
down_revision = 'd1f6b2a8e4c9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mock_product_overrides',
        sa.Column('Barcode', sa.String(length=50), nullable=False),
        sa.Column('Hidden', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('Name', sa.String(length=150), nullable=True),
        sa.Column('Brand', sa.String(length=80), nullable=True),
        sa.Column('Category', sa.String(length=20), nullable=True),
        sa.Column('Price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('UpdatedBy', sa.String(length=100), nullable=True),
        sa.Column('UpdatedOn', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('Barcode'),
    )
    op.create_table(
        'mock_retailer_overrides',
        sa.Column('Id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('Barcode', sa.String(length=50), nullable=False),
        sa.Column('Retailer', sa.String(length=50), nullable=False),
        sa.Column('Listed', sa.Boolean(), nullable=True),
        sa.Column('Price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('InStock', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('Id'),
        sa.UniqueConstraint('Barcode', 'Retailer', name='uq_mock_retailer'),
    )
    with op.batch_alter_table('mock_retailer_overrides', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mock_retailer_overrides_Barcode'), ['Barcode'], unique=False)


def downgrade():
    with op.batch_alter_table('mock_retailer_overrides', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mock_retailer_overrides_Barcode'))
    op.drop_table('mock_retailer_overrides')
    op.drop_table('mock_product_overrides')
