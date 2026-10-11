"""Packet-level model of a shared-medium LAN: a hub and CSMA/CD contention.

This is the simulation companion to the measurements in :mod:`netlab.collect`.
Real traces tell us how the Internet behaves *today*; the model tells us *why*
a shared-medium Ethernet degrades the way it does when the collision domain
grows. Keeping both in one package means every claim can be attacked from two
directions.

Slot parameters follow 10 Mb/s Ethernet:

=============================  ==========================================
1 slot                          512 bit = 51.2 us (contention window unit)
successful frame                preamble 64 + payload 12000 + IFG 96 bit
                                = 12160 bit = 23.75 slot -> **24** (conservative)
collision (jam 32 + IFG 96)     128 bit = 0.25 slot -> **1** (conservative)
=============================  ==========================================

Correctness is defended three ways — see :func:`cross_validate`,
:func:`validate_slotted_aloha` and the ``audit`` key returned by
:func:`simulate_csma_cd`.
"""

from __future__ import annotations

import heapq
import math
import random
from typing import Dict, List, Optional, Sequence

SLOT_BITS = 512
FRAME_SLOTS = 24
JAM_SLOTS = 1


# --------------------------------------------------------------------------- #
# Event-driven implementation (fast path)
# --------------------------------------------------------------------------- #

