#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""requirements.json 契约测试 —— 清单是 doctor 和 setup 的唯一真源。

这份清单一旦和代码漂移，症状很难看：doctor 报缺 X，setup 却不装 X，
或者反过来「装完了立刻说没装」。所以漂移必须由测试挡住，不靠人记得。
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
import kdocs_kit  # noqa: E402

MANIFEST = json.loads(
    (SKILL_DIR / "requirements.json").read_text(encoding="utf-8-sig"))
REQS = MANIFEST["requirements"]


class TestManifestShape(unittest.TestCase):
    def test_schema_and_pins(self):
        self.assertEqual(MANIFEST["schema"], "hulu-skill-requirements/v1")
        self.assertEqual(MANIFEST["skill"], SKILL_DIR.name)
        self.assertIn("kdocs_cli_version", MANIFEST["pins"])

    def test_every_entry_has_the_required_fields(self):
        for r in REQS:
            for field in ("id", "title", "kind", "level", "why", "check", "install"):
                self.assertIn(field, r, "requirement %r 少了 %s" % (r.get("id"), field))
            self.assertIn(r["level"], ("required", "recommended", "optional"))
            self.assertTrue(r["why"].strip(), "%s 的 why 不能空" % r["id"])

    def test_ids_are_unique(self):
        ids = [r["id"] for r in REQS]
        self.assertEqual(len(ids), len(set(ids)), "requirement id 重复：%s" % ids)

    def test_pin_placeholders_all_resolve(self):
        """清单里写的 {占位符} 必须都能在 pins 里找到，否则会把字面量发给用户。"""
        pins = MANIFEST["pins"]
        blob = json.dumps(REQS, ensure_ascii=False)
        used = set(re.findall(r"\{([a-z_]+)\}", blob))
        unknown = sorted(used - set(pins))
        self.assertEqual(unknown, [], "清单用了 pins 里没有的占位符：%s" % unknown)


class TestNoDriftBetweenManifestAndCode(unittest.TestCase):
    """这组是本文件的心脏：清单说什么，代码就得有什么。"""

    def test_every_check_type_has_a_handler(self):
        missing = sorted({r["check"]["type"] for r in REQS} - set(kdocs_kit.CHECKS))
        self.assertEqual(missing, [],
                         "清单要求的 check 类型没有实现：%s（doctor 会静默跳过它）" % missing)

    def test_every_auto_install_has_an_installer(self):
        need = {r["install"]["method"] for r in REQS
                if r["install"].get("auto")}
        missing = sorted(need - set(kdocs_kit.INSTALLERS))
        self.assertEqual(missing, [],
                         "清单标了 auto 安装但没有对应 installer：%s" % missing)

    def test_manual_entries_tell_the_user_what_to_do(self):
        for r in REQS:
            inst = r["install"]
            if inst.get("auto"):
                continue
            man = inst.get("manual") or {}
            self.assertTrue(man.get("all") or man.get(kdocs_kit._os_key()),
                            "%s 不能自动装，就必须给出手动步骤" % r["id"])

    def test_doctor_covers_every_requirement(self):
        """doctor 的输出必须每条都露面，不许悄悄漏掉一项。"""
        results = kdocs_kit.evaluate(MANIFEST)
        self.assertEqual([r["id"] for r, _s, _m in results], [r["id"] for r in REQS])


class TestLicenseInvariant(unittest.TestCase):
    """官方 skill 无协议声明，绝不能被打包转发 —— 这是法务不变量，值得一条测试。"""

    def test_official_skill_is_marked_not_redistributable(self):
        entry = next(r for r in REQS if r["id"] == "official-skill")
        self.assertIs(entry["install"].get("redistributable"), False)
        self.assertFalse(entry["install"].get("auto"),
                         "官方 skill 不允许我们代为拉取安装")

    def test_official_skill_files_are_not_vendored(self):
        vendored = [p for p in SKILL_DIR.rglob("*")
                    if p.is_file() and "kdocs/references" in p.as_posix()]
        self.assertEqual(vendored, [], "仓库里不得出现官方 skill 的文件副本")


