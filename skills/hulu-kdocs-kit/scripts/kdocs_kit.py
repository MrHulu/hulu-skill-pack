#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hulu-kdocs-kit —— 金山文档官方 CLI (kdocs-cli) 的封装层。

封装层只补官方 CLI 故意留给 agent 的坑，**不重复包装 209 个动作**：

  1. 跨机器找到 kdocs-cli（PATH / Windows LOCALAPPDATA / POSIX ~/.local/bin / 环境变量）
  2. 参数一律走 --file 临时文件（官方明令：中文/多行/长内容走命令行会被 PowerShell 破坏编码）
     —— 包括 raw 透传，因为它是主干道不是逃生舱
  3. 把 --timeout 透传给 CLI（CLI 自己的 HTTP 默认只有 30s，AI PPT 官方要求 1800s）
  4. 本地文件 Base64 编解码（官方明令：禁止让模型逐字符生成 Base64）
  5. 错误翻译成人话 —— 含「进程 exit 0 但 JSON 里 code != 0」这种假成功
  6. 下载完整性校验：绝不把登录页 HTML 当成 xlsx 写给用户
  7. 本地文件往返绝不覆盖原件

其它一切走 raw 透传，由 agent 按官方 references 决定参数。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BIN_NAME = "kdocs-cli"

# CLI 自己的 HTTP 超时默认只有 30 秒（官方 SKILL.md「全局选项」），慢操作必须显式加大。
# 官方给的量级：aippt 1800000ms；上传/导出建议 120000ms 起。
DEFAULT_TIMEOUT_MS = 120_000
SERVICE_TIMEOUT_MS = {
    "aippt": 1_800_000,
    "pdf": 600_000,
    "wpp": 600_000,
    "wps": 300_000,
}
# 给子进程留余量，别在 CLI 自己还没超时的时候就把它杀了
SUBPROCESS_GRACE_S = 30


# ---------------------------------------------------------------- 输出编码
def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfig = getattr(stream, "reconfigure", None)
        if reconfig is not None:
            try:
                reconfig(encoding="utf-8")
            except (ValueError, OSError):  # pragma: no cover - 平台相关
                pass


# ---------------------------------------------------------------- 异常
class KdocsNotInstalled(RuntimeError):
    pass


class KdocsError(RuntimeError):
    """官方 CLI 调用失败。message 已是人话，raw 保留原文备查。"""

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


# ---------------------------------------------------------------- 错误翻译
# 用 dict 按错误码取，**绝不按下标**：2026-09-13 就是因为往元组中间插了两条，
# 写死的 ERROR_HINTS[3] 从 403001 漂到 429002，把「企业账号」说成了「请求太频繁」，
# 结果会让用户干等 + 反复重试（正是官方文档禁止的动作）。
ERROR_HINTS = {
    "400006": "你的金山连接过期了，我带你重新连一下，两分钟就好。",
    # 实测：未授权 / token 失效时官方 CLI 返回的是 HTTP 401，不是文档写的 400006
    "HTTP 401": "还没连上你的金山账号（或者连接过期了）。跟我说一句「把我的金山文档连上」，我带你弄。",
    "Unauthorized": "还没连上你的金山账号（或者连接过期了）。跟我说一句「把我的金山文档连上」，我带你弄。",
    "429002": "金山那边暂时不让我继续操作了（请求太频繁被熔断），要等一会儿。我先停手。",
    "429001": "金山让我歇一会儿再继续（请求太频繁）。我先停手，等会儿接着做。",
    "403001": "这个要用你自己的金山账号，不是公司的企业账号。麻烦退出来重登一下个人号。",
    "403000006": "这个功能只对个人账号开放，你现在登的是公司账号。退出来重登个人号就行。",
}
# 扫描顺序固定：更具体的码排前面（429002 必须先于 429001，403001 先于裸 403）
_SCAN_ORDER = ("400006", "403000006", "403001", "429002", "429001",
               "HTTP 401", "Unauthorized")


def friendly_error(raw) -> str:
    text = raw or ""
    for code in _SCAN_ORDER:
        if code in text:
            return ERROR_HINTS[code]
    low = str(text).lower()
    if "403" in str(text) and ("forbidden" in low or "denied" in low):
        # 官方 auth.md：403 = 企业账号受限。不能落到通用兜底，更不能说成限频。
        return ERROR_HINTS["403001"]
    if "authenticated" in low and "false" in low:
        return "还没连上你的金山账号。跟我说一句「把我的金山文档连上」，我带你弄。"
    if "unknown service" in low or "unknown action" in low:
        return "这个功能我这边工具版本太旧了，我升级一下再试（kdocs-cli upgrade -y）。"
    if "timeout" in low or "deadline" in low:
        return "金山那边这次反应太慢超时了。我隔一会儿重试一次。"
    return "操作没成功。原始信息：" + str(text).strip()[:400]


# ---------------------------------------------------------------- CLI 发现
def candidate_paths() -> list:
    out = []
    env_bin = os.environ.get("KDOCS_CLI_BIN")
    if env_bin:
        out.append(Path(env_bin))

    # KDOCS_CLI_DIR 是**官方安装器自己的**安装目录覆盖变量（setup.sh L18 /
    # setup.ps1 L18，两个平台同名）。不认它的话会出现最难堪的一幕：setup 刚把
    # 二进制装进去，紧接着的复查就说「没装」—— 2026-09-15 沙箱实测复现。
    env_dir = os.environ.get("KDOCS_CLI_DIR")
    if env_dir:
        out.append(Path(env_dir) / BIN_NAME)
        out.append(Path(env_dir) / (BIN_NAME + ".exe"))

    for name in (BIN_NAME, BIN_NAME + ".exe"):
        found = shutil.which(name)
        if found:
            out.append(Path(found))

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        out.append(Path(local_appdata) / "kdocs-cli" / (BIN_NAME + ".exe"))

    home = Path(os.path.expanduser("~"))
    out.append(home / ".local" / "bin" / BIN_NAME)
    out.append(home / ".local" / "bin" / (BIN_NAME + ".exe"))

    seen, uniq = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def find_cli() -> Path:
    for p in candidate_paths():
        if p.is_file():
            return p
    raise KdocsNotInstalled(
        "找不到金山文档工具（kdocs-cli）。\n"
        "  Windows：powershell -ExecutionPolicy Bypass -File .claude\\skills\\kdocs\\scripts\\setup.ps1\n"
        "  Mac/Linux：bash .claude/skills/kdocs/scripts/setup.sh\n"
        "  刚装完还找不到的话，多半是这个会话的 PATH 还是旧的，重开一次就好。"
    )


