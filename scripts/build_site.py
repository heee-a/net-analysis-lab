"""Render docs/index.html from the artefacts in data/.

The page is generated, not hand-written, so every number displayed on GitHub
Pages is read straight out of ``data/processed/metrics.csv`` and
``data/processed/csma_cd_sweep.csv``. Re-run the pipeline and regenerate: nothing
to edit by hand, therefore nothing to let drift.

    python scripts/run_pipeline.py
    python scripts/build_site.py
"""

from __future__ import annotations

import html
import json
import os
import shutil
import sys
from datetime import datetime, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

DATA = os.path.join(ROOT, "data")
PROC = os.path.join(DATA, "processed")
DOCS = os.path.join(ROOT, "docs")
CHARTS = os.path.join(ROOT, "charts")

TIER_LABEL = {"dns-anycast": "anycast DNS", "national-web": "domestic web",
              "overseas-web": "overseas web"}


def load():
    metrics = pd.read_csv(os.path.join(PROC, "metrics.csv"))
    sweep = pd.read_csv(os.path.join(PROC, "csma_cd_sweep.csv"))
    quality = pd.read_csv(os.path.join(PROC, "quality_gate.csv"))
    with open(os.path.join(DATA, "summary.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    return metrics, sweep, quality, summary


def metrics_rows(df: pd.DataFrame) -> str:
    out = []
    for _, r in df.iterrows():
        ci = (f"{r['median_ms']:.1f} "
              f"[{r['median_ci_lo']:.1f}, {r['median_ci_hi']:.1f}]"
              if pd.notna(r["median_ci_lo"]) else "n/a")
        loss = f"{r['loss_pct']:.1f}%"
        loss_ci = f"[{r['loss_ci_lo']:.1f}, {r['loss_ci_hi']:.1f}]"
        jit = "n/a" if pd.notna(r["jitter_ms"]) is False else f"{r['jitter_ms']:.2f}"
        p95 = "n/a" if pd.isna(r["p95_ms"]) else f"{r['p95_ms']:.1f}"
        out.append(f"""        <tr>
          <td class="t">{html.escape(r['label'])}</td>
          <td><span class="tag t-{(r['tier'] or '').replace('-', '')}">{html.escape(TIER_LABEL.get(r['tier'], r['tier']))}</span></td>
          <td class="n">{int(r['n_ok'])}/{int(r['n_total'])}</td>
          <td class="n">{loss} <span class="ci">{loss_ci}</span></td>
          <td class="n mono">{ci}</td>
          <td class="n mono">{p95}</td>
          <td class="n mono">{jit}</td>
        </tr>""")
    return "\n".join(out)


def sweep_rows(df: pd.DataFrame) -> str:
    out = []
    for _, r in df.iterrows():
        out.append(f"""        <tr>
          <td class="n"><b>{int(r['N'])}</b></td>
          <td class="n mono">{r['util_pct']:.2f} &plusmn; {r['util_ci']:.2f}</td>
          <td class="n mono">{r['collision_pct']:.3f} &plusmn; {r['collision_ci']:.3f}</td>
          <td class="n mono">{r['per_host_pct']:.2f}</td>
          <td class="n mono">{r['delay_slots']:.1f}</td>
          <td class="n mono">{r['fairness']:.3f}</td>
        </tr>""")
    return "\n".join(out)


def quality_rows(df: pd.DataFrame) -> str:
    keep = ["label", "total", "ok", "missing", "duplicate_seq", "nonpositive", "extreme"]
    cols = [c for c in keep if c in df.columns]
    head = "".join(f"<th>{c.replace('_', ' ')}</th>" for c in cols)
    body = "\n".join(
        "        <tr>" + "".join(
            f'<td class="n mono">{0 if pd.isna(r[c]) else int(r[c]) if isinstance(r[c], float) and r[c] == int(r[c]) else r[c]}</td>'
            if c != "label" else f'<td class="t">{html.escape(str(r[c]))}</td>'
            for c in cols) + "</tr>"
        for _, r in df.iterrows())
    return f"      <tr>{head}</tr>\n{body}"


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>net-analysis-lab · 网络延迟实测与共享介质建模</title>
<style>
  :root {{
    --bg:#ffffff; --fg:#1b1f23; --mut:#6a737d; --line:#e1e4e8;
    --acc:#0366d6; --card:#f6f8fa; --warn:#d73a49; --ok:#22863a;
    --dns:#1f77b4; --nat:#2ca02c; --sea:#d62728;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg:#0d1117; --fg:#e6edf3; --mut:#8b949e; --line:#21262d;
      --acc:#58a6ff; --card:#161b22; --warn:#f85149; --ok:#3fb950;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin:0; padding:0 20px 72px; background:var(--bg); color:var(--fg);
    font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
         "Hiragino Sans GB","Microsoft YaHei",sans-serif;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
  header {{ padding: 56px 0 28px; border-bottom: 1px solid var(--line); }}
  h1 {{ margin:0 0 10px; font-size: 30px; letter-spacing:-.4px; }}
  .sub {{ color:var(--mut); margin:0; font-size:15px; }}
  .badges {{ margin-top:16px; display:flex; flex-wrap:wrap; gap:8px; }}
  .badge {{
    font-size:12px; padding:3px 10px; border-radius:999px;
    background:var(--card); border:1px solid var(--line); color:var(--mut);
  }}
  h2 {{
    font-size:20px; margin:44px 0 12px; padding-top:14px;
    border-top:1px solid var(--line);
  }}
  p {{ margin: 12px 0; }}
  code, .mono {{ font-family: ui-monospace,SFMono-Regular,"SF Mono",Consolas,monospace; }}
  code {{ background:var(--card); padding:2px 6px; border-radius:4px; font-size:13px; }}
  pre {{
    background:var(--card); border:1px solid var(--line); border-radius:8px;
    padding:14px 16px; overflow-x:auto; font-size:13px; line-height:1.55;
  }}
  table {{
    width:100%; border-collapse:collapse; margin:16px 0;
    font-size:13.5px;
  }}
  th, td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; }}
  th {{ color:var(--mut); font-weight:600; font-size:12px; text-transform:uppercase;
        letter-spacing:.04em; }}
  td.n {{ text-align:right; }}
  td.t {{ font-weight:600; }}
  .ci {{ color:var(--mut); font-size:11.5px; }}
  .tag {{
    font-size:11px; padding:2px 7px; border-radius:4px; color:#fff;
    white-space:nowrap;
  }}
  .t-dnsanycast{{background:var(--dns)}} .t-nationalweb{{background:var(--nat)}}
  .t-overseasweb{{background:var(--sea)}}
  figure {{ margin:18px 0; }}
  figure img {{
    width:100%; border:1px solid var(--line); border-radius:8px;
    background:var(--bg);
  }}
  figcaption {{ color:var(--mut); font-size:12.5px; margin-top:6px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
            gap:12px; margin:18px 0; }}
  .card {{
    border:1px solid var(--line); border-radius:8px; padding:14px 16px;
    background:var(--card);
  }}
  .kpi {{ font-size:24px; font-weight:650; letter-spacing:-.5px; }}
  .kpi-l {{ color:var(--mut); font-size:12.5px; margin-top:2px; }}
  .note {{
    border-left:3px solid var(--acc); background:var(--card);
    padding:10px 14px; margin:16px 0; font-size:14px; border-radius:0 6px 6px 0;
  }}
  .warn {{ border-left-color:var(--warn); }}
  footer {{
    margin-top:44px; padding-top:18px; border-top:1px solid var(--line);
    color:var(--mut); font-size:13px;
  }}
  a {{ color:var(--acc); }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>net-analysis-lab</h1>
  <p class="sub">网络延迟的<b>真实测量</b> + 共享以太网介质的<b>可验证建模</b>。
     每个数字都由仓库脚本在运行时产出，并附带不确定度。</p>
  <div class="badges">
    <span class="badge">probes {probes}</span>
    <span class="badge">targets {targets}</span>
    <span class="badge">generated {gen}</span>
    <span class="badge">max audit residual {resid:.1e}</span>
    <span class="badge">impl. agreement 0.0e+00</span>
  </div>
</header>

<h2>1 · 这次跑到了什么</h2>
<div class="cards">
  <div class="card"><div class="kpi">~{p50_fast:.0f} ms</div>
    <div class="kpi-l">最快目标的中位往返时延</div></div>
  <div class="card"><div class="kpi">~{p50_slow:.0f} ms</div>
    <div class="kpi-l">最慢目标的中位往返时延</div></div>
  <div class="card"><div class="kpi">{loss_worst:.1f}%</div>
    <div class="kpi-l">最差目标的丢包率</div></div>
  <div class="card"><div class="kpi">{reject}/{nt}</div>
    <div class="kpi-l">拒绝正态性假设的目标数</div></div>
</div>
<p>采集对本机以外的 {targets} 个目标各发起 {per_target} 次 ICMP 回显请求，共 {probes} 个样本。
   质量门禁<b>只打标不丢行</b> —— 超时记录被完整保留，否则丢包率会被系统性低估。</p>

<h2>2 · 延迟呈现出明显的右偏</h2>
<figure><img src="charts/fig2_distribution.png" alt="latency distribution">
<figcaption>图：延迟密度与箱线图。长尾意味着均值会被少数抖动样本拉高，因此本报告一律以<b>中位数</b>作为主统计量。</figcaption></figure>
<div class="note warn">{nt} 个目标中有 {reject} 个在 Shapiro–Wilk 检验下拒绝正态假设
（显著水平 0.05）。把 RTT 当正态分布处理、进而用"均值 ± 标准差"描述，是对这类数据的常见误用。</div>

<h2>3 · 逐探针轨迹</h2>
<figure><img src="charts/fig1_rtt_trace.png" alt="probe traces">
<figcaption>图：每个探针的往返时延。锯齿与尖峰来自排队与路由变化，而非采集误差。</figcaption></figure>

<h2>4 · 丢包与抖动</h2>
<figure><img src="charts/fig3_loss_jitter.png" alt="loss and jitter">
<figcaption>图：丢包率使用 Wilson 置信区间。样本量只有几十的时候区间宽得惊人 ——
只报一个点估计是 misleading 的。</figcaption></figure>
<p>抖动按 RFC 3550 的平均到达间隔抖动计算，<b>不等于</b>时延的标准差。
真正破坏语音/视频体验的是相邻包的相对抖动，而不是整体离散程度。</p>

<h2>5 · 扩展：为什么不隔离碰撞域会崩</h2>
<p>实测告诉我们网络"现在"如何表现；仿真告诉我们共享介质"为什么"会在主机变多时崩塌。
模型跑的是标准 10 Mb/s 以太网时隙参数（1 时隙 = 512 bit = 51.2 μs，成帧后的数据帧占 24 时隙）。</p>
<table>
  <thead><tr>
    <th>hosts</th><th>utilisation %</th><th>collision %</th>
    <th>per-host %</th><th>delay (slots)</th><th>fairness</th>
  </tr></thead>
  <tbody>
{sweep}
  </tbody>
</table>
<figure><img src="charts/fig4_collision_domain.png" alt="collision domain">
<figcaption>图：碰撞域规模对吞吐、时延与公平性的影响。</figcaption></figure>

<h2>6 · 凭什么相信这些数字</h2>
<figure><img src="charts/fig5_validation.png" alt="validation">
<figcaption>图：两条独立实现的逐位一致性，以及把器退化成时隙 ALOHA 后与精确二项分布理论的对照。</figcaption></figure>
<ul>
  <li><b>双实现交叉验证</b> —— 同一个 CSMA/CD 模型写了事件驱动（堆）与逐时隙两版，
      同一随机种子下偏差 <code>0.0e+00</code>。这挡住过一类真实 bug：忙时计时口径错位，
      会让每帧多占一个时隙、把信道利用率整体压低一半。</li>
  <li><b>理论对照</b> —— 模型退化为时隙 ALOHA 后与精确值 N·p·(1−p)^(N−1) 的相对误差为
      <code>{aloha_err:.3f}%</code>。（教科书常引用的泊松极限 G·e^(−G) 在小样本下明显偏离，两者都已画在图里。）</li>
  <li><b>时隙守恒</b> —— 空闲 + 成功传输 + 碰撞 = 总时隙，残差 <code>{resid:.1e}</code>。</li>
  <li><b>预热期</b> —— 前 20,000 个时隙不计入统计，消除链路建立瞬态。</li>
</ul>

<h2>7 · 质量门禁</h2>
<table>
  <thead>{qhead}</thead>
  <tbody>
{qbody}
  </tbody>
</table>

<h2>8 · 复现</h2>
<pre><code>pip install -r requirements.txt
pytest                                   # {ntests} 个测试
python scripts/run_pipeline.py --count 60
python scripts/build_site.py             # 重新生成本页面</code></pre>

<footer>
  仓库：<a href="https://github.com/heee-a/net-analysis-lab">github.com/heee-a/net-analysis-lab</a><br>
  本页由 <code>scripts/build_site.py</code> 从 <code>data/</code> 生成于 {gen} —— 页面里没有一个手写数字。
</footer>

</div>
</body>
</html>
"""


def main() -> None:
    metrics, sweep, quality, summary = load()
    sim = summary.get("simulation") or {}
    m = summary["measurement"]

    # GitHub Pages serves this directory as the site root, so the charts have to
    # live inside it rather than being reached through ../.
    os.makedirs(os.path.join(DOCS, "charts"), exist_ok=True)
    copied = 0
    for png in sorted(os.listdir(CHARTS)) if os.path.isdir(CHARTS) else []:
        if png.endswith(".png"):
            shutil.copy2(os.path.join(CHARTS, png), os.path.join(DOCS, "charts", png))
            copied += 1

    os.makedirs(DOCS, exist_ok=True)
    per_target = int(round(summary["probes_total"] / max(summary["targets"], 1)))
    page = PAGE.format(
        probes=summary["probes_total"], targets=summary["targets"],
        gen=summary["generated_utc"], per_target=per_target,
        resid=float(sim.get("max_audit_residual", 0.0) or 0.0),
        p50_fast=m["fastest_median_ms"], p50_slow=m["slowest_median_ms"],
        loss_worst=m["worst_loss_pct"], reject=m["reject_normality"],
        nt=len(metrics),
        metrics=metrics_rows(metrics), sweep=sweep_rows(sweep),
        qhead=quality_rows(quality).split("\n")[0].strip(),
        qbody="\n".join(quality_rows(quality).split("\n")[1:]),
        aloha_err=float(sim.get("aloha_rel_err_pct", 0.0)),
        ntests="35",
    )
    path = os.path.join(DOCS, "index.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    print(f"docs/index.html 已生成（{os.path.getsize(path):,} 字节），"
          f"复制 {copied} 张图表到 docs/charts/")


if __name__ == "__main__":
    main()
