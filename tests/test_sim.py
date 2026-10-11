from netlab.sim import (cross_validate, simulate_csma_cd, simulate_csma_cd_naive,
                        validate_slotted_aloha, sweep_csma_cd, FRAME_SLOTS)


def test_two_implementations_agree_exactly():
    """The guard against silent busying-accounting drift.

    A past revision advanced the slot counter in the success branch *and* kept a
    busy counter, costing every frame an extra slot and halving utilisation.
    This test is what makes that class of bug impossible to miss.
    """
    for n in (2, 4, 8):
        c = cross_validate(n, n_slots=40_000, seed=11)
        assert c["util_abs_diff"] < 1e-12, c
        assert c["delay_abs_diff"] < 1e-12, c


def test_every_slot_is_accounted_for():
    """Idle + transmitted + collided must equal the total. Nothing vanishes."""
    r = simulate_csma_cd(6, n_slots=60_000, seed=3)
    assert abs(r["audit"]["residual"]) < 1e-3


def test_reproducible_under_fixed_seed():
    a = simulate_csma_cd(8, n_slots=30_000, seed=99)
    b = simulate_csma_cd(8, n_slots=30_000, seed=99)
    assert a["utilization"] == b["utilization"]
    assert a["delay"] == b["delay"]


def test_utilisation_stays_within_physical_bounds():
    r = simulate_csma_cd(12, n_slots=50_000, seed=5)
    assert 0.0 <= r["utilization"] <= 1.0
    assert 0.0 <= r["idle"] <= 1.0


def test_collision_rate_grows_with_domain_size():
    """Growth of the collision domain must degrade the channel monotonically."""
    small = simulate_csma_cd(2, n_slots=80_000, seed=21)["collision_rate"]
    large = simulate_csma_cd(32, n_slots=80_000, seed=21)["collision_rate"]
    assert large > small


def test_delay_exceeds_one_frame_time():
    # Every frame pays at least FRAME_SLOTS on the wire, before any queueing.
    r = simulate_csma_cd(10, n_slots=50_000, seed=17)
    assert r["delay"] >= FRAME_SLOTS


def test_fairness_bounded():
    r = simulate_csma_cd(8, n_slots=50_000, seed=23)
    assert 0.0 < r["fairness"] <= 1.0


def test_aloha_matches_exact_binomial_theory():
    a = validate_slotted_aloha(n_hosts=50, p=0.02, n_slots=300_000, seed=1)
    # Must track the exact binomial value, not the coarser Poisson limit.
    assert a["rel_err_pct"] < 1.0
    assert abs(a["sim"] - a["theory"]) < abs(a["sim"] - a["poisson"])


def test_sweep_returns_intervals_for_every_n():
    res = sweep_csma_cd([2, 8], repeats=3, n_slots=20_000)
    for n in (2, 8):
        u_mean, u_ci = res[n]["util"]
        assert 0.0 <= u_mean <= 1.0 and u_ci >= 0.0
        assert res[n]["resid"] < 1e-2


def test_sweep_is_monotone_in_collision_rate():
    res = sweep_csma_cd([2, 16, 48], repeats=3, n_slots=30_000)
    cr = [res[n]["cr"][0] for n in (2, 16, 48)]
    assert cr[0] < cr[1] < cr[2]
