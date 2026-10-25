#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本项目发布到 GitHub（纯 Python 标准库，无需安装 git / gh）。

原理：使用 GitHub REST API 的 Git Data 接口（blob → tree → commit → ref），
一次性创建一个包含全部源码的提交；可选地把 dist/ 下的安装包上传为 Release 附件。

用法示例：
    # 1) 先导出令牌（推荐，避免出现在命令历史里）
    #    Linux/macOS:  export GITHUB_TOKEN=ghp_xxx
    #    Windows:      set GITHUB_TOKEN=ghp_xxx
    python3 tools/publish_github.py --owner <GitHub用户名> --repo uos-autostart-manager --visibility private

    # 2) 只预览会提交哪些文件（不需要令牌）
    python3 tools/publish_github.py --dry-run

    # 3) 同时创建 v1.1.0 Release 并上传 deb / 源码包
    python3 tools/publish_github.py --owner <用户名> --release

令牌权限（二选一）：
  - 经典 PAT：勾选 repo（私有仓库还需 workflow 权限，如要提交 .github/workflows）
  - Fine-grained PAT：Repository permissions → Contents: Read and write；
    Administration: Read and write（仅当需要自动创建仓库时）
令牌也可写入文件后用 --token-file 指定，或直接 --token 传入（不推荐）。
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.github.com"
USER_AGENT = "uos-autostart-manager-publisher/1.1.0"
API_VERSION = "2022-11-28"

SKIP_DIRS = {
    ".git", ".github-workflows-cache", "build", "dist", "__pycache__",
    ".venv", ".venv-tools", ".idea", ".vscode", ".pytest_cache",
}
SKIP_SUFFIXES = (".pyc", ".pyo", ".deb", ".tar.gz", ".log", ".tmp", ".swp")
SKIP_FILES = {".DS_Store", "Thumbs.db"}
TEXT_MODE_DEFAULT = 0o644


def info(message):
    sys.stdout.write("%s\n" % (message,))
    sys.stdout.flush()


def fail(message):
    sys.stderr.write("错误：%s\n" % (message,))
    sys.exit(1)


def read_version():
    path = os.path.join(ROOT, "VERSION")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return "0.0.0"


# ---------------------------------------------------------------------------
# 文件收集与哈希
# ---------------------------------------------------------------------------
def collect_files():
    """返回 [(相对路径, 字节内容, git mode)]，按路径排序。"""
    entries = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in SKIP_DIRS and not name.startswith(".venv")
        )
        for filename in sorted(filenames):
            if filename in SKIP_FILES:
                continue
            if filename.endswith(SKIP_SUFFIXES):
                continue
            full = os.path.join(dirpath, filename)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            if rel.startswith("dist/") or rel.startswith("build/"):
                continue
            if ".venv" in rel.split("/"):
                continue
            with open(full, "rb") as handle:
                data = handle.read()
            mode = "100755" if filename.endswith(".sh") else "100644"
            entries.append((rel, data, mode))
    entries.sort(key=lambda item: item[0])
    return entries


def git_blob_sha(data):
    header = b"blob %d\x00" % len(data)
    return hashlib.sha1(header + data).hexdigest()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http(method, path, token=None, payload=None, raw_body=None,
         content_type=None, timeout=90):
    """返回 (status, data)；data 为 dict/list 或原始 bytes。"""
    url = path if path.startswith("http") else API + path
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": API_VERSION,
    }
    if token:
        headers["Authorization"] = "Bearer %s" % (token,)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        headers["Content-Type"] = content_type or "application/octet-stream"
        headers["Content-Length"] = str(len(raw_body))
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    except urllib.error.URLError as exc:
        return 0, {"message": "网络错误：%s" % (exc.reason,)}
    if not body:
        return status, {}
    try:
        return status, json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return status, body


def api_ok(status, data, action):
    if 200 <= status < 300:
        return data
    message = ""
    if isinstance(data, dict):
        message = data.get("message", "")
        errors = data.get("errors")
        if errors:
            message += " " + json.dumps(errors, ensure_ascii=False)
    fail("%s 失败（HTTP %s）：%s" % (action, status, message))
    return None


