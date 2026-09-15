#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-09-14 Windows 真机验证（Windows 侧秘书）抓到的 F1-F8，每条一组回归。

⚠️ fixture 一律用**报告里贴的真响应原文形态**，不许自己编「理想形态」——
这次的两条阻断级 bug 正是理想形态 fixture 洗出来的。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import kdocs_kit  # noqa: E402


# 报告 F1 贴出的真实 `kdocs-cli auth status` 输出（token 值按报告已省略形式；结构一字不改）
REAL_AUTH_STATUS = """{
  "authenticated": true,
  "keychain": {
    "available": true,
    "backend": "system keychain",
    "consistent": true,
    "token": "UBIkbswf...hA=="
  },
  "source": "system keychain",
  "token": "UBIkbswf...hA=="
}"""

REAL_AUTH_STATUS_FALSE = """{
  "authenticated": false,
  "keychain": {
    "available": true,
    "backend": "system keychain",
    "consistent": false,
    "token": ""
  },
  "source": "",
  "token": ""
}"""

# 报告 F2 贴出的真实 `drive get-file-info` 信封
REAL_FILE_INFO = {
    "code": 0,
    "data": {"id": "f_xxx", "name": "产品登记表.xlsx",
             "size": 20480, "ftype": "file"},
    "msg": "ok",
}


class Runner:
    def __init__(self, code=0, out="", err=""):
        self.code, self.out, self.err = code, out, err
        self.calls = []

    def __call__(self, cmd, timeout):
        self.calls.append(list(cmd))
        return self.code, self.out, self.err


# ══════════════════════════════════════════════════ F1 CRITICAL
class TestF1AuthStatusNestedJson(unittest.TestCase):
    """授权成功的机器上 doctor 恒报「还没连上」。

    根因：非贪婪正则 `{.*?}` 对嵌套对象只截到「根 { → keychain 的 }」，json 必失败。
    """

    def test_real_nested_payload_is_recognised(self):
        ok, _ = kdocs_kit.auth_status(runner=Runner(out=REAL_AUTH_STATUS),
                                    cli_path="/fake/c")
        self.assertTrue(ok, "真实嵌套响应必须能读出 authenticated=true")

    def test_real_unauthenticated_payload(self):
        ok, _ = kdocs_kit.auth_status(runner=Runner(out=REAL_AUTH_STATUS_FALSE),
                                    cli_path="/fake/c")
        self.assertFalse(ok)

    def test_old_regex_would_have_failed(self):
        """把根因钉死：非贪婪正则在这段真实响应上确实解析不出来。"""
        import re
        chunks = re.findall(r"\{.*?\}", REAL_AUTH_STATUS, re.S)
        self.assertEqual(len(chunks), 1)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(chunks[0])

    def test_payload_on_stderr_still_works(self):
        ok, _ = kdocs_kit.auth_status(runner=Runner(out="", err=REAL_AUTH_STATUS),
                                    cli_path="/fake/c")
        self.assertTrue(ok, "status 走哪个流没保证，两边都要认")

    def test_payload_mixed_with_log_lines(self):
        noisy = "[warn] something\n" + REAL_AUTH_STATUS + "\n[info] done\n"
        ok, _ = kdocs_kit.auth_status(runner=Runner(out=noisy), cli_path="/fake/c")
        self.assertTrue(ok, "混了日志行也要能扫出来（兜底路径）")

    def test_scanner_handles_nesting(self):
        objs = list(kdocs_kit._scan_json_objects(
            'noise {"a": {"b": 1}, "c": 2} tail {"d": 3}'))
        self.assertEqual(objs, [{"a": {"b": 1}, "c": 2}, {"d": 3}])

    def test_garbage_output_is_not_authenticated(self):
        ok, _ = kdocs_kit.auth_status(runner=Runner(out="<html>"), cli_path="/fake/c")
        self.assertFalse(ok)

    def test_doctor_turns_green_on_real_payload(self):
        """端到端：真形态下 doctor 的 auth 那一项必须过。"""
        self.assertTrue(
            kdocs_kit.auth_ok(runner=Runner(out=REAL_AUTH_STATUS), cli_path="/fake/c"))


