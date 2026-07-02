"""drop unused feature-scaffolding columns

Removes columns that were added ahead of features that were never built:
message edit/delete (messages.edited_at / deleted_at), read receipts
(conversation_members.last_read_message_id), and presence
(users.last_seen_at). Nothing in the app ever wrote or read them, so all
values are NULL and this drop is lossless. Re-add them alongside the
feature if/when one lands.

Revision ID: 8f2a41c9d0b3
Revises: 6ceec4bb3042
Create Date: 2026-07-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f2a41c9d0b3'
down_revision: Union[str, Sequence[str], None] = '6ceec4bb3042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('messages', 'edited_at')
    op.drop_column('messages', 'deleted_at')
    # Dropping the column also drops its FK to messages.id.
    op.drop_column('conversation_members', 'last_read_message_id')
    op.drop_column('users', 'last_seen_at')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('users', sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('conversation_members', sa.Column('last_read_message_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        'conversation_members_last_read_message_id_fkey',
        'conversation_members',
        'messages',
        ['last_read_message_id'],
        ['id'],
    )
    op.add_column('messages', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('messages', sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True))
