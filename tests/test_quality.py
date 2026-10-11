import numpy as np

from netlab.quality import (bootstrap_ci, jitter_rfc3550, quality_gate,
                            clean_rtt, wilson_interval, describe_latency,
                            normality, norm_ppf, shapiro_critical)


def rows(spec):
    """Build rows where True -> OK with the given RTT, False -> timeout."""
    out = []
    for i, v in enumerate(spec, start=1):
        if v is None:
            out.append({"seq": i, "ok": False, "rtt_ms": None, "ttl": None, "bytes": None})
        else:
            out.append({"seq": i, "ok": True, "rtt_ms": v, "ttl": 64, "bytes": 32})
    return out


def test_quality_gate_keeps_timeouts():
    r = quality_gate(rows([10.0, None, 12.0]))
    assert r.total == 3 and r.ok == 2 and r.missing == 1
    # timeouts must survive -- dropping them would deflate the loss rate
    assert len(clean_rtt(rows([10.0, None, 12.0]))) == 2


def test_quality_gate_flags_duplicate_and_extreme():
    bad = rows([10.0, 9999.0, 0.0])
    bad[1]["seq"] = 1
    r = quality_gate(bad)
    assert r.duplicate_seq == 1 and r.extreme == 1 and r.nonpositive == 1


def test_clean_rtt_excludes_extremes():
    assert clean_rtt(rows([10.0, 5000.0])).tolist() == [10.0]


def test_wilson_handles_zero_loss():
    lo, hi = wilson_interval(30, 30)
    assert 0.0 <= lo < hi <= 1.0
    assert lo > 0.0 or hi > 0.0          # must not degenerate to (0, 0)
    # Wald would give 0.0 width here; Wilson must not
    assert hi - lo > 0.0


def test_wilson_symmetric_case():
    lo, hi = wilson_interval(15, 30)
    assert abs(((lo + hi) / 2) - 0.5) < 0.02


def test_jitter_of_constant_series_is_zero():
    assert abs(jitter_rfc3550([10.0] * 20)) < 1e-9


def test_jitter_detects_alternation_more_than_linear_trend():
    # Same mean-difference magnitude, different consecutive movement.
    alternating = [10.0, 20.0] * 20
    trend = list(np.linspace(10, 20, 40))
    assert jitter_rfc3550(alternating) > jitter_rfc3550(trend)


def test_bootstrap_ci_is_reproducible_and_brackets_median():
    data = [10.0, 11.0, 12.0, 13.0, 40.0, 12.5, 11.5]
    a = bootstrap_ci(data, seed=1)
    b = bootstrap_ci(data, seed=1)
    assert a == b                                    # same seed -> same result
    lo, hi = a
    assert lo <= np.median(data) <= hi


def test_bootstrap_ci_robust_to_single_sample():
    lo, hi = bootstrap_ci([7.0])
    assert lo == hi == 7.0


def test_describe_latency_fields():
    d = describe_latency([10.0, 12.0, 11.0, 13.0, 10.5])
    assert d["n"] == 5
    assert d["min_ms"] <= d["median_ms"] <= d["max_ms"]
    assert d["p95_ms"] >= d["p75_ms"]
    assert set(d) >= {"mean_ms", "sd_ms", "cv", "iqr_ms", "jitter_rfc3550_ms"}


def test_norm_ppf_known_values():
    assert abs(norm_ppf(0.5)) < 1e-9
    assert abs(norm_ppf(0.975) - 1.959964) < 1e-4
    assert abs(norm_ppf(0.025) + 1.959964) < 1e-4


def test_normality_rejects_skewed_data():
    rng = np.random.RandomState(0)
    skewed = list(rng.exponential(scale=50.0, size=60))
    res = normality(skewed)
    assert res["skew"] > 0.5
    assert res["p_lt_0.05"] is True


def test_normality_accepts_gaussian():
    rng = np.random.RandomState(1)
    gauss = list(rng.normal(50.0, 3.0, size=60))
    assert normality(gauss)["p_lt_0.05"] is False


def test_shapiro_critical_monotone():
    vals = [shapiro_critical(n) for n in (5, 10, 20, 30, 50)]
    assert vals == sorted(vals)