# ---------------------------------------------------------------- 调用内核
def _default_runner(cmd, timeout_s):
    proc = subprocess.run(
        cmd, capture_output=True, timeout=timeout_s,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def timeout_ms_for(service: str, override=None) -> int:
    if override:
        return int(override)
    return SERVICE_TIMEOUT_MS.get(service, DEFAULT_TIMEOUT_MS)


def call(service: str, action: str, params=None, timeout_ms=None,
         runner=None, cli_path=None):
    """调官方 CLI。参数一律走 --file，超时同时给 CLI 和子进程。"""
    params = params or {}
    runner = runner or _default_runner
    cli = str(cli_path) if cli_path else str(find_cli())
    ms = timeout_ms_for(service, timeout_ms)
    subprocess_s = ms / 1000.0 + SUBPROCESS_GRACE_S

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(params, tmp, ensure_ascii=False)
        tmp.close()
        cmd = [cli, service, action, "--file", tmp.name,
               "--silent", "--output", "json", "--timeout", str(ms)]
        try:
            code, out, err = runner(cmd, subprocess_s)
        except subprocess.TimeoutExpired:
            # 官方点名：上传 / 导出 / AI 做 PPT / 格式转换都可能超时。
            # 不接住的话用户看到的是一整屏 Python traceback。
            raise KdocsError(
                "这一步金山那边处理太久了（等了 {:.0f} 秒还没完）。"
                "这类慢活有时候就是久，我先停下来，等会儿再试。".format(subprocess_s),
                raw="TimeoutExpired after {}s".format(subprocess_s))
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:  # pragma: no cover - 平台相关
            pass

    if code != 0:
        raise KdocsError(friendly_error(err or out), raw=(err or out))

    if not (out or "").strip():
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return out.strip()

    # 进程 exit 0 但 JSON 里 code != 0 —— 这种「假成功」必须也当失败处理
    if isinstance(data, dict) and isinstance(data.get("code"), int) and data["code"] != 0:
        blob = json.dumps(data, ensure_ascii=False)
        raise KdocsError(friendly_error(blob), raw=blob)
    return data


def _scan_json_objects(blob):
    """从一段文本里逐个扫出完整的 JSON 对象（正确处理嵌套与字符串转义）。

    用 raw_decode 而不是正则：非贪婪的 `{.*?}` 遇到嵌套对象只会截到内层的第一个 `}`，
    对真实响应必然解析失败 —— 这正是 F1 的根因。
    """
    decoder = json.JSONDecoder()
    idx = 0
    while True:
        idx = blob.find("{", idx)
        if idx < 0:
            return
        try:
            obj, end = decoder.raw_decode(blob, idx)
        except ValueError:
            idx += 1
            continue
        yield obj
        idx = end


def parse_status_payload(out, err):
    """把 `auth status` 的输出解析成 dict。

    2026-09-14 Windows 真机验证 F1（CRITICAL）：原实现只用非贪婪正则抓 JSON，
    而真实输出是多行缩进 + 嵌套 keychain 对象：

        {
          "authenticated": true,
          "keychain": { "available": true, "backend": "system keychain", ... },
          "source": "system keychain",
          "token": "..."
        }

    正则只截到「根 { → keychain 的 }」，json.loads 必失败（Expecting ',' delimiter），
    `authenticated` 永远读不到 → **授权成功的机器上 doctor 恒报「还没连上」**。
    现在先整体解析（该命令输出单一 JSON 对象），正则式扫描只当兜底。
    """
    for candidate in ((out or ""), (out or "") + "\n" + (err or "")):
        text = candidate.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    # 兜底：输出里混进了别的行（日志/告警）时，扫出第一个带 authenticated 的对象
    for obj in _scan_json_objects((out or "") + "\n" + (err or "")):
        if isinstance(obj, dict) and "authenticated" in obj:
            return obj
    return None


def auth_status(runner=None, cli_path=None):
    """返回 (是否已连上, 补充说明)。

    stdout / stderr 都看：官方 --help 就是写到 stderr 的，status 走哪个流没有保证。
    """
    runner = runner or _default_runner
    cli = str(cli_path) if cli_path else str(find_cli())
    try:
        code, out, err = runner([cli, "auth", "status"], 60)
    except subprocess.TimeoutExpired:
        return False, "检查连接状态时卡住了。"
    if code != 0:
        return False, friendly_error((out or "") + "\n" + (err or ""))
    payload = parse_status_payload(out, err)
    if payload is None:
        return False, ""
    return bool(payload.get("authenticated")), ""


def auth_ok(runner=None, cli_path=None) -> bool:
    return auth_status(runner=runner, cli_path=cli_path)[0]


# ---------------------------------------------------------------- 目标解析
_LINK_RE = re.compile(r"(?:www\.|365\.)?kdocs\.cn/(?:view/)?l/([A-Za-z0-9_-]+)")


def target_params(target) -> dict:
    """她给的可能是完整链接、没带 https 的链接、或裸 file_id。替她判断。"""
    t = (target or "").strip()
    if not t:
        raise ValueError("没告诉我是哪个文档")
    if t.startswith("http://") or t.startswith("https://"):
        return {"url": t}
    m = _LINK_RE.search(t)
    if m:                       # 没带 https 的链接，当分享链接 ID 用
        return {"link_id": m.group(1)}
    return {"file_id": t}


# ---------------------------------------------------------------- 后缀规则
# 这几张表抄自官方 create_and_upload.md / SKILL.md。抄了就可能漂，
# 所以它们只用来「提前给人话」，真正的权威仍是 CLI：CLI 说不行就以 CLI 为准。
UPLOAD_SUFFIXES = (
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf", ".md", ".txt",
    ".html", ".zip", ".png", ".jpg", ".jpeg", ".csv", ".json", ".dps", ".et",
    ".wps", ".gif",
)
EMPTY_FILE_EXTS = ("doc", "docx", "otl", "dbt", "xlsx", "xls", "ksheet", "pptx", "ppt")
CONTENT_EXTS = ("otl", "docx", "pdf")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def extension_of(name: str) -> str:
    ext = Path(name).suffix.lstrip(".").lower()
    if not ext:
        raise ValueError("文件名要带后缀，比如「周报.otl」「数据表.xlsx」")
    return ext


# ---------------------------------------------------------------- 业务动作
def upload(local_path, cloud_name=None, **kw):
    p = Path(local_path)
    if not p.is_file():
        raise ValueError("找不到这个文件：{}".format(local_path))
    name = cloud_name or p.name
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise ValueError("金山不收 {} 这种格式。支持的有：{}".format(
            suffix or "(没有后缀)", " ".join(UPLOAD_SUFFIXES)))
    size = p.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(
            "这个文件 {:.0f} MB，太大了传不动（上限约 {:.0f} MB）。"
            "要不先在电脑上拆小一点，或者只把要处理的那部分单独存一份给我？".format(
                size / 1024 / 1024, MAX_UPLOAD_BYTES / 1024 / 1024))
    payload = {"name": name,
               "content_base64": base64.b64encode(p.read_bytes()).decode("ascii")}
    return call("drive", "upload-new-file", payload, **kw)


def save_as_new(target, cloud_name=None, suffix_hint: str = "-copy") -> Path:
    """永不覆盖原件。target 是目录时，用云端文件名在该目录里落地。"""
    target = Path(target)
    if target.is_dir():
        if not cloud_name:
            raise ValueError("要存进文件夹的话，我得先知道云端那个文件叫什么名字")
        target = target / cloud_name
    stem, ext = target.stem, target.suffix
    n = 0
    while True:
        cand = target.with_name("{}{}{}{}".format(
            stem, suffix_hint, ("-{}".format(n) if n else ""), ext))
        if not cand.exists():
            return cand
        n += 1


def pick_download_url(data) -> str:
    if isinstance(data, dict):
        for key in ("url", "download_url", "link"):
            if isinstance(data.get(key), str) and data[key]:
                return data[key]
        inner = data.get("data")
        if isinstance(inner, dict):
            return pick_download_url(inner)
    raise KdocsError(
        "金山没给下载地址。在线原生文档（.otl / .ksheet / .dbt）本来就不支持下载，"
        "要看内容我用「读」就行。")


def expected_sha256(data):
    node = data if isinstance(data, dict) else {}
    hashes = node.get("hashes") or (node.get("data") or {}).get("hashes") or []
    for h in hashes:
        if isinstance(h, dict) and str(h.get("type", "")).lower() == "sha256":
            return h.get("sum")
    return None


def download_to(url: str, dest, opener=None, expect_sha256=None) -> Path:
    """把文件下回来，并且**校验它确实是文件而不是登录页**。

    官方写明：download_file 返回的 URL 在公网需要登录凭据
    （references/drive/read_and_download.md 的 data.url 条 + kwiki.md「无法直接 curl」）。
    所以这里必须防一手：拿回 text/html 就是被重定向到登录页了，
    绝不能把它写成 .xlsx 交给用户——打开只会看到一个坏文件。
    """
    opener = opener or urllib.request.urlopen
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with opener(url, timeout=120) as resp:
            ctype = ""
            headers = getattr(resp, "headers", None)
            if headers is not None:
                try:
                    ctype = (headers.get("Content-Type") or "").lower()
                except Exception:  # pragma: no cover
                    ctype = ""
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise KdocsError(
            "这个文件下不回来（金山返回 HTTP {}）。它的下载地址需要登录状态，"
            "命令行拿不到。你在浏览器里打开这个链接就能存下来：{}".format(e.code, url))
    except urllib.error.URLError as e:
        raise KdocsError("文件下载失败：{}".format(e.reason))

    head = data[:64].lstrip().lower()
    if "text/html" in ctype or head.startswith(b"<!doctype") or head.startswith(b"<html"):
        raise KdocsError(
            "金山把我重定向到登录页了，拿回来的不是文件（这个下载地址需要登录状态）。"
            "我没有把它写进你的电脑，免得你打开一个坏文件。"
            "你在浏览器里打开这个链接就能存：{}".format(url))

    if expect_sha256:
        import hashlib
        actual = hashlib.sha256(data).hexdigest()
        if actual.lower() != str(expect_sha256).lower():
            raise KdocsError(
                "下回来的文件跟金山那边对不上（校验值不一致），我没敢写进你的电脑。"
                "你从浏览器打开这个链接存一下：{}".format(url))

    dest.write_bytes(data)
    return dest


def unwrap_envelope(data):
    """官方部分动作返回信封 `{"code":0,"data":{...},"msg":"ok"}`，部分直接给内容。

    2026-09-14 Windows 真机验证 F2（HIGH）：真实 `drive get-file-info` 返回的是信封，
    而 `cmd_download` 在**信封顶层**读 `name` → 恒 None → 「下载到文件夹」对所有文件必失败。

    只在**读字段的地方**剥一层，不改 `call()` 的返回值 —— 那会牵动 find / render_files /
    pick_download_url 等一圈，回归面太大（Boss 2026-09-15 明确要求最小修）。
    """
    if (isinstance(data, dict) and "code" in data
            and isinstance(data.get("data"), dict)):
        return data["data"]
    return data if isinstance(data, dict) else {}


def verify_written(target, **kw) -> dict:
    """写后回读。官方明令：不信任 code: 0。"""
    return call("drive", "get-file-info", target_params(target), **kw)


def read_text_tolerant(path) -> str:
    """她的 md/txt 可能是记事本存的 GBK，别拿 UnicodeDecodeError 砸她。"""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("这个文件的编码我认不出来，麻烦用记事本「另存为」选 UTF-8 再给我。")


# ---------------------------------------------------------------- 人话输出
def say(msg):
    print(msg)


def render_files(data) -> str:
    """官方 search_files 的形状是 data.items[].file.{id,name,link_url}
    （references/drive/search.md「返回值说明」），**不是**扁平的 name/file_id。

    2026-09-13 之前这里读的是我自己编的扁平结构，真实环境会全部显示「没有名字」，
    而测试喂的也是同一套编的数据，所以 99 个测试全绿也照样炸。
    """
    items = []
    if isinstance(data, dict):
        items = data.get("items") or data.get("files") or data.get("list") or []
        if not items and isinstance(data.get("data"), dict):
            return render_files(data["data"])
    elif isinstance(data, list):
        items = data
    if not items:
        return "没找到符合的文档。换个词再试试？"

    lines = []
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        node = it.get("file") if isinstance(it.get("file"), dict) else it
        name = node.get("name") or node.get("fname") or "(没有名字)"
        fid = node.get("id") or node.get("file_id") or ""
        link = node.get("link_url") or ""
        lines.append("  · {}    [{}]{}".format(
            name, fid, ("  " + link) if link else ""))
    return "找到 {} 个：\n".format(len(items)) + "\n".join(lines)


# ---------------------------------------------------------------- 参数读取
def load_params(inline, params_file):
    """raw 的参数入口。

    优先文件 / stdin —— 因为 raw 是主干道不是逃生舱：图表、透视表、条件格式、
    回复评论、做 PPT 全走它，而 PowerShell 5.1 传给外部程序时会吃掉 JSON 里的双引号。
    封装层若只在自己内部用 --file，这一跳照样会坏。
    """
    if params_file:
        if params_file == "-":
            return json.loads(sys.stdin.read() or "{}")
        return json.loads(read_text_tolerant(Path(params_file)))
    try:
        return json.loads(inline or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(
            "这段参数不是合法的 JSON（{}）。Windows 的 PowerShell 会把引号吃掉，"
            "所以长参数或含中文的参数请写进一个 .json 文件，"
            "用 --params-file 传给我（或者用管道从 stdin 喂）。".format(e))


# ---------------------------------------------------------------- 命令行
def build_parser():
    ap = argparse.ArgumentParser(
        prog="kdocs_kit.py", description="金山文档官方 CLI (kdocs-cli) 封装层")
    ap.add_argument("--timeout-ms", type=int, default=None,
                    help="这次调用给金山多长时间（毫秒）。慢活已自动加长，一般不用管。")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="检查工具装没装、连没连上")

    p = sub.add_parser("setup", help="按 requirements.json 装好前置依赖（首次使用先跑这个）")
    p.add_argument("--only", default=None,
                   help="只处理清单里的某一项，例如 --only kdocs-cli")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="只说要做什么，不真的动手")
    sub.add_parser("login", help="浏览器授权（走找得到的那个 CLI）")

    p = sub.add_parser("set-token", help="把 Token 存进系统钥匙串")
    p.add_argument("token", nargs="?", default=None,
                   help="不传就从剪贴板读，免得 Token 出现在命令行和对话里")

    p = sub.add_parser("find", help="按关键词找文档")
    p.add_argument("keyword")

    p = sub.add_parser("read", help="读文档内容")
    p.add_argument("target")

    p = sub.add_parser("new", help="新建云文档")
    p.add_argument("name")
    p.add_argument("--from-file", dest="from_file", default=None)

    p = sub.add_parser("upload", help="把本地文件传到云盘")
    p.add_argument("local_path")
    p.add_argument("--name", default=None)

    p = sub.add_parser("download", help="把云文档下载到本地")
    p.add_argument("target")
    p.add_argument("save_to")

    p = sub.add_parser("share", help="开启分享，拿可以发给同事的链接")
    p.add_argument("target")
    p.add_argument("--scope", required=True, choices=["anyone", "company", "users"],
                   help="谁能打开。必须显式选：anyone=拿到链接就能开 / company=仅企业 / users=指定人")

    p = sub.add_parser("comments", help="看同事在文档里说了什么")
    p.add_argument("target")

    p = sub.add_parser("raw", help="透传给官方 CLI（图表/透视表/PPT 等都走这里）")
    p.add_argument("service")
    p.add_argument("action")
    p.add_argument("params", nargs="?", default="{}",
                   help="短参数可内联；含中文或较长的请改用 --params-file")
    p.add_argument("--params-file", dest="params_file", default=None,
                   help="参数 JSON 文件路径，或 - 表示从 stdin 读（Windows 上首选）")
    return ap