class TestCliDiscovery(unittest.TestCase):
    """2026-09-15 沙箱实测：setup 装完紧接着复查说「没装」。"""

    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("KDOCS_CLI_DIR", "KDOCS_CLI_BIN")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_official_install_dir_env_is_probed(self):
        """KDOCS_CLI_DIR 是官方安装器自己的目录覆盖变量（setup.sh / setup.ps1 同名）。

        不认它 => 用户一改安装目录，我们就找不到自己刚装的东西。
        """
        os.environ["KDOCS_CLI_DIR"] = os.path.join(os.sep, "opt", "kdocs")
        names = {p.name for p in kdocs_kit.candidate_paths()}
        parents = {p.parent.as_posix() for p in kdocs_kit.candidate_paths()}
        self.assertIn("kdocs-cli", names)
        self.assertTrue(any(p.endswith("/opt/kdocs") for p in parents),
                        "KDOCS_CLI_DIR 指的目录没被探测：%s" % sorted(parents))

    def test_explicit_binary_still_wins_over_dir(self):
        os.environ["KDOCS_CLI_BIN"] = os.path.join(os.sep, "custom", "kdocs-cli")
        os.environ["KDOCS_CLI_DIR"] = os.path.join(os.sep, "opt", "kdocs")
        first = kdocs_kit.candidate_paths()[0]
        self.assertEqual(first.as_posix(), "/custom/kdocs-cli")


class TestMergeSettingsInstaller(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        # 不用 .claude/settings.json 这个真名：本仓的 guard hook 保护该路径。
        # 被测逻辑读的是清单里的 target 字段，换个文件名走的是同一段代码。
        self.target = "config-under-test.json"

    def tearDown(self):
        import shutil
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _req(self):
        return {"id": "skill-override",
                "install": {"target": self.target,
                            "patch": {"skillOverrides": {"kdocs": "user-invocable-only"}}}}

    def test_creates_file_when_absent(self):
        self.assertTrue(kdocs_kit.install_merge_settings(self._req(), {}))
        data = json.loads(Path(self.target).read_text(encoding="utf-8"))
        self.assertEqual(data["skillOverrides"]["kdocs"], "user-invocable-only")

    def test_preserves_existing_keys_and_siblings(self):
        Path(self.target).write_text(json.dumps(
            {"permissions": {"allow": ["Bash(ls:*)"]},
             "skillOverrides": {"other-skill": "off"}},
            ensure_ascii=False), encoding="utf-8")
        kdocs_kit.install_merge_settings(self._req(), {})
        data = json.loads(Path(self.target).read_text(encoding="utf-8"))
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls:*)"]})
        self.assertEqual(data["skillOverrides"]["other-skill"], "off")
        self.assertEqual(data["skillOverrides"]["kdocs"], "user-invocable-only")

    def test_keeps_cjk_unescaped(self):
        """PowerShell 的 ConvertTo-Json 会把中文变 \\uXXXX；我们用 Python 就是为了躲这个。"""
        Path(self.target).write_text(json.dumps({"备注": "中文必须原样存盘"},
                                                ensure_ascii=False), encoding="utf-8")
        kdocs_kit.install_merge_settings(self._req(), {})
        raw = Path(self.target).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\\u[0-9a-fA-F]{4}", raw), "中文被转义了")
        self.assertIn("中文必须原样存盘", raw)

    def test_backs_up_before_writing(self):
        Path(self.target).write_text('{"a": 1}', encoding="utf-8")
        kdocs_kit.install_merge_settings(self._req(), {})
        self.assertTrue(Path(self.target + ".bak").is_file())

    def test_refuses_to_touch_broken_json(self):
        Path(self.target).write_text("{ not json", encoding="utf-8")
        self.assertFalse(kdocs_kit.install_merge_settings(self._req(), {}))
        self.assertEqual(Path(self.target).read_text(encoding="utf-8"), "{ not json")


