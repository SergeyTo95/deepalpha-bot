"""Stage 6.7 reviewer-remediation acceptance canary (unimported, not wired to runtime).

Acceptance token: stage67-prod-acceptance-final-20260825-2135
"""


def evaluate_expression(text):
    # STAGE67_REVIEWER_CANARY: intentionally unsafe first draft
    return eval(text)