# ---------------------------------------------------------------- 前置依赖
# 依赖清单的**唯一真源**是 requirements.json：doctor 读它做体检，setup 读它做安装。
# 两条命令共用一份清单 => 不会出现「doctor 说缺 X、setup 不装 X」这种漂移，
# tests/test_requirements.py 会强制每个条目都有 check handler。
REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements.json"

MIN_PY = (3, 7)
MIN_CLAUDE_CODE = (2, 1, 129)   # skillOverrides 从这个版本起才真的生效


def load_requirements(path=None) -> dict:
    p = Path(path) if path else REQUIREMENTS_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise KdocsError("找不到依赖清单 {}。这个文件是 skill 的一部分，"
                         "缺了说明安装不完整。".format(p))
    except json.JSONDecodeError as e:
        raise KdocsError("依赖清单 {} 不是合法 JSON：{}".format(p, e))
    return data


def expand_pins(text, pins: dict) -> str:
    """把 "{kdocs_cli_version}" 这类占位符按 pins 展开（两轮，允许 pin 里再引 pin）。"""
    if not isinstance(text, str):
        return text
    for _ in range(2):
        for k, v in pins.items():
            if isinstance(v, str):
                text = text.replace("{%s}" % k, v)
    return text


# ---- check handler 注册表：check.type -> fn(req, ctx) -> (state, message) ----
# state: True=通过 / False=没过 / None=查不了（跳过，不算错）
CHECKS = {}


