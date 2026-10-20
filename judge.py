# -*- coding: utf-8 -*-
"""平台统一的判题逻辑（服务端）。

之前前后端各写一份「输出字符串相等」的朴素比较，既无法容忍换行/行尾空白差异，
也不支持一题多解。这里把规则收拢到一处：

- ``normalize``  去掉 BOM、统一换行、去掉每行行尾空白与首尾空行；
- ``accepted``   ``expected`` 可以是字符串或字符串列表（一题多解）；
- ``regex``      ``expected_regex`` 命中即通过（适合输出里带时间戳的题）；
- ``strict_stderr``  为真时，stderr 非空视为失败（默认允许 stderr，如 Shell 的提示信息）。
"""

from __future__ import annotations

import re

_BOM = "\ufeff"


def normalize(text: object) -> str:
    """把输出整理成可以稳定比较的形式。"""
    if text is None:
        return ""
    value = str(text).replace("\r\n", "\n").replace("\r", "\n")
    while value.startswith(_BOM):
        value = value[len(_BOM):]
    lines = [line.rstrip() for line in value.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def accepted_outputs(challenge: dict) -> list[str]:
    """返回该题所有被接受的输出（已归一化）。"""
    raw = challenge.get("expected", "")
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    result: list[str] = []
    for item in items:
        value = normalize(item)
        if value not in result:
            result.append(value)
    return result


def judge(
    challenge: dict,
    stdout: object = "",
    stderr: object = "",
    returncode: int = 0,
) -> dict:
    """判定一次提交，返回可直接 JSON 化的结果。"""
    actual = normalize(stdout)
    err_text = normalize(stderr)
    accepted = accepted_outputs(challenge)
    strict_stderr = bool(challenge.get("strict_stderr", False))

    passed = False
    reason = ""

    if returncode != 0 and not challenge.get("allow_nonzero"):
        passed = False
        reason = f"脚本退出码为 {returncode}，需要正常结束（退出码 0）"
    elif strict_stderr and err_text:
        passed = False
        reason = "标准错误输出不为空"
    else:
        pattern = str(challenge.get("expected_regex") or "").strip()
        if pattern:
            try:
                passed = re.search(pattern, actual, re.MULTILINE) is not None
            except re.error:
                passed = False
            reason = "" if passed else "输出与预期格式不匹配"
        else:
            passed = actual in accepted
            reason = "" if passed else "输出与预期不一致"

    payload = {
        "passed": passed,
        "reason": reason,
        "actual": actual,
        "expected": accepted[0] if accepted else "",
        "accepted": accepted,
        "hint": str(challenge.get("hint") or ""),
        "exit_code": int(returncode),
    }
    if passed:
        payload["reason"] = "输出与目标一致"
    return payload
