"""add avatar_media_id to conversations

Revision ID: 6ceec4bb3042
Revises: ce97cba21784
Create Date: 2026-07-01 23:40:41.646405

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6ceec4bb3042'
down_revision: Union[str, Sequence[str], None] = 'ce97cba21784'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'conversations', sa.Column('avatar_media_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        'fk_conversations_avatar_media_id_media', 'conversations', 'media', ['avatar_media_id'], ['id']
    )
    op.drop_column('conversations', 'avatar_url')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('conversations', sa.Column('avatar_url', sa.String(), nullable=True))
    op.drop_constraint('fk_conversations_avatar_media_id_media', 'conversations', type_='foreignkey')
    op.drop_column('conversations', 'avatar_media_id')
