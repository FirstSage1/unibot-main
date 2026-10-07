"""Добавить внутренний комментарий администратора к пользователю."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2c2a4f7d9e11"
down_revision: Union[str, Sequence[str], None] = "965df8d6b696"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить nullable-поле для внутренней заметки администратора."""
    op.add_column("users", sa.Column("admin_comment", sa.Text(), nullable=True))


def downgrade() -> None:
    """Удалить поле внутренней заметки администратора."""
    op.drop_column("users", "admin_comment")
