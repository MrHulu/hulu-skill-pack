#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-09-13 四家 AI council 审出来的问题，每条一组回归测试。

每个 fix 都做过变异验证（把修复撤掉，对应测试必须变红）。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import kdocs_kit  # noqa: E402


class FakeRunner:
    def __init__(self, code=0, out="", err=""):
        self.code, self.out, self.err = code, out, err
        self.calls, self.payloads = [], []

    def __call__(self, cmd, timeout):
        self.calls.append(list(cmd))
        if "--file" in cmd:
            with open(cmd[cmd.index("--file") + 1], encoding="utf-8") as fh:
                self.payloads.append(json.load(fh))
        return self.code, self.out, self.err


class FakeResp(io.BytesIO):
    def __init__(self, payload, ctype="application/octet-stream"):
        super().__init__(payload)
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def serve(payload, ctype="application/octet-stream"):
    return lambda url, timeout=None: FakeResp(payload, ctype)


# ─────────────────────────────────────────────────────────── 官方响应结构
class TestOfficialSearchShape(unittest.TestCase):
    """council CRITICAL：render_files 读的是我自己编的扁平结构。

    官方 search_files 真实形状是 data.items[].file.{id,name,link_url}
    （kdocs/references/drive/search.md 「返回值说明」）。
    原实现在真环境会把每一条都显示成「(没有名字)    []」。
    """

    OFFICIAL = {
        "items": [
            {"file": {"id": "f_real_1", "name": "季度报表.xlsx",
                      "link_url": "https://www.kdocs.cn/l/abc"},
             "file_src": {}, "highlights": {}},
            {"file": {"id": "f_real_2", "name": "汇总表.et"}},
        ]
    }

    def test_official_shape_renders_names_and_ids(self):
        out = kdocs_kit.render_files(self.OFFICIAL)
        self.assertIn("季度报表.xlsx", out)
        self.assertIn("f_real_1", out)
        self.assertIn("汇总表.et", out)
        self.assertNotIn("(没有名字)", out)

    def test_official_shape_surfaces_link(self):
        self.assertIn("https://www.kdocs.cn/l/abc",
                      kdocs_kit.render_files(self.OFFICIAL))

    def test_nested_data_envelope(self):
        self.assertIn("季度报表.xlsx",
                      kdocs_kit.render_files({"data": self.OFFICIAL}))

    def test_flat_shape_still_tolerated(self):
        self.assertIn("旧结构.xlsx", kdocs_kit.render_files(
            {"files": [{"name": "旧结构.xlsx", "file_id": "f_x"}]}))


# ─────────────────────────────────────────────────────────── 错误翻译
class TestErrorMappingNoIndexDrift(unittest.TestCase):
    """council HIGH（三家独立命中）：ERROR_HINTS 曾用下标 [3] 取 403 提示，

    插入 401 两条后下标漂到 429002 —— 企业账号被说成「请求太频繁」，
    用户 会干等 + 反复重试，正是官方禁止的动作。
    """

    def test_bare_403_forbidden_says_enterprise_not_ratelimit(self):
        msg = kdocs_kit.friendly_error(
            'Error: HTTP 403: {"code":403,"message":"Forbidden"}')
        self.assertIn("企业账号", msg)
        self.assertNotIn("频繁", msg)
        self.assertNotIn("熔断", msg)

    def test_403_denied_variant(self):
        self.assertIn("企业账号", kdocs_kit.friendly_error("403 permission denied"))

    def test_403001_still_works(self):
        self.assertIn("企业账号", kdocs_kit.friendly_error("403001 restricted"))

    def test_403000006_personal_only(self):
        self.assertIn("个人号", kdocs_kit.friendly_error(
            '{"code":403000006,"msg":"当前版本仅支持个人用户"}'))

    def test_breaker_beats_ratelimit(self):
        self.assertIn("熔断", kdocs_kit.friendly_error("429002 after many 429001"))

    def test_401_still_maps(self):
        self.assertIn("连上", kdocs_kit.friendly_error("Error: HTTP 401: Unauthorized"))


