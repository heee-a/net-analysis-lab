"""Parser tests with *recorded* ping output from each supported locale.

These fixtures are real captures. Keeping them in the repo means the parser is
verified on CI runners (which are always en-US) against output shapes that only
appear on zh-CN Windows hosts — exactly the combination that silently returned
zero rows during development.
"""

from netlab.collect import (parse_ping_output, extract_resolved_ip,
                            decode_ping_output, batches_to_records)

PNG_WIN_ZH = (
    "\r\n"
    "正在 Ping 8.8.8.8 具有 32 字节的数据:\r\n"
    "来自 8.8.8.8 的回复: 字节=32 时间=82ms TTL=109\r\n"
    "来自 8.8.8.8 的回复: 字节=32 时间=81ms TTL=109\r\n"
    "请求超时。\r\n"
    "来自 8.8.8.8 的回复: 字节=32 时间=80ms TTL=109\r\n"
    "\r\n"
    "8.8.8.8 的 Ping 统计信息:\r\n"
    "    数据包: 已发送 = 4，已接收 = 3，丢失 = 1 (25% 丢失)，\r\n"
).encode("gbk")

PNG_WIN_EN = (
    "\r\n"
    "Pinging google.com [142.250.1.139] with 32 bytes of data:\r\n"
    "Reply from 142.250.1.139: bytes=32 time=41ms TTL=115\r\n"
    "Request timed out.\r\n"
    "Reply from 142.250.1.139: bytes=32 time=39ms TTL=115\r\n"
    "Reply from 142.250.1.139: bytes=32 time=40ms TTL=115\r\n"
    "\r\n"
    "Ping statistics for 142.250.1.139:\r\n"
    "    Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),\r\n"
)

PNG_POSIX = (
    "PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.\n"
    "64 bytes from 8.8.8.8: icmp_seq=1 ttl=115 time=12.4 ms\n"
    "64 bytes from 8.8.8.8: icmp_seq=2 ttl=115 time=11.9 ms\n"
    "64 bytes from 8.8.8.8: icmp_seq=3 ttl=115 time=13.1 ms\n"
    "\n"
    "--- 8.8.8.8 ping statistics ---\n"
    "3 packets transmitted, 3 received, 0% packet loss, time 2003ms\n"
)


def _oks(rows):
    return [r["ok"] for r in rows]


def test_windows_chinese_output_parses():
    rows = parse_ping_output(PNG_WIN_ZH)
    assert _oks(rows) == [True, True, False, True]
    assert [r["rtt_ms"] for r in rows] == [82.0, 81.0, None, 80.0]
    assert all(r["ttl"] == 109 for r in rows if r["ok"])


def test_windows_english_output_parses():
    rows = parse_ping_output(PNG_WIN_EN)
    assert _oks(rows) == [True, False, True, True]
    assert [r["rtt_ms"] for r in rows] == [41.0, None, 39.0, 40.0]


def test_posix_output_parses():
    rows = parse_ping_output(PNG_POSIX)
    assert _oks(rows) == [True, True, True]
    assert [r["rtt_ms"] for r in rows] == [12.4, 11.9, 13.1]
    assert [r["seq"] for r in rows] == [1, 2, 3]


def test_sub_one_millisecond_is_captured():
    # Windows renders sub-ms RTTs as "time<1ms"
    rows = parse_ping_output("来自 1.1.1.1 的回复: 字节=32 时间<1ms TTL=57".encode("gbk"))
    assert rows[0]["ok"] and rows[0]["rtt_ms"] == 1.0


def test_wrong_codepage_still_recovers_via_ascii_fallback():
    # Decoding GBK bytes as latin-1 produces mojibake, yet "82ms" and "TTL=109"
    # survive because they are ASCII. The fallback must still extract them.
    mojibake = PNG_WIN_ZH.decode("latin-1")
    rows = parse_ping_output(mojibake)
    assert [r["rtt_ms"] for r in rows if r["ok"]] == [82.0, 81.0, 80.0]


def test_summary_lines_are_not_counted_as_probes():
    rows = parse_ping_output(PNG_WIN_ZH)
    assert len(rows) == 4                      # exactly the four echo requests


def test_unreachable_host_counts_as_failure():
    rows = parse_ping_output("来自 10.0.0.99 的回复: 无法访问目标主机。".encode("gbk"))
    assert rows and rows[0]["ok"] is False


def test_extract_resolved_ip():
    assert extract_resolved_ip(PNG_WIN_EN) == "142.250.1.139"
    assert extract_resolved_ip(
        "正在 Ping www.a.shifen.com [223.109.82.212] 具有 32 字节的数据:".encode("gbk")
    ) == "223.109.82.212"


def test_no_resolved_ip_for_literal_address():
    assert extract_resolved_ip(PNG_POSIX) is None


def test_decode_prefers_encoding_with_most_replies():
    text = decode_ping_output(PNG_WIN_ZH)
    assert "来自" in text          # must pick GBK, not latin-1


def test_batches_to_records_flattens_in_order():
    batches = [{
        "target": "8.8.8.8", "label": "Google DNS", "tier": "dns",
        "started_utc": "2026-10-10T00:00:00+00:00",
        "rows": [{"seq": 1, "ok": True, "rtt_ms": 10.0, "ttl": 100, "bytes": 32},
                 {"seq": 2, "ok": False, "rtt_ms": None, "ttl": None, "bytes": None}],
    }]
    rec = batches_to_records(batches)
    assert len(rec) == 2
    assert rec[0]["order"] == 1 and rec[1]["order"] == 2
    assert rec[0]["label"] == "Google DNS" and rec[1]["ok"] is False
