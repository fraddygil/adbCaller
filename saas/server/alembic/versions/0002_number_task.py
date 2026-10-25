"""Enlaza números de listas con tareas durables.

Revision ID: 0002_number_task
Revises: 0001_initial
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_number_task"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("call_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("numbers", sa.Column("task_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_numbers_tenant_id_tasks",
        "numbers",
        "tasks",
        ["tenant_id", "task_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "conference_participants",
        sa.Column("number_entry_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conference_participants_tenant_id_numbers",
        "conference_participants",
        "numbers",
        ["tenant_id", "number_entry_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_conference_participants_tenant_id_numbers",
        "conference_participants",
        type_="foreignkey",
    )
    op.drop_column("conference_participants", "number_entry_id")
    op.drop_constraint("fk_numbers_tenant_id_tasks", "numbers", type_="foreignkey")
    op.drop_column("numbers", "task_id")
    op.drop_column("devices", "call_count")