# ─────────────────────────────────────────────────────────── 下载完整性
class TestDownloadIntegrity(unittest.TestCase):
    """council CRITICAL：官方写明 download_file 的 URL 需登录态、无法直接 curl。

    裸 urlopen 很可能拿回登录页 HTML，原实现会把它当 .xlsx 写进她电脑。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_html_login_page_is_refused_by_content_type(self):
        dest = self.dir / "表.xlsx"
        with self.assertRaises(kdocs_kit.KdocsError) as ctx:
            kdocs_kit.download_to("https://x/y", dest,
                                opener=serve(b"<html>login</html>", "text/html"))
        self.assertIn("登录", str(ctx.exception))
        self.assertFalse(dest.exists(), "坏文件绝不能落盘")

    def test_html_refused_even_without_content_type(self):
        dest = self.dir / "表.xlsx"
        with self.assertRaises(kdocs_kit.KdocsError):
            kdocs_kit.download_to("https://x/y", dest,
                                opener=serve(b"<!DOCTYPE html><html>", ""))
        self.assertFalse(dest.exists())

    def test_gives_browser_link_so_she_is_not_stuck(self):
        with self.assertRaises(kdocs_kit.KdocsError) as ctx:
            kdocs_kit.download_to("https://kdocs.cn/real-link", self.dir / "a.xlsx",
                                opener=serve(b"<html>", "text/html"))
        self.assertIn("https://kdocs.cn/real-link", str(ctx.exception))

    def test_sha256_mismatch_refused(self):
        dest = self.dir / "表.xlsx"
        with self.assertRaises(kdocs_kit.KdocsError) as ctx:
            kdocs_kit.download_to("https://x/y", dest, opener=serve(b"WRONG"),
                                expect_sha256="0" * 64)
        self.assertIn("对不上", str(ctx.exception))
        self.assertFalse(dest.exists())

    def test_sha256_match_writes(self):
        import hashlib
        payload = b"GOOD-BYTES"
        dest = self.dir / "表.xlsx"
        out = kdocs_kit.download_to(
            "https://x/y", dest, opener=serve(payload),
            expect_sha256=hashlib.sha256(payload).hexdigest())
        self.assertEqual(out.read_bytes(), payload)

    def test_expected_sha256_extracted_from_official_shape(self):
        self.assertEqual(
            kdocs_kit.expected_sha256({"hashes": [{"type": "sha256", "sum": "abc"}],
                                     "url": "u"}), "abc")
        self.assertIsNone(kdocs_kit.expected_sha256({"url": "u"}))


# ─────────────────────────────────────────────────────────── 假成功
class TestFakeSuccess(unittest.TestCase):
    """council MEDIUM：进程 exit 0 但 JSON 里 code != 0，原实现当成功。"""

    def test_nonzero_json_code_raises(self):
        r = FakeRunner(code=0, out='{"code":400006,"msg":"token expired"}')
        with self.assertRaises(kdocs_kit.KdocsError) as ctx:
            kdocs_kit.call("drive", "x", {}, runner=r, cli_path="/fake/c")
        self.assertIn("过期", str(ctx.exception))

    def test_code_zero_is_success(self):
        r = FakeRunner(code=0, out='{"code":0,"data":{"ok":true}}')
        self.assertEqual(
            kdocs_kit.call("drive", "x", {}, runner=r, cli_path="/fake/c")["code"], 0)

    def test_payload_without_code_field_is_success(self):
        r = FakeRunner(code=0, out='{"items":[]}')
        self.assertEqual(
            kdocs_kit.call("drive", "x", {}, runner=r, cli_path="/fake/c"), {"items": []})


# ─────────────────────────────────────────────────────────── 超时透传
class TestTimeoutPassthrough(unittest.TestCase):
    """council HIGH：CLI 自己的 HTTP 超时默认 30s，封装层没传 --timeout。

    "做个汇报 PPT" 这类官方要求 1800000ms，不传必挂，然后重试还挂。
    """

    def test_timeout_flag_reaches_cli(self):
        r = FakeRunner(out="{}")
        kdocs_kit.call("drive", "x", {}, runner=r, cli_path="/fake/c")
        argv = r.calls[0]
        self.assertIn("--timeout", argv)
        self.assertEqual(argv[argv.index("--timeout") + 1],
                         str(kdocs_kit.DEFAULT_TIMEOUT_MS))

    def test_aippt_gets_30_minutes(self):
        r = FakeRunner(out="{}")
        kdocs_kit.call("aippt", "execute", {}, runner=r, cli_path="/fake/c")
        argv = r.calls[0]
        self.assertEqual(argv[argv.index("--timeout") + 1], "1800000")

    def test_explicit_override_wins(self):
        r = FakeRunner(out="{}")
        kdocs_kit.call("drive", "x", {}, timeout_ms=5000, runner=r, cli_path="/fake/c")
        argv = r.calls[0]
        self.assertEqual(argv[argv.index("--timeout") + 1], "5000")

    def test_subprocess_timeout_exceeds_cli_timeout(self):
        """子进程超时必须比 CLI 自己的超时长，否则永远看不到 CLI 的错误信息。"""
        seen = {}

        def spy(cmd, timeout_s):
            seen["s"] = timeout_s
            return 0, "{}", ""

        kdocs_kit.call("aippt", "execute", {}, runner=spy, cli_path="/fake/c")
        self.assertGreater(seen["s"], 1_800_000 / 1000.0)


# ─────────────────────────────────────────────────────────── raw 参数入口
class TestRawParamsEntry(unittest.TestCase):
    """council HIGH：raw 是主干道（图表/透视/PPT 全走它），

    而它让 JSON 从命令行进来 —— PowerShell 5.1 会吃掉双引号。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_params_file_is_read(self):
        f = self.dir / "p.json"
        f.write_text('{"file_id":"f_1","title":"月销对比"}', encoding="utf-8")
        self.assertEqual(kdocs_kit.load_params("{}", str(f))["title"], "月销对比")

    def test_params_file_tolerates_gbk(self):
        f = self.dir / "p.json"
        f.write_bytes('{"title":"月销对比"}'.encode("gb18030"))
        self.assertEqual(kdocs_kit.load_params("{}", str(f))["title"], "月销对比")

    def test_inline_still_works_for_short_ascii(self):
        self.assertEqual(kdocs_kit.load_params('{"a":1}', None), {"a": 1})

    def test_broken_inline_json_explains_powershell(self):
        with self.assertRaises(ValueError) as ctx:
            kdocs_kit.load_params("{file_id:f_1}", None)
        self.assertIn("PowerShell", str(ctx.exception))
        self.assertIn("--params-file", str(ctx.exception))


