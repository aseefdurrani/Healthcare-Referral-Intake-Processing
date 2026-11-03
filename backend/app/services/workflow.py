"""Which staff actions the current status allows."""

from __future__ import annotations

from app.db.models import ProcessingRun, Referral


def ai_stage_failure(run: ProcessingRun | None) -> bool:
    if run is None or run.status != "failed":
        return False
    code = run.error_code or ""
    return run.failure_stage == "ai" or code.startswith("AI_") or code == "INTERRUPTED"


def allowed_actions(
    referral: Referral,
    run: ProcessingRun | None,
    *,
    attempt_count: int,
    max_attempts: int,
) -> list[str]:
    if referral.status == "needs_review":
        actions = ["edit", "approve", "reject"]
        if (
            ai_stage_failure(run)
            and run is not None
            and run.retryable is not False
            and attempt_count < max_attempts
        ):
            actions.append("retry")
        return actions
    if referral.status == "failed":
        actions = ["reject"]
        if run is not None and run.retryable and attempt_count < max_attempts:
            actions.append("retry")
        return actions
    return []
