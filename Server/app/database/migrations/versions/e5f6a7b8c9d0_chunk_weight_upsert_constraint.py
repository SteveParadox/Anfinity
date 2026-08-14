"""Add a unique scope constraint for chunk weight upserts.

Revision ID: e5f6a7b8c9d0
Revises: b4c5d6e7f8a9, d4e5f6a7b8c9
Create Date: 2026-04-16 15:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = ("b4c5d6e7f8a9", "d4e5f6a7b8c9")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                workspace_id,
                document_id,
                chunk_id,
                ROW_NUMBER() OVER (
                    PARTITION BY workspace_id, document_id, chunk_id
                    ORDER BY created_at NULLS LAST, id
                ) AS rn,
                FIRST_VALUE(id) OVER (
                    PARTITION BY workspace_id, document_id, chunk_id
                    ORDER BY created_at NULLS LAST, id
                ) AS keep_id,
                COALESCE(positive_feedback_count, 0) AS positive_feedback_count,
                COALESCE(negative_feedback_count, 0) AS negative_feedback_count,
                COALESCE(total_uses, 0) AS total_uses,
                COALESCE(accuracy_rate, 0.5) AS accuracy_rate,
                COALESCE(credibility_score, 1.0) AS credibility_score
            FROM chunk_weights
        ),
        aggregated AS (
            SELECT
                keep_id,
                SUM(positive_feedback_count) AS positive_feedback_count,
                SUM(negative_feedback_count) AS negative_feedback_count,
                SUM(total_uses) AS total_uses,
                CASE
                    WHEN SUM(positive_feedback_count) + SUM(negative_feedback_count) > 0
                        THEN SUM(positive_feedback_count)::double precision
                             / (SUM(positive_feedback_count) + SUM(negative_feedback_count))
                    ELSE MAX(accuracy_rate)
                END AS accuracy_rate,
                GREATEST(0.1, LEAST(2.0, MAX(credibility_score))) AS credibility_score
            FROM ranked
            GROUP BY keep_id
        )
        UPDATE chunk_weights AS target
        SET
            positive_feedback_count = aggregated.positive_feedback_count,
            negative_feedback_count = aggregated.negative_feedback_count,
            total_uses = aggregated.total_uses,
            accuracy_rate = aggregated.accuracy_rate,
            credibility_score = aggregated.credibility_score
        FROM aggregated
        WHERE target.id = aggregated.keep_id
        """
    )

    op.execute(
        """
        DELETE FROM chunk_weights AS target
        USING (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY workspace_id, document_id, chunk_id
                    ORDER BY created_at NULLS LAST, id
                ) AS rn
            FROM chunk_weights
        ) AS ranked
        WHERE target.id = ranked.id
          AND ranked.rn > 1
        """
    )

    op.create_unique_constraint(
        "uq_chunk_weight_scope",
        "chunk_weights",
        ["workspace_id", "document_id", "chunk_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_chunk_weight_scope", "chunk_weights", type_="unique")
