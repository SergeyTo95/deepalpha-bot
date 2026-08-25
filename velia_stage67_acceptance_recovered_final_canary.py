# STAGE67_REVIEWER_CANARY: intentionally unsafe first draft
# This file is intentionally unimported and disconnected from application/runtime code.
# It exists solely as the Stage 6.7 reviewer-remediation acceptance canary.
# Acceptance token: stage67-prod-acceptance-recovered-final-20260825-2227


def evaluate_expression(text):
    """Intentionally unsafe first draft: direct eval of untrusted input.

    This MUST be rejected by the independent Senior Reviewer and may only be
    remediated by the bounded reviewer-remediation subsystem.
    """
    return eval(text)
