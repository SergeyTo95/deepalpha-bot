"""Persist and run on-demand PR reviews in the existing VELIA process."""

from contextlib import closing, contextmanager
import logging
import os
from uuid import uuid4

from psycopg2.extras import Json, RealDictCursor

from db.database import get_connection
from services import velia_developer_github_service as github
from services import velia_developer_project_service as projects
from services import velia_pr_review_engine as engine
from services.velia_admin_security_service import configured_admin_id
from services.velia_software_factory_rollout_service import admin_pilot_user_allowed, explicit_user_allowed

logger = logging.getLogger(__name__)
_LIMIT_PER_USER = 3
_LIMIT_GLOBAL = 12
ReviewError = engine.ReviewError


def enabled():
    return os.getenv("VELIA_PR_REVIEW_ENABLED", "").lower() in {"1", "true"}


def allowed(user_id):
    return enabled() and user_id > 0 and (
        user_id == configured_admin_id() or explicit_user_allowed(user_id) or admin_pilot_user_allowed(user_id)
    )


def ready():
    return os.access(engine.binary_path(), os.X_OK)


@contextmanager
def _cursor():
    with closing(get_connection()) as connection, connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        yield cursor


def ensure_schema():
    with _cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS velia_pr_reviews (
                id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                project_id TEXT NOT NULL REFERENCES velia_developer_projects(project_id),
                pull_number INTEGER NOT NULL,
                head_sha TEXT NOT NULL,
                base_sha TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                report JSONB,
                error TEXT,
                review_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS velia_pr_reviews_user_project_idx
            ON velia_pr_reviews(user_id, project_id, created_at DESC)
        """)


def project_for(user_id, project_id):
    project = projects.get_project(user_id, project_id)
    projects.get_installation(user_id, project["installation_id"])
    return project


def _public(record):
    return {
        "id": record["id"],
        "pull_number": record["pull_number"],
        "head_sha": record["head_sha"],
        "model": record["model"],
        "status": record["status"],
        "report": record["report"],
        "error": record["error"],
        "review_id": record["review_id"],
        "created_at": record["created_at"].isoformat(),
    }


def enqueue(user_id, project, data):
    if not allowed(user_id):
        raise ReviewError("pr_review_not_allowed", 403)
    if not ready():
        raise ReviewError("pr_review_not_ready", 503)
    if set(data) != {"pull_number", "head_sha", "base_sha", "model"}:
        raise ReviewError("pr_review_invalid_request")
    pull = data["pull_number"]
    model = data["model"]
    head, base = str(data["head_sha"]), str(data["base_sha"])
    if type(pull) is not int or not 0 < pull < 2_147_483_648 or model not in engine.available_models():
        raise ReviewError("pr_review_invalid_request")
    if not engine.SHA.fullmatch(head) or not engine.SHA.fullmatch(base):
        raise ReviewError("pr_review_invalid_request")

    with _cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext('velia_pr_review_enqueue'))")
        cursor.execute("""
            SELECT * FROM velia_pr_reviews
            WHERE user_id=%s AND project_id=%s AND pull_number=%s AND head_sha=%s AND base_sha=%s AND model=%s
              AND status IN ('queued','running','completed','publishing','published')
            ORDER BY created_at DESC LIMIT 1
        """, (user_id, project["id"], pull, head, base, model))
        previous = cursor.fetchone()
        if previous:
            return _public(previous)
        cursor.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE user_id=%s) AS actor,
                   COUNT(*) FILTER (WHERE user_id=%s AND status IN ('queued','running')) AS active
            FROM velia_pr_reviews WHERE created_at > now() - interval '1 day'
        """, (user_id, user_id))
        quotas = cursor.fetchone()
        if quotas["active"] or quotas["actor"] >= _LIMIT_PER_USER or quotas["total"] >= _LIMIT_GLOBAL:
            raise ReviewError("pr_review_limit", 429)
        cursor.execute("""
            INSERT INTO velia_pr_reviews (id,user_id,project_id,pull_number,head_sha,base_sha,model,status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'queued') RETURNING *
        """, (str(uuid4()), user_id, project["id"], pull, head, base, model))
        return _public(cursor.fetchone())


def list_reviews(user_id, project_id):
    with _cursor() as cursor:
        cursor.execute("""
            SELECT * FROM velia_pr_reviews WHERE user_id=%s AND project_id=%s
            ORDER BY created_at DESC LIMIT 10
        """, (user_id, project_id))
        return [_public(row) for row in cursor.fetchall()]


