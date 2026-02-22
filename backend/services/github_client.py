import base64
import json
import urllib.parse
import urllib.request
from typing import List, Optional

GITHUB_API = "https://api.github.com"


def _request(path: str, token: Optional[str] = None) -> dict | list:
    url = f"{GITHUB_API}{path}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "jarvis-backend")
    if token:
                req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_user(token: str) -> dict:
    return _request("/user", token)


def list_repos(user: str, token: Optional[str] = None) -> List[dict]:
    q_user = urllib.parse.quote(user)
    data = _request(f"/users/{q_user}/repos?per_page=100&sort=updated", token)
    if isinstance(data, list):
        return data
    return []


def get_repo(owner: str, repo: str, token: Optional[str] = None) -> dict:
    return _request(f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}", token)


def get_repo_tree(owner: str, repo: str, branch: str, token: Optional[str] = None) -> List[dict]:
    q_owner = urllib.parse.quote(owner)
    q_repo = urllib.parse.quote(repo)
    q_branch = urllib.parse.quote(branch)
    tree_data = _request(f"/repos/{q_owner}/{q_repo}/git/trees/{q_branch}?recursive=1", token)
    return tree_data.get("tree", []) if isinstance(tree_data, dict) else []


def get_file_content(owner: str, repo: str, path: str, token: Optional[str] = None) -> str:
    q_owner = urllib.parse.quote(owner)
    q_repo = urllib.parse.quote(repo)
    q_path = urllib.parse.quote(path)
    content_obj = _request(f"/repos/{q_owner}/{q_repo}/contents/{q_path}", token)
    if isinstance(content_obj, dict) and content_obj.get("encoding") == "base64":
        raw = base64.b64decode(content_obj.get("content", ""))
        return raw.decode("utf-8", errors="ignore")
    return ""


def get_readme(owner: str, repo: str, token: Optional[str] = None) -> str:
    q_owner = urllib.parse.quote(owner)
    q_repo = urllib.parse.quote(repo)
    readme = _request(f"/repos/{q_owner}/{q_repo}/readme", token)
    if isinstance(readme, dict) and readme.get("encoding") == "base64":
        raw = base64.b64decode(readme.get("content", ""))
        return raw.decode("utf-8", errors="ignore")
    return ""


def parse_full_name(full_name: str) -> tuple[str, str]:
    if "/" not in full_name:
        raise ValueError("Repository must be in owner/repo format")
    owner, repo = full_name.split("/", 1)
    return owner.strip(), repo.strip()
