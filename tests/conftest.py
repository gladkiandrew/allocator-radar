import pytest

from radar import settings as S


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    """Every test gets its own data dir, lake key, and DB. GLiNER off unless asked."""
    monkeypatch.setenv("RADAR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RADAR_LAKE_KEY_FILE", str(tmp_path / "lake.key"))
    monkeypatch.setenv("RADAR_FLAG_GLINER", "false")
    monkeypatch.setenv("RADAR_MIN_HOST_DELAY_S", "0")
    monkeypatch.setenv("RADAR_MANUAL_INBOX", str(tmp_path / "inbox" / "manual"))
    S.get_settings.cache_clear()
    from radar import db
    db.init_db()
    yield tmp_path
    S.get_settings.cache_clear()
