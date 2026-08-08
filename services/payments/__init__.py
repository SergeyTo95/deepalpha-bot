"""VELIA multi-rail payment foundation.

This package is intentionally isolated from Telegram handlers, VELIA chat, admin
mutations and wallet signing. Stage 1 of the payment foundation is watch-only:
it creates/observes payment-domain records but does not contact blockchains or
credit users automatically.
"""

from services.payments.schema import ensure_payment_tables, ensure_payment_tables_serialized

__all__ = ["ensure_payment_tables", "ensure_payment_tables_serialized"]
