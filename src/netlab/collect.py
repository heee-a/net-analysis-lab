"""Cross-platform ICMP latency collector built on the OS ``ping`` binary.

Why shell out to ``ping`` instead of using raw sockets?
  * Raw sockets require administrator/root privileges on Windows and most Linux
    distributions, which breaks reproducibility on CI machines.
  * Every OS ships a ``ping`` whose output grammar we can parse deterministically,
    so the very same code path runs on Windows, macOS and Linux runners.

Locale independence is a hard requirement: Windows prints Simplified Chinese
(``来自 8.8.8.8 的回复: 字节=32 时间=82ms TTL=109``, GBK encoded) on zh-CN hosts,
English on en-US hosts, and both use different summary punctuation.  The parser
therefore (1) tries several decodings, (2) matches locale-specific structured
patterns, and (3) falls back to an ASCII-only heuristic that still works even
when the text was decoded with the wrong codepage, because ``81ms`` and
``TTL=109`` are ASCII and survive any decode.
"""

from __future__ import annotations

import locale
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

# Windows (en-US): "Reply from 8.8.8.8: bytes=32 time=81ms TTL=115"
_RE_WIN = re.compile(
    r"Reply from\s+(?P<host>\S+?):\s+bytes=(?P<bytes>\d+)\s+"
    r"time\s*[=<]\s*(?P<time>[\d.,]+)\s*(?P<unit>ms|s)?\s+TTL\s*=\s*(?P<ttl>\d+)",
    re.IGNORECASE,
)
# Windows (zh-CN): "来自 8.8.8.8 的回复: 字节=32 时间=82ms TTL=109"
_RE_ZH = re.compile(
    r"来自\s*(?P<host>\S+?)\s*的回复\s*[:：]\s*字节\s*=\s*(?P<bytes>\d+)\s*"
    r"时间\s*[=<]\s*(?P<time>[\d.,]+)\s*(?P<unit>ms|s)?\s*TTL\s*=\s*(?P<ttl>\d+)",
)
# POSIX  : "64 bytes from 8.8.8.8: icmp_seq=0 ttl=115 time=81.2 ms"
_RE_POSIX = re.compile(
    r"(?P<bytes>\d+)\s+bytes from\s+(?P<host>\S+?):\s+"
    r"icmp_seq=(?P<seq>\d+)\s+ttl=(?P<ttl>\d+)\s+"
    r"time[=<]\s*(?P<time>[\d.]+)\s*(?P<unit>ms|s)?",
    re.IGNORECASE,
)
# Locale-agnostic rescue: any line carrying "TTL=<n>" plus "<n>ms" is a reply,
# whatever language (and whatever codepage) wrapped it.
_RE_TTL = re.compile(r"TTL\s*=\s*(?P<ttl>\d+)", re.IGNORECASE)
_RE_ANY_MS = re.compile(r"(?P<time>\d+(?:[.,]\d+)?)\s*(?P<unit>ms|s)\b", re.IGNORECASE)
# "正在 Ping www.a.shifen.com [223.109.82.212] 具有 32 字节的数据:"
# "Pinging www.a.shifen.com [223.109.82.212] with 32 bytes of data:"
_RE_RESOLVED = re.compile(r"\[(?P<ip>\d+\.\d+\.\d+\.\d+)\]")
# Failure signatures across locales / platforms.
_FAIL_PATTERNS = (
    "request timed out", "destination host unreachable", "请求超时",
    "无法访问", "目标主机不可达", "传输失败", "找不到主机",
    "100% packet loss", "timeout", "timed out",
    "host unreachable", "network is unreachable", "general failure",
    "transmit failed", "no route", "100% 丢失",
)
# Summary line: "Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)"
#               "数据包: 已发送 = 4，已接收 = 4，丢失 = 0 (0% 丢失)"   <- full-width commas
_RE_WIN_SUMMARY = re.compile(
    r"(?:Sent|已发送)\s*=\s*(?P<sent>\d+)\s*[,，]\s*(?:Received|已接收)\s*=\s*(?P<recv>\d+)"
    r"\s*[,，]\s*(?:Lost|丢失)\s*=\s*(?P<lost>\d+)",
    re.IGNORECASE,
)
# Summary line: "4 packets transmitted, 4 received, 0% packet loss"
_RE_POSIX_SUMMARY = re.compile(
    r"(?P<tx>\d+)\s+packets transmitted,\s*(?P<rx>\d+)\s+(?:packets\s+)?received"
    r"(?:,\s*(?P<loss>[\d.]+)%\s+packet loss)?",
    re.IGNORECASE,
)