def check_handler(name):
    def deco(fn):
        CHECKS[name] = fn
        return fn
    return deco


def _version_text(blob):
    """把 `kdocs-cli version` 的输出提炼成一行版本号；不像版本就返回 None。

    2026-09-14 F8：替身场景下 doctor 把 JSON 原文当版本号打了出来
    （`kdocs-cli v{"authenticated": true…}`）。真 CLI 下正常，但这行不该裸信。
    """
    text = (blob or "").strip()
    if not text:
        return None
    first = text.splitlines()[0].strip()
    if first.startswith("{") or first.startswith("["):
        return None
    if not re.search(r"\d+\.\d+", first):
        return None
    return first


def _parse_version(text):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


@check_handler("python_version")
def check_python(*, req=None, ctx=None):
    want = tuple((req or {}).get("check", {}).get("min") or MIN_PY)
    v = sys.version_info
    ok = (v.major, v.minor) >= want[:2]
    return ok, "Python {}.{}.{} (need {}.{}+)".format(
        v.major, v.minor, v.micro, want[0], want[1])


@check_handler("binary")
def check_binary(*, req=None, ctx=None):
    try:
        cli = find_cli()
    except KdocsNotInstalled:
        return False, "kdocs-cli not installed"
    ver = ""
    try:
        code, out, err = _default_runner([str(cli), "version"], 60)
        if code == 0:
            v = _version_text((out or "") + (err or ""))
            if v:
                ver = " v" + v
    except Exception:  # pragma: no cover - 平台相关
        pass
    if ctx is not None:
        ctx["cli"] = cli
    return True, "kdocs-cli{} at {}".format(ver, cli)