def load_token(args):
    if args.dry_run and not args.token and not os.environ.get("GITHUB_TOKEN"):
        return None
    if args.token:
        return args.token.strip()
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    if args.token_file:
        try:
            with open(args.token_file, "r", encoding="utf-8") as handle:
                token = handle.read().strip()
            if token:
                return token
        except OSError as exc:
            fail("读取令牌文件失败：%s" % (exc,))
    fail("未提供 GitHub 令牌：请设置环境变量 GITHUB_TOKEN，或使用 --token-file。")


# ---------------------------------------------------------------------------
# 仓库与提交
# ---------------------------------------------------------------------------
def ensure_repo(token, owner, repo, description, private, is_user):
    status, data = http("GET", "/repos/%s/%s" % (owner, repo), token)
    if status == 200:
        info("仓库已存在：%s" % (data.get("html_url"),))
        return data
    if status != 404:
        api_ok(status, data, "查询仓库")
    info("仓库不存在，正在创建：%s/%s（%s）" % (
        owner, repo, "私有" if private else "公开"))
    payload = {
        "name": repo,
        "description": description,
        "private": private,
        "has_issues": True,
        "has_wiki": False,
        "has_projects": False,
        "auto_init": False,
    }
    path = "/user/repos" if is_user else "/orgs/%s/repos" % (owner,)
    status, data = http("POST", path, token, payload)
    if status == 404 and not is_user:
        fail("组织 %s 不存在或无权限创建仓库" % (owner,))
    return api_ok(status, data, "创建仓库")


def get_branch_head(token, owner, repo, branch):
    status, data = http("GET", "/repos/%s/%s/git/ref/heads/%s" % (owner, repo, branch), token)
    if status == 200 and isinstance(data, dict):
        return data.get("object", {}).get("sha")
    if status in (404, 409):
        return None
    api_ok(status, data, "读取分支 %s" % (branch,))
    return None


def remote_tree(token, owner, repo, commit_sha):
    status, data = http(
        "GET", "/repos/%s/%s/git/trees/%s?recursive=1" % (owner, repo, commit_sha), token)
    api_ok(status, data, "读取远程文件树")
    result = {}
    for item in data.get("tree", []) or []:
        if item.get("type") == "blob":
            result[item["path"]] = item
    return result


def create_blob(token, owner, repo, data):
    payload = {
        "content": base64.b64encode(data).decode("ascii"),
        "encoding": "base64",
    }
    status, body = http("POST", "/repos/%s/%s/git/blobs" % (owner, repo), token, payload)
    return api_ok(status, body, "创建 blob")["sha"]


def bootstrap_empty_repo(token, owner, repo, files, branch):
    """Git Data API 不能在完全空的仓库上创建 blob，先用 Contents API 建初始提交。"""
    head = get_branch_head(token, owner, repo, branch)
    if head:
        return head
    info("仓库为空，先创建初始提交（README）……")
    readme = b""
    for rel, data, _mode in files:
        if rel == "README.md":
            readme = data
            break
    if not readme:
        readme = ("# %s\n" % (repo,)).encode("utf-8")
    payload = {
        "message": "chore: 初始化仓库",
        "content": base64.b64encode(readme).decode("ascii"),
        "branch": branch,
    }
    status, data = http(
        "PUT", "/repos/%s/%s/contents/README.md" % (owner, repo), token, payload)
    data = api_ok(status, data, "初始化仓库")
    commit_sha = ""
    if isinstance(data, dict):
        commit_sha = (data.get("commit") or {}).get("sha", "")
    if not commit_sha:
        fail("初始化仓库失败：未返回提交信息。")
    info("初始提交：%s" % (commit_sha[:10],))
    return commit_sha