_CANDIDATE_ENCODINGS = (
    locale.getpreferredencoding(False),
    "utf-8",
    "gbk" if platform.system().lower() == "windows" else "utf-8",
    "cp936" if platform.system().lower() == "windows" else "utf-8",
    "mbcs" if platform.system().lower() == "windows" else "utf-8",
    "latin-1",
)


def decode_ping_output(raw: bytes) -> str:
    """Decode ping bytes, preferring the encoding that yields the most replies."""
    seen = set()
    best, best_score = "", -1
    for enc in _CANDIDATE_ENCODINGS:
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        # Score = number of lines that look like a successful reply.
        score = sum(1 for ln in text.splitlines() if _RE_TTL.search(ln))
        if score > best_score:
            best, best_score = text, score
    return best or raw.decode("utf-8", errors="replace")


def _to_ms(value: str, unit: Optional[str]) -> float:
    """Normalise a ping duration to milliseconds."""
    v = float(str(value).replace(",", "."))
    if unit and unit.lower() == "s":
        v *= 1000.0
    return round(v, 3)


def _row(seq: int, time_tok: str | None, unit: str | None, ttl: int | None,
         size: int | None) -> Dict:
    return {
        "seq": seq,
        "ok": time_tok is not None,
        "rtt_ms": _to_ms(time_tok, unit) if time_tok is not None else None,
        "ttl": ttl,
        "bytes": size,
    }


def parse_ping_output(raw: str | bytes) -> List[Dict]:
    """Parse ping stdout into per-probe rows.

    Returns rows with keys: ``seq``, ``ok``, ``rtt_ms``, ``ttl``, ``bytes``.
    A failed probe yields ``ok=False`` and ``rtt_ms=None`` — it is kept, because
    dropping it would bias every loss-rate estimate towards zero.
    """
    if isinstance(raw, (bytes, bytearray)):
        text = decode_ping_output(bytes(raw))
    else:
        text = raw
    rows: List[Dict] = []
    seq = 0
    for line in text.splitlines():
        low = line.lower()
        m = _RE_WIN.search(line) or _RE_ZH.search(line) or _RE_POSIX.search(line)
        if m:
            seq += 1
            g = m.groupdict()
            rows.append(_row(int(g["seq"]) if g.get("seq") else seq,
                             g["time"], g["unit"], int(g["ttl"]), int(g["bytes"])))
            continue
        # Locale-agnostic fallback: TTL= plus a "<n>ms" token means a reply.
        ttl = _RE_TTL.search(line)
        if ttl:
            ms_tokens = _RE_ANY_MS.findall(line[:ttl.start()])
            if ms_tokens:
                seq += 1
                tok, unit = ms_tokens[-1]
                size = re.search(r"bytes\s*=\s*(\d+)", line, re.IGNORECASE)
                rows.append(_row(seq, tok, unit, int(ttl.group("ttl")),
                                 int(size.group(1)) if size else None))
                continue
        if any(p in low for p in _FAIL_PATTERNS):
            seq += 1
            rows.append(_row(seq, None, None, None, None))
    return rows


def extract_resolved_ip(raw: str | bytes) -> Optional[str]:
    """Pull the resolved IPv4 address out of the ping banner, if present."""
    text = raw if isinstance(raw, str) else decode_ping_output(bytes(raw))
    m = _RE_RESOLVED.search(text)
    return m.group("ip") if m else None


def _ping_command(target: str, count: int, timeout_s: int) -> List[str]:
    system = platform.system().lower()
    if system == "windows":
        return ["ping", "-n", str(count), "-w", str(timeout_s * 1000), target]
    if system == "darwin":
        # macOS uses -W in milliseconds and does not support -W with deadlines.
        return ["ping", "-c", str(count), "-W", str(timeout_s * 1000), target]
    return ["ping", "-c", str(count), "-W", str(timeout_s), target]