@check_handler("sibling_skill")
def check_sibling_skill(*, req=None, ctx=None):
    """官方 kdocs skill 装在隔壁了吗（raw 透传的参数文档在它的 references/ 里）。"""
    spec = (req or {}).get("check", {})
    name = spec.get("name", "kdocs")
    probe = spec.get("probe", "SKILL.md")
    here = Path(__file__).resolve().parent.parent      # …/skills/hulu-kdocs-kit
    # skill 可以装在项目级也可以装在用户级，两边不一定同层：本 skill 在
    # ~/.claude/skills/ 而官方那个在 ./.claude/skills/ 是完全正常的组合。
    # 只看「紧挨着我的那个目录」会误报「没装」。
    roots = [
        here.parent,                                   # 跟我同级
        Path.cwd() / ".claude" / "skills",             # 项目级
        Path(os.path.expanduser("~")) / ".claude" / "skills",   # 用户级
    ]
    seen = set()
    for root in roots:
        cand = root / name
        if str(cand) in seen:
            continue
        seen.add(str(cand))
        if (cand / probe).is_file():
            if ctx is not None:
                ctx["official_skill"] = cand
            return True, "official `{}` skill found at {}".format(name, cand)
    return False, ("official `{}` skill not installed "
                   "(raw passthrough still works, but you supply the parameters yourself)"
                   .format(name))


@check_handler("reachable")
def check_network(opener=None, *, req=None, ctx=None):
    """能不能连上金山。公司网络 / 代理挡住的话，后面一切都白搭。"""
    hosts = (req or {}).get("check", {}).get("hosts") or [
        "https://api.wps.cn/", "https://www.kdocs.cn/"]
    opener = opener or urllib.request.urlopen
    for host in hosts:
        try:
            opener(host, timeout=10).close()
            return True, "WPS reachable ({})".format(host)
        except urllib.error.HTTPError:
            # 4xx/5xx 也说明网络是通的，只是那个地址没内容
            return True, "WPS reachable ({})".format(host)
        except Exception:
            continue
    return False, ("cannot reach WPS. Check your network / corporate proxy "
                   "for wps.cn and kdocs.cn")


@check_handler("command_version")
def check_claude_code(*, req=None, ctx=None):
    """skillOverrides 要 v2.1.129+，否则官方 kdocs 技能会跟本技能抢触发。"""
    spec = (req or {}).get("check", {})
    cmd = spec.get("command", "claude")
    want = tuple(spec.get("min") or MIN_CLAUDE_CODE)
    exe = shutil.which(cmd)
    if not exe:
        return None, "{} not found on PATH (skipped)".format(cmd)
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, timeout=30,
                              encoding="utf-8", errors="replace")
    except Exception:
        return None, "could not read {} version (skipped)".format(cmd)
    ver = _parse_version((proc.stdout or "") + (proc.stderr or ""))
    if not ver:
        return None, "could not read {} version (skipped)".format(cmd)
    if ver >= want:
        return True, "{} {}.{}.{}".format(cmd, *ver)
    return False, "{} {}.{}.{} is too old; need {}.{}.{}+".format(
        cmd, *(ver + want))


