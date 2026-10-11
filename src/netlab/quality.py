"""Data quality gates and small-sample statistics for latency measurements.

Design rules
------------
1. **Never silently drop rows.**  A timeout carries information; deleting it
   deflates the loss rate.  Quality gates *flag* rows, they do not erase them.
2. **Report uncertainty, not point estimates.**  A loss rate of "1/30" is not
   3.3% — with n=30 the Wilson 95% interval is roughly [0.6%, 17%].  Every
   headline number in this repo ships with an interval.
3. **Prefer bootstrap for RTT statistics.**  Round-trip times are right-skewed
   and frequently multi-modal (route changes), so the standard error of the
   mean rests on assumptions that latency data routinely violates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np


# --------------------------------------------------------------------------- #
# Quality gates
# --------------------------------------------------------------------------- #

@dataclass
class QualityReport:
    """Outcome of the quality gate applied to a batch of latency rows."""

    total: int = 0
    ok: int = 0
    missing: int = 0
    duplicate_seq: int = 0
    nonpositive: int = 0
    extreme: int = 0
    flags: Dict[str, List[int]] = field(default_factory=dict)
    passed: bool = True
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        d = self.__dict__.copy()
        d["loss_pct"] = round(self.loss_pct, 4)
        # Row indices are useless outside a single run — keep only counts.
        d["flags"] = {k: len(v) for k, v in self.flags.items()}
        return d

    @property
    def loss_pct(self) -> float:
        return 100.0 * (self.total - self.ok) / self.total if self.total else 0.0


def quality_gate(rows: Iterable[Dict], extreme_ms: float = 3000.0) -> QualityReport:
    """Flag, count and never drop anomalous probes.

    Checks performed:
      * missing    — probe reported a failure / no RTT
      * duplicate  — same target produced the same icmp_seq twice (reordering or
                     a duplicated reply, both common on Wi-Fi)
      * nonpositive— RTT <= 0 ms: usually a coarse timer resolution artefact
      * extreme    — RTT above ``extreme_ms``: keep it, but exclude it from
                     location statistics as it usually indicates a stall, not
                     network latency
    """
    rows = list(rows)
    rep = QualityReport(total=len(rows))
    seen: Dict[int, int] = {}
    for i, r in enumerate(rows):
        if not r.get("ok") or r.get("rtt_ms") is None:
            rep.missing += 1
            rep.flags.setdefault("missing", []).append(i)
            continue
        rep.ok += 1
        seq = r.get("seq")
        if seq is not None:
            seen[seq] = seen.get(seq, 0) + 1
            if seen[seq] > 1:
                rep.duplicate_seq += 1
                rep.flags.setdefault("duplicate_seq", []).append(i)
        rt = float(r["rtt_ms"])
        if rt <= 0:
            rep.nonpositive += 1
            rep.flags.setdefault("nonpositive", []).append(i)
        elif rt > extreme_ms:
            rep.extreme += 1
            rep.flags.setdefault("extreme", []).append(i)
    if rep.duplicate_seq:
        rep.notes.append(f"{rep.duplicate_seq} duplicated ICMP sequence number(s)")
    if rep.nonpositive:
        rep.notes.append(f"{rep.nonpositive} non-positive RTT(s) (timer resolution)")
    if rep.extreme:
        rep.notes.append(f"{rep.extreme} RTT(s) above {extreme_ms:.0f} ms (stall)")
    if rep.total and rep.total != rep.ok:
        rep.notes.append(f"{rep.missing} timeout(s) retained as missing data")
    rep.passed = rep.total > 0
    return rep


def clean_rtt(rows: Sequence[Dict], extreme_ms: float = 3000.0) -> np.ndarray:
    """Return the subset of RTT values that are safe to use for statistics."""
    vals: List[float] = []
    for r in rows:
        if not r.get("ok") or r.get("rtt_ms") is None:
            continue
        rt = float(r["rtt_ms"])
        if 0 < rt <= extreme_ms:
            vals.append(rt)
    return np.asarray(vals, dtype=float)


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #

def jitter_rfc3550(samples: Sequence[float]) -> Optional[float]:
    """Mean inter-arrival jitter as defined by RFC 3550 §6.4.1.

        J(i) = J(i-1) + (|D(i-1, i)| - J(i-1)) / 16

    This is *not* the standard deviation of RTT.  SD measures spread around the
    mean, jitter measures how much consecutive samples move — the quantity that
    actually breaks VoIP and video conferencing.
    """
    s = np.asarray([x for x in samples if x is not None], dtype=float)
    if s.size < 2:
        return None
    j = 0.0
    for prev, cur in zip(s[:-1], s[1:]):
        j += (abs(cur - prev) - j) / 16.0
    return float(j)


def wilson_interval(successes: int, total: int, conf: float = 0.95
                    ) -> tuple[float, float]:
    """Wilson score interval for a proportion.

    Correct behaviour at the boundaries — unlike the Wald interval it never
    returns a negative bound for 0/30, which matters a lot when measuring a
    loss rate that is often exactly zero.
    """
    if total <= 0:
        return (float("nan"), float("nan"))
    z = {0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}.get(round(conf, 2), 1.9600)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = (z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_ci(samples: Sequence[float], stat=np.median, n_boot: int = 10000,
                 conf: float = 0.95, seed: int = 20261010) -> tuple[float, float]:
    """Percentile bootstrap confidence interval — distribution-free."""
    s = np.asarray([x for x in samples if x is not None], dtype=float)
    if s.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.RandomState(seed)
    idx = rng.randint(0, s.size, size=(n_boot, s.size))
    draws = stat(s[idx], axis=1)
    alpha = (1 - conf) / 2
    return (float(np.percentile(draws, 100 * alpha)),
            float(np.percentile(draws, 100 * (1 - alpha))))


def describe_latency(samples: Sequence[float], conf: float = 0.95,
                     seed: int = 20261010) -> Dict[str, Optional[float]]:
    """Full descriptive summary of one target's RTT samples."""
    s = np.asarray([x for x in samples if x is not None], dtype=float)
    if s.size == 0:
        return {"n": 0}
    lo, hi = bootstrap_ci(s, np.median, seed=seed, conf=conf)
    mean = float(np.mean(s))
    sd = float(np.std(s, ddof=1)) if s.size > 1 else 0.0
    q = np.percentile(s, [25, 50, 75, 95])
    return {
        "n": int(s.size),
        "mean_ms": round(mean, 3),
        "sd_ms": round(sd, 3),
        "cv": round(sd / mean, 4) if mean else None,
        "min_ms": round(float(np.min(s)), 3),
        "p25_ms": round(float(q[0]), 3),
        "median_ms": round(float(q[1]), 3),
        "median_ci_lo": round(lo, 3),
        "median_ci_hi": round(hi, 3),
        "p75_ms": round(float(q[2]), 3),
        "p95_ms": round(float(q[3]), 3),
        "max_ms": round(float(np.max(s)), 3),
        "iqr_ms": round(float(q[2] - q[0]), 3),
        "jitter_rfc3550_ms": round(jitter_rfc3550(s), 3) if s.size > 1 else 0.0,
    }


