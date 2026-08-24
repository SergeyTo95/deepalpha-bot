from __future__ import annotations

import math
import os
import re
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from db.database import get_connection
from services import velia_agent_chat_presentation_service as presentation_store


class CodingQuoteError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, quote: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)
        self.quote = dict(quote or {})


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def ensure_coding_quote_tables() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_developer_coding_quotes (
                job_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                conversation_id TEXT NOT NULL,
                quoted_tokens INTEGER NOT NULL,
                balance_at_quote INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                charged_tokens INTEGER NOT NULL DEFAULT 0,
                pricing_version TEXT NOT NULL DEFAULT 'coding-budget-v1',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                charged_at TIMESTAMP NULL,
                completed_at TIMESTAMP NULL,
                refunded_at TIMESTAMP NULL,
                CHECK (quoted_tokens > 0),
                CHECK (charged_tokens >= 0),
                CHECK (status IN ('pending','insufficient','charged','consumed','refunded','cancelled'))
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_velia_coding_quotes_owner
            ON velia_developer_coding_quotes(user_id, conversation_id, created_at DESC)
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (TypeError, IndexError):
        return default


def _serialize_quote(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "job_id": str(_row_value(row, "job_id", 0, "") or ""),
        "user_id": int(_row_value(row, "user_id", 1, 0) or 0),
        "conversation_id": str(_row_value(row, "conversation_id", 2, "") or ""),
        "quoted_tokens": int(_row_value(row, "quoted_tokens", 3, 0) or 0),
        "balance_tokens": int(_row_value(row, "balance_at_quote", 4, 0) or 0),
        "status": str(_row_value(row, "status", 5, "") or ""),
        "charged_tokens": int(_row_value(row, "charged_tokens", 6, 0) or 0),
        "pricing_version": str(_row_value(row, "pricing_version", 7, "") or ""),
    }


def _quote_select_sql(*, for_update: bool = False) -> str:
    suffix = " FOR UPDATE" if for_update else ""
    return (
        "SELECT job_id,user_id,conversation_id,quoted_tokens,balance_at_quote,status,"
        "charged_tokens,pricing_version FROM velia_developer_coding_quotes WHERE job_id=%s" + suffix
    )


def quote_for_job(job_id: str) -> Dict[str, Any]:
    ensure_coding_quote_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(_quote_select_sql(), (str(job_id),))
        return _serialize_quote(cursor.fetchone())
    finally:
        cursor.close()
        conn.close()


def _user_balance(cursor: Any, user_id: int, *, for_update: bool = False) -> int:
    suffix = " FOR UPDATE" if for_update else ""
    cursor.execute(f"SELECT token_balance FROM users WHERE user_id=%s{suffix}", (int(user_id),))
    row = cursor.fetchone()
    if not row:
        raise CodingQuoteError("developer_coding_user_missing", status=404)
    return int(_row_value(row, "token_balance", 0, 0) or 0)


def quote_tokens_for_job(job: Mapping[str, Any]) -> int:
    """Return a fixed execution quote from the existing Coding Agent budget envelope.

    The planner call has already happened when a job reaches this point. We quote only
    the execution phase: each planned step may spend up to the existing per-step model
    budget, capped by the existing whole-job model budget. The USD budget is converted
    to internal VELIA usage tokens through a coding-specific configurable ratio rather
    than claiming that a VELIA token has a universal fixed USD value.
    """
    plan = job.get("plan") if isinstance(job.get("plan"), Mapping) else {}
    raw_steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    steps = max(1, int(job.get("total_steps") or len(raw_steps) or 1))
    per_step_budget = _env_float(
        "VELIA_DEVELOPER_CODING_MAX_COST_PER_STEP_USD", 0.06, 0.02, 0.15
    )
    job_budget = _env_float("VELIA_DEVELOPER_CODING_MAX_JOB_COST_USD", 0.24, 0.05, 1.0)
    execution_budget = min(job_budget, steps * per_step_budget)
    usd_budget_per_token = _env_float(
        "VELIA_DEVELOPER_CODING_USD_BUDGET_PER_TOKEN", 0.001, 0.0001, 0.1
    )
    minimum = _env_int("VELIA_DEVELOPER_CODING_MIN_QUOTE_TOKENS", 20, 1, 100000)
    maximum = _env_int("VELIA_DEVELOPER_CODING_MAX_QUOTE_TOKENS", 5000, minimum, 1000000)
    quoted = int(math.ceil(execution_budget / usd_budget_per_token))
    return min(maximum, max(minimum, quoted))


def create_quote(*, user_id: int, conversation_id: str, job: Mapping[str, Any]) -> Dict[str, Any]:
    ensure_coding_quote_tables()
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        raise CodingQuoteError("developer_coding_quote_job_missing", status=400)
    existing = quote_for_job(job_id)
    if existing:
        return existing
    quoted = quote_tokens_for_job(job)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        balance = _user_balance(cursor, int(user_id), for_update=False)
        status = "pending" if balance >= quoted else "insufficient"
        cursor.execute(
            """
            INSERT INTO velia_developer_coding_quotes (
                job_id,user_id,conversation_id,quoted_tokens,balance_at_quote,status,
                charged_tokens,pricing_version
            ) VALUES (%s,%s,%s,%s,%s,%s,0,'coding-budget-v1')
            ON CONFLICT (job_id) DO NOTHING
            """,
            (job_id, int(user_id), str(conversation_id), quoted, balance, status),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    value = quote_for_job(job_id)
    if not value:
        raise CodingQuoteError("developer_coding_quote_persist_failed", status=500)
    return value


def charge_quote(*, user_id: int, job_id: str) -> Dict[str, Any]:
    """Atomically re-check balance and debit the fixed quote before any repository write."""
    ensure_coding_quote_tables()
    conn = get_connection()
    cursor = conn.cursor()
    insufficient: Optional[Dict[str, Any]] = None
    try:
        cursor.execute(_quote_select_sql(for_update=True), (str(job_id),))
        row = cursor.fetchone()
        if not row:
            raise CodingQuoteError("developer_coding_quote_missing", status=409)
        quote = _serialize_quote(row)
        if int(quote.get("user_id") or 0) != int(user_id):
            raise CodingQuoteError("developer_coding_quote_owner_mismatch", status=403)
        if quote.get("status") in {"charged", "consumed"}:
            conn.commit()
            return quote
        if quote.get("status") != "pending":
            raise CodingQuoteError(
                "developer_coding_quote_not_payable", status=409, quote=quote
            )
        balance = _user_balance(cursor, int(user_id), for_update=True)
        quoted = int(quote.get("quoted_tokens") or 0)
        if balance < quoted:
            cursor.execute(
                """
                UPDATE velia_developer_coding_quotes
                SET status='insufficient', balance_at_quote=%s
                WHERE job_id=%s
                """,
                (balance, str(job_id)),
            )
            conn.commit()
            insufficient = {**quote, "status": "insufficient", "balance_tokens": balance}
        else:
            cursor.execute(
                "UPDATE users SET token_balance=token_balance-%s WHERE user_id=%s",
                (quoted, int(user_id)),
            )
            cursor.execute(
                """
                UPDATE velia_developer_coding_quotes
                SET status='charged', charged_tokens=%s, balance_at_quote=%s, charged_at=%s
                WHERE job_id=%s
                """,
                (quoted, balance, datetime.utcnow(), str(job_id)),
            )
            conn.commit()
            return {
                **quote,
                "status": "charged",
                "charged_tokens": quoted,
                "balance_tokens": balance,
            }
    except CodingQuoteError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    if insufficient is not None:
        raise CodingQuoteError(
            "developer_coding_insufficient_tokens", status=402, quote=insufficient
        )
    raise CodingQuoteError("developer_coding_quote_charge_failed", status=500)


def consume_quote(job_id: str) -> Dict[str, Any]:
    ensure_coding_quote_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(_quote_select_sql(for_update=True), (str(job_id),))
        quote = _serialize_quote(cursor.fetchone())
        if quote and quote.get("status") == "charged":
            cursor.execute(
                """
                UPDATE velia_developer_coding_quotes
                SET status='consumed', completed_at=%s WHERE job_id=%s
                """,
                (datetime.utcnow(), str(job_id)),
            )
            conn.commit()
            quote["status"] = "consumed"
            return quote
        conn.commit()
        return quote
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def refund_quote(*, user_id: int, job_id: str) -> Dict[str, Any]:
    """Refund a charged quote when Coding Agent did not complete successfully."""
    ensure_coding_quote_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(_quote_select_sql(for_update=True), (str(job_id),))
        quote = _serialize_quote(cursor.fetchone())
        if not quote or int(quote.get("user_id") or 0) != int(user_id):
            conn.commit()
            return quote
        if quote.get("status") != "charged":
            conn.commit()
            return quote
        charged = int(quote.get("charged_tokens") or quote.get("quoted_tokens") or 0)
        _user_balance(cursor, int(user_id), for_update=True)
        cursor.execute(
            "UPDATE users SET token_balance=token_balance+%s WHERE user_id=%s",
            (charged, int(user_id)),
        )
        cursor.execute(
            """
            UPDATE velia_developer_coding_quotes
            SET status='refunded', refunded_at=%s WHERE job_id=%s
            """,
            (datetime.utcnow(), str(job_id)),
        )
        conn.commit()
        return {**quote, "status": "refunded"}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def cancel_quote(job_id: str) -> None:
    if not str(job_id or "").strip():
        return
    ensure_coding_quote_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_coding_quotes
            SET status='cancelled'
            WHERE job_id=%s AND status IN ('pending','insufficient')
            """,
            (str(job_id),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _russian(message: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", str(message or "")))


def insufficient_text(message: str, quote: Mapping[str, Any]) -> str:
    price = int(quote.get("quoted_tokens") or 0)
    balance = int(quote.get("balance_tokens") or 0)
    if _russian(message):
        return (
            f"Для выполнения этой задачи нужно {price} VELIA-токенов, а на балансе {balance}. "
            "Задание отменено — код, ветка и PR не создавались."
        )
    return (
        f"This coding task requires {price} VELIA tokens, but the balance is {balance}. "
        "The task was cancelled; no code, branch, or PR was created."
    )


def quote_text(message: str, quote: Mapping[str, Any]) -> str:
    price = int(quote.get("quoted_tokens") or 0)
    if _russian(message):
        return f"Стоимость выполнения всего плана — {price} VELIA-токенов. Выполняем?"
    return f"The fixed price for executing the whole plan is {price} VELIA tokens. Proceed?"


def enrich_result_with_quote(
    result: Dict[str, Any],
    *,
    user_id: int,
    conversation_id: str,
    request_id: str,
    message: str,
    quote: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    context = result.get("agent_context") if isinstance(result.get("agent_context"), dict) else {}
    presentation = context.get("presentation") if isinstance(context.get("presentation"), dict) else {}
    coding = presentation.get("coding") if isinstance(presentation.get("coding"), dict) else {}
    if not coding:
        return result
    value = dict(quote or {})
    if not value:
        job_id = str((result.get("developer_context") or {}).get("coding_job_id") or "")
        if not job_id:
            # Presentation history/status can omit developer_context; resolve the newest quote.
            conn = get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT job_id,user_id,conversation_id,quoted_tokens,balance_at_quote,status,
                           charged_tokens,pricing_version
                    FROM velia_developer_coding_quotes
                    WHERE user_id=%s AND conversation_id=%s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (int(user_id), str(conversation_id)),
                )
                value = _serialize_quote(cursor.fetchone())
            finally:
                cursor.close()
                conn.close()
        else:
            value = quote_for_job(job_id)
    if not value:
        return result
    coding = dict(coding)
    coding.update(
        {
            "quoted_tokens": int(value.get("quoted_tokens") or 0),
            "balance_tokens": int(value.get("balance_tokens") or 0),
            "quote_status": str(value.get("status") or ""),
        }
    )
    presentation = dict(presentation)
    presentation["coding"] = coding
    if value.get("status") in {"insufficient", "cancelled", "refunded"}:
        presentation["can_execute"] = False
        presentation["can_cancel"] = False
        presentation["execute_command"] = ""
        presentation["cancel_command"] = ""
    context = dict(context)
    context["presentation"] = presentation
    result["agent_context"] = context
    if str(request_id or "").strip():
        presentation_store.persist_context_best_effort(
            request_id=str(request_id),
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            context=context,
        )
    return result