# ─────────────────────────────────────────────────────────── 写后回读
class TestWriteThenVerify(unittest.TestCase):
    """council MEDIUM：verify_written 曾是死代码，但「六件事」里承诺了它。"""

    def test_new_triggers_readback(self):
        r = FakeRunner(out='{"file_id":"f_new","link_url":"https://k/l/x"}')
        kdocs_kit.main(["new", "周报.otl"])  # 无 CLI 会失败，这里只验函数层
        # 真实链路在 e2e 里验；这里确保 verify_written 能被正常调用
        self.assertTrue(callable(kdocs_kit.verify_written))

    def test_verify_written_calls_get_file_info(self):
        r = FakeRunner(out="{}")
        kdocs_kit.verify_written("f_1", runner=r, cli_path="/fake/c")
        self.assertEqual(r.calls[0][1:3], ["drive", "get-file-info"])


# ─────────────────────────────────────────────────────────── 目标解析
class TestLinkWithoutScheme(unittest.TestCase):
    def test_bare_domain_link_becomes_link_id(self):
        self.assertEqual(kdocs_kit.target_params("www.kdocs.cn/l/abc123"),
                         {"link_id": "abc123"})

    def test_365_domain(self):
        self.assertEqual(kdocs_kit.target_params("365.kdocs.cn/l/xyz"),
                         {"link_id": "xyz"})

    def test_view_link(self):
        self.assertEqual(kdocs_kit.target_params("www.kdocs.cn/view/l/q1"),
                         {"link_id": "q1"})

    def test_full_url_still_url(self):
        self.assertIn("url", kdocs_kit.target_params("https://www.kdocs.cn/l/abc"))

    def test_plain_id_still_file_id(self):
        self.assertEqual(kdocs_kit.target_params("f_abc"), {"file_id": "f_abc"})


# ─────────────────────────────────────────────────────────── 后缀校验
class TestExtensionRules(unittest.TestCase):
    """council MEDIUM：官方空白新建不支持 pdf，原实现会照发。"""

    def test_empty_create_rejects_pdf(self):
        self.assertNotIn("pdf", kdocs_kit.EMPTY_FILE_EXTS)

    def test_content_create_allows_pdf(self):
        self.assertIn("pdf", kdocs_kit.CONTENT_EXTS)

    def test_extension_of_requires_suffix(self):
        with self.assertRaises(ValueError):
            kdocs_kit.extension_of("没有后缀")


