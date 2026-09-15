#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kdocs_kit.py 单元测试 —— 纯 stdlib，不联网，不需要 kdocs-cli 真的装了。

跑法：python3 tests/test_kdocs_kit.py   或   python3 -m unittest discover tests
"""
import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import kdocs_kit  # noqa: E402


class FakeRunner:
    """替身 runner：记录收到的命令，返回预设结果。"""

    def __init__(self, code=0, out="", err=""):
        self.code, self.out, self.err = code, out, err
        self.calls = []
        self.payloads = []

    def __call__(self, cmd, timeout):
        self.calls.append(list(cmd))
        # --file 的参数文件在进程退出前必须还在，这里趁机读出来断言内容
        if "--file" in cmd:
            path = cmd[cmd.index("--file") + 1]
            with open(path, encoding="utf-8") as fh:
                self.payloads.append(json.load(fh))
        return self.code, self.out, self.err


class TestFriendlyError(unittest.TestCase):
    def test_token_expired(self):
        self.assertIn("过期", kdocs_kit.friendly_error("error 400006 token invalid"))

    def test_rate_limit_and_breaker_are_distinct(self):
        limited = kdocs_kit.friendly_error("429001 too many requests")
        broken = kdocs_kit.friendly_error("429002 circuit open")
        self.assertNotEqual(limited, broken)
        self.assertIn("歇一会儿", limited)
        self.assertIn("熔断", broken)

    def test_breaker_wins_over_ratelimit_when_both_present(self):
        # 429002 报文里常同时含 429001，必须命中更严重的那条
        msg = kdocs_kit.friendly_error("429002 triggered by repeated 429001")
        self.assertIn("熔断", msg)

    def test_enterprise_account(self):
        msg = kdocs_kit.friendly_error("403001 enterprise restricted")
        self.assertIn("企业账号", msg)
        self.assertIn("个人", msg)

    def test_not_authenticated(self):
        self.assertIn("连上", kdocs_kit.friendly_error('{"authenticated": false}'))

    def test_stale_cli(self):
        self.assertIn("升级", kdocs_kit.friendly_error("unknown action foo"))

    def test_unknown_error_is_not_swallowed(self):
        raw = "some weird backend explosion #9931"
        self.assertIn("9931", kdocs_kit.friendly_error(raw))

    def test_empty_input_does_not_crash(self):
        self.assertIsInstance(kdocs_kit.friendly_error(""), str)
        self.assertIsInstance(kdocs_kit.friendly_error(None), str)


class TestTargetParams(unittest.TestCase):
    def test_https_link(self):
        self.assertEqual(
            kdocs_kit.target_params("https://www.kdocs.cn/l/abc123"),
            {"url": "https://www.kdocs.cn/l/abc123"},
        )

    def test_http_link(self):
        self.assertIn("url", kdocs_kit.target_params("http://kdocs.cn/l/x"))

    def test_file_id(self):
        self.assertEqual(kdocs_kit.target_params("f_abc123"), {"file_id": "f_abc123"})

    def test_whitespace_is_trimmed(self):
        self.assertEqual(kdocs_kit.target_params("  f_x  "), {"file_id": "f_x"})

    def test_empty_rejected(self):
        for bad in ("", "   ", None):
            with self.assertRaises(ValueError):
                kdocs_kit.target_params(bad)


class TestCliDiscovery(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        # 必须把 HOME 也隔离：candidate_paths 永远会探测 ~/.local/bin/kdocs-cli，
        # 那是官方 setup.sh 的默认安装位。不隔离的话，开发机上「没装 CLI」的用例
        # 会找到真二进制，甚至拿开发者钥匙串里的 token 真去调一次网络。
        self._home = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self._home.name
        os.environ["USERPROFILE"] = self._home.name

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._home.cleanup()

    def test_env_override_wins(self):
        # 比较 Path 对象而不是 str：pathlib 在 Windows 上会把 / 归一化成 \\，
        # 硬编码 POSIX 字符串的断言在 Windows 必红（2026-09-14 验证 F4）。
        os.environ["KDOCS_CLI_BIN"] = "/custom/path/kdocs-cli"
        self.assertEqual(kdocs_kit.candidate_paths()[0], Path("/custom/path/kdocs-cli"))

    def test_windows_localappdata_is_probed(self):
        """断言必须精确到 LOCALAPPDATA 那条路径。

        原来只断言"存在某个以 .exe 结尾且含 kdocs-cli 的候选"——
        而 ~/.local/bin/kdocs-cli.exe 是无条件追加的，删掉 Windows 探测这条也照样通过（假绿）。
        """
        os.environ["LOCALAPPDATA"] = r"D:\ci-fixture\AppData\Local"
        found = [str(p) for p in kdocs_kit.candidate_paths()]
        expected = str(Path(r"D:\ci-fixture\AppData\Local") / "kdocs-cli" / "kdocs-cli.exe")
        self.assertIn(expected, found, "LOCALAPPDATA 安装位没被探测：%s" % found)

    def test_localappdata_entry_is_actually_selected(self):
        """只有 LOCALAPPDATA 那个位置真实存在时，find_cli 必须选中它。"""
        with tempfile.TemporaryDirectory() as d:
            appdata = Path(d) / "AppData" / "Local"
            target = appdata / "kdocs-cli" / "kdocs-cli.exe"
            target.parent.mkdir(parents=True)
            target.write_text("stub", encoding="utf-8")
            os.environ["LOCALAPPDATA"] = str(appdata)
            os.environ["PATH"] = str(Path(d) / "empty")
            os.environ.pop("KDOCS_CLI_BIN", None)
            self.assertEqual(kdocs_kit.find_cli(), target)

    def test_posix_local_bin_is_probed(self):
        found = [p.as_posix() for p in kdocs_kit.candidate_paths()]
        self.assertTrue(any(p.endswith("/.local/bin/kdocs-cli") for p in found),
                        "~/.local/bin 安装位应被探测: %s" % found)

    def test_no_duplicates(self):
        os.environ["KDOCS_CLI_BIN"] = "/dup/kdocs-cli"
        paths = [str(p) for p in kdocs_kit.candidate_paths()]
        self.assertEqual(len(paths), len(set(paths)), "候选路径不该重复")

    def test_missing_cli_raises_with_install_hint(self):
        os.environ["KDOCS_CLI_BIN"] = "/definitely/not/here/kdocs-cli"
        os.environ["PATH"] = "/definitely/not/here"
        os.environ.pop("LOCALAPPDATA", None)
        with self.assertRaises(kdocs_kit.KdocsNotInstalled) as ctx:
            kdocs_kit.find_cli()
        self.assertIn("setup", str(ctx.exception))


class TestCall(unittest.TestCase):
    def test_params_go_through_file_not_argv(self):
        """跨平台关键：中文绝不能出现在 argv 里（PowerShell 会破坏 UTF-8）。"""
        r = FakeRunner(out='{"code":0}')
        kdocs_kit.call("drive", "search-files", {"keyword": "季度报表"},
                     runner=r, cli_path="/fake/kdocs-cli")
        argv = r.calls[0]
        self.assertIn("--file", argv)
        self.assertNotIn("季度报表", " ".join(argv), "中文泄漏进了命令行参数")
        self.assertEqual(r.payloads[0], {"keyword": "季度报表"})

    def test_chinese_written_as_utf8_not_escaped(self):
        r = FakeRunner(out="{}")
        kdocs_kit.call("drive", "search-files", {"keyword": "汇总"},
                     runner=r, cli_path="/fake/kdocs-cli")
        self.assertEqual(r.payloads[0]["keyword"], "汇总")

    def test_tempfile_is_cleaned_up(self):
        r = FakeRunner(out="{}")
        kdocs_kit.call("drive", "x", {"a": 1}, runner=r, cli_path="/fake/kdocs-cli")
        path = r.calls[0][r.calls[0].index("--file") + 1]
        self.assertFalse(os.path.exists(path), "临时参数文件没删干净")

    def test_tempfile_cleaned_up_even_on_error(self):
        r = FakeRunner(code=1, err="boom")
        with self.assertRaises(kdocs_kit.KdocsError):
            kdocs_kit.call("drive", "x", {"a": 1}, runner=r, cli_path="/fake/kdocs-cli")
        path = r.calls[0][r.calls[0].index("--file") + 1]
        self.assertFalse(os.path.exists(path), "出错路径上临时文件泄漏")

    def test_nonzero_exit_raises_friendly(self):
        r = FakeRunner(code=3, err="400006 expired")
        with self.assertRaises(kdocs_kit.KdocsError) as ctx:
            kdocs_kit.call("drive", "x", {}, runner=r, cli_path="/fake/kdocs-cli")
        self.assertIn("过期", str(ctx.exception))
        self.assertIn("400006", ctx.exception.raw, "原始报文必须保留供排查")

    def test_empty_output_returns_none(self):
        r = FakeRunner(out="   ")
        self.assertIsNone(kdocs_kit.call("drive", "x", {}, runner=r,
                                       cli_path="/fake/kdocs-cli"))

    def test_non_json_output_returned_as_text(self):
        r = FakeRunner(out="not json at all")
        self.assertEqual(
            kdocs_kit.call("drive", "x", {}, runner=r, cli_path="/fake/kdocs-cli"),
            "not json at all")

    def test_cmd_shape(self):
        r = FakeRunner(out="{}")
        kdocs_kit.call("sheet", "add-chart", {}, runner=r, cli_path="/fake/kdocs-cli")
        argv = r.calls[0]
        self.assertEqual(argv[0], "/fake/kdocs-cli")
        self.assertEqual(argv[1], "sheet")
        self.assertEqual(argv[2], "add-chart")
        self.assertIn("--silent", argv)


class TestSlowOperationTimeout(unittest.TestCase):
    """慢操作超时必须变人话，不能把 traceback 甩给 用户。"""

    def test_timeout_becomes_friendly_kdocs_error(self):
        import subprocess as sp

        def timing_out(cmd, timeout):
            raise sp.TimeoutExpired(cmd, timeout)

        with self.assertRaises(kdocs_kit.KdocsError) as ctx:
            kdocs_kit.call("aippt", "execute", {"x": 1}, timeout_ms=7000,
                         runner=timing_out, cli_path="/fake/kdocs-cli")
        self.assertIn("太久", str(ctx.exception))
        self.assertIn("37", str(ctx.exception))   # 7s + 30s 余量
        self.assertIn("Timeout", ctx.exception.raw)

    def test_timeout_is_not_raw_subprocess_error(self):
        import subprocess as sp

        def timing_out(cmd, timeout):
            raise sp.TimeoutExpired(cmd, timeout)

        try:
            kdocs_kit.call("drive", "x", {}, runner=timing_out,
                         cli_path="/fake/kdocs-cli")
        except sp.TimeoutExpired:
            self.fail("TimeoutExpired 泄漏到了调用方")
        except kdocs_kit.KdocsError:
            pass

    def test_tempfile_cleaned_up_on_timeout(self):
        import subprocess as sp
        captured = {}

        def timing_out(cmd, timeout):
            captured["path"] = cmd[cmd.index("--file") + 1]
            raise sp.TimeoutExpired(cmd, timeout)

        with self.assertRaises(kdocs_kit.KdocsError):
            kdocs_kit.call("drive", "x", {}, runner=timing_out,
                         cli_path="/fake/kdocs-cli")
        self.assertFalse(os.path.exists(captured["path"]),
                         "超时路径上临时文件泄漏")


class TestUploadSizeGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_oversized_file_rejected_before_reading_into_memory(self):
        f = self.dir / "巨表.xlsx"
        f.write_bytes(b"x" * 1024)
        r = FakeRunner(out="{}")
        original = kdocs_kit.MAX_UPLOAD_BYTES
        kdocs_kit.MAX_UPLOAD_BYTES = 100          # 临时调小，不用真造大文件
        try:
            with self.assertRaises(ValueError) as ctx:
                kdocs_kit.upload(f, runner=r, cli_path="/fake/kdocs-cli")
        finally:
            kdocs_kit.MAX_UPLOAD_BYTES = original
        self.assertIn("太大", str(ctx.exception))
        self.assertEqual(r.calls, [], "超限就不该发出请求")

    def test_normal_file_passes_guard(self):
        f = self.dir / "正常.xlsx"
        f.write_bytes(b"x" * 2048)
        r = FakeRunner(out="{}")
        kdocs_kit.upload(f, runner=r, cli_path="/fake/kdocs-cli")
        self.assertEqual(len(r.calls), 1)


class TestAuth(unittest.TestCase):
    def test_authenticated_true(self):
        self.assertTrue(kdocs_kit.auth_ok(
            runner=FakeRunner(out='{"authenticated": true, "token": "x"}'),
            cli_path="/fake/kdocs-cli"))

    def test_authenticated_false(self):
        self.assertFalse(kdocs_kit.auth_ok(
            runner=FakeRunner(out='{"authenticated": false, "token": ""}'),
            cli_path="/fake/kdocs-cli"))

    def test_nonzero_exit_is_false(self):
        self.assertFalse(kdocs_kit.auth_ok(runner=FakeRunner(code=1),
                                         cli_path="/fake/kdocs-cli"))

    def test_garbage_output_is_false(self):
        self.assertFalse(kdocs_kit.auth_ok(runner=FakeRunner(out="<html>"),
                                         cli_path="/fake/kdocs-cli"))


class TestUpload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_base64_roundtrip_binary_safe(self):
        f = self.dir / "表.xlsx"
        raw = bytes(range(256))
        f.write_bytes(raw)
        r = FakeRunner(out="{}")
        kdocs_kit.upload(f, runner=r, cli_path="/fake/kdocs-cli")
        sent = r.payloads[0]["content_base64"]
        self.assertEqual(base64.b64decode(sent), raw, "二进制往返不一致")

    def test_cloud_name_defaults_to_local_name(self):
        f = self.dir / "汇总表.xlsx"
        f.write_bytes(b"x")
        r = FakeRunner(out="{}")
        kdocs_kit.upload(f, runner=r, cli_path="/fake/kdocs-cli")
        self.assertEqual(r.payloads[0]["name"], "汇总表.xlsx")

    def test_cloud_name_override(self):
        f = self.dir / "a.xlsx"
        f.write_bytes(b"x")
        r = FakeRunner(out="{}")
        kdocs_kit.upload(f, cloud_name="重命名.xlsx", runner=r, cli_path="/fake/kdocs-cli")
        self.assertEqual(r.payloads[0]["name"], "重命名.xlsx")

    def test_unsupported_suffix_rejected_before_network(self):
        f = self.dir / "a.sketch"
        f.write_bytes(b"x")
        r = FakeRunner(out="{}")
        with self.assertRaises(ValueError):
            kdocs_kit.upload(f, runner=r, cli_path="/fake/kdocs-cli")
        self.assertEqual(r.calls, [], "格式不对就不该发出请求")

    def test_suffix_check_is_case_insensitive(self):
        f = self.dir / "A.XLSX"
        f.write_bytes(b"x")
        r = FakeRunner(out="{}")
        kdocs_kit.upload(f, runner=r, cli_path="/fake/kdocs-cli")
        self.assertEqual(len(r.calls), 1)

    def test_wps_native_formats_accepted(self):
        for ext in (".et", ".wps", ".dps"):
            f = self.dir / ("x" + ext)
            f.write_bytes(b"x")
            r = FakeRunner(out="{}")
            kdocs_kit.upload(f, runner=r, cli_path="/fake/kdocs-cli")
            self.assertEqual(len(r.calls), 1, "WPS 自家格式 %s 应被接受" % ext)

    def test_missing_file_rejected(self):
        with self.assertRaises(ValueError):
            kdocs_kit.upload(self.dir / "nope.xlsx", cli_path="/fake/kdocs-cli")


class TestSaveAsNew(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_never_returns_existing_path(self):
        orig = self.dir / "汇总.xlsx"
        orig.write_bytes(b"original")
        out = kdocs_kit.save_as_new(orig)
        self.assertNotEqual(out, orig)
        self.assertFalse(out.exists())
        self.assertEqual(orig.read_bytes(), b"original", "原件被动了")

    def test_collision_gets_incremented(self):
        orig = self.dir / "a.xlsx"
        orig.write_bytes(b"x")
        first = kdocs_kit.save_as_new(orig)
        first.write_bytes(b"y")
        second = kdocs_kit.save_as_new(orig)
        self.assertNotEqual(first, second)
        self.assertFalse(second.exists())

    def test_suffix_preserved(self):
        orig = self.dir / "a.docx"
        orig.write_bytes(b"x")
        self.assertEqual(kdocs_kit.save_as_new(orig).suffix, ".docx")


class TestRenderFiles(unittest.TestCase):
    def test_empty_is_friendly_not_crash(self):
        for empty in ({}, {"files": []}, [], None):
            self.assertIn("没找到", kdocs_kit.render_files(empty))

    def test_lists_names_and_ids(self):
        out = kdocs_kit.render_files({"files": [{"name": "数据表.xlsx", "file_id": "f_1"}]})
        self.assertIn("数据表.xlsx", out)
        self.assertIn("f_1", out)

    def test_tolerates_alternate_key_shapes(self):
        self.assertIn("x.docx", kdocs_kit.render_files({"items": [{"fname": "x.docx"}]}))

    def test_skips_non_dict_entries(self):
        self.assertIn("好.xlsx",
                      kdocs_kit.render_files([{"name": "好.xlsx"}, "垃圾", None]))


class TestMainExitCodes(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["KDOCS_CLI_BIN"] = "/definitely/not/here/kdocs-cli"
        os.environ["PATH"] = "/definitely/not/here"
        os.environ.pop("LOCALAPPDATA", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_missing_cli_exits_1(self):
        self.assertEqual(kdocs_kit.main(["find", "x"]), 1)

    def test_doctor_missing_cli_exits_1(self):
        self.assertEqual(kdocs_kit.main(["doctor"]), 1)

    def test_no_subcommand_prints_help_exits_0(self):
        self.assertEqual(kdocs_kit.main([]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
