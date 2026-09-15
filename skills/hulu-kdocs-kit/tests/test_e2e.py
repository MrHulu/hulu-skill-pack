#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kdocs_kit.py 端到端测试 —— 真的起子进程跑命令行，对着一个假 kdocs-cli。

为什么用替身而不是真账号：真调用需要 用户 的个人金山 Token，谁都不该把它放进测试。
替身严格照官方 CLI 的契约（--file 收参、stdout 出 JSON、非零 exit 表示失败），
所以这里验的是【我们这层】的全链路：argparse → 子进程 → 临时文件 → 编码 → 输出。

真账号的联通验证另有 tests/live_smoke.md 清单，首次在她机器上人工跑一次。

跑法：python3 tests/test_e2e.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KDOCS_KIT = HERE.parent / "scripts" / "kdocs_kit.py"
IS_WINDOWS = os.name == "nt"

# 替身 CLI 的行为由这个 JSON 驱动（脚本每次调用都重读，方便逐用例改）
STUB_PY = r'''
import json, sys, os
cfg_path = os.environ["STUB_CFG"]
cfg = json.load(open(cfg_path, encoding="utf-8"))
# 把收到的 argv 和 --file 内容录下来，测试用来断言"中文没进命令行"
rec = {"argv": sys.argv[1:], "payload": None}
if "--file" in sys.argv:
    p = sys.argv[sys.argv.index("--file") + 1]
    rec["payload"] = json.load(open(p, encoding="utf-8"))
with open(os.environ["STUB_REC"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
sys.stdout.write(cfg.get("stdout", ""))
sys.stderr.write(cfg.get("stderr", ""))
sys.exit(cfg.get("code", 0))
'''


def make_stub(dirpath: Path) -> Path:
    """造一个当前平台真能执行的假 kdocs-cli。"""
    py = dirpath / "stub_impl.py"
    py.write_text(STUB_PY, encoding="utf-8")
    if IS_WINDOWS:
        # Windows 不能直接 exec .py，包一层 .bat
        bat = dirpath / "kdocs-cli.bat"
        # cmd.exe 按 OEM 代码页读批处理，不是 UTF-8。中文用户名（python.org 按用户安装
        # 就在用户目录下）或中文 TEMP 会让 UTF-8 写的路径解错，替身直接跑不起来。
        content = '@echo off\r\nchcp 65001 >nul\r\n"%s" "%s" %%*\r\n' % (sys.executable, py)
        try:
            bat.write_bytes(content.encode("oem"))
        except (LookupError, UnicodeEncodeError):
            bat.write_bytes(content.encode("utf-8"))
        return bat
    sh = dirpath / "kdocs-cli"
    sh.write_text("#!/bin/sh\nexec %s %s \"$@\"\n" % (sys.executable, py),
                  encoding="utf-8")
    sh.chmod(0o755)
    return sh


