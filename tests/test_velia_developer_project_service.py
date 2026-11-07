from services import velia_developer_project_service as projects


def test_install_state_round_trip_and_expiry(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_STATE_SECRET", "x" * 48)
    token = projects.create_install_state(77, now=1_800_000_000)

    payload = projects.verify_install_state(token, now=1_800_000_100)
    assert payload["user_id"] == 77
    assert payload["v"] == 1

    try:
        projects.verify_install_state(token, now=1_800_001_000)
        assert False
    except projects.DeveloperProjectError as exc:
        assert exc.code == "install_state_expired"


def test_install_state_rejects_tampering(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_STATE_SECRET", "y" * 48)
    token = projects.create_install_state(88, now=1_800_000_000)
    encoded, signature = token.split(".")
    tampered = ("A" if encoded[0] != "A" else "B") + encoded[1:] + "." + signature

    try:
        projects.verify_install_state(tampered, now=1_800_000_100)
        assert False
    except projects.DeveloperProjectError as exc:
        assert exc.code == "invalid_install_state"


def test_state_secret_is_required(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_STATE_SECRET", raising=False)
    try:
        projects.create_install_state(1, now=1_800_000_000)
        assert False
    except projects.DeveloperProjectError as exc:
        assert exc.code == "developer_state_secret_missing"
        assert exc.status == 503

def test_start_run_expires_stale_pending_before_insert(monkeypatch):
    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))

        def close(self):
            pass

    class Connection:
        def __init__(self):
            self.cursor_value = Cursor()
            self.committed = False

        def cursor(self):
            return self.cursor_value

        def commit(self):
            self.committed = True

        def rollback(self):
            pass

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(projects, "_SCHEMA_READY", True)
    monkeypatch.setattr(projects, "get_connection", lambda: connection)
    monkeypatch.setenv("VELIA_DEVELOPER_RUN_LEASE_SECONDS", "300")

    run_id = projects.start_run(7, "project-1", "question")

    assert run_id
    assert connection.committed is True
    assert len(connection.cursor_value.calls) == 2
    expire_sql, expire_params = connection.cursor_value.calls[0]
    insert_sql, _ = connection.cursor_value.calls[1]
    assert "developer_run_expired" in expire_sql
    assert "status='pending'" in expire_sql
    assert expire_params[1] == 7
    assert "INSERT INTO velia_developer_runs" in insert_sql


def test_disconnect_hides_only_owners_repositories_and_preserves_history(monkeypatch):
    import sqlite3
    import pytest
    database = sqlite3.connect(':memory:')
    database.executescript('''
      CREATE TABLE velia_developer_installations (installation_id INTEGER PRIMARY KEY, user_id INTEGER,
        account_login TEXT, account_type TEXT, repository_selection TEXT, created_at TEXT, updated_at TEXT, deleted_at TEXT);
      CREATE TABLE velia_developer_projects (project_id TEXT, user_id INTEGER, installation_id INTEGER,
        repository_id INTEGER, repository_full_name TEXT, default_branch TEXT, selected_branch TEXT,
        is_private INTEGER, is_archived INTEGER, created_at TEXT, updated_at TEXT, deleted_at TEXT);
      CREATE TABLE velia_developer_runs (run_id TEXT, project_id TEXT, answer TEXT);
      INSERT INTO velia_developer_installations VALUES (10,7,'a','User','selected','','',NULL),(20,8,'b','User','selected','','',NULL);
      INSERT INTO velia_developer_projects VALUES ('p7',7,10,1,'a/repo','main','main',1,0,'','',NULL),('p8',8,20,2,'b/repo','main','main',1,0,'','',NULL);
      INSERT INTO velia_developer_runs VALUES ('r7','p7','Saved answer');
    ''')
    class Cursor:
        def __init__(self): self.cursor = database.cursor()
        def execute(self, sql, params): self.cursor.execute(sql.replace('%s','?'), tuple(str(v) if hasattr(v,'isoformat') else v for v in params))
        def fetchone(self): return self.cursor.fetchone()
        def fetchall(self): return self.cursor.fetchall()
        def close(self): self.cursor.close()
    class Connection:
        def cursor(self, **kwargs): return Cursor()
        def commit(self): database.commit()
        def rollback(self): database.rollback()
        def close(self): pass
    monkeypatch.setattr(projects, '_SCHEMA_READY', True)
    monkeypatch.setattr(projects, 'get_connection', Connection)
    assert projects.get_project(7, 'p7')['repository_full_name'] == 'a/repo'
    projects.disconnect_github(7)
    projects.disconnect_github(7)  # idempotent
    assert projects.list_installations(7) == [] and projects.list_projects(7) == []
    with pytest.raises(projects.DeveloperProjectError, match='developer_project_not_found'):
        projects.get_project(7, 'p7')
    assert projects.get_project(8, 'p8')['repository_full_name'] == 'b/repo'
    assert len(projects.list_installations(8)) == len(projects.list_projects(8)) == 1
    assert database.execute('SELECT answer FROM velia_developer_runs').fetchone()[0] == 'Saved answer'
    assert database.execute('SELECT count(*) FROM velia_developer_projects').fetchone()[0] == 2
    database.execute('UPDATE velia_developer_installations SET deleted_at=NULL WHERE installation_id=10')
    assert projects.get_project(7, 'p7')['repository_full_name'] == 'a/repo'
    database.close()
