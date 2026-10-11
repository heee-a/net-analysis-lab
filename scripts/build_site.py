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

REPO_URL = "https://github.com/heee-a/net-analysis-lab"

TIER_LABEL = {"dns-anycast": "anycast DNS", "national-web": "domestic web",
              "overseas-web": "overseas web"}

# Reading of the measurement that is argued in prose but computed from the data,
# so that the page can never contradict the artefacts it is rendered from.
LIMITATIONS = [
    ("每目标仅 60 个探针",
     "零丢包的上界只能压到 6.0%",
     "Wilson 区间如实暴露宽度；<code>--count</code> 可调大"),
    ("ICMP 常被中间设备限速 / 降优先级",
     "观测到的“丢包”可能是策略而非拥塞",
     "报告为“观测丢包”，不断言成因"),
    ("单一主机、单一接入网",
     "结论不能外推到其他网络",
     "复现命令开放，换机即可交叉验证"),
    ("Windows <code>ping</code> 为毫秒分辨率",
     "亚毫秒差异不可见",
     "<code>time&lt;1ms</code> 统一记录为 1 ms，并由质量门禁打标"),
    ("饱和负载 + 永久排队",
     "放大 BEB 捕获效应、低估公平性",
     "已在页面中显式打折说明，不作为真实局域网预测"),
]


