"""Add a numerical position column to sort header matches.

Revision ID: d4fbb4fd34ca
Revises: bfda02ab3a9b
Create Date: 2016-02-01 15:57:09.807678

"""

# revision identifiers, used by Alembic.
revision = 'd4fbb4fd34ca'
down_revision = 'bfda02ab3a9b'

import sqlalchemy as sa
from alembic import op
from mailman.database.helpers import is_sqlite


def upgrade():
    with op.batch_alter_table('headermatch') as batch_op:
        batch_op.add_column(
            sa.Column('position', sa.Integer(), nullable=True))
        batch_op.alter_column(
            'mailing_list_id', existing_type=sa.INTEGER(), nullable=False)
        batch_op.create_index(
            op.f('ix_headermatch_position'), ['position'], unique=False)
        batch_op.create_index(
            op.f('ix_headermatch_mailing_list_id'), ['mailing_list_id'],
            unique=False)


def downgrade():
    with op.batch_alter_table('headermatch') as batch_op:
        batch_op.drop_index(op.f('ix_headermatch_mailing_list_id'))
        batch_op.drop_index(op.f('ix_headermatch_position'))
        batch_op.alter_column(
            'mailing_list_id', existing_type=sa.INTEGER(), nullable=True)
        batch_op.drop_column('position')
