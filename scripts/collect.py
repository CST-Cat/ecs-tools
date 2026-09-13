#!/usr/bin/env python3
"""ecs-tools collector: 自动镜像上游项目 (CST-Cat/ecs) 所需第三方软件的全部发布版本。

目录布局（data root 默认为仓库根目录）：

    <软件>/<版本>/<平台-架构|all>/<类型>/<文件名>

- 平台: linux / freebsd（文件名无平台标记时归入 all）
- 架构: amd64, arm64, armv7, 386, s390x, riscv64, ppc64le（或 all）
- 类型: source / binary / deb / rpm / msi / meta（校验和、签名）

支持三种来源：
- github_releases: 上游仓库的全部 GitHub Release 资产；release 无资产时可
  回退下载 tag 源码包（fallback_tags）
- url_index: 抓取目录列表页，按正则下载文件（如 fio 官方源码站）
- url_list: 下载固定 URL 列表（如 NPB、stream）

每次运行都会同步上游仓库的 tools/lock.json：上游新增依赖软件时自动登记。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

API = "https://api.github.com"
USER_AGENT = "ecs-tools-collector/1.0 (+https://github.com/CST-Cat/ecs-tools)"
MB = 1024 * 1024


def log(msg):
    print(msg, flush=True)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FileTooBig(Exception):
    pass


class Http:
    def __init__(self, token=None):
        self.token = token
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        self.opener = urllib.request.build_opener()
        if proxy:
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"https": proxy, "http": proxy})
            )

    def _request(self, url, headers=None, timeout=60):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        if self.token and "api.github.com" in url and "githubusercontent" not in url:
            req.add_header("Authorization", f"Bearer {self.token}")
        return self.opener.open(req, timeout=timeout)

    def get_json(self, url, retries=5):
        delay = 2
        last = None
        for _ in range(retries):
            try:
                with self._request(url, {"Accept": "application/vnd.github+json"}) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                last = e
                remaining = (e.headers or {}).get("X-RateLimit-Remaining")
                if e.code in (403, 429) and remaining == "0":
                    reset = int((e.headers or {}).get("X-RateLimit-Reset") or 0)
                    wait = min(max(reset - time.time(), 1) + 2, 900)
                    log(f"  API 限流，等待 {wait:.0f}s 后重试")
                    time.sleep(wait)
                    continue
                if e.code >= 500 or e.code == 403:
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = e
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise RuntimeError(f"GET 重试后仍失败: {url} ({last})")

    def get_text(self, url, retries=4):
        delay = 2
        last = None
        for _ in range(retries):
            try:
                with self._request(url) as r:
                    return r.read().decode("utf-8", "replace")
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                    ConnectionError, OSError) as e:
                last = e
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise RuntimeError(f"GET 重试后仍失败: {url} ({last})")

    def download(self, url, dest, max_bytes, retries=4):
        """下载到临时目录校验后再落盘，返回 (size, sha256)。"""
        delay = 2
        last = None
        for _ in range(retries):
            h = hashlib.sha256()
            size = 0
            tmp = None
            try:
                with self._request(url, timeout=180) as r, tempfile.NamedTemporaryFile(
                    delete=False, suffix=".part", dir=tempfile.gettempdir()
                ) as f:
                    tmp = Path(f.name)
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            raise FileTooBig(f"{url} 超过 {max_bytes // MB}MB 上限")
                        h.update(chunk)
                        f.write(chunk)
                if size == 0:
                    raise RuntimeError(f"空响应体: {url}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tmp), dest)
                return size, h.hexdigest()
            except FileTooBig:
                if tmp:
                    tmp.unlink(missing_ok=True)
                raise
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                    ConnectionError, OSError) as e:
                last = e
                if tmp:
                    tmp.unlink(missing_ok=True)
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise RuntimeError(f"下载重试后仍失败: {url} ({last})")


# ---------------------------------------------------------------- 分类规则

PLATFORM_TOKENS = [
    ("freebsd", "freebsd"), ("openbsd", "openbsd"), ("netbsd", "netbsd"),
    ("dragonfly", "dragonfly"), ("darwin", "darwin"), ("macosx", "darwin"),
    ("macos", "darwin"), ("android", "android"), ("windows", "windows"),
    ("win64", "windows"), ("win32", "windows"), ("linux", "linux"),
]

ARCH_PATTERNS = [
    (re.compile(r"(?:^|[^a-z0-9])(?:x86_64|amd64|x64)(?:[^a-z0-9]|$)"), "amd64"),
    (re.compile(r"(?:^|[^a-z0-9])(?:aarch64|arm64)(?:[^a-z0-9]|$)"), "arm64"),
    (re.compile(r"(?:^|[^a-z0-9])(?:armv7|armhf)(?:[^a-z0-9]|$)"), "armv7"),
    (re.compile(r"(?:^|[^a-z0-9])(?:i[36]86|386|x86)(?:[^a-z0-9]|$)"), "386"),
    (re.compile(r"(?:^|[^a-z0-9])s390x(?:[^a-z0-9]|$)"), "s390x"),
    (re.compile(r"(?:^|[^a-z0-9])riscv64(?:[^a-z0-9]|$)"), "riscv64"),
    (re.compile(r"(?:^|[^a-z0-9])ppc64le(?:[^a-z0-9]|$)"), "ppc64le"),
]

SOURCE_EXTS = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2",
               ".tar.zst", ".tar", ".zip")
WIN_EXTS = (".exe", ".msi")
DARWIN_EXTS = (".dmg", ".pkg")
META_RE = re.compile(r"(\.(sha256|sha512|sha1|md5|asc|sig|pem|minisig)|"
                     r"sha256sums?|checksums?)(\.asc|\.sig|\.txt)?$")


def classify_platform(name_lower):
    if name_lower.endswith(WIN_EXTS):
        return "windows"
    if name_lower.endswith(DARWIN_EXTS):
        return "darwin"
    for token, plat in PLATFORM_TOKENS:
        if re.search(rf"(?:^|[^a-z]){token}(?:[^a-z]|$)", name_lower):
            return plat
    return None


def classify_arch(name_lower):
    for pattern, arch in ARCH_PATTERNS:
        if pattern.search(name_lower):
            return arch
    return None


def classify_type(name_lower):
    if name_lower.endswith(".deb"):
        return "deb"
    if name_lower.endswith(".rpm"):
        return "rpm"
    if name_lower.endswith(".msi"):
        return "msi"
    if name_lower.endswith(SOURCE_EXTS):
        return "source"
    if META_RE.search(name_lower):
        return "meta"
    return "binary"


def classify_asset(filename):
    """返回 (platform, arch, type)；platform/arch 可能为 None 表示无标记。"""
    low = filename.lower()
    return classify_platform(low), classify_arch(low), classify_type(low)


def slot_for(platform, arch):
    """计算目录槽位：linux_amd64 -> linux-amd64；无标记 -> all。"""
    if platform is None and arch is None:
        return "all"
    return f"{platform or 'linux'}-{arch or 'all'}"


# ---------------------------------------------------------------- 主逻辑

def paginate(http, path, max_pages=60):
    page = 1
    while page <= max_pages:
        sep = "&" if "?" in path else "?"
        data = http.get_json(f"{API}{path}{sep}per_page=100&page={page}")
        if not data:
            return
        yield from data
        if len(data) < 100:
            return
        page += 1


def version_sort_key(v):
    parts = re.split(r"(\d+)", v)
    return tuple((1, int(p)) if p.isdigit() else (0, p) for p in parts)


def place_file(http, tool_cfg, defaults, dest, url, tool, version, plat, arch,
               kind, dry_run, index_entries, stats, size_hint=None):
    max_bytes = (tool_cfg.get("max_asset_mb") or defaults.get("max_asset_mb") or 200) * MB
    if dry_run:
        log(f"  [dry-run] {dest}")
        stats["planned"] += 1
        return True
    if dest.exists() and (size_hint is None or dest.stat().st_size == size_hint):
        stats["already"] += 1
        return False
    size, sha = http.download(url, dest, max_bytes)
    rel = dest.relative_to(REPO_ROOT).as_posix() if REPO_ROOT in dest.parents else str(dest)
    index_entries[rel] = {
        "tool": tool, "version": version, "platform": plat or "all",
        "arch": arch or "all", "type": kind, "size": size, "sha256": sha,
        "source": url, "fetched_at": now_iso(),
    }
    stats["added"] += 1
    stats["added_bytes"] += size
    log(f"  + {rel} ({size / MB:.1f}MB)")
    return True


def asset_kept(filename, tool_cfg, defaults):
    """返回 (platform, arch, kind, slot) 或 None 表示跳过。"""
    low = filename.lower()
    include = tool_cfg.get("asset_include")
    if include and not re.search(include, filename):
        return None
    plat, arch, kind = classify_asset(filename)
    platforms = tool_cfg.get("platforms") or defaults.get("platforms") or ["linux"]
    archs = tool_cfg.get("architectures") or defaults.get("architectures") or []
    if plat is not None and plat not in platforms:
        return None
    if arch is not None and arch not in archs:
        return None
    if plat is None and arch is not None:
        plat = "linux"  # 有架构无平台标记的资产按 linux 处理
    return plat, arch, kind, slot_for(plat, arch)


def collect_github_releases(http, tool, cfg, defaults, data_root, dry_run,
                            index_entries, stats, report):
    repo = cfg["repo"]
    releases = list(paginate(http, f"/repos/{repo}/releases"))
    releases = [r for r in releases if not r.get("draft")]
    if not (cfg.get("include_prereleases", defaults.get("include_prereleases", False))):
        releases = [r for r in releases if not r.get("prerelease")]
    glob = cfg.get("version_glob")
    if glob:
        releases = [r for r in releases if re.search(glob, r.get("tag_name", ""))]
    years = cfg.get("recent_years", defaults.get("recent_years"))
    if years:
        cutoff = time.time() - float(years) * 365.25 * 86400

        def release_ts(r):
            stamp = r.get("published_at") or r.get("created_at") or ""
            try:
                return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return 0.0

        releases = [r for r in releases if release_ts(r) >= cutoff]
    keep = cfg.get("keep_latest", defaults.get("keep_latest"))
    if keep:
        releases.sort(key=lambda r: r.get("published_at") or r.get("created_at") or "",
                      reverse=True)
        releases = releases[:keep]
    stats["scanned"] = len(releases)

    fallback = cfg.get("fallback_tags", defaults.get("fallback_tags", True))
    owner = repo.split("/")[0]
    name = repo.split("/")[1]
    for release in sorted(releases, key=lambda r: r.get("published_at") or ""):
        tag = release.get("tag_name", "")
        added = 0
        added_main = 0  # source/binary/包类文件数；meta 不计入回退判断
        for asset in release.get("assets") or []:
            fname = asset.get("name") or ""
            kept = asset_kept(fname, cfg, defaults)
            if not kept:
                continue
            plat, arch, kind, slot = kept
            dest = data_root / tool / tag / slot / kind / fname
            try:
                placed = place_file(http, cfg, defaults, dest,
                                    asset.get("browser_download_url"), tool, tag,
                                    plat, arch, kind, dry_run, index_entries,
                                    stats, size_hint=asset.get("size") or None)
            except Exception as e:
                report["errors"].append(f"{tag}/{fname}: {e}")
                log(f"  ! {tag}/{fname} 下载失败: {e}")
                continue
            if placed:
                added += 1
                if kind != "meta":
                    added_main += 1
        if added_main == 0 and fallback and tag:
            # release 没有可用资产时回退下载 tag 源码包
            url = f"https://codeload.github.com/{repo}/tar.gz/refs/tags/{tag}"
            fname = f"{name}-{tag}.tar.gz"
            dest = data_root / tool / tag / "all" / "source" / fname
            log(f"  release {tag} 无可用源码/二进制资产，回退 tag 源码包")
            try:
                place_file(http, cfg, defaults, dest, url, tool, tag, None, None,
                           "source", dry_run, index_entries, stats)
            except Exception as e:
                report["errors"].append(f"{tag}/{fname}: {e}")
                log(f"  ! {tag}/{fname} 回退下载失败: {e}")
                continue
            added += 1
        stats["versions_done"] += 1


def collect_url_index(http, tool, cfg, defaults, data_root, dry_run,
                      index_entries, stats, report):
    text = http.get_text(cfg["index_url"])
    links = re.findall(r'href="([^"?]+)"', text)
    pattern = re.compile(cfg["link_regex"])
    files = []
    seen = set()
    for link in links:
        fname = link.rstrip("/").rsplit("/", 1)[-1]
        if fname in seen or not pattern.match(fname):
            continue
        seen.add(fname)
        m = re.search(cfg["version_regex"], fname)
        version = m.group(1) if m and m.groups() else "unknown"
        files.append((version, fname, urljoin(cfg["index_url"], link)))
    keep = cfg.get("keep_latest", defaults.get("keep_latest"))
    if keep:
        files.sort(key=lambda f: version_sort_key(f[0]), reverse=True)
        files = files[:keep]
    stats["scanned"] = len(files)
    for version, fname, url in files:
        dest = data_root / tool / version / "all" / "source" / fname
        try:
            place_file(http, cfg, defaults, dest, url, tool, version, None, None,
                       "source", dry_run, index_entries, stats)
        except Exception as e:
            report["errors"].append(f"{version}/{fname}: {e}")
            log(f"  ! {version}/{fname} 下载失败: {e}")
            continue
        stats["versions_done"] = len({f[0] for f in files})


def collect_url_list(http, tool, cfg, defaults, data_root, dry_run,
                     index_entries, stats, report):
    default_version = cfg.get("version", "current")
    stats["scanned"] = len(cfg.get("urls", []))
    for entry in cfg.get("urls", []):
        url = entry["url"]
        fname = url.rstrip("/").rsplit("/", 1)[-1]
        version = entry.get("version") or default_version
        if entry.get("version_regex"):
            m = re.search(entry["version_regex"], fname)
            version = m.group(1) if m and m.groups() else version
        plat, arch, kind = classify_asset(fname)
        if kind == "binary":
            kind = "source"  # url_list 提供的多为源码文件
        dest = data_root / tool / version / slot_for(plat, arch) / kind / fname
        try:
            place_file(http, cfg, defaults, dest, url, tool, version, plat, arch,
                       kind, dry_run, index_entries, stats)
        except Exception as e:
            report["errors"].append(f"{version}/{fname}: {e}")
            log(f"  ! {version}/{fname} 下载失败: {e}")
            continue
        stats["versions_done"] += 1


# ---------------------------------------------------------------- 上游同步

def sync_registry(http, config_path):
    """按上游 lock.json 增补工具注册表，返回 (config, sync_notes)。"""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proj = config.get("upstream_project") or {}
    if not proj.get("repo"):
        return config, ["未配置 upstream_project，跳过同步"]
    raw_url = (f"https://raw.githubusercontent.com/{proj['repo']}/"
               f"{proj.get('ref', 'main')}/{proj.get('lock_path', 'tools/lock.json')}")
    try:
        lock = json.loads(http.get_text(raw_url))
    except Exception as e:  # 上游同步失败不阻塞本次收集
        return config, [f"读取上游 lock.json 失败: {e}"]
    notes = []
    lock_names = [t.get("name") for t in lock.get("tools", []) if t.get("name")]
    aliases = config.get("aliases", {})
    ignore = set(config.get("ignore_tools", []))
    covered = set()
    for name in lock_names:
        if name in config.get("tools", {}):
            covered.add(name)
        elif aliases.get(name) in config.get("tools", {}):
            covered.add(aliases[name])
            notes.append(f"{name} -> {aliases[name]}")
        elif name in ignore:
            covered.add(name)
        else:
            upstream = next((t.get("upstream", "") for t in lock["tools"]
                             if t.get("name") == name), "")
            m = re.match(r"^https://github\.com/([^/]+/[^/]+?)/?$", upstream or "")
            if m:
                config.setdefault("tools", {})[name] = {
                    "repo": m.group(1), "auto_added": True,
                }
                covered.add(name)
                notes.append(f"自动登记新工具 {name} ({m.group(1)})")
            else:
                notes.append(f"上游新工具 {name} 无法自动登记（来源: {upstream or '未知'}），"
                             f"请在 config/tools.json 手工配置")
    if any("自动登记" in n for n in notes):
        write_json(config_path, config)
        notes.append("config/tools.json 已更新")
    missing = [n for n in lock_names
               if n not in covered and aliases.get(n) not in config.get("tools", {})]
    if missing:
        notes.append(f"未覆盖的上游工具: {', '.join(missing)}")
    return config, notes


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def update_index(data_root, index_path, index_entries):
    index = {"schema": "ecs-tools.index/v1", "generated_at": now_iso()}
    files = {}
    if index_path.exists():
        try:
            files = json.loads(index_path.read_text(encoding="utf-8")).get("files", {})
        except Exception:
            files = {}
    files = {p: m for p, m in files.items() if (data_root / p).exists()}
    files.update(index_entries)
    index["file_count"] = len(files)
    index["files"] = dict(sorted(files.items()))
    write_json(index_path, index)
    return index


def write_summary(summary_path, notes, reports):
    lines = ["## ecs-tools 收集报告", ""]
    if notes:
        lines += ["### 上游同步", ""] + [f"- {n}" for n in notes] + [""]
    lines += ["| 工具 | 扫描版本 | 新增文件 | 新增体积 | 已存在 | 失败 |",
              "|---|---|---|---|---|---|"]
    total_added = 0
    for r in reports:
        s = r["stats"]
        total_added += s["added"]
        lines.append(f"| {r['tool']} | {s.get('scanned', '-')} | {s['added']} | "
                     f"{s['added_bytes'] / MB:.1f}MB | {s['already']} | "
                     f"{len(r['errors'])} |")
    lines.append(f"| **合计** | - | **{total_added}** | "
                 f"**{sum(r['stats']['added_bytes'] for r in reports) / MB:.1f}MB** | "
                 f"**{sum(r['stats']['already'] for r in reports)}** | "
                 f"**{sum(len(r['errors']) for r in reports)}** |")
    lines.append("")
    for r in reports:
        if r["errors"]:
            lines += [f"### {r['tool']} 失败详情", ""]
            lines += [f"- {e}" for e in r["errors"]] + [""]
    text = "\n".join(lines)
    log("\n" + text)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


# ---------------------------------------------------------------- 入口

REPO_ROOT = Path.cwd()


def main():
    global REPO_ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/tools.json")
    ap.add_argument("--only", default=os.environ.get("COLLECT_ONLY", ""),
                    help="只收集指定软件（逗号分隔）")
    ap.add_argument("--data-root", default=None,
                    help="数据根目录（默认为 config 所在仓库根）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-lock-sync", action="store_true")
    args = ap.parse_args()

    config_path = Path(args.config).resolve()
    REPO_ROOT = config_path.parent.parent
    data_root = Path(args.data_root).resolve() if args.data_root else REPO_ROOT
    http = Http(token=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))

    notes = []
    if args.skip_lock_sync:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config, notes = sync_registry(http, config_path)

    tools = config.get("tools", {})
    defaults = config.get("defaults", {})
    only = {n.strip() for n in args.only.split(",") if n.strip()}
    if only:
        unknown = only - set(tools)
        if unknown:
            log(f"警告: --only 中存在未登记工具: {', '.join(sorted(unknown))}")
        tools = {k: v for k, v in tools.items() if k in only}

    index_path = REPO_ROOT / "index.json"
    index_entries = {}
    reports = []
    for tool, cfg in tools.items():
        cfg = cfg or {}
        if cfg.get("enabled") is False:
            log(f"[{tool}] 已在配置中停用，跳过")
            continue
        stats = {"scanned": 0, "added": 0, "added_bytes": 0, "already": 0,
                 "planned": 0, "versions_done": 0}
        report = {"tool": tool, "stats": stats, "errors": []}
        reports.append(report)
        log(f"[{tool}] 开始收集 (type={cfg.get('type', 'github_releases')})")
        try:
            kind = cfg.get("type", "github_releases")
            if kind == "github_releases":
                collect_github_releases(http, tool, cfg, defaults, data_root,
                                        args.dry_run, index_entries, stats, report)
            elif kind == "url_index":
                collect_url_index(http, tool, cfg, defaults, data_root,
                                  args.dry_run, index_entries, stats, report)
            elif kind == "url_list":
                collect_url_list(http, tool, cfg, defaults, data_root,
                                 args.dry_run, index_entries, stats, report)
            else:
                raise RuntimeError(f"未知来源类型: {kind}")
            log(f"[{tool}] 完成: 扫描 {stats['scanned']} 版本, "
                f"新增 {stats['added']} 文件, 已存在 {stats['already']}")
        except Exception as e:
            report["errors"].append(str(e))
            log(f"[{tool}] 失败: {e}")

    if not args.dry_run:
        update_index(data_root, index_path, index_entries)
    write_summary(os.environ.get("GITHUB_STEP_SUMMARY"), notes, reports)


if __name__ == "__main__":
    main()
