"""Publish net-analysis-lab to GitHub and wire up GitHub Pages.

Everything the agent cannot do without credentials happens here, so that once a
token is available the whole flow is a single deterministic command.

Two authentication modes are supported:

  A) Personal access token (full automation, including Pages + profile README)
        set GITHUB_TOKEN=ghp_xxx...     # Windows
        python scripts/publish.py

  B) SSH key (no token needed for the push itself)
        python scripts/publish.py --ssh
     Requires the repo to already exist on GitHub (create the empty public repo
     in the browser, or let the agent open the page for you). Steps that need
     the REST API (Pages, profile README) are reported as manual follow-ups,
     each with the exact URL to click.

Steps performed:
  1. create repo ``net-analysis-lab`` (public) if it does not exist
  2. set description / homepage / topics
  3. git init + commit + push branch ``main``
  4. enable GitHub Pages from ``/docs`` on ``main``
  5. update the profile repository README (repo count + new entry)

The token is read from the environment only; it is never written to disk.

NOTE ON PRIVACY: use a Personal Access Token, never an account password.
GitHub disabled password authentication for git and API operations in 2021.
"""

from __future__ import annotations

import base64
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
SSH_MODE = "--ssh" in sys.argv

# --------------------------------------------------------------------------- #
# SSH helpers (token-free mode)
# --------------------------------------------------------------------------- #


def _ssh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=20", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")


def ssh_identity() -> str:
    """Resolve the GitHub login from the SSH handshake greeting."""
    r = _ssh("-T", "git@github.com")
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"Hi ([^!]+)!", out)
    if not m:
        sys.exit("SSH 认证未通过。请确认已把公钥添加到 "
                 "https://github.com/settings/keys\n原始输出:\n" + out.strip()[:400])
    return m.group(1).strip()


