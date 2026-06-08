from services import skill_loader_service as svc


def test_load_skill_edge_education_contains_edge():
    content = svc.load_skill("edge_education")

    assert "edge" in content.lower()


def test_load_skill_parent_secret_returns_empty():
    assert svc.load_skill("../secret") == ""


def test_load_skill_unknown_returns_empty():
    assert svc.load_skill("unknown_skill") == ""


def test_load_skills_includes_multiple_requested_skills():
    content = svc.load_skills(["edge_education", "risk_coach"])

    assert "edge" in content.lower()
    assert "Resolution ambiguity" in content or "resolution ambiguity" in content.lower()


def test_loader_refuses_path_traversal():
    assert svc.load_skill("edge_education/../../risk_coach") == ""
    assert svc.load_skill("/tmp/edge_education") == ""


def test_loader_strips_dangerous_fenced_shell_blocks():
    dangerous = """# Safe title

Keep this.

```bash
rm -rf /tmp/example
```

Still safe.
"""

    cleaned = svc._strip_dangerous_sections(dangerous)

    assert "Keep this." in cleaned
    assert "Still safe." in cleaned
    assert "rm -rf" not in cleaned
    assert "```bash" not in cleaned


def test_loader_rejects_symlinked_skill_file(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "edge_education"
    skill_dir.mkdir(parents=True)
    target = tmp_path / "target.md"
    target.write_text("# Edge\n", encoding="utf-8")
    (skill_dir / "SKILL.md").symlink_to(target)
    monkeypatch.setattr(svc, "_SKILLS_ROOT", skills_root)

    assert svc.load_skill("edge_education") == ""


def test_loader_rejects_oversized_skill_file(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "edge_education"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("x" * (svc._MAX_SKILL_BYTES + 1), encoding="utf-8")
    monkeypatch.setattr(svc, "_SKILLS_ROOT", skills_root)

    assert svc.load_skill("edge_education") == ""
