#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包契约测试 —— 防文档和代码漂移。

这类 bug 不会让程序崩，只会让 agent 照着文档跑一条不存在的命令，然后说「出错了」。
公开发布还多一层：frontmatter 必须守 Agent Skills 标准，否则走 Skills API
/ package_skill.py 的用户会在上传时硬报错。
"""
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
import kdocs_kit  # noqa: E402

MANIFEST = json.loads(
    (SKILL_DIR / "requirements.json").read_text(encoding="utf-8-sig"))


def skill_md() -> str:
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def frontmatter() -> str:
    body = skill_md()
    assert body.startswith("---")
    return body.split("---", 2)[1]


class TestFrontmatter(unittest.TestCase):
    def test_has_name_and_description(self):
        self.assertTrue(skill_md().startswith("---"), "SKILL.md 必须以 YAML frontmatter 开头")
        fm = frontmatter()
        self.assertRegex(fm, r"(?m)^name:\s*\S+")
        self.assertRegex(fm, r"(?m)^description:\s*\S+")

    def test_name_matches_directory(self):
        name = re.search(r"(?m)^name:\s*(\S+)", frontmatter()).group(1)
        self.assertEqual(name, SKILL_DIR.name)

    def test_name_follows_spec_charset(self):
        """spec：1-64 字符，只许小写字母数字和连字符，不许首尾连字符或连续连字符。"""
        name = re.search(r"(?m)^name:\s*(\S+)", frontmatter()).group(1)
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(name), 64)

    def test_only_spec_frontmatter_keys(self):
        """Agent Skills 标准只认这 6 个键；多余键在上传路径会硬报错。

        反面教材：同赛道的 WPS-AirPage-Skill 自创了顶层 `requires:` / `platform:`。
        我们把机器可读的依赖放 requirements.json，frontmatter 保持合规。
        """
        allowed = {"name", "description", "license", "compatibility",
                   "metadata", "allowed-tools"}
        keys = set(re.findall(r"(?m)^([a-zA-Z-]+):", frontmatter()))
        self.assertTrue(keys <= allowed, "超出标准的 frontmatter 键：%s" % (keys - allowed))

    def test_description_within_spec_limit(self):
        desc = re.search(r"(?ms)^description:\s*(.+?)(?=^[a-z-]+:|\Z)", frontmatter())
        self.assertIsNotNone(desc)
        self.assertLessEqual(len(desc.group(1).strip()), 1024, "description 超出 spec 的 1024 上限")

    def test_declares_license(self):
        """公开发布必须声明协议，否则别人不敢用（我们自己就被这个卡过）。"""
        self.assertRegex(frontmatter(), r"(?m)^license:\s*\S+")

    def test_compatibility_names_the_hard_requirements(self):
        """spec 的 compatibility 就是环境依赖的官方声明位，别让它和清单脱节。"""
        fm = frontmatter()
        compat = re.search(r"(?ms)^compatibility:\s*(.+?)(?=^[a-z-]+:|\Z)", fm)
        self.assertIsNotNone(compat, "必须有 compatibility 字段")
        text = compat.group(1)
        self.assertLessEqual(len(text.strip()), 500, "compatibility 超出 spec 的 500 上限")
        for req in MANIFEST["requirements"]:
            if req["level"] != "required" or req["id"] == "network":
                continue
            token = {"python": "Python", "kdocs-cli": "kdocs-cli",
                     "account": "WPS"}[req["id"]]
            self.assertIn(token, text, "compatibility 没提到必需依赖：%s" % req["id"])


class TestCommandsDocumentedAreReal(unittest.TestCase):
    def setUp(self):
        parser = kdocs_kit.build_parser()
        self.real = set()
        for act in parser._actions:
            if hasattr(act, "choices") and act.choices:
                self.real |= set(act.choices)

    def test_every_documented_subcommand_exists(self):
        used = set(re.findall(r"kdocs_kit\.py\s+([a-z][a-z-]*)", skill_md()))
        used.discard("raw")  # raw 后面跟的是 service 名，另测
        unknown = sorted(used - self.real)
        self.assertEqual(unknown, [],
                         "SKILL.md 写了不存在的命令 %s（真实命令：%s）"
                         % (unknown, sorted(self.real)))

    def test_every_real_subcommand_is_documented(self):
        undocumented = sorted(c for c in self.real
                              if "kdocs_kit.py %s" % c not in skill_md())
        self.assertEqual(undocumented, [], "这些命令没写进 SKILL.md：%s" % undocumented)


class TestFirstRunIsDocumented(unittest.TestCase):
    """首次使用装依赖是这个包的卖点，文档必须真的把路指出来。"""

    def test_setup_is_the_documented_entry_point(self):
        self.assertIn("kdocs_kit.py setup", skill_md(),
                      "SKILL.md 必须告诉 agent 首次使用先跑 setup")

    def test_manifest_is_referenced(self):
        self.assertIn("requirements.json", skill_md(),
                      "SKILL.md 应指向依赖清单，用户才知道到哪看全量依赖")

    def test_license_constraint_is_disclosed(self):
        """不能让用户以为官方 skill 会被我们一并装上。"""
        doc = skill_md()
        self.assertIn("kdocs-app/kdocs-skill", doc)
        self.assertTrue(
            any(w in doc for w in ("no license", "not redistribut", "license: null")),
            "必须写明官方 skill 未声明协议、我们不转发")


class TestReferencedPathsExist(unittest.TestCase):
    def test_official_reference_paths_are_marked_optional(self):
        """公开版不再打包官方 references，路由表得写清楚它来自另一个 skill。"""
        refs = set(re.findall(r"`(\.\./kdocs/references/[A-Za-z0-9_./-]+\.md)`", skill_md()))
        self.assertTrue(refs, "SKILL.md 应给出 raw 的官方文档路由")
        official = SKILL_DIR.parent / "kdocs"
        if not official.is_dir():
            self.skipTest("官方 kdocs skill 未安装（公开版不打包它），跳过路径存在性检查")
        missing = [r for r in sorted(refs) if not (SKILL_DIR / r).resolve().is_file()]
        self.assertEqual(missing, [], "SKILL.md 指向了不存在的官方文档：%s" % missing)

    def test_live_smoke_checklist_exists(self):
        self.assertIn("tests/live_smoke.md", skill_md())
        self.assertTrue((SKILL_DIR / "tests" / "live_smoke.md").is_file())

    def test_third_party_notice_exists(self):
        notice = SKILL_DIR / "THIRD_PARTY.md"
        self.assertTrue(notice.is_file(), "公开发布必须有第三方声明")
        body = notice.read_text(encoding="utf-8")
        self.assertIn("kdocs-app/kdocs-skill", body)
        self.assertIn("kdocs-cli", body)


class TestCrossPlatformDocs(unittest.TestCase):
    def test_examples_do_not_use_python3_command(self):
        """python.org 的 Windows 安装包没有 python3.exe，示例不能假设它存在。"""
        bad = [ln.strip() for ln in skill_md().splitlines()
               if re.search(r"^\s*python3\s+\S", ln)]
        self.assertEqual(bad, [], "示例里不能直接用 python3：%s" % bad)

    def test_interpreter_note_present(self):
        self.assertIn("py -3", skill_md(), "必须告诉 agent 怎么确定 Python 命令名")


class TestLiveSmokeIsWindowsReady(unittest.TestCase):
    """冒烟清单要能在 Windows 上照抄照跑。"""

    def smoke(self) -> str:
        return (SKILL_DIR / "tests" / "live_smoke.md").read_text(encoding="utf-8")

    def test_no_bare_python3_commands(self):
        bad = [ln.strip() for ln in self.smoke().splitlines()
               if re.search(r"(?<![\w-])python3\s+[-\w.\\/]", ln)]
        self.assertEqual(bad, [], "冒烟清单不能用 python3：%s" % bad)

    def test_no_posix_env_prefix(self):
        bad = [ln.strip() for ln in self.smoke().splitlines()
               if re.search(r"^\s*\|?\s*\d*\s*\|?\s*`?[A-Z_]+=\S+\s+python", ln)]
        self.assertEqual(bad, [], "PowerShell 不认 VAR=x cmd 写法：%s" % bad)

    def test_uses_powershell_env_syntax(self):
        self.assertIn("$env:KDOCS_E2E_LIVE", self.smoke())

    def test_covers_the_three_council_bugs(self):
        body = self.smoke()
        for must in ("没有名字", "浏览器里点", "请求太频繁"):
            self.assertIn(must, body, "冒烟清单必须验收 council 抓到的真 bug：%s" % must)


class TestCountsDoNotDrift(unittest.TestCase):
    """文档里写的测试条数 / 冒烟条数必须跟真实数量一致。

    council 抓到过三处互相矛盾的数字（67 / 94 / 148）。靠人手改必然漂，让测试自己核对。
    """

    def actual_test_count(self) -> int:
        out = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=str(SKILL_DIR), capture_output=True, encoding="utf-8", timeout=600)
        m = re.search(r"Ran (\d+) tests", (out.stdout or "") + (out.stderr or ""))
        self.assertIsNotNone(m, "数不出测试数量")
        return int(m.group(1))

    def test_skill_md_count_matches_reality(self):
        if os.environ.get("SKIP_COUNT_SELFCHECK"):
            self.skipTest("避免递归调用")
        os.environ["SKIP_COUNT_SELFCHECK"] = "1"
        try:
            actual = self.actual_test_count()
        finally:
            os.environ.pop("SKIP_COUNT_SELFCHECK", None)
        claimed = re.search(r"(\d+) tests, all green", skill_md())
        self.assertIsNotNone(claimed, "SKILL.md 应写明测试条数")
        self.assertEqual(int(claimed.group(1)), actual,
                         "SKILL.md 写的测试数跟实际对不上")

    def test_smoke_count_matches_reality(self):
        smoke = (SKILL_DIR / "tests" / "live_smoke.md").read_text(encoding="utf-8")
        rows = len(re.findall(r"(?m)^\| \d+ \|", smoke))
        claimed = re.search(r"(\d+) manual checks", skill_md())
        self.assertIsNotNone(claimed, "SKILL.md 应写明冒烟条数")
        self.assertEqual(int(claimed.group(1)), rows, "冒烟条数对不上")


if __name__ == "__main__":
    unittest.main(verbosity=2)
