# data/ 数据字典

本目录下的 CSV 与 JSON **全部由脚本生成**，请勿手工编辑 —— 重跑流水线会直接覆盖：
`python scripts/run_pipeline.py --reuse`（不发新包）或 `--count 60`（重新采集）。

原始数据随仓库提交，是为了让别人不联网也能复算所有结论。

## data/raw/ping_latest.csv

每次采集的原始 ICMP 样本，一行一个探针。带时间戳的历史批次（`ping_YYYYMMDDTHHMMSSZ.csv`）
默认不入版本库，只有 `ping_latest.csv` 保留。

| 字段 | 含义 |
|---|---|
| `target` | 探测目标（IP 或主机名） |
| `label` | 展示用的名字，如 `AliDNS` |
| `tier` | 层级：`dns-anycast` / `national-web` / `overseas-web` |
| `resolved_ip` | 解析出的 IP（Windows 中文输出里常缺失） |
| `started_utc` | 该批次开始时间（UTC） |
| `seq` | `ping` 输出的 `icmp_seq` |
| `order` | 该目标内的第几个探针（1 起） |
| `ok` | 是否收到回复；`False` 表示超时 |
| `rtt_ms` | 往返时延（毫秒）。**超时为空，不填 0** —— 填 0 会把均值系统性拉低 |
| `ttl` | 回复包的 TTL |
| `bytes` | 回复包字节数 |

## data/processed/quality_gate.csv

质量门禁的计数结果。门禁**只打标、不丢行**：所有原始行都保留在
`data/raw/` 里，这里只是把异常分类计数。

| 字段 | 含义 |
|---|---|
| `label` | 目标名 |
| `total` | 探针总数 |
| `ok` | 成功收到的数量 |
| `missing` | 超时（显式缺失） |
| `duplicate_seq` | 重复的 `icmp_seq` |
| `nonpositive` | 非正值时延（Windows 下 `time<1ms` 记为 1 ms，不算此类） |
| `extreme` | 超出合理范围的离群值 |

## data/processed/metrics.csv

每个目标的派生指标，**逐行对应 README 与结果页里的实测总表**。

| 字段 | 含义 |
|---|---|
| `loss_pct` | 观测丢包率（%） |
| `loss_ci_lo` / `loss_ci_hi` | 丢包率的 **Wilson 95% 置信区间**（%） |
| `median_ms` | 中位 RTT（主统计量，因为分布右偏） |
| `median_ci_lo` / `median_ci_hi` | 中位数的 **Bootstrap** 置信区间 |
| `p95_ms` / `max_ms` | 95 分位与最大值 |
| `mean_ms` / `sd_ms` / `cv` | 均值、标准差、变异系数（列出但**不作主统计量**） |
| `jitter_ms` | RFC 3550 平均到达间隔抖动（**不等于**时延标准差） |
| `normality_W` | Shapiro–Wilk 统计量 W |
| `skew` | 偏度 |
| `reject_normal` | 是否在 α=0.05 下拒绝正态假设 |

## data/processed/csma_cd_sweep.csv

CSMA/CD 碰撞域扫描，一行一个主机数配置。

| 字段 | 含义 |
|---|---|
| `N` | 共享同一碰撞域的主机数 |
| `util_pct` / `util_ci` | 信道利用率（%）及其置信区间半宽 |
| `collision_pct` / `collision_ci` | 碰撞率（%）及其置信区间半宽 |
| `per_host_pct` | 单主机可得带宽（%），理论上限为 1/N |
| `delay_slots` / `delay_ci` | 帧时延（时隙）及其置信区间半宽 |
| `fairness` | Jain 公平性指数（1 为完全公平） |
| `audit_resid` | 时隙记账残差：空闲 + 成功 + 碰撞 与总时隙之差 |

模型参数：10 Mb/s 以太网，1 时隙 = 512 bit = 51.2 μs，成帧后数据帧占 24 时隙，
冲突阻塞占 1 时隙；每配置 8 个独立种子 × 200,000 时隙，前 20,000 时隙作预热剔除。

## data/summary.json

流水线汇总，供 README 叙述与结果页模板读取。新增字段
`measurement.jitter_vs_latency`（Pearson / Spearman）仅为**形状描述**：
只有 6 个目标，样本量远不足以做相关性检验。
