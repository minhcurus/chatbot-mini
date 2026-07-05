import base64
import os
import re
import subprocess

import requests

GITHUB_API = "https://api.github.com"
MANIFEST_PATH = "manifest.json"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")


def _detect_repo_slug() -> str:
    slug = os.getenv("GITHUB_REPOSITORY")
    if slug:
        return slug

    slug = os.getenv("RENDER_GIT_REPO_SLUG")
    if slug:
        return slug


    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True,
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


def _headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def _contents_url() -> str:
    return f"{GITHUB_API}/repos/{GITHUB_REPOSITORY}/contents/{MANIFEST_PATH}"


def pull_latest_manifest():
    if not _configured():
        print("manifest_sync: GITHUB_TOKEN/GITHUB_REPOSITORY not set; skipping pull.")
        return

    resp = requests.get(
        _contents_url(), headers=_headers(),
        params={"ref": GITHUB_BRANCH}, timeout=30,
    )

    if resp.status_code == 200:
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"manifest_sync: pulled {MANIFEST_PATH} from "
              f"{GITHUB_REPOSITORY}@{GITHUB_BRANCH} via GitHub API.")
    elif resp.status_code == 404:
        print(f"manifest_sync: {MANIFEST_PATH} not found in repo yet (first run).")
    else:
        print(f"manifest_sync: pull failed ({resp.status_code}): {resp.text[:300]}")


def push_manifest():
    if not _configured():
        print("manifest_sync: GITHUB_TOKEN/GITHUB_REPOSITORY not set; skipping push.")
        return
    if not os.path.exists(MANIFEST_PATH):
        print(f"manifest_sync: no {MANIFEST_PATH} on disk; nothing to push.")
        return

    with open(MANIFEST_PATH, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    sha = None
    check = requests.get(
        _contents_url(), headers=_headers(),
        params={"ref": GITHUB_BRANCH}, timeout=30,
    )
    if check.status_code == 200:
        sha = check.json().get("sha")
    elif check.status_code != 404:
        print(f"manifest_sync: could not check existing file "
              f"({check.status_code}); aborting push.")
        return

    payload = {
        "message": "chore: update manifest.json [skip ci]",
        "content": encoded,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(_contents_url(), headers=_headers(), json=payload, timeout=30)
    if put_resp.status_code in (200, 201):
        print("manifest_sync: committed and pushed manifest.json via GitHub API.")
    else:
        print(f"manifest_sync: push failed ({put_resp.status_code}): {put_resp.text[:300]}")