def ping_once(target: str, count: int = 20, timeout_s: int = 2,
              interval_s: float = 0.0) -> Dict:
    """Run one ping batch against ``target`` and return a structured batch."""
    if shutil.which("ping") is None:
        raise RuntimeError("`ping` binary not available on this system")

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    proc = subprocess.run(
        _ping_command(target, count, timeout_s),
        capture_output=True,
        timeout=max(count * (timeout_s + 1), 10),
    )
    wall_s = round(time.perf_counter() - t0, 3)
    out_bytes = proc.stdout or b""
    rows = parse_ping_output(out_bytes)

    # Reconcile with the OS-reported summary: some firewalls answer with ICMP
    # errors that our line patterns do not classify.
    summary_text = decode_ping_output(out_bytes)
    loss_pct_summary = None
    m = (_RE_WIN_SUMMARY.search(summary_text)
         or _RE_POSIX_SUMMARY.search(summary_text))
    if m:
        g = m.groupdict()
        if g.get("sent") is not None:
            sent = int(g["sent"])
            loss_pct_summary = 100.0 * int(g["lost"]) / sent if sent else None
        elif g.get("loss"):
            loss_pct_summary = float(g["loss"])

    return {
        "target": target,
        "started_utc": started.isoformat(timespec="seconds"),
        "resolved_ip": extract_resolved_ip(out_bytes),
        "requested": count,
        "captured": len(rows),
        "wall_s": wall_s,
        "returncode": proc.returncode,
        "summary_loss_pct": loss_pct_summary,
        "rows": rows,
        "stderr_tail": (decode_ping_output(proc.stderr or b"").strip()
                        .splitlines()[-1:] or None),
    }


@dataclass(frozen=True)
class Target:
    """One measurement target plus the network tier it belongs to."""

    host: str
    tier: str            # gateway / national / overseas
    label: str = ""      # human readable name used in charts

    def __post_init__(self) -> None:
        if not self.label:
            object.__setattr__(self, "label", self.host)


# Default target set: three tiers so that we can compare a network "close by"
# against one that is several AS hops away.
DEFAULT_TARGETS: List[Target] = [
    Target("8.8.8.8", "dns-anycast", "Google DNS"),
    Target("1.1.1.1", "dns-anycast", "Cloudflare DNS"),
    Target("223.5.5.5", "dns-anycast", "AliDNS"),
    Target("www.baidu.com", "national-web", "Baidu"),
    Target("www.qq.com", "national-web", "QQ"),
    Target("www.github.com", "overseas-web", "GitHub"),
]


def collect(targets: Optional[List[Target]] = None, count: int = 30,
            timeout_s: int = 2, pause_s: float = 0.6,
            verbose: bool = True) -> List[Dict]:
    """Ping every target sequentially and return a list of batches."""
    targets = list(targets or DEFAULT_TARGETS)
    batches: List[Dict] = []
    for t in targets:
        try:
            batch = ping_once(t.host, count=count, timeout_s=timeout_s)
        except Exception as exc:                      # noqa: BLE001 - report, do not die
            if verbose:
                print(f"  ! {t.host:<18} failed: {exc}")
            batches.append({
                "target": t.host, "tier": t.tier, "label": t.label,
                "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "requested": count, "captured": 0, "wall_s": None,
                "returncode": None, "summary_loss_pct": None,
                "rows": [], "error": repr(exc),
            })
            continue
        batch.update(tier=t.tier, label=t.label)
        batches.append(batch)
        ok = sum(1 for r in batch["rows"] if r["ok"])
        if verbose:
            print(f"  · {t.label:<16} {ok}/{batch['captured'] or batch['requested']} ok  "
                  f"({batch['wall_s']}s)")
        time.sleep(pause_s)
    return batches


def batches_to_records(batches: List[Dict]) -> List[Dict]:
    """Flatten batches into the tidy long-format rows used throughout the repo."""
    out: List[Dict] = []
    order = 0
    for b in batches:
        for r in b.get("rows", []):
            order += 1
            out.append({
                "target": b["target"],
                "label": b.get("label", b["target"]),
                "tier": b.get("tier", "unknown"),
                "resolved_ip": b.get("resolved_ip"),
                "started_utc": b.get("started_utc"),
                "seq": r["seq"],
                "order": order,
                "ok": r["ok"],
                "rtt_ms": r["rtt_ms"],
                "ttl": r["ttl"],
                "bytes": r["bytes"],
            })
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(collect(count=10), indent=2, default=str))