def commit_all(args, token, owner, repo, files, login):
    """把本地文件整体同步为一个提交（完整树重建，自动跳过未修改文件）。"""
    version = read_version()
    head_sha = get_branch_head(token, owner, repo, args.branch)
    remote = {}
    if head_sha:
        status, commit = http("GET", "/repos/%s/%s/git/commits/%s" % (owner, repo, head_sha), token)
        api_ok(status, commit, "读取最新提交")
        remote = remote_tree(token, owner, repo, commit["tree"]["sha"])
        info("远程分支 %s 当前提交：%s（%d 个文件）" % (
            args.branch, head_sha[:10], len(remote)))
    else:
        info("仓库为空，将创建首个提交（分支 %s）。" % (args.branch,))

    local_paths = set(rel for rel, _data, _mode in files)
    tree_entries = []
    changed = 0
    for rel, data, mode in files:
        local_sha = git_blob_sha(data)
        existing = remote.get(rel)
        if existing and existing.get("sha") == local_sha:
            blob_sha = local_sha
        else:
            blob_sha = create_blob(token, owner, repo, data)
            changed += 1
            if changed % 10 == 0:
                info("  已上传 %d 个文件……" % (changed,))
        tree_entries.append({
            "path": rel,
            "mode": mode,
            "type": "blob",
            "sha": blob_sha,
        })

    removed_paths = sorted(rel for rel in remote if rel not in local_paths)
    if head_sha and not changed and not removed_paths:
        info("远程内容已是最新，无需提交。")
        return {"changed": 0, "removed": 0, "head": head_sha}

    status, tree = http(
        "POST", "/repos/%s/%s/git/trees" % (owner, repo), token, {"tree": tree_entries})
    api_ok(status, tree, "创建文件树")

    message = args.commit_message or (
        "发布 UOS 开机自启动管理器 v%s\n\n"
        "由 tools/publish_github.py 自动提交。" % (version,))
    commit_payload = {"message": message, "tree": tree["sha"]}
    if head_sha:
        commit_payload["parents"] = [head_sha]
    status, commit = http(
        "POST", "/repos/%s/%s/git/commits" % (owner, repo), token, commit_payload)
    api_ok(status, commit, "创建提交")

    if head_sha:
        status, ref = http(
            "PATCH", "/repos/%s/%s/git/refs/heads/%s" % (owner, repo, args.branch),
            token, {"sha": commit["sha"], "force": False})
        api_ok(status, ref, "更新分支")
    else:
        status, ref = http(
            "POST", "/repos/%s/%s/git/refs" % (owner, repo), token,
            {"ref": "refs/heads/%s" % (args.branch,), "sha": commit["sha"]})
        api_ok(status, ref, "创建分支")
    return {
        "changed": changed,
        "removed": len(removed_paths),
        "head": commit["sha"],
        "commit_url": commit.get("html_url", ""),
    }


