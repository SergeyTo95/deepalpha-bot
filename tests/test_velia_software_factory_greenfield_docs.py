from pathlib import Path


def test_greenfield_contract_documented_as_no_repository_creation():
    text = Path("docs/VELIA_SOFTWARE_FACTORY_STAGE4_5_GREENFIELD.md").read_text(encoding="utf-8")
    assert "does not create GitHub repositories" in text
    assert "exact `owner/name`" in text
    assert "initial commit" in text
    assert "explicit approval" in text
