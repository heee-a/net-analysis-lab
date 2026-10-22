# 变更记录

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的格式。
版本号与 `src/netlab/__init__.py` 中的 `__version__` 保持一致。

## [0.1.1] — 2026-10-22

### 修复

- **Pages 站点的图表全部是死链**。`docs/index.html` 引用 `charts/*.png`，但 Pages 只发布 `docs/`
  目录，而 `charts/` 又被 `.gitignore` 排除，图从未进入版本库 —— 线上页面 5 张图全是裂图。
  现在 `.gitignore` 只忽略根级 `/charts/`，`docs/charts/` 由 `build_site.py` 复制并入库。
- **README 的图片路径同样失效**，已改指向 `docs/charts/`。
- **页面漏渲染实测总表**。`build_site.py` 计算了每个目标的丢包率 / Wilson 区间 / 中位 RTT，
  但模板从未插入 `{metrics}`，访客看不到核心结果表。
- **matplotlib 3.11 兼容性**。`boxplot(labels=, vert=)` 在新版本中已移除，而 CI 安装的是最新版，
  流水线必然失败。改为按版本分支调用，保持 `matplotlib>=3.4` 兼容。
- README 中一处被截断的句子（"超时()保留" → "超时（timeout）保留"），以及仿真表缺失的 N=12 行。

### 新增

- **图 6：抖动 vs 时延**。"抖动与时延几乎无关"此前只有文字论述，没有图形支撑。
  新增散点图（时延取对数轴）并给出 Pearson / Spearman 系数，
  同时明确标注 n=6 不足以做相关性检验 —— 结论来自量级比较而非相关系数。
- **页面**：目录导航、由数据计算出的三条结论、已知局限表、源码与 CI 入口、
  favicon、Open Graph / Twitter 卡片元信息（分享链接时有预览图）、图片可点击看原图、懒加载。
- **`docs/404.html`**：Pages 自定义 404 页，风格与主站一致。
- **`CITATION.cff`**：GitHub 会据此在仓库页显示 "Cite this repository"。
- **测试**：新增 `tests/test_site.py`（5 项），守卫刚修的两类缺陷 ——
  页面/README 引用的每张图必须存在、模板不能残留未替换的占位符、
  测量结果表必须逐目标渲染、`docs/charts/` 不得被 `.gitignore` 排除。

### 变更

- 测试数 35 → 40。

## [0.1.0] — 2026-10-11

首个版本：跨语言中立的 ICMP 采集、只打标不丢行的质量门禁、Wilson / Bootstrap 区间、
RFC 3550 抖动、Shapiro–Wilk 正态性检验，以及三重校验的 CSMA/CD 共享介质模型。
