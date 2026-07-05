import os
import re
import subprocess

REPO_DIR = os.getcwd()
MANIFEST_PATH = "manifest.json"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")


def _detect_repo_slug() -> str:
    # 1. manual override
    slug = os.getenv("GITHUB_REPOSITORY")
    if slug:
        return slug

    # 2. Render auto-injects this when deploying from a connected repo
    slug = os.getenv("RENDER_GIT_REPO_SLUG")
    if slug:
        return slug

    # 3. fall back to parsing the local git remote (useful for local runs)
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=REPO_DIR, capture_output=True, text=True,
    )
    if result.returncode == 0:
        url = result.stdout.strip()
        match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(\.git)?$", url)
        if match:
            return match.group(1)

    return ""


def _detect_branch() -> str:
    return os.getenv("GITHUB_BRANCH") or os.getenv("RENDER_GIT_BRANCH") or "main"


GITHUB_REPOSITORY = _detect_repo_slug()
GITHUB_BRANCH = _detect_branch()


def _configured() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_REPOSITORY)


def _is_git_repo() -> bool:
    return os.path.isdir(os.path.join(REPO_DIR, ".git"))


def _run(cmd):
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"manifest_sync: `{' '.join(cmd)}` failed: {result.stderr.strip()}")
    return result


def _remote_url() -> str:
    return f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_REPOSITORY}.git"


def pull_latest_manifest():
    if not _configured():
        print("manifest_sync: GITHUB_TOKEN/GITHUB_REPOSITORY not set; skipping pull.")
        return
    if not _is_git_repo():
        print("manifest_sync: no .git directory found; skipping pull.")
        return

    _run(["git", "config", "user.email", "optibot@render.local"])
    _run(["git", "config", "user.name", "OptiBot Daily Job"])
    _run(["git", "remote", "set-url", "origin", _remote_url()])
    _run(["git", "fetch", "origin", GITHUB_BRANCH])

    reset = _run(["git", "reset", "--hard", f"origin/{GITHUB_BRANCH}"])
    if reset.returncode == 0:
        print(f"manifest_sync: pulled latest state from origin/{GITHUB_BRANCH}.")
    else:
        print("manifest_sync: pull failed; continuing with whatever manifest.json is on disk (if any).")


def push_manifest():
    if not _configured():
        print("manifest_sync: GITHUB_TOKEN/GITHUB_REPOSITORY not set; skipping push.")
        return
    if not _is_git_repo():
        print("manifest_sync: no .git directory found; skipping push.")
        return
    if not os.path.exists(MANIFEST_PATH):
        print("manifest_sync: no manifest.json on disk; nothing to push.")
        return

    _run(["git", "add", MANIFEST_PATH])

    diff = _run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print("manifest_sync: manifest.json unchanged; nothing to commit.")
        return

    commit = _run(["git", "commit", "-m", "chore: update manifest.json [skip ci]"])
    if commit.returncode != 0:
        print("manifest_sync: commit failed; manifest.json changes were NOT pushed.")
        return

    push = _run(["git", "push", "origin", f"HEAD:{GITHUB_BRANCH}"])
    if push.returncode == 0:
        print("manifest_sync: committed and pushed manifest.json.")
    else:
        print("manifest_sync: push failed; manifest.json changes were NOT saved to remote.")