def create_release(args, token, owner, repo):
    version = read_version()
    tag = args.tag or ("v%s" % (version,))
    status, data = http("GET", "/repos/%s/%s/releases/tags/%s" % (owner, repo, tag), token)
    if status == 200:
        info("Release %s 已存在：%s" % (tag, data.get("html_url", "")))
        release = data
    else:
        info("创建 Release %s ……" % (tag,))
        payload = {
            "tag_name": tag,
            "target_commitish": args.branch,
            "name": "%s %s" % (args.repo, tag),
            "body": args.release_notes or (
                "统信 UOS 开机自启动管理器 %s\n\n"
                "- 图形化管理系统开机自启动应用\n"
                "- 掉线自动重启（默认每 20 分钟检测）\n"
                "- 支持从已安装应用中选择或自定义命令\n"
                "- 提供 .deb 安装包（Architecture: all）\n" % (tag,)),
            "draft": False,
            "prerelease": False,
        }
        status, release = http(
            "POST", "/repos/%s/%s/releases" % (owner, repo), token, payload)
        release = api_ok(status, release, "创建 Release")

    dist = os.path.join(ROOT, "dist")
    if not os.path.isdir(dist):
        info("dist/ 不存在，跳过附件上传。")
        return release
    upload_template = release.get("upload_url", "")
    if not upload_template:
        return release
    for filename in sorted(os.listdir(dist)):
        if filename == "SHA256SUMS":
            continue
        if not (filename.endswith(".deb") or filename.endswith(".tar.gz")):
            continue
        path = os.path.join(dist, filename)
        with open(path, "rb") as handle:
            payload = handle.read()
        upload_url = upload_template.split("{")[0] + "?name=" + urllib.parse.quote(filename)
        content_type = "application/vnd.debian.binary-package" \
            if filename.endswith(".deb") else "application/gzip"
        status, data = http(
            "POST", upload_url, token, raw_body=payload, content_type=content_type,
            timeout=300)
        if 200 <= status < 300:
            info("  已上传附件：%s（%d 字节）" % (filename, len(payload)))
        else:
            info("  [警告] 附件上传失败：%s（HTTP %s）" % (filename, status))
    return release


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="把本项目发布到 GitHub（纯标准库，无需 git）")
    parser.add_argument("--owner", default="", help="GitHub 用户名或组织")
    parser.add_argument("--repo", default="uos-autostart-manager", help="仓库名")
    parser.add_argument("--description",
                        default="统信 UOS 开机自启动管理器：图形化管理系统开机自启动，支持掉线自动重启",
                        help="仓库描述")
    parser.add_argument("--visibility", choices=("private", "public"), default="private")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--commit-message", default="")
    parser.add_argument("--release", action="store_true", help="创建 Release 并上传 dist/ 附件")
    parser.add_argument("--tag", default="")
    parser.add_argument("--release-notes", default="")
    parser.add_argument("--token", default="", help="GitHub 令牌（不推荐，优先用环境变量）")
    parser.add_argument("--token-file", default="", help="从文件读取令牌")
    parser.add_argument("--skip-workflows", action="store_true",
                        help="不提交 .github/workflows（令牌缺少 workflow 权限时使用）")
    parser.add_argument("--dry-run", action="store_true", help="只列出将提交的文件")
    args = parser.parse_args(argv)

    files = collect_files()
    if args.skip_workflows:
        files = [entry for entry in files if not entry[0].startswith(".github/workflows/")]
    total = sum(len(data) for _rel, data, _mode in files)
    info("项目根目录：%s" % (ROOT,))
    info("版本：%s" % (read_version(),))
    info("待提交文件：%d 个，共 %.1f KiB" % (len(files), total / 1024.0))
    for rel, data, _mode in files:
        info("  %8d  %s" % (len(data), rel))
    if args.dry_run:
        info("\n[dry-run] 未访问网络。去掉 --dry-run 并提供令牌即可真正上传。")
        return 0

    token = load_token(args)
    status, user = http("GET", "/user", token)
    if status != 200 or not isinstance(user, dict):
        fail("令牌无效或网络异常（HTTP %s）。请确认令牌有效且未过期。" % (status,))
    login = user.get("login", "")
    owner = args.owner or login
    is_user = (owner.lower() == login.lower())
    info("已认证：%s（目标仓库：%s/%s）" % (login, owner, args.repo))

    repo_data = ensure_repo(
        token, owner, args.repo, args.description,
        args.visibility == "private", is_user)
    info("仓库地址：%s" % (repo_data.get("html_url", ""),))

    bootstrap_empty_repo(token, owner, args.repo, files, args.branch)

    result = commit_all(args, token, owner, args.repo, files, login)
    if result.get("commit_url"):
        info("提交成功：%s" % (result["commit_url"],))
    info("本次变更：新增/更新 %d 个文件，删除 %d 个文件" % (
        result.get("changed", 0), result.get("removed", 0)))

    if args.release:
        release = create_release(args, token, owner, args.repo)
        if isinstance(release, dict) and release.get("html_url"):
            info("Release 地址：%s" % (release["html_url"],))

    info("完成：%s" % (repo_data.get("html_url", ""),))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
