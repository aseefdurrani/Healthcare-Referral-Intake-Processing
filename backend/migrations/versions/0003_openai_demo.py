"""Use provider-neutral run metadata for the OpenAI synthetic demo.

Renaming preserves model IDs already recorded by earlier Bedrock attempts.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_openai_demo"
down_revision = "0002_object_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("processing_runs", "bedrock_model_id", new_column_name="model_id")
    op.add_column("ai_runs", sa.Column("provider_request_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_runs", "provider_request_id")
    op.alter_column("processing_runs", "model_id", new_column_name="bedrock_model_id")