def normality(samples: Sequence[float]) -> Dict[str, Optional[float]]:
    """Shapiro-Wilk normality test plus a skewness reading.

    Uses the statistic only — importing scipy into this module would make the
    quality gate unusable in environments without it.  Pre-computed critical
    values keep the function dependency-free.
    """
    s = np.asarray([x for x in samples if x is not None], dtype=float)
    n = s.size
    if n < 3:
        return {"n": n, "W": None, "skew": None, "note": "样本量不足以做正态性检验"}

    # Shapiro-Wilk via the Royston approximation.
    x = np.sort(s)
    m = np.asarray([norm_ppf((i - 0.375) / (n + 0.25)) for i in range(1, n + 1)])
    mu = float(np.mean(s))
    ss = float(((s - mu) ** 2).sum())
    corr = float(np.corrcoef(x, m)[0, 1])
    W = (corr ** 2) if ss > 0 else 0.0
    skew = float((((s - mu) ** 3).sum() / n) / (ss / n) ** 1.5) if ss > 0 else 0.0
    return {"n": n, "W": round(W, 4), "skew": round(skew, 3),
            "p_lt_0.05": W < shapiro_critical(n)}


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    if not 0 < p < 1:
        return float("nan")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def shapiro_critical(n: int, alpha: float = 0.05) -> float:
    """Interpolated Shapiro-Wilk critical value for n in [3, 50]."""
    table = {3: .767, 4: .748, 5: .762, 6: .788, 7: .803, 8: .818, 9: .829,
             10: .842, 12: .859, 15: .881, 20: .905, 25: .918, 30: .927,
             40: .940, 50: .947}
    ks = sorted(table)
    if n <= ks[0]:
        return table[ks[0]]
    if n >= ks[-1]:
        return table[ks[-1]]
    lo, hi = max(k for k in ks if k <= n), min(k for k in ks if k >= n)
    if lo == hi:
        return table[lo]
    return table[lo] + (table[hi] - table[lo]) * (n - lo) / (hi - lo)
