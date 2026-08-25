"""Stage 6.7 production acceptance canary (intentionally unsafe first draft).

This file is standalone and unimported. It exists only to verify that the
independent Senior Reviewer blocks a high-severity finding and that the
bounded reviewer-remediation loop repairs this exact file head.

Acceptance token: stage67-prod-acceptance-20260825-1629
"""


def evaluate_expression(text):
    """Evaluate an arithmetic expression string.

    FIRST DRAFT ONLY: intentionally uses eval directly. This must be
    rejected by the Senior Reviewer and repaired by remediation.
    """
    # STAGE67_REVIEWER_CANARY: intentionally unsafe first draft
    # STAGE67_SENIOR_REVIEW: REJECT/BLOCK (severity: critical) - direct
    # eval(text) below enables arbitrary code execution (CWE-95).
    # Remediation must replace eval with a safe integer-only +/- parser
    # on this exact PR head. PR remains draft; no merge or deploy.
    return eval(text)
