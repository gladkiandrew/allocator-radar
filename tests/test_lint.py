from radar.wall.lint import lint


def test_factual_passes():
    assert lint("The Board voted to issue an RFP for a hedge fund of funds manager.", "both").ok
    assert lint("Target allocation to alternatives increased from 10% to 15%.", "both").ok


def test_evaluative_fails():
    for s in ["Manager X underperformed its benchmark.", "A strong candidate for outreach.",
              "This is a hot opportunity.", "Best-in-class returns.", "Likely to hire soon."]:
        assert not lint(s, "both").ok, s


def test_posture_none_is_base_only():
    assert lint("This is likely.", "none").ok
    assert not lint("Strong returns.", "none").ok
