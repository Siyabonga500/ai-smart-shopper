"""catalogue products added by admins, and what each store sells (grocery / clothing / both)

Revision ID: f4a2c8b1d7e3
Revises: e3b9a1c7d5f2
Create Date: 2026-09-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f4a2c8b1d7e3'
down_revision = 'e3b9a1c7d5f2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('stores', schema=None) as batch_op:
        batch_op.add_column(sa.Column('StoreType', sa.String(length=20), nullable=False, server_default='grocery'))

    op.create_table(
        'catalogue_products',
        sa.Column('ProductId', sa.String(length=30), nullable=False),
        sa.Column('Category', sa.String(length=20), nullable=False),
        sa.Column('Name', sa.String(length=150), nullable=False),
        sa.Column('Brand', sa.String(length=80), nullable=True),
        sa.Column('Sku', sa.String(length=50), nullable=True),
        sa.Column('Barcode', sa.String(length=50), nullable=True),
        sa.Column('Price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('ImageUrl', sa.String(length=1000), nullable=True),
        sa.Column('Photos', sa.JSON(), nullable=True),
        sa.Column('Size', sa.String(length=30), nullable=True),
        sa.Column('Colour', sa.String(length=40), nullable=True),
        sa.Column('StockStatus', sa.String(length=20), nullable=False),
        sa.Column('StockQuantity', sa.Integer(), nullable=True),
        sa.Column('StoreId', sa.String(length=30), nullable=False),
        sa.Column('CreatedBy', sa.String(length=100), nullable=True),
        sa.Column('CreatedOn', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('UpdatedOn', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['StoreId'], ['stores.StoreId'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('ProductId'),
    )
    with op.batch_alter_table('catalogue_products', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_catalogue_products_Category'), ['Category'], unique=False)
        batch_op.create_index(batch_op.f('ix_catalogue_products_Sku'), ['Sku'], unique=False)
        batch_op.create_index(batch_op.f('ix_catalogue_products_Barcode'), ['Barcode'], unique=False)
        batch_op.create_index(batch_op.f('ix_catalogue_products_StoreId'), ['StoreId'], unique=False)


def downgrade():
    with op.batch_alter_table('catalogue_products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_catalogue_products_StoreId'))
        batch_op.drop_index(batch_op.f('ix_catalogue_products_Barcode'))
        batch_op.drop_index(batch_op.f('ix_catalogue_products_Sku'))
        batch_op.drop_index(batch_op.f('ix_catalogue_products_Category'))
    op.drop_table('catalogue_products')

    with op.batch_alter_table('stores', schema=None) as batch_op:
        batch_op.drop_column('StoreType')
