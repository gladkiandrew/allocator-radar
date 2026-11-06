"""Wall layer 4b: no outbound-capable dependency or import exists."""
from radar.wall import live


def test_no_banned_packages_or_imports():
    assert live.check_dependencies() == []