def _get_review(cursor, user_id, project_id, review_id, *, lock=False):
    query = "SELECT * FROM velia_pr_reviews WHERE id=%s AND project_id=%s AND user_id=%s"
    cursor.execute(query + (" FOR UPDATE" if lock else ""), (review_id, project_id, user_id))
    review = cursor.fetchone()
    if not review:
        raise ReviewError("pr_review_not_found", 404)
    return review


def fetch_review(user_id, project_id, review_id):
    with _cursor() as cursor:
        return _public(_get_review(cursor, user_id, project_id, review_id))


def _claim():
    with _cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_xact_lock(hashtext('velia_pr_review_claim')) AS acquired")
        if not cursor.fetchone()["acquired"]:
            return None
        cursor.execute("""
            UPDATE velia_pr_reviews SET status='failed',error='pr_review_worker_restarted',updated_at=now()
            WHERE status='running' AND updated_at < now() - interval '12 minutes'
        """)
        cursor.execute("""
            UPDATE velia_pr_reviews SET status='publication_unknown',error='pr_review_publication_unknown',updated_at=now()
            WHERE status='publishing' AND updated_at < now() - interval '10 minutes'
        """)
        cursor.execute("SELECT 1 FROM velia_pr_reviews WHERE status='running' LIMIT 1")
        if cursor.fetchone():
            return None
        cursor.execute("""
            UPDATE velia_pr_reviews SET status='running',updated_at=now()
            WHERE id=(SELECT id FROM velia_pr_reviews WHERE status='queued' ORDER BY created_at LIMIT 1)
            RETURNING *
        """)
        return cursor.fetchone()


def process_once():
    if not enabled() or not ready():
        return False
    review = _claim()
    if not review:
        return False
    report = None
    error = None
    try:
        if not allowed(review["user_id"]):
            raise ReviewError("pr_review_not_allowed", 403)
        project = project_for(review["user_id"], review["project_id"])
        report = engine.run_review(project, review)
    except (ReviewError, github.DeveloperGithubError, projects.DeveloperProjectError) as exc:
        error = exc.code
    except Exception as exc:
        logger.warning("VELIA_PR_REVIEW_FAILED %s", type(exc).__name__)
        error = "pr_review_failed"
    with _cursor() as cursor:
        cursor.execute("""
            UPDATE velia_pr_reviews SET status=%s,report=%s,error=%s,updated_at=now()
            WHERE id=%s AND status='running'
        """, ("completed" if report else "failed", Json(report) if report else None, error, review["id"]))
        cursor.execute("DELETE FROM velia_pr_reviews WHERE created_at < now() - interval '30 days' AND status NOT IN ('queued','running','publishing')")
    return True


def publish(user_id, project, review_id):
    if not allowed(user_id):
        raise ReviewError("pr_review_not_allowed", 403)
    with _cursor() as cursor:
        review = _get_review(cursor, user_id, project["id"], review_id, lock=True)
        if review["status"] == "published":
            return _public(review)
        if review["status"] != "completed":
            raise ReviewError("pr_review_publication_unavailable", 409)
        cursor.execute("UPDATE velia_pr_reviews SET status='publishing',updated_at=now() WHERE id=%s", (review_id,))
    try:
        review_comment_id = engine.publish_review(project, review)
    except ReviewError as exc:
        _finish_publication(review_id, "stale" if exc.code == "pr_review_stale" else "completed", exc.code)
        raise
    except github.DeveloperGithubError as exc:
        if exc.code in {"github_forbidden", "github_not_found", "github_invalid_request", "github_rate_limited"}:
            _finish_publication(review_id, "completed", exc.code)
            raise
        _finish_publication(review_id, "publication_unknown", "pr_review_publication_unknown")
        raise ReviewError("pr_review_publication_unknown", 502) from exc
    except Exception as exc:
        _finish_publication(review_id, "publication_unknown", "pr_review_publication_unknown")
        raise ReviewError("pr_review_publication_unknown", 502) from exc
    _finish_publication(review_id, "published", None, review_comment_id)
    return fetch_review(user_id, project["id"], review_id)


def _finish_publication(review_id, status, error, comment_id=None):
    with _cursor() as cursor:
        cursor.execute("""
            UPDATE velia_pr_reviews SET status=%s,error=%s,review_id=%s,updated_at=now()
            WHERE id=%s AND status='publishing'
        """, (status, error, comment_id, review_id))