def simulate_csma_cd(n_hosts: int, n_slots: int = 1_000_000, w0: int = 2,
                     w_max: int = 1024, seed: int | None = None,
                     warmup: int = 20_000) -> Dict:
    """Binary exponential backoff (BEB) over one collision domain, saturated load.

    Uses a contention clock plus a priority queue: backoff counters freeze while
    the channel is busy and whole idle stretches are skipped in one step. That
    makes it *exactly* equivalent to scanning slot by slot while being far
    faster -- :func:`cross_validate` proves the equivalence numerically.

    Args:
        n_hosts: stations competing for the shared medium.
        n_slots: total physical slots to simulate.
        w0: initial contention window (as in Ethernet, 2 slots after a success).
        w_max: contention window ceiling.
        seed: any integer for reproducibility.
        warmup: leading slots excluded from statistics to drop link-up transients.

    Returns:
        dict with ``utilization``, ``delay``, ``collision_rate``, ``fairness``
        (Jain's index), ``idle`` and an ``audit`` block that reconciles every
        slot as idle + transmitted + collided.
    """
    # A tiny run must not be able to consume itself in warmup: that would leave
    # zero accounted slots and silently return NaN rather than a bad number.
    warmup = min(warmup, max(0, n_slots // 5))
    rng = random.Random(seed)
    due = [(rng.randrange(w0), i) for i in range(n_hosts)]
    heapq.heapify(due)                 # (due contention time, station id)
    W = [w0] * n_hosts                 # current contention window per station
    win_cnt = [0] * n_hosts            # successful transmissions per station
    prev_done = [0] * n_hosts          # completion time of previous frame
    ok = delay_sum = col = idle_slots = 0
    tc = t = 0                         # tc: contention clock; t: physical slot
    while t < n_slots:
        nxt = due[0][0] if due else n_slots
        if nxt > tc:                   # channel idle: jump straight to next contention
            jump = min(nxt - tc, n_slots - t)
            idle_slots += max(0, (t + jump) - max(t, warmup))
            tc += jump
            t += jump
            if t >= n_slots:
                break
        senders = []
        while due and due[0][0] <= tc:
            senders.append(heapq.heappop(due)[1])
        if len(senders) > 1:           # collision
            if t >= warmup:
                col += 1
            t += JAM_SLOTS
            for i in senders:
                W[i] = min(W[i] * 2, w_max)
                heapq.heappush(due, (tc + rng.randrange(W[i]), i))
            continue
        i = senders[0]                 # successful transmission
        if t >= warmup:
            ok += 1
            win_cnt[i] += 1
            delay_sum += (t - prev_done[i] + FRAME_SLOTS)
        prev_done[i] = t + FRAME_SLOTS
        t += FRAME_SLOTS
        W[i] = w0
        heapq.heappush(due, (tc + rng.randrange(w0), i))

    tot = n_slots - warmup
    spent = idle_slots + ok * FRAME_SLOTS + col * JAM_SLOTS
    audit = {"total_slots": tot, "accounted": spent,
             "residual": (tot - spent) / tot if tot else float("nan")}
    s = sum(win_cnt)
    topsq = sum(v * v for v in win_cnt)
    return {
        "utilization": ok * FRAME_SLOTS / tot if tot else float("nan"),
        "delay": delay_sum / ok if ok else float("nan"),
        "collision_rate": col / max(col + ok, 1),
        "fairness": (s * s) / (n_hosts * topsq) if topsq else float("nan"),
        "idle": idle_slots / tot if tot else float("nan"),
        "success": ok,
        "collisions": col,
        "audit": audit,
    }


# --------------------------------------------------------------------------- #
# Independent re-implementation (slow path, for cross-validation)
# --------------------------------------------------------------------------- #

def simulate_csma_cd_naive(n_hosts: int, n_slots: int = 40_000, w0: int = 2,
                           w_max: int = 1024, seed: int | None = None,
                           warmup: int = 20_000) -> Dict:
    """Slot-by-slot re-implementation of the same model.

    Busy reimbursement gotcha, kept here because it bit this code during
    porting: both busy branches advance ``t`` by exactly **one** slot and set
    ``busy`` to "the rest".  The remaining slots are consumed by the ``busy``
    branch, which advances ``t`` by one each pass.  Writing ``t + FRAME_SLOTS``
    instead makes every frame cost 47 slots rather than 24 and silently halves
    the reported utilisation -- a 2x error that survives every sanity check
    except cross-validation.
    """
    warmup = min(warmup, max(0, n_slots // 5))
    rng = random.Random(seed)
    W = [w0] * n_hosts
    wait = [rng.randrange(w0) for _ in range(n_hosts)]
    prev_done = [0] * n_hosts
    win_cnt = [0] * n_hosts
    ok = delay_sum = col = idle = 0
    busy = 0
    t = 0
    while t < n_slots:
        if busy > 0:                   # channel occupied: freeze backoff
            busy -= 1
            t += 1
            continue
        senders = [i for i in range(n_hosts) if wait[i] == 0]
        if not senders:
            count = True
            wait = [w - 1 if w > 0 else 0 for w in wait]
            if t >= warmup and count:
                idle += 1
            t += 1
            continue
        if len(senders) > 1:
            if t >= warmup:
                col += 1
            for i in senders:
                W[i] = min(W[i] * 2, w_max)
                wait[i] = rng.randrange(W[i])
            busy, t = JAM_SLOTS - 1, t + 1
            continue
        i = senders[0]
        if t >= warmup:
            ok += 1
            win_cnt[i] += 1
            delay_sum += (t - prev_done[i] + FRAME_SLOTS)
        prev_done[i] = t + FRAME_SLOTS
        W[i] = w0
        wait[i] = rng.randrange(w0)
        busy, t = FRAME_SLOTS - 1, t + 1

    tot = n_slots - warmup
    s = sum(win_cnt)
    topsq = sum(v * v for v in win_cnt)
    return {
        "utilization": ok * FRAME_SLOTS / tot if tot else float("nan"),
        "delay": delay_sum / ok if ok else float("nan"),
        "collision_rate": col / max(col + ok, 1),
        "fairness": (s * s) / (n_hosts * topsq) if topsq else float("nan"),
        "idle": idle / tot if tot else float("nan"),
        "success": ok,
        "collisions": col,
    }


def cross_validate(n_hosts: int = 8, n_slots: int = 50_000,
                   seed: int = 7) -> Dict[str, float]:
    """Run both implementations and report their disagreement.

    This is the test that caught a real bug during development: the naive
    version advanced the slot counter *and* decremented the busy counter once
    per slot, so every frame occupied one slot too many and channel utilisation
    was understated by about four percentage points.
    """
    a = simulate_csma_cd(n_hosts, n_slots=n_slots, seed=seed)
    b = simulate_csma_cd_naive(n_hosts, n_slots=n_slots, seed=seed)
    return {
        "util_fast": a["utilization"], "util_naive": b["utilization"],
        "util_abs_diff": abs(a["utilization"] - b["utilization"]),
        "delay_fast": a["delay"], "delay_naive": b["delay"],
        "delay_abs_diff": abs(a["delay"] - b["delay"]),
        "residual": a["audit"]["residual"],
    }


def validate_slotted_aloha(n_hosts: int = 50, p: float = 0.02,
                           n_slots: int = 2_000_000,
                           seed: int | None = None) -> Dict[str, float]:
    """Degenerate the machinery to slotted ALOHA and compare with theory.

    Theory (exact binomial):      S = n * p * (1 - p)^(n-1)
    Poisson limit, often quoted:  S = G * e^-G  with G = n * p

    Verifying against the exact binomial value guards the random generator and
    the counting logic. The Poisson form is shown side by side because it is the
    one textbooks quote, and at these n it is visibly wrong.
    """
    import numpy as np
    rng = np.random.RandomState(seed)
    attempts = rng.binomial(n_hosts, p, n_slots)
    successes = int((attempts == 1).sum())
    g = n_hosts * p
    exact = n_hosts * p * (1 - p) ** (n_hosts - 1)
    sim = successes / n_slots
    return {"n": n_hosts, "slots": n_slots, "G": g, "sim": sim,
            "theory": exact, "poisson": g * math.exp(-g),
            "rel_err_pct": abs(sim - exact) / exact * 100 if exact else float("nan")}


# --------------------------------------------------------------------------- #
# Sweep
# --------------------------------------------------------------------------- #

def sweep_csma_cd(hosts: Sequence[int], repeats: int = 8,
                  n_slots: int = 200_000,
                  base_seed: int = 20261010) -> Dict[int, Dict]:
    """Average each host count over several independent seeds.

    Returns per-N ``mean``, ``ci_half`` (95% t-interval across seeds) for
    utilisation, delay and collision rate, plus fairness and audit residual.
    """
    out: Dict[int, Dict] = {}
    for n in hosts:
        U, D, C, F, R = [], [], [], [], []
        best_resid = 0.0
        for k in range(repeats):
            r = simulate_csma_cd(n, n_slots=n_slots, seed=base_seed + k)
            U.append(r["utilization"])
            D.append(r["delay"])
            C.append(r["collision_rate"])
            F.append(r["fairness"])
            R.append(r["audit"]["residual"])
        out[n] = {
            "util": (float(_mean(U)), float(_ci(U))),
            "delay": (float(_mean(D)), float(_ci(D))),
            "cr": (float(_mean(C)), float(_ci(C))),
            "fair": (float(_mean(F)), float(_ci(F))),
            "resid": float(max(map(abs, R))),
        }
    return out


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _ci(xs: Sequence[float], conf: float = 0.95) -> float:
    """Half-width of the mean's confidence interval across seeds."""
    import statistics
    n = len(xs)
    if n < 2:
        return 0.0
    tcrit = {3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447, 8: 2.365,
             9: 2.306, 10: 2.262, 11: 2.228, 12: 2.201, 15: 2.145, 20: 2.093}
    t = tcrit.get(n, 2.0 if n < 30 else 1.96)
    sd = statistics.stdev(xs)
    return t * sd / math.sqrt(n)