# ══════════════════════════════════════════════════ F2 HIGH
class TestF2EnvelopeUnwrap(unittest.TestCase):
    """「下载到文件夹」对所有文件必失败：在信封顶层读 name 恒得 None。"""

    def test_envelope_name_is_read(self):
        self.assertEqual(kdocs_kit.unwrap_envelope(REAL_FILE_INFO).get("name"),
                         "产品登记表.xlsx")

    def test_bare_payload_still_read(self):
        self.assertEqual(
            kdocs_kit.unwrap_envelope({"name": "直给.xlsx"}).get("name"), "直给.xlsx")

    def test_not_an_envelope_when_code_missing(self):
        """只有 data 没有 code 的，不能当信封剥——那可能就是内容本身。"""
        payload = {"data": {"name": "内层"}, "name": "外层"}
        self.assertEqual(kdocs_kit.unwrap_envelope(payload).get("name"), "外层")

    def test_non_dict_is_safe(self):
        self.assertEqual(kdocs_kit.unwrap_envelope(None), {})
        self.assertEqual(kdocs_kit.unwrap_envelope("text"), {})

    def test_save_as_new_gets_a_real_name_from_envelope(self):
        with tempfile.TemporaryDirectory() as d:
            name = kdocs_kit.unwrap_envelope(REAL_FILE_INFO).get("name")
            out = kdocs_kit.save_as_new(Path(d), cloud_name=name)
            self.assertEqual(out.parent, Path(d))
            self.assertEqual(out.suffix, ".xlsx")
            self.assertIn("产品登记表", out.name)

    def test_file_id_also_read_through_envelope(self):
        env = {"code": 0, "data": {"file_id": "f_new", "link_url": "https://k/l/x"},
               "msg": "ok"}
        self.assertEqual(kdocs_kit.unwrap_envelope(env).get("file_id"), "f_new")


# ══════════════════════════════════════════════════ F4 MEDIUM
class TestF4PathAssertionIsPlatformNeutral(unittest.TestCase):
    """`str(Path('/custom/...'))` 在 Windows 上是反斜杠，POSIX 硬编码断言必红。"""

    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_env_override_compares_as_path_not_string(self):
        os.environ["KDOCS_CLI_BIN"] = "/custom/path/kdocs-cli"
        self.assertEqual(kdocs_kit.candidate_paths()[0], Path("/custom/path/kdocs-cli"))

    def test_windows_separator_semantics_locked(self):
        """用 PureWindowsPath 锁住 Windows 语义（在 macOS 上也能跑）。"""
        self.assertEqual(str(PureWindowsPath("/custom/path/kdocs-cli")),
                         "\\custom\\path\\kdocs-cli")
        self.assertEqual(PureWindowsPath("/custom/path/kdocs-cli").as_posix(),
                         "/custom/path/kdocs-cli")

    def test_localappdata_candidate_uses_platform_join(self):
        os.environ["LOCALAPPDATA"] = r"D:\ci-fixture\AppData\Local"
        found = [p.as_posix() for p in kdocs_kit.candidate_paths()]
        self.assertTrue(any(p.endswith("kdocs-cli/kdocs-cli.exe") for p in found),
                        "LOCALAPPDATA 候选应存在（用 as_posix 比较，跨平台稳定）：%s" % found)


# ══════════════════════════════════════════════════ F6 LOW
class TestF6DoctorDoesNotShortCircuit(unittest.TestCase):
    """CLI 缺失时 doctor 只打两行就返回，网络/Claude Code/skillOverrides 全看不到。

    用户 场景下这几项恰恰是最该一次看全的（任务书也这么预期）。
    """

    def setUp(self):
        self._env = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KDOCS_CLI_BIN"] = str(Path(self.tmp.name) / "nope")
        os.environ["PATH"] = str(Path(self.tmp.name) / "empty")
        os.environ.pop("LOCALAPPDATA", None)
        os.environ["HOME"] = self.tmp.name
        os.environ["USERPROFILE"] = self.tmp.name

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    def test_missing_cli_still_reports_every_item(self):
        proc = subprocess.run(
            [sys.executable, str(HERE.parent / "scripts" / "kdocs_kit.py"), "doctor"],
            capture_output=True, encoding="utf-8", timeout=120,
            cwd=self.tmp.name, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        import json as _json
        manifest = _json.loads(
            (HERE.parent / "requirements.json").read_text(encoding="utf-8"))
        for req in manifest["requirements"]:
            self.assertIn(req["title"], proc.stdout,
                          "CLI 缺失时也该把这项列出来：%s\n%s" % (req["id"], proc.stdout))


# ══════════════════════════════════════════════════ F8 LOW
class TestF8VersionLineIsGuarded(unittest.TestCase):
    """替身场景下 doctor 把 JSON 原文当版本号打了出来。"""

    def test_json_blob_is_not_shown_as_version(self):
        self.assertIsNone(kdocs_kit._version_text(REAL_AUTH_STATUS))

    def test_real_version_is_shown(self):
        self.assertEqual(kdocs_kit._version_text("2.5.29"), "2.5.29")
        self.assertEqual(kdocs_kit._version_text("kdocs-cli v2.5.29\n"), "kdocs-cli v2.5.29")


if __name__ == "__main__":
    unittest.main(verbosity=2)
