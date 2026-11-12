"""Require the one-folder object-key layout.

Source files are ``referrals/<referral-uuid>/source.<ext>``.
Each Textract attempt is ``referrals/<referral-uuid>/ocr/<ocr-run-uuid>.json.gz``.
Existing rows are left in place. When any still use the previous layout, the
checks are added NOT VALID so new writes follow the new keys. An empty
database gets the checks validated. This revision does not delete S3 objects.
"""

from __future__ import annotations

import sys

from alembic import op
from sqlalchemy import text

revision = "0002_object_keys"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_DOCUMENT_KEY = r"""
ALTER TABLE documents
  ADD CONSTRAINT documents_s3_key_ck CHECK (
    s3_key ~ (
      '^referrals/' || referral_id::text || '/source[.](pdf|png|jpg)$'
    )
  ) NOT VALID
"""

_OCR_KEY = r"""
ALTER TABLE ocr_runs
  ADD CONSTRAINT ocr_runs_raw_output_s3_key_ck CHECK (
    raw_output_s3_key IS NULL
    OR raw_output_s3_key = (
      'referrals/' || referral_id::text || '/ocr/' || id::text || '.json.gz'
    )
  ) NOT VALID
"""


def upgrade() -> None:
    bind = op.get_bind()
    stale_documents = bind.execute(
        text(
            r"""
            SELECT count(*)
            FROM documents
            WHERE s3_key !~ (
              '^referrals/' || referral_id::text || '/source[.](pdf|png|jpg)$'
            )
            """
        )
    ).scalar_one()
    stale_ocr = bind.execute(
        text(
            r"""
            SELECT count(*)
            FROM ocr_runs
            WHERE raw_output_s3_key IS NOT NULL
              AND raw_output_s3_key IS DISTINCT FROM (
                'referrals/' || referral_id::text || '/ocr/' || id::text || '.json.gz'
              )
            """
        )
    ).scalar_one()
    bind.exec_driver_sql(_DOCUMENT_KEY)
    bind.exec_driver_sql(_OCR_KEY)
    if stale_documents or stale_ocr:
        print(
            "Leaving object-key checks NOT VALID because "
            f"{stale_documents} documents and {stale_ocr} OCR runs "
            "still use the previous layout. New writes must use the new keys. "
            "Recreate the database when those rows are gone. "
            "This revision does not delete S3 objects.",
            file=sys.stderr,
        )
        return
    bind.exec_driver_sql("ALTER TABLE documents VALIDATE CONSTRAINT documents_s3_key_ck")
    bind.exec_driver_sql("ALTER TABLE ocr_runs VALIDATE CONSTRAINT ocr_runs_raw_output_s3_key_ck")


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_s3_key_ck")
    bind.exec_driver_sql(
        "ALTER TABLE ocr_runs DROP CONSTRAINT IF EXISTS ocr_runs_raw_output_s3_key_ck"
    )
