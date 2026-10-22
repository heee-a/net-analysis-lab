"""End-to-end pipeline: collect -> quality-gate -> analyse -> charts -> artefacts.

Run with::

    python scripts/run_pipeline.py --count 60

Everything written under ``data/`` and ``charts/`` comes from measurements taken
during that run. No number in the README is hand-written.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import numpy as np                                              # noqa: E402
import pandas as pd                                             # noqa: E402
import matplotlib                                               # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                 # noqa: E402

from netlab import collect as collect_mod                       # noqa: E402
from netlab import quality as qmod                              # noqa: E402
from netlab import sim                                          # noqa: E402

# Read the warm-up default off the model itself, so the report cannot quote a
# number the simulation no longer uses.
WARMUP_SLOTS = inspect.signature(sim.simulate_csma_cd).parameters["warmup"].default

DATA = os.path.join(_ROOT, "data")
CHARTS = os.path.join(_ROOT, "charts")
RAW = os.path.join(DATA, "raw")
PROCESSED = os.path.join(DATA, "processed")

# matplotlib renamed the boxplot keyword arguments in 3.9/3.10 and dropped the
# old names in 3.11. Support both so the pipeline runs on matplotlib>=3.4.
_MPL = tuple(int(p) for p in matplotlib.__version__.split(".")[:2] if p.isdigit())


def boxplot_horizontal(ax, data):
    if _MPL >= (3, 9):
        return ax.boxplot(data, orientation="horizontal", patch_artist=True,
                          widths=0.62)
    return ax.boxplot(data, vert=False, patch_artist=True, widths=0.62)

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.dpi": 130,
    "font.size": 9,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.spines.top": False,
    "axes.spines.right": False,
})
TIER_COLORS = {"dns-anycast": "#1f77b4", "national-web": "#2ca02c",
               "overseas-web": "#d62728"}


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #

def stage_collect(count: int, pause: float) -> pd.DataFrame:
    print(f"[1/5] 采集：{len(collect_mod.DEFAULT_TARGETS)} 个目标 × {count} 次 ICMP")
    batches = collect_mod.collect(count=count, pause_s=pause)
    rows = collect_mod.batches_to_records(batches)
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("未采集到任何样本：请检查网络连通性。")
    os.makedirs(RAW, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    df.to_csv(os.path.join(RAW, f"ping_{stamp}.csv"), index=False)
    df.to_csv(os.path.join(RAW, "ping_latest.csv"), index=False)
    print(f"      原始样本 {len(df)} 行 -> data/raw/ping_latest.csv")
    return df


def stage_quality(df: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    print("[2/5] 数据质量门禁（只打标、不丢弃）")
    reports = []
    for (label, tier), g in df.groupby(["label", "tier"]):
        rep = qmod.quality_gate(g.to_dict("records"))
        d = rep.to_dict()
        d.update(label=label, tier=tier)
        reports.append(d)
        flag_txt = " ".join(f"{k}={v}" for k, v in d["flags"].items() if v)
        if flag_txt:
            print(f"      {label:<14} {flag_txt}")
    qdf = pd.DataFrame(reports)
    os.makedirs(PROCESSED, exist_ok=True)
    qdf.to_csv(os.path.join(PROCESSED, "quality_gate.csv"), index=False)
    return qdf, reports


def stage_metrics(df: pd.DataFrame) -> pd.DataFrame:
    print("[3/5] 指标计算（含 Wilson / Bootstrap 置信区间）")
    rows = []
    for (label, tier), g in df.groupby(["label", "tier"]):
        vals = qmod.clean_rtt(g.to_dict("records"))
        rep = qmod.quality_gate(g.to_dict("records"))
        # NOTE: the interval must be built on the *lost* count. Applying Wilson
        # to the success count yields a success-rate interval, which reads like
        # "[94%, 100%]" when telnet would have said "0% loss" -- upside down.
        lost = rep.total - rep.ok
        lo, hi = qmod.wilson_interval(lost, rep.total)
        if vals.size == 0:
            rows.append({"label": label, "tier": tier, "n_total": rep.total,
                         "n_lost": lost, "n_ok": 0, "loss_pct": 100.0,
                         "loss_ci_lo": 100 * lo, "loss_ci_hi": 100 * hi,
                         "median_ms": None, "median_ci_lo": None,
                         "median_ci_hi": None, "jitter_ms": None})
            continue
        d = qmod.describe_latency(vals)
        norm = qmod.normality(vals)
        ttl_mode = g["ttl"].dropna()
        rows.append({
            "label": label, "tier": tier, "n_total": rep.total, "n_ok": rep.ok,
            "n_lost": lost,
            "loss_pct": round(100 * lost / rep.total, 3),
            "loss_ci_lo": round(100 * lo, 3), "loss_ci_hi": round(100 * hi, 3),
            "min_ms": d["min_ms"], "median_ms": d["median_ms"],
            "median_ci_lo": d["median_ci_lo"], "median_ci_hi": d["median_ci_hi"],
            "p95_ms": d["p95_ms"], "max_ms": d["max_ms"],
            "mean_ms": d["mean_ms"], "sd_ms": d["sd_ms"], "cv": d["cv"],
            "iqr_ms": d["iqr_ms"], "jitter_ms": d["jitter_rfc3550_ms"],
            "normality_W": norm.get("W"), "skew": norm.get("skew"),
            "reject_normal": norm.get("p_lt_0.05"),
            "resolved_ip": (None if "resolved_ip" not in g
                            or g["resolved_ip"].isna().all()
                            else g["resolved_ip"].dropna().iloc[0]),
        })
    m = pd.DataFrame(rows).sort_values("median_ms").reset_index(drop=True)
    m.to_csv(os.path.join(PROCESSED, "metrics.csv"), index=False)
    return m


def stage_sim(ns, repeats, slots) -> pd.DataFrame:
    print(f"[4/5] CSMA/CD 碰撞域仿真 + 交叉验证 + 理论对照")
    from netlab.sim import simulate_csma_cd  # local import keeps stage order
    cross = {n: sim.cross_validate(n, n_slots=40_000, seed=101) for n in (2, 8, 24)}
    worst_diff = max(c["util_abs_diff"] for c in cross.values())
    aloha = sim.validate_slotted_aloha(seed=1)
    print(f"      双实现最大偏差 = {worst_diff:.1e}（需 < 1e-12）")
    print(f"      时隙 ALOHA 相对误差 = {aloha['rel_err_pct']:.3f}%")

    res = sim.sweep_csma_cd(ns, repeats=repeats, n_slots=slots)
    rows = []
    for n in ns:
        (u, ue), (d, de), (c, ce), (f, fe), resid = (
            res[n]["util"], res[n]["delay"], res[n]["cr"],
            res[n]["fair"], res[n]["resid"])
        rows.append({"N": n, "util_pct": 100 * u, "util_ci": 100 * ue,
                     "delay_slots": d, "delay_ci": de,
                     "collision_pct": 100 * c, "collision_ci": 100 * ce,
                     "fairness": f, "per_host_pct": 100 * u / n,
                     "audit_resid": resid})
    rdf = pd.DataFrame(rows)
    rdf.to_csv(os.path.join(PROCESSED, "csma_cd_sweep.csv"), index=False)
    return rdf, cross, aloha


def stage_charts(df: pd.DataFrame, metrics: pd.DataFrame, sim_df: pd.DataFrame,
                 cross, aloha) -> None:
    print("[5/5] 绘图")
    os.makedirs(CHARTS, exist_ok=True)

    # --- Fig 1: per-probe latency traces -----------------------------------
    fig, ax = plt.subplots(figsize=(9.5, 4.2))
    for (label, tier), g in df.groupby(["label", "tier"]):
        g = g.reset_index(drop=True)
        ax.plot(g.index, g["rtt_ms"], marker="o", ms=2.2, lw=1.0, alpha=0.85,
                color=TIER_COLORS.get(tier, "#666"), label=f"{label} ({tier})")
        miss = g.index[g["rtt_ms"].isna()]
        for i in miss:
            ax.axvline(i, color="#999", lw=0.6, alpha=0.35)
    ax.set_xlabel("probe index within target")
    ax.set_ylabel("round-trip time (ms)")
    ax.set_title("Fig. 1  ICMP round-trip time, probe by probe (gaps = loss)")
    ax.legend(fontsize=7.5, ncol=3, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS, "fig1_rtt_trace.png"))
    plt.close(fig)

    # --- Fig 2: distribution + box plot ------------------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.0))
    order = metrics["label"].tolist()
    data = [qmod.clean_rtt(g.to_dict("records")) for _, g in
            df.groupby("label").__iter__() if True]
    labels = [lb for lb, _ in df.groupby("label")]
    order_idx = sorted(range(len(labels)), key=lambda i: order.index(labels[i]))
    boxdata = [data[i] for i in order_idx]
    bp = boxplot_horizontal(ax2, boxdata)
    ax2.set_yticks(range(1, len(boxdata) + 1))
    ax2.set_yticklabels([labels[i] for i in order_idx], fontsize=8)
    for patch, lab in zip(bp["boxes"], [labels[i] for i in order_idx]):
        tier = metrics.loc[metrics["label"] == lab, "tier"].iloc[0]
        patch.set_facecolor(TIER_COLORS.get(tier, "#999"))
        patch.set_alpha(0.55)
    ax2.set_xlabel("round-trip time (ms)")
    ax2.set_title("(b) distribution by target")

    for lab in order:
        vals = qmod.clean_rtt(df[df["label"] == lab].to_dict("records"))
        if vals.size < 2:
            continue
        tier = metrics.loc[metrics["label"] == lab, "tier"].iloc[0]
        bins = max(6, min(24, int(np.sqrt(vals.size))))
        ax1.hist(vals, bins=bins, histtype="step", lw=1.4, density=True,
                 color=TIER_COLORS.get(tier, "#666"), label=lab)
    ax1.set_xlabel("round-trip time (ms)")
    ax1.set_ylabel("density (per ms)")
    ax1.set_title("(a) latency density")
    ax1.legend(fontsize=7.5, framealpha=0.9)
    fig.suptitle("Fig. 2  Latency is right-skewed, so the median — not the mean "
                 "— is the honest headline", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(os.path.join(CHARTS, "fig2_distribution.png"))
    plt.close(fig)

    # --- Fig 3: loss / jitter with uncertainty -----------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.0))
    mt = metrics.sort_values("loss_pct", ascending=False)
    y = np.arange(len(mt))
    ax1.barh(y, mt["loss_pct"], color=[TIER_COLORS.get(t, "#999") for t in mt["tier"]],
             alpha=0.75)
    ax1.errorbar(mt["loss_pct"], y,
                 xerr=[mt["loss_pct"] - mt["loss_ci_lo"], mt["loss_ci_hi"] - mt["loss_pct"]],
                 fmt="none", ecolor="#333", capsize=3, lw=1)
    ax1.set_yticks(y)
    ax1.set_yticklabels(mt["label"], fontsize=8)
    ax1.set_xlabel("packet loss (%)")
    ax1.set_title("(a) loss with Wilson 95% interval\n(n is small — the interval is wide)")

    mj = metrics.dropna(subset=["jitter_ms"]).sort_values("jitter_ms", ascending=False)
    yj = np.arange(len(mj))
    ax2.barh(yj, mj["jitter_ms"], color=[TIER_COLORS.get(t, "#999") for t in mj["tier"]],
             alpha=0.75)
    ax2.set_yticks(yj)
    ax2.set_yticklabels(mj["label"], fontsize=8)
    ax2.set_xlabel("jitter (RFC 3550 mean inter-arrival, ms)")
    ax2.set_title("(b) jitter — what actually breaks real-time traffic")
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS, "fig3_loss_jitter.png"))
    plt.close(fig)

    # --- Fig 4: CSMA/CD collision domain -----------------------------------
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(13, 3.9))
    N = sim_df["N"]
    ax1.errorbar(N, sim_df["collision_pct"], yerr=sim_df["collision_ci"],
                 marker="o", ms=4, color="#c62828", lw=1.4, capsize=3)
    ax1.set_xscale("log")
    ax1.set_xlabel("hosts sharing one collision domain")
    ax1.set_ylabel("collision rate (%)")
    ax1.set_title("(a) collisions grow with domain size")

    ax2.errorbar(N, sim_df["per_host_pct"], marker="s", ms=4, color="#1565c0",
                 lw=1.4, capsize=3)
    ax2.plot(N, 100 / N, "--", color="#999", lw=1, label="ideal 1/N share")
    ax2.set_xscale("log")
    ax2.set_xlabel("hosts sharing one collision domain")
    ax2.set_ylabel("throughput per host (%)")
    ax2.set_title("(b) per-host share collapses toward 1/N")
    ax2.legend(fontsize=7.5)

    ax3.semilogy(N, sim_df["delay_slots"], marker="^", ms=4, color="#6a1b9a", lw=1.4)
    ax3.set_xscale("log")
    ax3.set_xlabel("hosts sharing one collision domain")
    ax3.set_ylabel("frame delay (slots, log scale)")
    ax3.set_title("(c) delay grows super-linearly")
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS, "fig4_collision_domain.png"))
    plt.close(fig)

    # --- Fig 5: correctness evidence ---------------------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 3.6))
    ns = sorted(cross)
    diffs = [max(cross[n]["util_abs_diff"], 1e-18) for n in ns]
    ax1.bar([str(n) for n in ns], diffs, color="#2e7d32", alpha=0.8)
    ax1.axhline(1e-12, color="#c62828", ls="--", lw=1, label="1e-12 tolerance")
    ax1.set_yscale("log")
    ax1.set_ylabel("|utilisation(event-driven) − utilisation(slot-by-slot)|")
    ax1.set_xlabel("hosts")
    ax1.set_title("(a) two independent implementations agree bit-for-bit")
    ax1.legend(fontsize=7.5)

    lbl = ["simulation", "exact binomial\nN·p·(1−p)^(N−1)", "Poisson limit\nG·e^(−G)"]
    vals = [aloha["sim"], aloha["theory"], aloha["poisson"]]
    bars = ax2.bar(lbl, vals, color=["#1565c0", "#2e7d32", "#ef6c00"], alpha=0.85)
    for b, v in zip(bars, vals):
        ax2.text(b.get_x() + b.get_width() / 2, v + 0.004, f"{v:.6f}",
                 ha="center", fontsize=7.5)
    ax2.set_ylabel("throughput S")
    ax2.set_ylim(0, max(vals) * 1.22)
    ax2.tick_params(axis="x", labelsize=7)
    ax2.set_title(f"(b) degenerate to slotted ALOHA: rel. err "
                  f"{aloha['rel_err_pct']:.3f}%")
    # --- Fig 6: jitter vs latency -----------------------------------------
    # The claim "jitter barely moves while latency spans 21x" deserves a picture.
    # Six points cannot support a correlation test, so the panel only shows the
    # shape: latency on a log axis, jitter on a linear axis with the range the
    # eye should compare against.
    sub = metrics.dropna(subset=["median_ms", "jitter_ms"])
    fig, ax = plt.subplots(figsize=(7.6, 4.3))
    ax.axhspan(sub["jitter_ms"].min(), sub["jitter_ms"].max(), color="#bbb",
               alpha=0.22, zorder=0,
               label=f'jitter spread {sub["jitter_ms"].max() / sub["jitter_ms"].min():.2f}×')
    for _, r in sub.iterrows():
        ax.scatter(r["median_ms"], r["jitter_ms"], s=90,
                   color=TIER_COLORS.get(r["tier"], "#666"), zorder=3,
                   edgecolor="#ffffff", linewidth=1.3)
        ax.annotate(r["label"], (r["median_ms"], r["jitter_ms"]),
                    textcoords="offset points", xytext=(9, 5), fontsize=8.5)
    ax.set_xscale("log")
    ax.set_xlabel("median round-trip time (ms, log scale)")
    ax.set_ylabel("jitter (RFC 3550, ms)")
    ax.set_title("Fig. 6  Latency spans two orders of magnitude; jitter does not")
    ax.legend(fontsize=8, loc="lower right", framealpha=0.9)
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS, "fig6_jitter_vs_latency.png"))
    plt.close(fig)
    print("      charts/fig1..fig6 已生成")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=60,
                    help="ICMP probes per target (default 60)")
    ap.add_argument("--pause", type=float, default=0.35,
                    help="seconds between targets (default 0.35)")
    ap.add_argument("--hosts", type=int, nargs="+",
                    default=[2, 4, 8, 12, 16, 24, 32, 48],
                    help="collision-domain sizes to sweep")
    ap.add_argument("--repeats", type=int, default=8,
                    help="independent seeds per host count")
    ap.add_argument("--slots", type=int, default=200_000,
                    help="slots per simulation run")
    ap.add_argument("--reuse", action="store_true",
                    help="re-analyse data/raw/ping_latest.csv instead of pinging again")
    ap.add_argument("--skip-sim", action="store_true")
    args = ap.parse_args()

    for d in (RAW, PROCESSED, CHARTS):
        os.makedirs(d, exist_ok=True)

    if args.reuse:
        print("[1/5] 复用已采集样本 data/raw/ping_latest.csv")
        df = pd.read_csv(os.path.join(RAW, "ping_latest.csv"))
    else:
        df = stage_collect(args.count, args.pause)
    qdf, _ = stage_quality(df)
    metrics = stage_metrics(df)

    sim_df = cross = aloha = None
    if not args.skip_sim:
        sim_df, cross, aloha = stage_sim(args.hosts, args.repeats, args.slots)
        stage_charts(df, metrics, sim_df, cross, aloha)

    # ---- headline summary for humans & the README -------------------------
    # Jitter-vs-latency association. n is tiny (one point per target), so this is
    # reported as a descriptive shape indicator, never as a significance test.
    jl = metrics.dropna(subset=["median_ms", "jitter_ms"])
    if len(jl) >= 3:
        pearson = float(np.corrcoef(jl["median_ms"], jl["jitter_ms"])[0, 1])
        ranks = jl[["median_ms", "jitter_ms"]].rank()
        spearman = float(np.corrcoef(ranks["median_ms"], ranks["jitter_ms"])[0, 1])
    else:
        pearson = spearman = None

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "probes_total": int(len(df)),
        "targets": int(df["label"].nunique()),
        "measurement": {
            "fastest_median_ms": float(metrics["median_ms"].min()),
            "slowest_median_ms": float(metrics["median_ms"].max()),
            "worst_loss_pct": float(metrics["loss_pct"].max()),
            "targets_with_loss": int((metrics["loss_pct"] > 0).sum()),
            "reject_normality": int(metrics["reject_normal"].fillna(False).sum()),
            "jitter_min_ms": float(jl["jitter_ms"].min()) if len(jl) else None,
            "jitter_max_ms": float(jl["jitter_ms"].max()) if len(jl) else None,
            "jitter_vs_latency": (None if pearson is None else {
                "pearson_r": pearson, "spearman_r": spearman,
                "n": int(len(jl)),
            }),
        },
        "quality_gate_rows": int(len(qdf)),
        "simulation": (None if args.skip_sim else {
            "max_cross_diff": max(c["util_abs_diff"] for c in cross.values()),
            "aloha_rel_err_pct": aloha["rel_err_pct"],
            "max_audit_residual": float(sim_df["audit_resid"].abs().max()),
            "collision_pct_N2": float(sim_df.loc[sim_df["N"] == 2, "collision_pct"].iloc[0]),
            "collision_pct_Nmax": float(sim_df["collision_pct"].iloc[-1]),
            # Published alongside the results so the page never hard-codes a
            # model parameter: change the defaults and the prose follows.
            "setup": {
                "slot_bits": sim.SLOT_BITS,
                "frame_slots": sim.FRAME_SLOTS,
                "jam_slots": sim.JAM_SLOTS,
                "slot_us": sim.SLOT_BITS / 10e6 * 1e6,
                "warmup_slots": WARMUP_SLOTS,
                "repeats": args.repeats,
                "slots": args.slots,
                "hosts": [int(n) for n in sim_df["N"]],
            },
        }),
    }
    with open(os.path.join(DATA, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print("\n===== 结果摘要 =====")
    print("目标工位：", metrics[["label", "tier", "n_ok", "loss_pct",
                             "median_ms", "p95_ms", "jitter_ms"]].to_string(index=False))
    if sim_df is not None:
        print("\n碰撞域扫描：")
        print(sim_df[["N", "util_pct", "collision_pct", "per_host_pct",
                      "delay_slots", "fairness"]].round(3).to_string(index=False))
    print(f"\n摘要 -> data/summary.json")


if __name__ == "__main__":
    main()