class TestUpstreamScriptInstaller(unittest.TestCase):
    def test_dry_run_downloads_nothing(self):
        req = next(r for r in REQS if r["id"] == "kdocs-cli")
        calls = []
        real = kdocs_kit.urllib.request.urlopen
        kdocs_kit.urllib.request.urlopen = lambda *a, **k: calls.append(a) # noqa: E731
        try:
            ok = kdocs_kit.install_upstream_script(req, MANIFEST["pins"], dry_run=True)
        finally:
            kdocs_kit.urllib.request.urlopen = real
        self.assertTrue(ok)
        self.assertEqual(calls, [], "dry-run 不许发起任何下载")

    def test_source_url_points_at_the_official_repo(self):
        req = next(r for r in REQS if r["id"] == "kdocs-cli")
        for key in ("source", "source_windows"):
            url = kdocs_kit.expand_pins(req["install"][key], MANIFEST["pins"])
            self.assertTrue(url.startswith("https://"), url)
            self.assertIn("kdocs-app/kdocs-skill", url,
                          "安装脚本必须来自版权方自己的仓库")
            self.assertNotIn("{", url, "占位符没展开：%s" % url)

    def test_pinned_version_is_passed_to_upstream(self):
        req = next(r for r in REQS if r["id"] == "kdocs-cli")
        env = {k: kdocs_kit.expand_pins(v, MANIFEST["pins"])
               for k, v in req["install"]["env"].items()}
        self.assertEqual(env["KDOCS_CLI_VERSION"], MANIFEST["pins"]["kdocs_cli_version"])
        self.assertRegex(env["KDOCS_CLI_VERSION"], r"^\d+\.\d+\.\d+$")


class TestInstallerIsPinnedAndVerified(unittest.TestCase):
    """我们下载并**执行**上游脚本 —— 这是整个包唯一的远程代码执行点。

    上游自己会校验二进制的 SHA-256，但没人校验脚本本身。不补上这一跳，
    整条供应链最弱的环节就是我们加的这一环。
    """

    def _req(self):
        return next(r for r in REQS if r["id"] == "kdocs-cli")

    def test_ref_is_a_commit_not_a_branch(self):
        ref = MANIFEST["pins"]["official_skill_ref"]
        self.assertRegex(ref, r"^[0-9a-f]{40}$",
                         "要执行的脚本必须钉在 commit 上；分支是移动靶，"
                         "同一个 URL 明天可能是另一段代码")

    def test_both_platform_scripts_have_a_pinned_hash(self):
        v = self._req()["install"].get("verify") or {}
        for key in ("sha256", "sha256_windows"):
            self.assertRegex(v.get(key, ""), r"^[0-9a-f]{64}$",
                             "%s 缺少合法的 SHA-256" % key)

    def test_tampered_script_is_refused_and_never_executed(self):
        """核心断言：校验不过就**不许执行**。"""
        req = self._req()
        ran = []

        class _Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return b"#!/bin/sh\necho pwned\n"      # 不是我们钉的那份

        real_open, real_run = kdocs_kit.urllib.request.urlopen, kdocs_kit.subprocess.run
        kdocs_kit.urllib.request.urlopen = lambda *a, **k: _Resp()
        kdocs_kit.subprocess.run = lambda *a, **k: ran.append(a) or None
        try:
            ok = kdocs_kit.install_upstream_script(req, MANIFEST["pins"])
        finally:
            kdocs_kit.urllib.request.urlopen = real_open
            kdocs_kit.subprocess.run = real_run

        self.assertFalse(ok, "校验失败必须返回失败")
        self.assertEqual(ran, [], "校验失败后绝不允许起子进程执行它")

    def test_matching_script_is_allowed_to_run(self):
        """反向：字节对得上就该放行，否则这道闸等于把功能焊死。"""
        import hashlib
        req = self._req()
        body = b"#!/bin/sh\nexit 0\n"
        digest = hashlib.sha256(body).hexdigest()
        patched = json.loads(json.dumps(req))
        key = "sha256_windows" if os.name == "nt" else "sha256"
        patched["install"]["verify"] = {key: digest}
        ran = []

        class _Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return body

        class _Proc:
            returncode = 0

        real_open, real_run = kdocs_kit.urllib.request.urlopen, kdocs_kit.subprocess.run
        kdocs_kit.urllib.request.urlopen = lambda *a, **k: _Resp()
        kdocs_kit.subprocess.run = lambda *a, **k: (ran.append(a), _Proc())[1]
        try:
            ok = kdocs_kit.install_upstream_script(patched, MANIFEST["pins"])
        finally:
            kdocs_kit.urllib.request.urlopen = real_open
            kdocs_kit.subprocess.run = real_run

        self.assertTrue(ok)
        self.assertEqual(len(ran), 1, "字节对得上就应该真的执行")


if __name__ == "__main__":
    unittest.main(verbosity=2)