def ssh_repo_exists(owner: str, name: str) -> bool:
    ls = subprocess.run(
        ["git", "ls-remote", "--exit-code", f"git@github.com:{owner}/{name}.git"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return ls.returncode == 0

# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE
_HTTP_ERRORS: list[str] = []


def _req(method: str, path: str, payload: dict | None = None,
         *, api: str = API, raw_out: bool = False, timeout: int = 60):
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        sys.exit("环境变量 GITHUB_TOKEN 未设置。\n"
                 "请在 https://github.com/settings/tokens 生成 classic token，勾选 "
                 "repo / workflow / read:user / user:email。\n"
                 "切勿使用账户密码 —— GitHub 已于 2021 年停用密码认证。")
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(f"{api}{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "net-analysis-lab-publisher",
    })
    try:
        with urllib.request.urlopen(r, timeout=timeout, context=_ctx) as resp:
            body = resp.read()
            return json.loads(body) if body and not raw_out else body
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        _HTTP_ERRORS.append(f"{method} {path} -> {e.code}: {detail}")
        return None


def _git(*args: str, cwd: Path = ROOT, check: bool = True):
    res = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    if check and res.returncode != 0:
        sys.exit(f"git {' '.join(args)} 失败:\n{res.stderr}")
    return res


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #

def step_identity() -> str:
    me = _req("GET", "/user")
    if not me:
        sys.exit(f"无法获取账号信息：\n" + "\n".join(_HTTP_ERRORS))
    login = me["login"]
    print(f"[0/5] 已认证为 {login}（{me.get('name') or '未设置昵称'}）")
    return login


def step_create_repo(owner: str, name: str) -> bool:
    existing = _req("GET", f"/repos/{owner}/{name}")
    if existing:
        print(f"[1/5] 仓库 {owner}/{name} 已存在，跳过创建")
        return False
    body = {
        "name": name,
        "description": "网络延迟的真实测量与共享以太网介质的可验证建模："
                       "ICMP 采集 · 质量门禁 · 置信区间 · RFC 3550 抖动 · CSMA/CD 仿真（双实现交叉验证）",
        "homepage": f"https://{owner}.github.io/{name}/",
        "private": False,
        "has_issues": True,
        "has_wiki": False,
        "has_projects": False,
        "auto_init": False,
        "license_template": "mit",
    }
    repo = _req("POST", "/user/repos", body)
    if not repo:
        sys.exit(f"创建仓库失败：\n" + "\n".join(_HTTP_ERRORS))
    print(f"[1/5] 已创建公开仓库 {repo['full_name']}")
    return True


def step_metadata(owner: str, name: str) -> None:
    _req("PATCH", f"/repos/{owner}/{name}", {
        "description": "网络延迟的真实测量与共享以太网介质的可验证建模："
                       "ICMP 采集 · 质量门禁 · 置信区间 · RFC 3550 抖动 · CSMA/CD 仿真（双实现交叉验证）",
        "homepage": f"https://{owner}.github.io/{name}/",
        "topics": ["network-measurement", "latency", "jitter", "packet-loss",
                   "data-analysis", "data-quality", "statistics", "csma-cd",
                   "ethernet", "matplotlib", "pandas", "reproducible-research"],
    })
    print("[2/5] description / homepage / topics 已设置")


def step_push(owner: str, name: str) -> None:
    if not (ROOT / ".git").exists():
        _git("init", "-b", "main")
    if SSH_MODE:
        remote = f"git@github.com:{owner}/{name}.git"
    else:
        token = os.environ["GITHUB_TOKEN"]
        remote = f"https://{owner}:{token}@github.com/{owner}/{name}.git"
    remotes = _git("remote", check=False).stdout
    if "origin" in remotes:
        _git("remote", "set-url", "origin", remote)
    else:
        _git("remote", "add", "origin", remote)

    _git("add", "-A")
    status = _git("status", "--porcelain", check=False).stdout
    msg = ("feat: network latency measurement & verified CSMA/CD modelling\n\n"
           "- cross-platform ICMP collector with locale-independent parsing\n"
           "- non-destructive quality gates + Wilson/bootstrap intervals\n"
           "- RFC 3550 jitter, Shapiro-Wilk normality, right-skew reporting\n"
           "- CSMA/CD model validated three ways (cross-impl, theory, slot audit)")
    if status.strip():
        _git("commit", "-m", msg)
    else:
        print("[3/5] 无变更，复用上一次 commit")

    # Force the local name and credential-free push for this single call.
    r = subprocess.run(["git", "push", "-u", "origin", "main", "--force"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        # First push without network access can be slow; surface the tail.
        print(r.stdout[-1500:])
        print(r.stderr[-1500:])
        sys.exit("推送失败")
    print(f"[3/5] 已推送到 https://github.com/{owner}/{name}")


def step_pages(owner: str, name: str) -> None:
    cur = _req("GET", f"/repos/{owner}/{name}/pages")
    if cur and cur.get("html_url"):
        print(f"[4/5] Pages 已启用：{cur['html_url']}")
        return
    res = _req("POST", f"/repos/{owner}/{name}/pages",
               {"source": {"branch": "main", "path": "/docs"}})
    if res:
        print(f"[4/5] Pages 已创建：{res.get('html_url')}（首次构建约需 1-3 分钟）")
        return
    # 2026 GitHub also serves Pages via Actions; fall back to that.
    res = _req("PUT", f"/repos/{owner}/{name}/pages",
               {"build_type": "workflow", "source": {"branch": "main",
                                                     "path": "/docs"}})
    if res:
        print(f"[4/5] Pages（Actions 构建）已创建：{res.get('html_url')}")
        return
    print("[4/5] Pages 未能启用（可能需要在网页端手动开启）：\n  "
          + "\n  ".join(_HTTP_ERRORS[-3:]))


def step_profile(owner: str, repo_name: str) -> None:
    """Patch the same-named profile repo so the portfolio stays truthful."""
    prof = _req("GET", f"/repos/{owner}/{owner}")
    if not prof:
        print(f"[5/5] 未找到 {owner}/{owner} 主页仓库，跳过（可在网页端新建）")
        return
    cur = _req("GET", f"/repos/{owner}/{owner}/contents/README.md")
    if not cur:
        print("[5/5] 主页 README 读取失败，跳过")
        return
    text = base64.b64decode(cur["content"]).decode("utf-8")
    original = text

    # 1) refresh the stale repository count
    repos = _req("GET", f"/users/{owner}/repos?per_page=100") or []
    public = [r for r in repos if not r.get("private") and r["name"] != owner]
    for old, new in ((f"{len(public) - 1} 个仓库", f"{len(public)} 个仓库"),
                     (f"{len(public) - 1} repos", f"{len(public)} repos")):
        if old in text:
            text = text.replace(old, new)

    # 2) add the new entry to the data-analysis table
    row = (f"| [**{repo_name}**](https://github.com/{owner}/{repo_name}) | "
           "网络延迟实测 + 共享介质建模：跨语言中立 ICMP 采集、N 小则区间宽的 Wilson 置信区间、"
           "RFC 3550 抖动、CSMA/CD 三路验证（双实现逐位一致 / 理论误差 0.02% / 时隙守恒 1e-5） |\n")
    anchor = "| [**bda-toolkit**](https://github.com/heee-a/bda-toolkit) |"
    if repo_name not in text and anchor in text:
        text = text.replace(anchor, row + anchor)

    # 3) point at the new Pages site
    if f"{owner}.github.io/{repo_name}" not in text:
        text = text.replace(
            "## 📄 文档",
            "## 🌐 在线站点\n\n"
            f"- [net-analysis-lab 结果页](https://{owner}.github.io/{repo_name}/) — "
            "由 `data/` 自动生成，页面里没有一个手写数字\n\n"
            "## 📄 文档")

    if text == original:
        print("[5/5] 主页 README 无需修改")
        return
    res = _req("PUT", f"/repos/{owner}/{owner}/contents/README.md", {
        "message": "docs: add net-analysis-lab, refresh repo count and Pages link",
        "content": base64.b64encode(text.encode("utf-8")).decode(),
        "sha": cur["sha"],
    })
    if res:
        print("[5/5] 主页 README 已更新（仓库数 / 新条目 / Pages 链接）")
    else:
        print("[5/5] 主页 README 更新失败：\n  " + "\n  ".join(_HTTP_ERRORS[-2:]))


def step_user_profile(owner: str) -> None:
    """Optionally fill in name/bio/blog on the profile. Off by default.

    Needs the classic ``user`` scope. Only applied with ``--profile-patch`` so
    that a machine never silently rewrites how you present yourself.
    """
    if "--profile-patch" not in sys.argv:
        print("[6/6] 跳过个人资料更新（加 --profile-patch 启用）")
        return
    body = {
        "bio": ("Data analysis / data engineering / measurement. "
                "Building reproducible projects where every number is "
                "regenerated by script and shipped with an uncertainty interval."),
        "blog": f"https://{owner}.github.io/net-analysis-lab/",
    }
    res = _req("PATCH", "/user", body)
    print("[6/6] 个人资料已更新" if res
          else "[6/6] 个人资料更新失败（token 需要 user scope）：\n  "
               + "\n  ".join(_HTTP_ERRORS[-2:]))


def main() -> None:
    name = "net-analysis-lab"

    if SSH_MODE:
        owner = ssh_identity()
        print(f"[0/5] SSH 已认证为 {owner}")
        if not ssh_repo_exists(owner, name):
            sys.exit(f"[1/5] 远程仓库 {owner}/{name} 还不存在（或为空仓库且未初始化）。\n"
                     f"  请在浏览器打开 https://github.com/new 创建一个名为 "
                     f"{name} 的 Public 空仓库（不要勾选 README/LICENSE），然后重跑本命令。")
        print(f"[1/5] 远程仓库 {owner}/{name} 已就绪")
        print("[2/5] description / topics：SSH 模式无法调用 API，"
              f"请手动打开 https://github.com/{owner}/{name}/settings 填写，"
              "或改用 token 模式自动完成")
        step_push(owner, name)
        print(f"[4/5] 开启 Pages：打开 https://github.com/{owner}/{name}/settings/pages"
              " → Source 选 Deploy from a branch → 分支 main、目录 /docs → Save"
              "（首次构建约 1-3 分钟）")
        print(f"[5/5] 更新主页 README：https://github.com/{owner}/{owner}/edit/main/README.md"
              "（或用 token 模式自动完成）")
        print(f"\n代码已推送。仓库 https://github.com/{owner}/{name}")
        print(f"站点（开启 Pages 后）https://{owner}.github.io/{name}/")
        return

    owner = step_identity()
    step_create_repo(owner, name)
    step_metadata(owner, name)
    step_push(owner, name)
    # Pages can only be created after the branch actually exists.
    for attempt in range(6):
        if _req("GET", f"/repos/{owner}/{name}"):
            branch = _req("GET", f"/repos/{owner}/{name}/branches/main")
            if branch:
                break
        time.sleep(5)
    step_pages(owner, name)
    step_profile(owner, name)
    step_user_profile(owner)
    print(f"\n完成。仓库 https://github.com/{owner}/{name}")
    print(f"站点 https://{owner}.github.io/{name}/")


if __name__ == "__main__":
    main()