class E2EBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.stub = make_stub(self.dir)
        self.cfg = self.dir / "cfg.json"
        self.rec = self.dir / "rec.json"
        self.set_stub()

    def tearDown(self):
        self.tmp.cleanup()

    def set_stub(self, stdout="", stderr="", code=0):
        self.cfg.write_text(json.dumps(
            {"stdout": stdout, "stderr": stderr, "code": code}), encoding="utf-8")

    def run_kit(self, args, with_cli=True, cwd=None):
        env = dict(os.environ)
        env["STUB_CFG"] = str(self.cfg)
        env["STUB_REC"] = str(self.rec)
        env["PYTHONIOENCODING"] = "utf-8"
        # 隔离 HOME：否则「没装 CLI」的用例会摸到开发机上真装的 ~/.local/bin/kdocs-cli
        env["HOME"] = str(self.dir)
        env["USERPROFILE"] = str(self.dir)
        if with_cli:
            env["KDOCS_CLI_BIN"] = str(self.stub)
        else:
            env["KDOCS_CLI_BIN"] = str(self.dir / "nope")
            env["PATH"] = str(self.dir / "empty")
            env.pop("LOCALAPPDATA", None)
        proc = subprocess.run(
            [sys.executable, str(KDOCS_KIT)] + args,
            capture_output=True, env=env, timeout=60, cwd=(str(cwd) if cwd else None),
            encoding="utf-8", errors="replace")
        return proc

    def all_calls(self):
        """替身收到的每一次调用，按顺序。"""
        if not self.rec.exists():
            return []
        return [json.loads(ln) for ln in
                self.rec.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def recorded(self):
        """第一次调用（业务动作本身；写后回读是第二次）。"""
        calls = self.all_calls()
        if not calls:
            raise AssertionError("替身一次都没被调用")
        return calls[0]


def _online() -> bool:
    """有没有外网。doctor 的「全绿」判定包含一次真实连通性探测，
    锁网 / 离线机器上那条 e2e 会假红（2026-09-14 验证 F5）。"""
    try:
        import urllib.request
        urllib.request.urlopen("https://api.wps.cn/", timeout=8).close()
        return True
    except Exception:
        import urllib.error
        return isinstance(sys.exc_info()[1], urllib.error.HTTPError)


class TestDoctor(E2EBase):
    def test_missing_cli_exits_1_with_install_hint(self):
        p = self.run_kit(["doctor"], with_cli=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("setup", p.stdout)

    def test_present_but_not_authed_exits_2(self):
        self.set_stub(stdout='{"authenticated": false, "token": ""}')
        p = self.run_kit(["doctor"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("not signed in", p.stdout)

    def test_authed_but_missing_prereq_exits_6(self):
        """连上了但前置依赖有缺（如没压住官方技能）→ 能用，但要报出来。"""
        self.set_stub(stdout='{"authenticated": true, "token": "x"}')
        # 造出真实场景：官方 skill 装了（项目级），但 skillOverrides 没配。
        # 官方 skill 不在时 skillOverrides 属 N/A，退 6 这条路根本走不到。
        official = self.dir / ".claude" / "skills" / "kdocs" / "references"
        official.mkdir(parents=True, exist_ok=True)
        (official / "sheet.md").write_text("# stub", encoding="utf-8")
        p = self.run_kit(["doctor"], cwd=self.dir)
        self.assertEqual(p.returncode, 6, p.stdout)
        self.assertIn("signed in to WPS", p.stdout)
        self.assertIn("steal the trigger", p.stdout)

    @unittest.skipUnless(_online(), "这台机器连不上金山，doctor 不可能全绿")
    def test_all_green_exits_0(self):
        self.set_stub(stdout='{"authenticated": true, "token": "x"}')
        cfg = self.dir / ".claude"
        cfg.mkdir(exist_ok=True)
        (cfg / "settings.json").write_text(
            json.dumps({"skillOverrides": {"kdocs": "user-invocable-only"}}),
            encoding="utf-8")
        p = self.run_kit(["doctor"], cwd=self.dir)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("All set", p.stdout)

    def test_doctor_lists_every_prerequisite(self):
        self.set_stub(stdout='{"authenticated": true, "token": "x"}')
        p = self.run_kit(["doctor"], cwd=self.dir)
        # 不写死字符串：直接拿 requirements.json 的标题逐条核对。
        # 清单里新增一项而 doctor 漏报，这里立刻红 —— 比原来的硬编码清单强。
        import json as _json
        manifest = _json.loads(
            (HERE.parent / "requirements.json").read_text(encoding="utf-8"))
        for req in manifest["requirements"]:
            self.assertIn(req["title"], p.stdout,
                          "doctor 漏报了前置依赖：%s" % req["id"])


class TestFind(E2EBase):
    def test_lists_results_in_plain_language(self):
        self.set_stub(stdout=json.dumps(
            {"files": [{"name": "季度报表.xlsx", "file_id": "f_1"},
                       {"name": "汇总表.et", "file_id": "f_2"}]}))
        p = self.run_kit(["find", "周报"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("季度报表.xlsx", p.stdout)
        self.assertIn("f_2", p.stdout)

    def test_empty_result_is_friendly(self):
        self.set_stub(stdout='{"files": []}')
        p = self.run_kit(["find", "不存在的东西"])
        self.assertEqual(p.returncode, 0)
        self.assertIn("没找到", p.stdout)

    def test_chinese_keyword_never_reaches_argv(self):
        """跨平台核心回归：中文只能走 --file，不能进命令行。"""
        self.set_stub(stdout='{"files": []}')
        self.run_kit(["find", "季度报表"])
        rec = self.recorded()
        self.assertNotIn("季度报表", " ".join(rec["argv"]))
        self.assertEqual(rec["payload"]["keyword"], "季度报表")

    def test_chinese_survives_roundtrip_unmangled(self):
        self.set_stub(stdout='{"files": []}')
        self.run_kit(["find", "测试·中文 空格"])
        self.assertEqual(self.recorded()["payload"]["keyword"], "测试·中文 空格")


class TestErrorPaths(E2EBase):
    def test_expired_token_exits_3_with_human_message(self):
        self.set_stub(stderr="request failed: 400006 token expired", code=1)
        p = self.run_kit(["find", "x"])
        self.assertEqual(p.returncode, 3)
        self.assertIn("过期", p.stdout)
        self.assertNotIn("400006", p.stdout, "错误码不该甩给 用户")

    def test_rate_limit_message(self):
        self.set_stub(stderr="429001 rate limited", code=1)
        p = self.run_kit(["find", "x"])
        self.assertEqual(p.returncode, 3)
        self.assertIn("歇一会儿", p.stdout)

    def test_enterprise_account_message(self):
        self.set_stub(stderr="403001 enterprise account restricted", code=1)
        p = self.run_kit(["find", "x"])
        self.assertIn("企业账号", p.stdout)

    def test_unknown_error_preserves_raw_for_debugging(self):
        self.set_stub(stderr="backend exploded #77219", code=1)
        p = self.run_kit(["find", "x"])
        self.assertIn("77219", p.stdout, "没见过的错误必须保留原文，不能吞")

    def test_empty_target_rejected_exit_4(self):
        self.set_stub(stdout="{}")
        p = self.run_kit(["read", "   "])
        self.assertEqual(p.returncode, 4)


class TestUpload(E2EBase):
    def test_local_file_roundtrip(self):
        f = self.dir / "汇总表.xlsx"
        raw = bytes(range(256)) * 4
        f.write_bytes(raw)
        self.set_stub(stdout='{"file_id": "f_new", "link_url": "https://kdocs.cn/l/x"}')
        p = self.run_kit(["upload", str(f)])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        import base64
        sent = self.recorded()["payload"]
        self.assertEqual(base64.b64decode(sent["content_base64"]), raw)
        self.assertEqual(sent["name"], "汇总表.xlsx")

    def test_bad_suffix_exits_4_without_calling_cli(self):
        f = self.dir / "x.sketch"
        f.write_bytes(b"x")
        p = self.run_kit(["upload", str(f)])
        self.assertEqual(p.returncode, 4)
        self.assertFalse(self.rec.exists(), "格式不对不该真去调 CLI")

    def test_missing_file_exits_4(self):
        p = self.run_kit(["upload", str(self.dir / "nope.xlsx")])
        self.assertEqual(p.returncode, 4)


class TestTargetResolution(E2EBase):
    def test_link_becomes_url_param(self):
        self.set_stub(stdout="{}")
        self.run_kit(["share", "https://www.kdocs.cn/l/abc", "--scope", "anyone"])
        self.assertEqual(self.recorded()["payload"]["url"],
                         "https://www.kdocs.cn/l/abc")

    def test_bare_id_becomes_file_id_param(self):
        self.set_stub(stdout="{}")
        self.run_kit(["share", "f_abc", "--scope", "anyone"])
        self.assertEqual(self.recorded()["payload"]["file_id"], "f_abc")

    def test_comments_sends_root_origin_id(self):
        self.set_stub(stdout="{}")
        self.run_kit(["comments", "f_abc"])
        self.assertEqual(self.recorded()["payload"]["origin_id"], "0")


class TestRawPassthrough(E2EBase):
    def test_raw_forwards_service_action_and_params(self):
        self.set_stub(stdout='{"ok": true}')
        p = self.run_kit(["raw", "sheet", "add-chart",
                              '{"file_id":"f_1","title":"月销对比"}'])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rec = self.recorded()
        self.assertEqual(rec["argv"][0], "sheet")
        self.assertEqual(rec["argv"][1], "add-chart")
        self.assertEqual(rec["payload"]["title"], "月销对比")
        self.assertNotIn("月销对比", " ".join(rec["argv"]))


class TestRequiredParamsRegression(E2EBase):
    """这 6 条对应 2026-09-13 拿真二进制核对时抓到的 6 个 bug。

    根因：替身 CLI 什么都不校验，所以 67 个测试全绿但 find/new/share 在真环境全炸。
    """

    def test_find_sends_required_page_size(self):
        self.set_stub(stdout='{"files": []}')
        self.run_kit(["find", "x"])
        self.assertIn("page_size", self.recorded()["payload"],
                      "search-files 的 page_size 是必填")

    def test_share_requires_explicit_scope(self):
        """默认公开分享是泄密风险（报表/汇总），必须让她显式选。"""
        self.set_stub(stdout="{}")
        p = self.run_kit(["share", "f_1"])
        self.assertNotEqual(p.returncode, 0, "没给 --scope 就不该放行")
        self.assertFalse(self.rec.exists(), "参数不全时不该真去调 CLI")

    def test_share_scope_passed_through(self):
        self.set_stub(stdout="{}")
        self.run_kit(["share", "f_1", "--scope", "anyone"])
        self.assertEqual(self.recorded()["payload"]["scope"], "anyone")

    def test_share_scope_is_overridable_and_disclosed(self):
        self.set_stub(stdout="{}")
        p = self.run_kit(["share", "f_1", "--scope", "company"])
        self.assertEqual(self.recorded()["payload"]["scope"], "company")
        self.assertIn("公司内部", p.stdout, "开放范围必须让 用户 看见")

    def test_new_empty_sends_required_file_extension(self):
        self.set_stub(stdout="{}")
        self.run_kit(["new", "周报.otl"])
        self.assertEqual(self.recorded()["payload"]["file_extension"], "otl")

    def test_new_with_content_sends_extension_and_content(self):
        md = self.dir / "body.md"
        md.write_text("# 标题\n正文", encoding="utf-8")
        self.set_stub(stdout="{}")
        self.run_kit(["new", "周报.otl", "--from-file", str(md)])
        payload = self.recorded()["payload"]
        self.assertEqual(payload["file_extension"], "otl")
        self.assertIn("正文", payload["content"])
        self.assertNotIn("format", payload, "format 不是官方参数，不该再出现")

    def test_new_without_suffix_rejected(self):
        p = self.run_kit(["new", "没有后缀"])
        self.assertEqual(p.returncode, 4)
        self.assertIn("后缀", p.stdout)

    def test_new_content_on_unsupported_ext_rejected(self):
        md = self.dir / "b.md"
        md.write_text("x", encoding="utf-8")
        p = self.run_kit(["new", "表.xlsx", "--from-file", str(md)])
        self.assertEqual(p.returncode, 4)

    def test_http_401_is_translated(self):
        self.set_stub(stderr='Error: HTTP 401: {"code":401,"message":"Unauthorized"}',
                      code=1)
        p = self.run_kit(["find", "x"])
        self.assertIn("连上", p.stdout)
        self.assertNotIn("401", p.stdout, "HTTP 码不该甩给 用户")


class TestDownloadActuallyWrites(E2EBase):
    """曾经的 CRITICAL：download 只打印链接、不落盘，却告诉用户已保存。"""

    def _serve(self, payload: bytes):
        import io
        import contextlib

        class FakeResp(io.BytesIO):
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

        return lambda url, timeout=None: FakeResp(payload)

    def test_file_is_really_written(self):
        # 走库函数，避免子进程里没法注入 opener
        sys.path.insert(0, str(KDOCS_KIT.parent))
        import kdocs_kit
        dest = self.dir / "下载的表.xlsx"
        out = kdocs_kit.download_to("https://example.com/x", dest,
                                  opener=self._serve(b"REAL-BYTES"))
        self.assertTrue(out.exists(), "文件没真的落盘")
        self.assertEqual(out.read_bytes(), b"REAL-BYTES")

    def test_original_is_never_overwritten(self):
        sys.path.insert(0, str(KDOCS_KIT.parent))
        import kdocs_kit
        orig = self.dir / "汇总.xlsx"
        orig.write_bytes(b"ORIGINAL")
        dest = kdocs_kit.save_as_new(orig)
        kdocs_kit.download_to("https://example.com/x", dest,
                            opener=self._serve(b"NEW"))
        self.assertEqual(orig.read_bytes(), b"ORIGINAL", "原件被覆盖了")
        self.assertEqual(dest.read_bytes(), b"NEW")

    def test_online_native_doc_without_url_gives_clear_message(self):
        sys.path.insert(0, str(KDOCS_KIT.parent))
        import kdocs_kit
        with self.assertRaises(kdocs_kit.KdocsError) as ctx:
            kdocs_kit.pick_download_url({"hashes": []})
        self.assertIn("otl", str(ctx.exception))

    def test_url_picked_from_nested_data(self):
        sys.path.insert(0, str(KDOCS_KIT.parent))
        import kdocs_kit
        self.assertEqual(
            kdocs_kit.pick_download_url({"data": {"url": "https://x/y"}}),
            "https://x/y")


class TestContractAgainstRealBinary(unittest.TestCase):
    """契约测试：拿【真的 kdocs-cli】的 --help 解析必填参数，比对我们实际发的 payload。

    这是 6 个 bug 的根本防线 —— 替身永远不会告诉你官方改了必填项。
    本机没装 kdocs-cli 就跳过；用户 机器上装了，就会真的跑。
    """

    ACTIONS = {
        ("drive", "search-files"): ["find", "x"],
        ("drive", "share-file"): ["share", "f_1", "--scope", "anyone"],
        ("drive", "create-empty-file"): ["new", "a.otl"],
        ("drive", "list-document-comments"): ["comments", "f_1"],
        ("drive", "read-file"): ["read", "f_1"],
        ("drive", "download-file"): ["download", "f_1", "OUT.xlsx"],
        ("drive", "upload-new-file"): ["upload", "UPLOAD.xlsx"],
        ("drive", "create-file-with-content"): ["new", "a.otl", "--from-file", "BODY.md"],
    }

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(KDOCS_KIT.parent))
        import kdocs_kit
        try:
            cls.cli = str(kdocs_kit.find_cli())
        except Exception:
            cls.cli = None

    def required_params(self, service, action):
        """解析官方 --help 的必填参数。

        实测：kdocs-cli 的 --help 写到 **stderr**，不是 stdout。
        第一版只读 stdout，结果必填清单恒为空、契约测试恒绿（假绿）——
        是变异测试（故意撤掉修复看测试红不红）把它揪出来的。所以这里两路都读，
        并且解析不到参数区就直接判失败，绝不静默放行。
        """
        proc = subprocess.run([self.cli, service, action, "--help"],
                              capture_output=True, encoding="utf-8", timeout=30)
        help_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if "Parameters:" not in help_text:
            self.fail("解析不到 %s %s 的参数区，契约测试自身坏了：%r"
                      % (service, action, help_text[:200]))
        req, seen_any = [], False
        for line in help_text.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1] in ("string", "integer", "boolean",
                                                "number", "array[object]",
                                                "array[string]", "object"):
                seen_any = True
                if parts[2] == "required":
                    req.append(parts[0])
        if not seen_any:
            self.fail("%s %s 一个参数都没解析出来，解析器坏了" % (service, action))
        return req

    def test_every_wrapped_action_sends_all_required_params(self):
        if not self.cli:
            self.skipTest("本机没装 kdocs-cli，跳过契约测试")
        harness = E2EBase("run")
        for (service, action), argv in self.ACTIONS.items():
            with self.subTest(action="%s.%s" % (service, action)):
                harness.setUp()
                try:
                    harness.set_stub(stdout="{}")
                    real_argv = []
                    for a in argv:
                        if a == "UPLOAD.xlsx":
                            f = harness.dir / "UPLOAD.xlsx"
                            f.write_bytes(b"x")
                            a = str(f)
                        elif a == "BODY.md":
                            f = harness.dir / "BODY.md"
                            f.write_text("正文", encoding="utf-8")
                            a = str(f)
                        elif a == "OUT.xlsx":
                            a = str(harness.dir / "OUT.xlsx")
                        real_argv.append(a)
                    harness.run_kit(real_argv)
                    sent = set(harness.recorded()["payload"].keys())
                finally:
                    harness.tearDown()
                missing = [r for r in self.required_params(service, action)
                           if r not in sent]
                self.assertEqual(missing, [],
                                 "%s.%s 缺必填参数 %s（我们发的是 %s）"
                                 % (service, action, missing, sorted(sent)))


class TestLiveSmoke(unittest.TestCase):
    """真账号联通测试。默认跳过；在她机器上装完后设 KDOCS_E2E_LIVE=1 跑一次。"""

    @unittest.skipUnless(os.environ.get("KDOCS_E2E_LIVE") == "1",
                         "需要真实金山账号，设 KDOCS_E2E_LIVE=1 才跑")
    def test_real_doctor_reports_authenticated(self):
        proc = subprocess.run([sys.executable, str(KDOCS_KIT), "doctor"],
                              capture_output=True, encoding="utf-8", timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("已经连上", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
