"""Guards for the generated report.

These tests exist because of bugs that actually shipped:

* the Pages site referenced ``charts/fig1..fig5.png`` while ``docs/`` (the only
  directory Pages publishes) contained no image directory at all — every figure
  on the live page was a dead link, and nothing complained;
* ``build_site.py`` computed the per-target measurement table but the template
  never inserted it, so the headline results silently vanished.

Both are invisible in a diff and only show up in a browser. They are asserted
here instead, against the artefacts that are actually committed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CHARTS = DOCS / "charts"
PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
IMG_SRC = re.compile(r'<img src="([^"]+)"')
MD_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


_PAGE: str | None = None


def page() -> str:
    """Regenerate the site from the committed artefacts and cache the HTML."""
    global _PAGE
    if _PAGE is not None:
        return _PAGE
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_site
    except ImportError as exc:                                  # pragma: no cover
        pytest.skip(f"build_site 依赖不可用: {exc}")
    build_site.main()
    _PAGE = (DOCS / "index.html").read_text(encoding="utf-8")
    return _PAGE


def test_every_figure_the_page_links_to_exists():
    """A missing PNG is the failure mode that made the live site blank."""
    srcs = IMG_SRC.findall(page())
    assert srcs, "页面没有引用任何图表 —— 模板或数据出了问题"
    missing = [s for s in srcs if not (DOCS / s).is_file()]
    assert not missing, f"页面引用了不存在的图片: {missing}"


def test_every_figure_the_readme_links_to_exists():
    """Same failure mode one directory up: GitHub renders README images too."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    refs = [m for m in MD_IMAGE.findall(text) if not m.startswith(("http", "#"))]
    assert refs, "README 没有引用任何本地图表"
    missing = [m for m in refs if not (ROOT / m).is_file()]
    assert not missing, f"README 引用了不存在的图片: {missing}"


def test_no_unrendered_template_placeholders():
    """``PAGE.format`` fails loudly; a stray brace survives silently."""
    found = PLACEHOLDER.findall(page())
    assert not found, f"页面残留未替换的占位符: {found[:5]}"


def test_page_reports_the_committed_measurement():
    """The headline table must be rendered, not just computed."""
    import json

    summary = json.loads((ROOT / "data" / "summary.json").read_text(encoding="utf-8"))
    html = page()
    assert f"probes {summary['probes_total']}" in html
    assert f"targets {summary['targets']}" in html
    # One row per measured target, i.e. build_site inserted {metrics}.
    import pandas as pd

    metrics = pd.read_csv(ROOT / "data" / "processed" / "metrics.csv")
    for label in metrics["label"]:
        assert f">{label}<" in html, f"测量结果表缺少 {label} 这一行"


def test_charts_directory_is_tracked_by_git():
    """docs/charts/ must not be gitignored, or Pages publishes text only."""
    import subprocess

    out = subprocess.run(["git", "check-ignore", "-q", "docs/charts"],
                         cwd=str(ROOT), capture_output=True)
    assert out.returncode != 0, \
        "docs/charts/ 被 .gitignore 排除了 —— Pages 站点会失去所有图表"
    if CHARTS.is_dir():
        assert any(CHARTS.glob("*.png")), "docs/charts/ 里没有任何图表"