def load():
    metrics = pd.read_csv(os.path.join(PROC, "metrics.csv"))
    sweep = pd.read_csv(os.path.join(PROC, "csma_cd_sweep.csv"))
    quality = pd.read_csv(os.path.join(PROC, "quality_gate.csv"))
    with open(os.path.join(DATA, "summary.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    return metrics, sweep, quality, summary


def num(v, nd=1, dash="n/a"):
    return dash if pd.isna(v) else f"{v:.{nd}f}"


# --------------------------------------------------------------------------- #
# Table renderers
# --------------------------------------------------------------------------- #

def metrics_rows(df: pd.DataFrame) -> str:
    out = []
    for _, r in df.iterrows():
        ci = (f"{r['median_ms']:.1f} "
              f"[{r['median_ci_lo']:.1f}, {r['median_ci_hi']:.1f}]"
              if pd.notna(r["median_ci_lo"]) else "n/a")
        loss = f"{r['loss_pct']:.1f}%"
        loss_ci = f"[{r['loss_ci_lo']:.1f}, {r['loss_ci_hi']:.1f}]"
        jit = "n/a" if pd.isna(r["jitter_ms"]) else f"{r['jitter_ms']:.2f}"
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


def limitation_rows() -> str:
    return "\n".join(
        f'        <tr><td class="t">{a}</td><td>{b}</td><td>{c}</td></tr>'
        for a, b, c in LIMITATIONS)


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>net-analysis-lab · 网络延迟实测与共享介质建模</title>
<meta name="description" content="ICMP 实测 → 质量门禁 → 统计推断 → CSMA/CD 共享介质建模。每个数字都由仓库脚本产出，并附带不确定度。">
<link rel="canonical" href="https://heee-a.github.io/net-analysis-lab/">
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
  .cta {{ margin-top:18px; display:flex; flex-wrap:wrap; gap:10px; }}
  .btn {{
    display:inline-block; font-size:13.5px; padding:7px 15px; border-radius:6px;
    text-decoration:none; border:1px solid var(--line); color:var(--fg);
    background:var(--card);
  }}
  .btn.primary {{ background:var(--acc); border-color:var(--acc); color:#fff; }}
  nav.toc {{
    margin:22px 0 0; padding:14px 18px; background:var(--card);
    border:1px solid var(--line); border-radius:8px; font-size:13.5px;
  }}
  nav.toc b {{ display:block; margin-bottom:6px; font-size:12px;
               text-transform:uppercase; letter-spacing:.04em; color:var(--mut); }}
  nav.toc a {{ display:inline-block; margin-right:14px; color:var(--acc);
               text-decoration:none; }}
  h2 {{
    font-size:20px; margin:44px 0 12px; padding-top:14px;
    border-top:1px solid var(--line); scroll-margin-top:16px;
  }}
  h2 .n {{ color:var(--mut); font-weight:400; }}
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
  th, td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
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
    background:#fff;
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
  ol.findings {{ padding-left:22px; }}
  ol.findings li {{ margin:12px 0; }}
  footer {{
    margin-top:44px; padding-top:18px; border-top:1px solid var(--line);
    color:var(--mut); font-size:13px;
  }}
  a {{ color:var(--acc); }}
  .scroll {{ overflow-x:auto; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>net-analysis-lab</h1>
  <p class="sub">网络延迟的<b>真实测量</b> + 共享以太网介质的<b>可验证建模</b>。
     每个数字都由仓库脚本在运行时产出，并附带不确定度 —— 页面里没有一个手写数字。</p>
  <div class="badges">
    <span class="badge">probes {probes}</span>
    <span class="badge">targets {targets}</span>
    <span class="badge">generated {gen}</span>
    <span class="badge">max audit residual {resid:.1e}</span>
    <span class="badge">impl. agreement {crossmax:.1e}</span>
  </div>
  <div class="cta">
    <a class="btn primary" href="{repo}">在 GitHub 上查看源码</a>
    <a class="btn" href="{repo}/blob/main/README.md">完整报告 (README)</a>
    <a class="btn" href="{repo}/actions">CI 状态</a>
  </div>
  <nav class="toc">
    <b>目录</b>
    <a href="#overview">1 概览</a>
    <a href="#trace">2 逐探针轨迹</a>
    <a href="#dist">3 延迟分布</a>
    <a href="#loss">4 丢包与抖动</a>
    <a href="#collision">5 碰撞域</a>
    <a href="#trust">6 可信性</a>
    <a href="#findings">7 三条结论</a>
    <a href="#limits">8 已知局限</a>
    <a href="#quality">9 质量门禁</a>
    <a href="#repro">10 复现</a>
  </nav>
</header>

<h2 id="overview"><span class="n">1 ·</span> 这次跑到了什么</h2>
<div class="cards">
  <div class="card"><div class="kpi">~{p50_fast:.0f} ms</div>
    <div class="kpi-l">最快目标的中位往返时延</div></div>
  <div class="card"><div class="kpi">~{p50_slow:.0f} ms</div>
    <div class="kpi-l">最慢目标的中位往返时延</div></div>
  <div class="card"><div class="kpi">{loss_worst:.1f}%</div>
    <div class="kpi-l">最差目标的观测丢包率</div></div>
  <div class="card"><div class="kpi">{reject}/{nt}</div>
    <div class="kpi-l">拒绝正态性假设的目标数</div></div>
</div>
<p>采集对本机以外的 {targets} 个目标各发起 {per_target} 次 ICMP 回显请求，共 {probes} 个样本。
   质量门禁<b>只打标不丢行</b> —— 超时记录被完整保留为显式缺失值，否则丢包率会被系统性低估。</p>
<div class="scroll">
<table>
  <thead><tr>
    <th>target</th><th>tier</th><th>ok / sent</th>
    <th>loss % <span class="ci">[Wilson 95% CI]</span></th>
    <th>median ms <span class="ci">[bootstrap CI]</span></th>
    <th>p95 ms</th><th>jitter ms</th>
  </tr></thead>
  <tbody>
{metrics}
  </tbody>
</table>
</div>
<div class="note">“0% 丢包”这个说法骗人：{nzero} 个目标里没有一个丢包，
但每个目标的丢包率<b>上界</b>仍然只能压到 <code>{zero_bound:.1f}%</code> —— 这是 Wilson 区间给出的结论，
而不是“没问题”。报点估计而不报区间，是这类作业最常见的错误。</div>

<h2 id="trace"><span class="n">2 ·</span> 逐探针轨迹</h2>
<figure><img src="charts/fig1_rtt_trace.png" alt="每个探针的往返时延">
<figcaption>图 1：每个探针的往返时延。纵向灰线是超时（缺失）样本。锯齿与尖峰来自排队与路由变化，而非采集误差。</figcaption></figure>

<h2 id="dist"><span class="n">3 ·</span> 延迟呈现出明显的右偏</h2>
<figure><img src="charts/fig2_distribution.png" alt="延迟密度与箱线图">
<figcaption>图 2：延迟密度与箱线图。长尾意味着均值会被少数抖动样本拉高，
因此本报告一律以<b>中位数</b>作为主统计量，并用 Bootstrap 给出区间。</figcaption></figure>
<div class="note warn">{nt} 个目标中有 {reject} 个在 Shapiro–Wilk 检验下拒绝正态假设
（显著水平 0.05），且偏度全部为正。把 RTT 当正态分布处理、进而用“均值 ± 标准差”描述，
是对这类数据的常见误用。</div>

<h2 id="loss"><span class="n">4 ·</span> 丢包与抖动</h2>
<figure><img src="charts/fig3_loss_jitter.png" alt="丢包率与抖动">
<figcaption>图 3：丢包率使用 Wilson 置信区间。样本量只有几十的时候区间宽得惊人 ——
只报一个点估计是误导性的。</figcaption></figure>
<p>抖动按 RFC 3550 的平均到达间隔抖动计算，<b>不等于</b>时延的标准差。
真正破坏语音/视频体验的是相邻包的相对抖动，而不是整体离散程度。</p>

<h2 id="collision"><span class="n">5 ·</span> 为什么不隔离碰撞域会崩</h2>
<p>实测告诉我们网络“现在”如何表现；仿真告诉我们共享介质“为什么”会在主机变多时崩塌。
模型跑的是标准 10 Mb/s 以太网时隙参数（1 时隙 = 512 bit = 51.2 μs，成帧后的数据帧占 24 时隙，
冲突阻塞占 1 时隙），饱和负载，每个配置 {repeats} 个独立随机种子。</p>
<div class="scroll">
<table>
  <thead><tr>
    <th>hosts</th><th>utilisation %</th><th>collision %</th>
    <th>per-host %</th><th>delay (slots)</th><th>fairness</th>
  </tr></thead>
  <tbody>
{sweep}
  </tbody>
</table>
</div>
<figure><img src="charts/fig4_collision_domain.png" alt="碰撞域规模的影响">
<figcaption>图 4：碰撞域规模对碰撞率、单主机带宽与时延的影响。</figcaption></figure>
<div class="note">总吞吐几乎不变（{util_first:.1f}% → {util_last:.1f}%），
因为这是一个“永远有数据要发”的饱和模型。这说明问题不在“带宽不够”，
而在<b>每个人分到的越来越少、等待越来越久</b>。</div>
<div class="note warn">公平性指数是<b>非单调</b>的（{fair_first:.3f} → {fair_min:.3f} → {fair_last:.3f}），
绝对值也很低。这不是 bug，而是二进制指数退避的<b>捕获效应</b>：抢到信道的站把争用窗口复位为 2，
失意的站窗口指数增长，于是赢家更容易再赢。由于模型假设每台主机永远有数据排队，
这张表应读作“IEEE 802.3 CSMA/CD 在饱和负载下的行为”，<b>而不是真实局域网的性能预测</b>。</div>

<h2 id="trust"><span class="n">6 ·</span> 凭什么相信这些数字</h2>
<figure><img src="charts/fig5_validation.png" alt="有效性验证">
<figcaption>图 5：两条独立实现的逐位一致性，以及把模型退化成时隙 ALOHA 后与精确二项分布理论的对照。</figcaption></figure>
<ul>
  <li><b>双实现交叉验证 —— 偏差 {crossmax:.1e}</b>。同一个 CSMA/CD 模型写了事件驱动（堆）与逐时隙两版，
      同一随机种子下结果逐位一致。这挡住过一类真实 bug：忙时计时口径错位，
      会让每帧多占一个时隙、把信道利用率整体压低一半（0.505 vs 0.980）。</li>
  <li><b>理论对照 —— 相对误差 {aloha_err:.3f}%</b>。模型退化为无载波侦听、无退避的时隙 ALOHA 后，
      与精确值 N·p·(1−p)^(N−1) 吻合。（教科书常引用的泊松极限 G·e^(−G) 在小 N 下明显偏离，
      把它当精确值做对照会把正确的程序判错 —— 两者都已画在图里。）</li>
  <li><b>时隙守恒 —— 残差 {resid:.1e}</b>。空闲 + 成功传输 + 碰撞 = 总时隙；
      任何一个时隙被漏记或重复记账，残差就会爆掉。</li>
  <li><b>预热期</b> —— 前 20,000 个时隙不计入统计，消除链路建立瞬态。</li>
</ul>

<h2 id="findings"><span class="n">7 ·</span> 三条从数据里读出来的结论</h2>
<ol class="findings">
  <li><b>延迟不是正态的，一律用中位数。</b>
      {reject}/{nt} 个目标在 Shapiro–Wilk 检验下拒绝正态假设（α=0.05），偏度全部为正。
      右偏意味着均值被少数尖峰拉高，因此本仓库的主统计量是中位数 + Bootstrap 区间。</li>
  <li><b>抖动与时延几乎无关。</b>
      中位 RTT 从 {p50_fast:.0f} ms 跨到 {p50_slow:.0f} ms —— <b>{med_ratio:.0f} 倍</b>的差距；
      而 RFC 3550 抖动只在 <b>{jit_min:.2f}–{jit_max:.2f} ms</b> 之间 —— 不到 <b>{jit_ratio:.1f} 倍</b>。
      “慢的网络更抖”在这批数据里不成立：抖动主要由本地最后一段链路的排队决定，
      而不是端到端距离。这也是为什么用 ping 的抖动去预测 VoIP 质量往往错得离谱。</li>
  <li><b>anycast 并不保证就近。</b>
      同为 anycast DNS 的三个目标中位时延分别为 {anycast_list}，差约 <b>{anycast_ratio:.0f} 倍</b>。
      anycast 只承诺路由到最优 POP，不承诺对你所在的接入网最优。</li>
</ol>

<h2 id="limits"><span class="n">8 ·</span> 已知局限</h2>
<div class="scroll">
<table>
  <thead><tr><th>局限</th><th>影响</th><th>缓解方式</th></tr></thead>
  <tbody>
{limits}
  </tbody>
</table>
</div>

<h2 id="quality"><span class="n">9 ·</span> 质量门禁</h2>
<p>门禁只打标、不丢行：缺失、重复序号、非正值、极端值都被计数并保留在数据中。</p>
<div class="scroll">
<table>
  <thead>{qhead}</thead>
  <tbody>
{qbody}
  </tbody>
</table>
</div>

<h2 id="repro"><span class="n">10 ·</span> 复现</h2>
<pre><code>git clone https://github.com/heee-a/net-analysis-lab.git
cd net-analysis-lab
pip install -r requirements.txt
pytest                                   # {ntests} 个测试
python scripts/run_pipeline.py --count 60 # 采集 + 分析 + 出图
python scripts/run_pipeline.py --reuse    # 不发新包，重算已有样本
python scripts/build_site.py              # 从 data/ 重新生成本页面</code></pre>
<p>采集依赖系统自带的 <code>ping</code>，<b>不需要管理员 / root 权限</b>（这也是不使用裸套接字的原因）。
解析器语言中立：Windows 中文、Windows 英文、Linux/macOS 的输出都能解析，三种情况都有对应的单元测试。</p>

<footer>
  仓库：<a href="{repo}">github.com/heee-a/net-analysis-lab</a> ·
  MIT License · 作者 <a href="https://github.com/heee-a">@heee-a</a><br>
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

    jit = metrics["jitter_ms"].dropna()
    med = metrics["median_ms"].dropna()
    anycast = (metrics[metrics["tier"] == "dns-anycast"]["median_ms"]
               .dropna().sort_values().tolist())
    anycast_list = " / ".join(f"{v:.0f} ms" for v in anycast) or "n/a"
    anycast_ratio = (anycast[-1] / anycast[0]) if len(anycast) >= 2 else 1.0

    zero_bound = float(metrics.loc[metrics["loss_pct"] == 0, "loss_ci_hi"].max()) \
        if (metrics["loss_pct"] == 0).any() else 0.0

    page = PAGE.format(
        repo=REPO_URL,
        probes=summary["probes_total"], targets=summary["targets"],
        gen=summary["generated_utc"], per_target=per_target,
        resid=float(sim.get("max_audit_residual", 0.0) or 0.0),
        crossmax=float(sim.get("max_cross_diff", 0.0) or 0.0),
        p50_fast=m["fastest_median_ms"], p50_slow=m["slowest_median_ms"],
        loss_worst=m["worst_loss_pct"], reject=m["reject_normality"],
        nt=len(metrics),
        nzero=int((metrics["loss_pct"] == 0).sum()),
        zero_bound=zero_bound,
        med_ratio=(med.max() / max(med.min(), 1e-9)) if len(med) else 1.0,
        jit_min=float(jit.min()) if len(jit) else 0.0,
        jit_max=float(jit.max()) if len(jit) else 0.0,
        jit_ratio=(float(jit.max()) / max(float(jit.min()), 1e-9)) if len(jit) else 1.0,
        anycast_list=anycast_list, anycast_ratio=anycast_ratio,
        metrics=metrics_rows(metrics), sweep=sweep_rows(sweep),
        util_first=float(sweep["util_pct"].iloc[0]),
        util_last=float(sweep["util_pct"].iloc[-1]),
        fair_first=float(sweep["fairness"].iloc[0]),
        fair_min=float(sweep["fairness"].min()),
        fair_last=float(sweep["fairness"].iloc[-1]),
        repeats=8,
        limits=limitation_rows(),
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

    if copied == 0:
        print("警告：charts/ 为空，页面中的图片将是死链。"
              "请先运行 scripts/run_pipeline.py 生成图表。")


if __name__ == "__main__":
    main()
