from radar.resolve import normalize, score


def test_positives():
    assert score("Elliott Management", "Elliott Investment Management L.P.") >= 0.8
    assert score("Citadel Advisors", "Citadel Advisors LLC") == 1.0
    assert score("Mariner", "Mariner Institutional") >= 0.8


def test_hard_negatives_never_merge():
    assert score("Millennium Management", "Millennium Partners") < 0.95
    assert score("Pershing Square", "Pershing LLC") < 0.95
    assert score("Davidson Kempner", "Kempner Capital") < 0.95
    assert score("AndCo Consulting", "Segal Marco Advisors") == 0.0


def test_normalize():
    assert normalize("Cohen & Steers, Inc.") == "cohen and steers"
