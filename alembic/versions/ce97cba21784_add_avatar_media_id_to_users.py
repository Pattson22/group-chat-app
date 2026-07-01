"""add avatar_media_id to users

Revision ID: ce97cba21784
Revises: c45921dba5a5
Create Date: 2026-07-01 23:10:43.020332

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ce97cba21784'
down_revision: Union[str, Sequence[str], None] = 'c45921dba5a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('avatar_media_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_users_avatar_media_id_media', 'users', 'media', ['avatar_media_id'], ['id'])
    op.drop_column('users', 'avatar_url')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('users', sa.Column('avatar_url', sa.String(), nullable=True))
    op.drop_constraint('fk_users_avatar_media_id_media', 'users', type_='foreignkey')
    op.drop_column('users', 'avatar_media_id')
