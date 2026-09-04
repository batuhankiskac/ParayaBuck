from scripts.research.equity_profit_protection_2026_09 import EpisodeState


def state(basis=100, qty=10):
    return EpisodeState(qty, basis)


def test_activation_boundary_and_no_activation_bar_exit():
    for close, armed in [(104.999, False), (105, True), (105.001, True)]:
        s = state()
        assert not s.observe(close, "E2_PROFIT_LOCK_50")
        assert s.armed is armed


def test_take_profit_boundary():
    s = state()
    assert not s.observe(104.999, "E1_TP5_CLOSE")
    assert s.observe(105, "E1_TP5_CLOSE")


def test_profit_lock_later_close_and_gap_is_not_threshold_fill():
    s = state()
    assert not s.observe(106, "E2_PROFIT_LOCK_50")
    assert not s.observe(104, "E2_PROFIT_LOCK_50")
    assert s.observe(103, "E2_PROFIT_LOCK_50")


def test_confirmed_alpha_requires_two_later_bars():
    s = state()
    s.observe(105, "E3_PROFIT_ALPHA_CONFIRMED")
    assert not s.observe(104, "E3_PROFIT_ALPHA_CONFIRMED", rank=13)
    assert s.observe(103, "E3_PROFIT_ALPHA_CONFIRMED", rank=13)


def test_buy_resets_weighted_basis_and_state():
    s = state()
    s.observe(106, "E2_PROFIT_LOCK_50")
    s.apply_buy(10, 120)
    assert (s.quantity, s.basis, s.armed) == (20, 110, False)


def test_trim_preserves_and_full_exit_resets_then_reentry_fresh():
    s = state()
    s.observe(106, "E2_PROFIT_LOCK_50")
    s.apply_sell(4)
    assert s.armed
    s.apply_sell(6)
    assert not s.armed
    s.apply_buy(2, 90)
    assert s.basis == 90 and not s.armed


def test_partial_pending_and_restart_equivalence():
    s = state()
    assert s.observe(105, "E1_TP5_CLOSE")
    s.apply_sell(4)
    restored = EpisodeState.restore(s.snapshot())
    assert restored.pending and restored.quantity == 6
    assert not restored.observe(110, "E1_TP5_CLOSE")


def test_split_adjusted_basis_contract():
    s = state(50, 20)
    assert s.observe(52.5, "E1_TP5_CLOSE")


def test_external_quantity_excluded_and_duplicate_prevented():
    s = state(qty=4)
    assert s.observe(105, "E1_TP5_CLOSE")
    assert not s.observe(106, "E1_TP5_CLOSE")
    assert s.quantity == 4
