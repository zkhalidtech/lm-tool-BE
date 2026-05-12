"""
LVRG Lead Magnet Engine — GitHub Pages Deployer
Uses GitHub Git Data API (blob + tree + commit) to handle files of any size.
No git clone needed. No file size limits.
"""

import os
import base64
import json
import urllib.request
import urllib.error
from config import GITHUB_USER, GITHUB_REPO, PREVIEW_BASE_URL


def _api(method: str, path: str, body: dict = None) -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "lvrg-engine")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API {method} {path} → {e.code}: {e.read().decode()}")


def deploy_site(prospect_id: str, site_dir: str) -> str:
    """Push site to GitHub Pages via Git Data API. No size limits. Returns public URL."""

    print(f"  [deploy] Pushing {prospect_id} via Git Data API...")

    # Read the HTML file
    index_path = os.path.join(site_dir, "index.html")
    with open(index_path, "rb") as f:
        content = f.read()

    # 1. Create a blob with the file content
    blob = _api("POST", "git/blobs", {
        "content": base64.b64encode(content).decode(),
        "encoding": "base64"
    })
    blob_sha = blob["sha"]

    # 2. Get current HEAD commit and its tree
    ref = _api("GET", "git/ref/heads/main")
    head_sha = ref["object"]["sha"]
    head_commit = _api("GET", f"git/commits/{head_sha}")
    base_tree_sha = head_commit["tree"]["sha"]

    # 3. Create a new tree with our file
    tree = _api("POST", "git/trees", {
        "base_tree": base_tree_sha,
        "tree": [
            {
                "path": f"{prospect_id}/index.html",
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha
            }
        ]
    })
    tree_sha = tree["sha"]

    # 4. Create a commit pointing to the new tree
    commit = _api("POST", "git/commits", {
        "message": f"Add preview: {prospect_id}",
        "tree": tree_sha,
        "parents": [head_sha]
    })
    commit_sha = commit["sha"]

    # 5. Update the branch ref to the new commit
    _api("PATCH", "git/refs/heads/main", {
        "sha": commit_sha,
        "force": False
    })

    public_url = f"{PREVIEW_BASE_URL}/{prospect_id}/index.html"
    print(f"  [deploy] Live at: {public_url}")
    return public_url