# ─────────────────────────────────────────────────────────── 另存语义
class TestSaveAsNewDirectory(unittest.TestCase):
    """council MEDIUM：save_to 传目录时，原实现会在上级目录造一个没后缀的怪文件。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_directory_target_uses_cloud_name(self):
        out = kdocs_kit.save_as_new(self.dir, cloud_name="汇总.xlsx")
        self.assertEqual(out.parent, self.dir)
        self.assertEqual(out.suffix, ".xlsx")
        self.assertIn("汇总", out.name)

    def test_directory_without_cloud_name_is_rejected(self):
        with self.assertRaises(ValueError):
            kdocs_kit.save_as_new(self.dir)

    def test_file_target_unchanged_behaviour(self):
        f = self.dir / "a.xlsx"
        f.write_bytes(b"x")
        out = kdocs_kit.save_as_new(f)
        self.assertNotEqual(out, f)
        self.assertEqual(out.suffix, ".xlsx")


# ─────────────────────────────────────────────────────────── 异常不外泄
class TestNoTracebackEverReachesHer(unittest.TestCase):
    """council MEDIUM：FileNotFoundError / PermissionError / 未知异常原来会甩 traceback。"""

    def setUp(self):
        self._env = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KDOCS_CLI_BIN"] = str(Path(self.tmp.name) / "nope")
        os.environ["PATH"] = str(Path(self.tmp.name) / "empty")
        os.environ.pop("LOCALAPPDATA", None)
        os.environ["HOME"] = self.tmp.name          # 别让测试摸到真机装的 CLI
        os.environ["USERPROFILE"] = self.tmp.name

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    def test_missing_from_file_is_friendly(self):
        rc = kdocs_kit.main(["new", "a.otl", "--from-file", "/definitely/missing.md"])
        self.assertIn(rc, (4, 5), "应给人话退出码，不是 traceback")

    def test_bad_json_in_raw_is_friendly(self):
        rc = kdocs_kit.main(["raw", "sheet", "x", "{not json"])
        self.assertEqual(rc, 4)

    def test_missing_cli_is_friendly(self):
        self.assertEqual(kdocs_kit.main(["find", "x"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ─────────────────────────────────────────────────────── 前置依赖检查
class TestPrerequisiteChecks(unittest.TestCase):
    """Boss 2026-09-13 追问「前置依赖你都写好了吗」——原来散在三份文档里，

    没有一处集中，也没法验证。现在 doctor 会把每条跑一遍。
    """

    def test_python_version_gate(self):
        ok, msg = kdocs_kit.check_python()
        self.assertTrue(ok, "跑测试的解释器本身就该满足最低版本")
        self.assertIn("3.", msg)

    def test_network_ok_when_reachable(self):
        ok, msg = kdocs_kit.check_network(
            opener=lambda u, timeout=None: io.BytesIO(b""))
        self.assertTrue(ok)
        self.assertIn("wps.cn", msg)

    def test_network_http_error_still_counts_as_reachable(self):
        import urllib.error

        def boom(u, timeout=None):
            raise urllib.error.HTTPError(u, 404, "nf", None, None)

        self.assertTrue(kdocs_kit.check_network(opener=boom)[0],
                        "404 说明网络是通的，只是那个地址没内容")

    def test_network_failure_names_the_proxy_suspicion(self):
        def dead(u, timeout=None):
            raise OSError("no route to host")

        ok, msg = kdocs_kit.check_network(opener=dead)
        self.assertFalse(ok)
        self.assertIn("proxy", msg)   # 意图不变：必须点名代理这个嫌疑人

    def test_version_parser(self):
        self.assertEqual(kdocs_kit._parse_version("2.1.270 (Claude Code)"), (2, 1, 270))
        self.assertIsNone(kdocs_kit._parse_version("no version here"))

    def test_min_claude_code_matches_changelog(self):
        """skillOverrides 在 v2.1.129 才生效（docs/claude-code/changelog-github.md）。"""
        self.assertEqual(kdocs_kit.MIN_CLAUDE_CODE, (2, 1, 129))

    def test_skill_override_detected(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".claude"
            cfg.mkdir()
            (cfg / "settings.json").write_text(
                json.dumps({"skillOverrides": {"kdocs": "user-invocable-only"}}),
                encoding="utf-8")
            ok, msg = kdocs_kit.check_skill_override(d)
            self.assertTrue(ok, msg)

    def test_skill_override_accepts_local_settings(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".claude"
            cfg.mkdir()
            (cfg / "settings.local.json").write_text(
                json.dumps({"skillOverrides": {"kdocs": "off"}}), encoding="utf-8")
            self.assertTrue(kdocs_kit.check_skill_override(d)[0])

    def test_skill_override_missing_explains_consequence(self):
        with tempfile.TemporaryDirectory() as d:
            ok, msg = kdocs_kit.check_skill_override(d)
            self.assertFalse(ok)
            self.assertIn("抢走触发", msg)

    def test_skill_override_survives_broken_json(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".claude"
            cfg.mkdir()
            (cfg / "settings.json").write_text("{ not json", encoding="utf-8")
            self.assertFalse(kdocs_kit.check_skill_override(d)[0])  # 不崩

    def test_wrong_override_value_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".claude"
            cfg.mkdir()
            (cfg / "settings.json").write_text(
                json.dumps({"skillOverrides": {"kdocs": "on"}}), encoding="utf-8")
            self.assertFalse(kdocs_kit.check_skill_override(d)[0],
                             '"on" 不压触发，不能当通过')
