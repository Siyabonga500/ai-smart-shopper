"""sale price on catalogue products, and pasted product pictures by barcode

Revision ID: d1f6b2a8e4c9
Revises: c9e5a1f4d7b3
Create Date: 2026-09-26 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1f6b2a8e4c9'
down_revision = 'c9e5a1f4d7b3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('catalogue_products', schema=None) as batch_op:
        batch_op.add_column(sa.Column('SalePrice', sa.Numeric(precision=10, scale=2), nullable=True))
    op.create_table(
        'product_pictures',
        sa.Column('PictureId', sa.String(length=30), nullable=False),
        sa.Column('Barcode', sa.String(length=50), nullable=False),
        sa.Column('Url', sa.String(length=1000), nullable=False),
        sa.Column('Position', sa.Integer(), nullable=False),
        sa.Column('CreatedOn', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('PictureId'),
    )
    with op.batch_alter_table('product_pictures', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_product_pictures_Barcode'), ['Barcode'], unique=False)


def downgrade():
    with op.batch_alter_table('product_pictures', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_product_pictures_Barcode'))
    op.drop_table('product_pictures')
    with op.batch_alter_table('catalogue_products', schema=None) as batch_op:
        batch_op.drop_column('SalePrice')
