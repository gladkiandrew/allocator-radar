from radar import scope


def test_south_florida_scope():
    c = scope.config()
    assert c["name"] == "south_florida" and c["label"] == "South Florida"
    assert scope.contains({"allocator_id": "x", "jurisdiction_city": "Boca Raton"})
    assert scope.contains({"allocator_id": "fl-sba-frs", "jurisdiction_city": "Tallahassee"})  # statewide plan
    assert not scope.contains({"allocator_id": "y", "jurisdiction_city": "Tampa"})
    assert scope.county({"allocator_id": "x", "jurisdiction_city": "Pompano Beach"}) == "Broward"
