"""File cache and template manager.

Revision ID: fa0d96e28631
Revises: bfda02ab3a9b
Create Date: 2016-02-21 16:21:48.277654
"""

import os
import shutil
import sqlalchemy as sa

from alembic import op
from mailman.config import config
from mailman.database.helpers import exists_in_db


# revision identifiers, used by Alembic.
revision = 'fa0d96e28631'
down_revision = '7b254d88f122'


CONVERSION_MAPPING = dict(
    digest_footer_uri='list:digest:footer',
    digest_header_uri='list:digest:header',
    footer_uri='list:regular:footer',
    goodbye_message_uri='user:ack:goodbye',
    header_uri='list:regular:header',
    welcome_message_uri='user:ack:welcome',
    )

REVERSE_MAPPING = {value: key for key, value in CONVERSION_MAPPING.items()}


def upgrade():
    op.create_table(
        'file_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.Unicode(), nullable=False),
        sa.Column('file_id', sa.Unicode(), nullable=True),
        sa.Column('is_bytes', sa.Boolean(), nullable=False),
        sa.Column('created_on', sa.DateTime(), nullable=False),
        sa.Column('expires_on', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    template_table = op.create_table(
        'template',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.Unicode(), nullable=False),
        sa.Column('context', sa.Unicode(), nullable=True),
        sa.Column('uri', sa.Unicode(), nullable=False),
        sa.Column('username', sa.Unicode(), nullable=True),
        sa.Column('password', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    connection = op.get_bind()
    # For all existing mailing lists, turn the *_uri attributes into entries
    # in the template cache.  Don't import the table definition from the
    # models, it may break this migration when the model is updated in the
    # future (see the Alembic doc).
    mlist_table = sa.sql.table(
        'mailinglist',
        sa.sql.column('id', sa.Integer),
        sa.sql.column('list_id', sa.Unicode),
        sa.sql.column('digest_footer_uri', sa.Unicode),
        sa.sql.column('digest_header_uri', sa.Unicode),
        sa.sql.column('footer_uri', sa.Unicode),
        sa.sql.column('header_uri', sa.Unicode),
        sa.sql.column('goodbye_message_uri', sa.Unicode),
        sa.sql.column('welcome_message_uri', sa.Unicode),
        )
    for (mlist_id, list_id,
         digest_footer_uri, digest_header_uri,
         nondigest_footer_uri, nondigest_header_uri,
         goodbye_uri, welcome_uri
         ) in connection.execute(mlist_table.select()):
        inserts = []
        if digest_footer_uri is not None:
            entry = dict(
                name=CONVERSION_MAPPING['digest_footer_uri'],
                uri=digest_footer_uri,
                )
            inserts.append(entry)
        if digest_header_uri is not None:
            entry = dict(
                name=CONVERSION_MAPPING['digest_header_uri'],
                uri=digest_header_uri,
                )
            inserts.append(entry)
        if nondigest_footer_uri is not None:
            entry = dict(
                name=CONVERSION_MAPPING['footer_uri'],
                uri=nondigest_footer_uri,
                )
            inserts.append(entry)
        if nondigest_header_uri is not None:
            entry = dict(
                name=CONVERSION_MAPPING['header_uri'],
                uri=nondigest_header_uri,
                )
            inserts.append(entry)
        if goodbye_uri is not None:
            entry = dict(
                name=CONVERSION_MAPPING['goodbye_message_uri'],
                uri=goodbye_uri,
                )
            inserts.append(entry)
        if welcome_uri is not None:
            entry = dict(
                name=CONVERSION_MAPPING['welcome_message_uri'],
                uri=welcome_uri,
                )
            inserts.append(entry)
        for entry in inserts:
            # In the source tree, footer-generic.txt was renamed.
            entry['context'] = list_id
            connection.execute(template_table.insert().values(**entry))
    with op.batch_alter_table('mailinglist') as batch_op:
        batch_op.drop_column('digest_footer_uri')
        batch_op.drop_column('digest_header_uri')
        batch_op.drop_column('footer_uri')
        batch_op.drop_column('header_uri')
        batch_op.drop_column('goodbye_message_uri')
        batch_op.drop_column('welcome_message_uri')
    with op.batch_alter_table('domain') as batch_op:
        batch_op.drop_column('base_url')


def downgrade():
    # Add back the original columns to the mailinglist table.
    for column in CONVERSION_MAPPING:
        if not exists_in_db(op.get_bind(), 'mailinglist', column):
            op.add_column(
                'mailinglist',
                sa.Column(column, sa.Unicode, nullable=True))
    op.add_column('domain', sa.Column('base_url', sa.Unicode))
    # Put all the templates with a context mapping the list-id back into the
    # mailinglist table.  No other contexts are supported, so just throw those
    # away.
    template_table = sa.sql.table(
        'template',
        sa.sql.column('id', sa.Integer),
        sa.sql.column('name', sa.Unicode),
        sa.sql.column('context', sa.Unicode),
        sa.sql.column('uri', sa.Unicode),
        sa.sql.column('username', sa.Unicode),
        sa.sql.column('password', sa.Unicode),
        )
    mlist_table = sa.sql.table(
        'mailinglist',
        sa.sql.column('id', sa.Integer),
        sa.sql.column('list_id', sa.Unicode),
        sa.sql.column('digest_footer_uri', sa.Unicode),
        sa.sql.column('digest_header_uri', sa.Unicode),
        sa.sql.column('footer_uri', sa.Unicode),
        sa.sql.column('header_uri', sa.Unicode),
        sa.sql.column('goodbye_message_uri', sa.Unicode),
        sa.sql.column('welcome_message_uri', sa.Unicode),
        )
    connection = op.get_bind()
    for (table_id, name, context, uri, username, password
         ) in connection.execute(template_table.select()).fetchall():
        mlist = connection.execute(mlist_table.select().where(
            mlist_table.c.list_id == context)).fetchone()
        if mlist is None:
            continue
        attribute = REVERSE_MAPPING.get(name)
        if attribute is not None:
            connection.execute(mlist_table.update().where(
                mlist_table.c.list_id == context).values(
                    **{attribute: uri}))
    op.drop_table('file_cache')
    op.drop_table('template')
    # Also delete the file cache directories.  Don't delete the cache
    # directory itself though.
    for path in os.listdir(config.CACHE_DIR):
        full_path = os.path.join(config.CACHE_DIR, path)
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
