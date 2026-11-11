import pytest

from radar import settings as S


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    """Every test gets its own data dir, lake key, and DB. GLiNER off unless asked."""
    monkeypatch.setenv("RADAR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RADAR_LAKE_KEY_FILE", str(tmp_path / "lake.key"))
    monkeypatch.setenv("RADAR_FLAG_GLINER", "false")
    # The suite exercises the strict Wall (contacts scrubbed) unless a test opts into D-B35's default (contacts kept).
    monkeypatch.setenv("RADAR_SCRUB_CONTACTS", "true")
    monkeypatch.setenv("RADAR_MIN_HOST_DELAY_S", "0")
    monkeypatch.setenv("RADAR_MANUAL_INBOX", str(tmp_path / "inbox" / "manual"))
    # Tests exercise manual review by default; the owner's auto-approve policy is tested explicitly.
    cfg = tmp_path / "config"
    import shutil
    shutil.copytree(S.REPO_ROOT / "config", cfg)
    (cfg / "review_policy.yaml").write_text("signals: {auto_approve: false}\nregistry: {auto_approve: false}\n")
    monkeypatch.setenv("RADAR_CONFIG_DIR", str(cfg))
    S.get_settings.cache_clear()
    from radar import db
    db.init_db()
    yield tmp_path
    S.get_settings.cache_clear()
