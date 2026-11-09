import shutil

from radar import settings as S
from radar.registry import importer


def test_import_entries(tmp_path, monkeypatch):
    reg = tmp_path / "registry"; reg.mkdir()
    shutil.copy(S.REPO_ROOT / "registry" / "seed_florida.yaml", reg / "seed_florida.yaml")
    monkeypatch.setenv("RADAR_REGISTRY_DIR", str(reg))
    S.get_settings.cache_clear(); S.sources_config.cache_clear()
    entries = [
        {"name": "Testville Police Officers' Pension Fund", "city": "Testville", "type": "POLICE_FIRE",
         "url": "https://testvillepension.example.org/board/", "platform": "WordPress", "status": "VERIFIED"},
        {"name": "Blockburg General Employees", "city": "Blockburg", "type": "PUBLIC_PENSION_LOCAL",
         "url": "https://www.blockburg.example.gov/pension", "platform": "CivicPlus", "status": "BLOCKED", "note": "403"},
        {"name": "X Plan", "city": "X", "url": "https://www.resourcecenters.com/x", "status": "VERIFIED"},
        {"name": "Nowhere Plan", "city": "Nowhere", "status": "NOT FOUND"},
    ]
    out = importer.import_entries(entries, "t1")
    assert out["added"] == ["fl-testville-police"] and out["manual"] == ["fl-blockburg-general"]
    assert len(out["skipped"]) == 2 and out["new_hosts"] == 2
    seed = (reg / "seed_florida.yaml").read_text()
    assert "fl-testville-police" in seed and "template: T-WP" in seed and "template: MANUAL" in seed
    src = (S.get_settings().config_dir / "sources.yaml").read_text()
    assert "testvillepension.example.org], verdict: CRAWL" in src and "www.blockburg.example.gov], verdict: MANUAL" in src
    S.sources_config.cache_clear()