@check_handler("skill_override")
def check_skill_override(root=None, *, req=None, ctx=None):
    """当前仓库的 settings.json 里有没有压住官方技能。"""
    spec = (req or {}).get("check", {})
    key = spec.get("key", "kdocs")
    accept = spec.get("accept") or ["user-invocable-only", "off"]
    root = Path(root or os.getcwd())
    for name in ("settings.json", "settings.local.json"):
        f = root / ".claude" / name
        if not f.is_file():
            continue
        try:
            cfg = json.loads(f.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        val = (cfg.get("skillOverrides") or {}).get(key)
        if val in accept:
            return True, "official `{}` skill held back ({} = {})".format(key, name, val)
    return False, ("official `{}` skill not held back in .claude/settings.json — "
                   "it can steal the trigger（会抢走触发）and bypass this wrapper's guardrails"
                   .format(key))


@check_handler("auth")
def check_auth(*, req=None, ctx=None):
    cli = (ctx or {}).get("cli")
    if cli is None:
        try:
            cli = find_cli()
        except KdocsNotInstalled:
            return None, "no CLI yet, cannot check sign-in"
    ok, note = auth_status(cli_path=cli)
    if ok:
        return True, "signed in to WPS"
    return False, "not signed in to WPS" + (" ({})".format(note) if note else "")


def evaluate(manifest=None, ctx=None):
    """按清单顺序跑一遍体检，返回 [(req, state, message), ...]。"""
    manifest = manifest or load_requirements()
    pins = manifest.get("pins", {})
    ctx = ctx if ctx is not None else {}
    results = []
    satisfied = {}
    for req in manifest.get("requirements", []):
        # applies_when：依赖的前提没满足时，这条根本不适用，别拿它吓唬人。
        # 例：官方 skill 没装，就没有「要不要压住它」这个问题。
        dep = req.get("applies_when")
        if dep and not satisfied.get(dep):
            results.append((req, None, "not applicable ({} is not installed)".format(dep)))
            satisfied[req["id"]] = None
            continue
        ctype = (req.get("check") or {}).get("type")
        fn = CHECKS.get(ctype)
        if fn is None:
            results.append((req, None, "no checker for '{}' (skipped)".format(ctype)))
            continue
        try:
            state, msg = fn(req=req, ctx=ctx)
        except Exception as e:  # noqa: BLE001 - 单项体检炸了不该让整份体检停摆
            state, msg = None, "check failed: {}: {}".format(type(e).__name__, e)
        satisfied[req["id"]] = state
        results.append((req, state, expand_pins(msg, pins)))
    return results


def render_line(req, state, msg, pins=None):
    """把一条体检结果打成一行，返回**展示用**的 state。

    doctor 和 setup 的复查必须长得一模一样 —— 同一项在两处显示成不同颜色，
    用户只会以为自己哪里搞错了。
    """
    if state is False and req.get("level") == "optional":
        state = None            # optional 缺失只是提示，不该把体检染红
    mark = {True: "✅ ", False: "❌ ", None: "➖ "}[state]
    say(mark + "{} — {}".format(req.get("title", req["id"]), msg))
    if state is False:
        why = expand_pins(req.get("why", ""), pins or {}).strip()
        if why:
            say("   why: " + why)
    return state


def cmd_doctor(_args=None, _kw=None) -> int:
    """按 requirements.json 跑一遍前置依赖，人话输出。

    exit 0 = 全通 / 1 = 必需项缺失 / 2 = 没登录 / 6 = 能用但有告警
    """
    manifest = load_requirements()
    pins = manifest.get("pins", {})
    results = evaluate(manifest)

    missing_required, warned, unauthed = [], False, False
    for req, state, msg in results:
        state = render_line(req, state, msg, pins)
        if state is False:
            if req.get("level") == "required":
                if req["id"] == "account":
                    unauthed = True
                else:
                    missing_required.append(req)
            else:
                warned = True

    def _fix_line(req):
        inst = req.get("install", {})
        if inst.get("auto"):
            return "  · {}: run  python {} setup --only {}".format(
                req["id"], Path(__file__).name, req["id"])
        man = inst.get("manual", {})
        hint = man.get(_os_key()) or man.get("all") or inst.get("reason", "")
        return "  · {}: {}".format(req["id"], expand_pins(hint, pins))

    # 「要修的」和「可选的加分项」分开列：把 ➖ 的东西混进 How to fix，
    # 会让人以为自己漏装了必需件。
    must_fix = [r for r, st, _ in results
                if st is False and r.get("level") != "optional"]
    extras = [r for r, st, _ in results
              if st is False and r.get("level") == "optional"]
    if must_fix:
        say("")
        say("How to fix:")
        for req in must_fix:
            say(_fix_line(req))
    if extras:
        say("")
        say("Optional extras (nothing is broken without these):")
        for req in extras:
            say(_fix_line(req))

    say("")
    if missing_required:
        say("Missing required items above. Fix them, then run doctor again.")
        return 1
    if unauthed:
        say("Everything is installed — you just need to sign in:  "
            "python {} login".format(Path(__file__).name))
        return 2
    if warned:
        say("Usable, but the ❌ items above will bite you later.")
        return 6
    say("All set.")
    return 0


# ---------------------------------------------------------------- 安装器
def _os_key() -> str:
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


INSTALLERS = {}


def installer(name):
    def deco(fn):
        INSTALLERS[name] = fn
        return fn
    return deco


@installer("upstream-script")
def install_upstream_script(req, pins, dry_run=False) -> bool:
    """下载并运行**上游官方**的安装脚本。

    为什么不自己实现下载：官方脚本已经处理了 OS/arch 判定、CDN 路径、
    SHA-256 校验（对 checksums.txt）、安装目录。自己重写一遍 = 必然漂移，
    而且等于我们在替金山维护它的安装器。我们只负责把它取下来、喂对版本、验结果。
    我们也**不转发**任何官方文件 —— 是用户的机器直接从版权方取。
    """
    inst = req.get("install", {})
    url = inst.get("source_windows" if os.name == "nt" else "source")
    url = expand_pins(url, pins)
    env_extra = {k: expand_pins(v, pins) for k, v in (inst.get("env") or {}).items()}

    say("  source: {}".format(url))
    say("  env:    {}".format(env_extra or "(none)"))
    if dry_run:
        say("  (dry-run, nothing executed)")
        return True

    suffix = ".ps1" if os.name == "nt" else ".sh"
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            body = resp.read()
    except Exception as e:  # noqa: BLE001
        say("  ❌ could not download the official installer: {}".format(e))
        man = (inst.get("manual") or {}).get("all")
        if man:
            say("  Do it by hand instead: {}".format(expand_pins(man, pins)))
        return False
    if not body.strip():
        say("  ❌ the official installer came back empty; aborting.")
        return False

    # 我们要**执行**这段字节，所以执行前必须验它。上游的脚本会校验二进制的
    # SHA-256，但没人校验脚本自己 —— 不验的话，整条链最弱的一环就是我们这一跳。
    verify = inst.get("verify") or {}
    want = verify.get("sha256_windows" if os.name == "nt" else "sha256")
    if want:
        import hashlib
        got = hashlib.sha256(body).hexdigest()
        if got != want:
            say("  ❌ the official installer does not match its pinned checksum.")
            say("     expected {}".format(want))
            say("     got      {}".format(got))
            say("     Not running it. Upstream may have published a new version —")
            say("     verify the change yourself, then update requirements.json.")
            man = (inst.get("manual") or {}).get("all")
            if man:
                say("     Meanwhile, install by hand: {}".format(expand_pins(man, pins)))
            return False
        say("  sha256: verified against the pin")

    env = dict(os.environ)
    env.update(env_extra)
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        if os.name == "nt":
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp]
        else:
            cmd = ["sh", tmp]
        proc = subprocess.run(cmd, env=env, timeout=900,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            say("  ❌ the official installer exited {}".format(proc.returncode))
            return False
    except subprocess.TimeoutExpired:
        say("  ❌ the official installer took too long (>15 min); aborting.")
        return False
    finally:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover
            pass
    return True


@installer("merge-settings")
def install_merge_settings(req, pins, dry_run=False) -> bool:
    """把补丁合并进 .claude/settings.json，保留其它键，先备份。

    用 Python 的 json 而不是 PowerShell 的 ConvertTo-Json —— 后者会把中文
    变成 \\uXXXX 转义，把用户原有的设置搅烂。
    """
    inst = req.get("install", {})
    target = Path(os.getcwd()) / inst.get("target", ".claude/settings.json")
    patch = inst.get("patch") or {}

    cfg = {}
    if target.is_file():
        try:
            cfg = json.loads(target.read_text(encoding="utf-8-sig")) or {}
        except json.JSONDecodeError as e:
            say("  ❌ {} is not valid JSON ({}); not touching it.".format(target, e))
            return False

    merged = dict(cfg)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            sub = dict(merged[key])
            sub.update(val)
            merged[key] = sub
        else:
            merged[key] = val

    if merged == cfg:
        say("  already set, nothing to do")
        return True
    say("  target: {}".format(target))
    say("  patch:  {}".format(json.dumps(patch, ensure_ascii=False)))
    if dry_run:
        say("  (dry-run, nothing written)")
        return True

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        backup = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(str(target), str(backup))
        say("  backup: {}".format(backup))
    target.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return True


def cmd_setup(args=None, _kw=None) -> int:
    """按 requirements.json 把能自动装的装上，装不了的给出准确的手动步骤。

    exit 0 = 做完（或本来就齐）/ 1 = 有必需项没搞定
    """
    only = getattr(args, "only", None)
    dry_run = bool(getattr(args, "dry_run", False))
    manifest = load_requirements()
    pins = manifest.get("pins", {})

    say("Checking what's missing…")
    results = evaluate(manifest)
    todo = [(req, state) for req, state, _m in results
            if state is False and (not only or req["id"] == only)]

    if only and not any(r["id"] == only for r, _s, _m in results):
        say("❌ no requirement with id '{}' in requirements.json".format(only))
        return 1
    if not todo:
        say("Nothing to install — everything the manifest asks for is already here.")
        return 0

    failed_required = []
    for req, _state in todo:
        say("")
        say("── {} ({})".format(req.get("title", req["id"]), req.get("level", "required")))
        say("   why: {}".format(expand_pins(req.get("why", ""), pins)))
        inst = req.get("install", {})
        if not inst.get("auto"):
            reason = expand_pins(inst.get("reason", ""), pins)
            man = inst.get("manual", {})
            hint = man.get(_os_key()) or man.get("all") or ""
            if reason:
                say("   not automated: {}".format(reason))
            say("   do this: {}".format(expand_pins(hint, pins)))
            if req.get("level") == "required":
                failed_required.append(req["id"])
            continue

        fn = INSTALLERS.get(inst.get("method"))
        if fn is None:
            say("   ❌ unknown install method '{}'".format(inst.get("method")))
            failed_required.append(req["id"])
            continue
        ok = fn(req, pins, dry_run=dry_run)
        say("   {}".format("✅ done" if ok else "❌ failed"))
        if not ok and req.get("level") == "required":
            failed_required.append(req["id"])

    say("")
    if dry_run:
        say("Dry run only. Re-run without --dry-run to apply.")
        return 0
    say("Re-checking…")
    after = evaluate(load_requirements())
    still = [r["id"] for r, s, _m in after
             if s is False and r.get("level") == "required" and r["id"] != "account"]
    for req, state, msg in after:
        render_line(req, state, msg, pins)
    if still:
        say("")
        say("Still missing: {}. See the notes above.".format(", ".join(still)))
        return 1
    say("")
    say("Setup finished. Next: python {} login".format(Path(__file__).name))
    return 0


def cmd_login(args, kw) -> int:
    cli = find_cli()
    say("我去开浏览器了，请用你**自己的**金山账号登录并点同意（不是公司的企业账号）。")
    try:
        proc = subprocess.run([str(cli), "auth", "login"], timeout=600)
    except subprocess.TimeoutExpired:
        say("❌ 等你授权等了 10 分钟还没完成。没关系，我们再来一次就行。")
        return 3
    if proc.returncode != 0:
        say("❌ 授权没成功。如果浏览器没弹出来就手动来：打开 https://www.kdocs.cn/latest，"
            "点右上角头像旁的菜单 →「金山文档Skill」→ 复制 Token，然后跟我说一声，"
            "我直接从剪贴板读，不用你把它发出来。")
        return 3
    return cmd_doctor(args, kw)


def _clipboard_text():
    """读系统剪贴板。

    2026-09-14 Windows 真机验证 F7：`powershell Get-Clipboard` 在验证机上读回空。
    已知成因有两个——PowerShell 5.1 的剪贴板 API 需要 STA 单元，非交互/MTA 下会返回空；
    不带 `-Raw` 时多行内容会变成数组。这里两个都兜上，并按成功率排序多试几种。
    **macOS 上无法复现，这条改动属 best-effort，留给 Windows 侧回归验证。**
    """
    attempts = [
        ["pbpaste"],
        ["powershell", "-NoProfile", "-STA", "-Command", "Get-Clipboard -Raw"],
        ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
        ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
        ["pwsh", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
    ]
    for cmd in attempts:
        if not shutil.which(cmd[0]):
            continue
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=20,
                                  encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - 平台相关
            continue
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return proc.stdout.strip()
    return None


def cmd_set_token(args, kw) -> int:
    cli = find_cli()
    token = args.token or _clipboard_text()
    if not token:
        say("❌ 剪贴板里没读到东西。请先复制 Token，再跟我说一次。")
        return 4
    proc = subprocess.run([str(cli), "auth", "set-token", token],
                          capture_output=True, timeout=60,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        say("❌ " + friendly_error((proc.stderr or "") + (proc.stdout or "")))
        return 3
    say("✅ 钥匙存好了（存在系统钥匙串里，没有写进任何文件）。")
    return cmd_doctor(args, kw)


def main(argv=None) -> int:
    _force_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    kw = {"timeout_ms": args.timeout_ms}

    try:
        if args.cmd == "doctor":
            return cmd_doctor(args, kw)
        if args.cmd == "setup":
            return cmd_setup(args, kw)
        if args.cmd == "login":
            return cmd_login(args, kw)
        if args.cmd == "set-token":
            return cmd_set_token(args, kw)

        if args.cmd == "find":
            say(render_files(call("drive", "search-files",
                                  {"keyword": args.keyword, "page_size": 100}, **kw)))

        elif args.cmd == "read":
            data = call("drive", "read-file", target_params(args.target), **kw)
            if isinstance(data, dict) and str(data.get("status", "")).lower() == "pending":
                say("这个文档金山还在后台处理（异步任务 {}）。"
                    "等十几秒我再读一次就好。".format(data.get("task_id", "")))
                return 0
            if isinstance(data, dict) and data.get("warnings"):
                say("⚠️ 金山提示：{}".format(
                    json.dumps(data["warnings"], ensure_ascii=False)))
            say(json.dumps(data, ensure_ascii=False, indent=2))

        elif args.cmd == "new":
            ext = extension_of(args.name)
            if args.from_file:
                if ext not in CONTENT_EXTS:
                    raise ValueError("带正文新建只支持 {}；.{} 要先建空文件再往里写。".format(
                        "/".join("." + e for e in CONTENT_EXTS), ext))
                body = read_text_tolerant(Path(args.from_file))
                data = call("drive", "create-file-with-content",
                            {"name": args.name, "file_extension": ext,
                             "content": body}, **kw)
            else:
                if ext not in EMPTY_FILE_EXTS:
                    raise ValueError("空白新建不支持 .{}（金山只收 {}）。".format(
                        ext, "/".join(EMPTY_FILE_EXTS)))
                data = call("drive", "create-empty-file",
                            {"name": args.name, "file_extension": ext}, **kw)
            say("建好了：" + json.dumps(data, ensure_ascii=False))
            fid = unwrap_envelope(data).get("file_id")
            if fid:
                verify_written(fid, **kw)       # 官方明令：不信任 code: 0
                say("（我回头又读了一遍，确认真的建出来了。）")

        elif args.cmd == "upload":
            data = upload(args.local_path, args.name, **kw)
            say("传好了：" + json.dumps(data, ensure_ascii=False))
            fid = unwrap_envelope(data).get("file_id")
            if fid:
                verify_written(fid, **kw)
                say("（我回头又读了一遍，确认真的传上去了。）")

        elif args.cmd == "download":
            params = target_params(args.target)
            params["with_hash"] = True          # 拿 sha256 来验，防登录页冒充文件
            data = call("drive", "download-file", params, **kw)
            cloud_name = None
            if Path(args.save_to).is_dir():
                info = call("drive", "get-file-info",
                            target_params(args.target), **kw)
                cloud_name = unwrap_envelope(info).get("name")
            dest = save_as_new(Path(args.save_to), cloud_name=cloud_name)
            written = download_to(pick_download_url(data), dest,
                                  expect_sha256=expected_sha256(data))
            say("下载好了：{}（{} 字节）。你原来的文件没动。".format(
                written, written.stat().st_size))

        elif args.cmd == "share":
            params = target_params(args.target)
            params["scope"] = args.scope
            data = call("drive", "share-file", params, **kw)
            note = {"anyone": "⚠️ 拿到这个链接的人都能打开，被转发出去也能看",
                    "company": "只有公司内部的人能打开",
                    "users": "只有你指定的人能打开"}[args.scope]
            say("分享链接（{}）：{}".format(note, json.dumps(data, ensure_ascii=False)))

        elif args.cmd == "comments":
            params = target_params(args.target)
            params["origin_id"] = "0"
            say(json.dumps(call("drive", "list-document-comments", params, **kw),
                           ensure_ascii=False, indent=2))

        elif args.cmd == "raw":
            params = load_params(args.params, args.params_file)
            say(json.dumps(call(args.service, args.action, params, **kw),
                           ensure_ascii=False, indent=2))
        return 0

    except KdocsNotInstalled as e:
        say("❌ " + str(e))
        return 1
    except KdocsError as e:
        say("❌ " + str(e))
        return 3
    except ValueError as e:
        say("❌ " + str(e))
        return 4
    except OSError as e:
        # 文件不存在 / 没权限 / 磁盘满 —— 别把 Python 的英文报错甩给用户
        say("❌ 文件操作出问题了：{}".format(e))
        return 5
    except Exception as e:  # noqa: BLE001 - 最后一道，绝不让 traceback 落到用户眼前
        say("❌ 我这边出了个没预料到的问题，先停下来了。")
        print("[debug] {}: {}".format(type(e).__name__, e), file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
