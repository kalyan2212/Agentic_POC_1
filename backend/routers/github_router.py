import json
import os
from fastapi import APIRouter, HTTPException

from database import db
from models import GitHubConnectRequest
from services import github_client

router = APIRouter()


def _mask_token(token: str) -> str:
    if len(token) < 8:
        return "***"
    return token[:4] + "..." + token[-4:]


async def _get_saved_token(user: str | None = None) -> str | None:
    async with db() as conn:
        if user:
            row = await conn.execute_fetchone("SELECT token FROM github_tokens WHERE username=?", (user,))
        else:
            row = await conn.execute_fetchone("SELECT token FROM github_tokens ORDER BY connected_at DESC LIMIT 1")
        return row[0] if row else None


async def bootstrap_env_credentials() -> bool:
    user = os.getenv("JARVIS_GITHUB_USER", "").strip()
    token = os.getenv("JARVIS_GITHUB_PAT", "").strip()
    if not user or not token:
        return False

    try:
        profile = github_client.get_user(token)
    except Exception:
        return False

    async with db() as conn:
        await conn.execute(
            """
            INSERT INTO github_tokens(username, token, profile_json)
            VALUES (?,?,?)
            ON CONFLICT(username) DO UPDATE SET
              token=excluded.token,
              connected_at=datetime('now'),
              profile_json=excluded.profile_json
            """,
            (user, token, json.dumps(profile)),
        )
        await conn.commit()
    return True


@router.post("/connect")
async def connect(req: GitHubConnectRequest):
    try:
        profile = github_client.get_user(req.token)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"GitHub auth failed: {e}")

    async with db() as conn:
        await conn.execute(
            """
            INSERT INTO github_tokens(username, token, profile_json)
            VALUES (?,?,?)
            ON CONFLICT(username) DO UPDATE SET
              token=excluded.token,
              connected_at=datetime('now'),
              profile_json=excluded.profile_json
            """,
            (req.user, req.token, json.dumps(profile)),
        )
        await conn.commit()

    return {
        "connected": True,
        "username": profile.get("login", req.user),
        "name": profile.get("name"),
        "avatar_url": profile.get("avatar_url"),
        "public_repos": profile.get("public_repos", 0),
        "message": f"Connected with token {_mask_token(req.token)}",
    }


@router.get("/profile")
async def profile(user: str | None = None):
    token = await _get_saved_token(user)
    if not token:
        raise HTTPException(status_code=404, detail="No GitHub connection found")
    try:
        return github_client.get_user(token)
    except Exception:
        async with db() as conn:
            if user:
                row = await conn.execute_fetchone("SELECT profile_json FROM github_tokens WHERE username=?", (user,))
            else:
                row = await conn.execute_fetchone("SELECT profile_json FROM github_tokens ORDER BY connected_at DESC LIMIT 1")
        if row and row[0]:
            try:
                return json.loads(row[0])
            except Exception:
                pass
        raise HTTPException(status_code=400, detail="Failed to read profile")


@router.get("/repos/{user}")
async def repos(user: str):
    token = await _get_saved_token(user)
    try:
        data = github_client.list_repos(user, token)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed listing repos: {e}")

    async with db() as conn:
        for r in data:
            await conn.execute(
                """
                INSERT INTO repos(github_id, owner, name, full_name, description, language, stars, forks, size_kb,
                                  default_branch, private, html_url, clone_url, topics_json, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                ON CONFLICT(full_name) DO UPDATE SET
                    description=excluded.description,
                    language=excluded.language,
                    stars=excluded.stars,
                    forks=excluded.forks,
                    size_kb=excluded.size_kb,
                    default_branch=excluded.default_branch,
                    private=excluded.private,
                    html_url=excluded.html_url,
                    clone_url=excluded.clone_url,
                    topics_json=excluded.topics_json,
                    fetched_at=datetime('now')
                """,
                (
                    r.get("id"),
                    r.get("owner", {}).get("login", user),
                    r.get("name"),
                    r.get("full_name"),
                    r.get("description"),
                    r.get("language"),
                    r.get("stargazers_count", 0),
                    r.get("forks_count", 0),
                    r.get("size", 0),
                    r.get("default_branch", "main"),
                    1 if r.get("private") else 0,
                    r.get("html_url"),
                    r.get("clone_url"),
                    json.dumps(r.get("topics", [])),
                ),
            )
        await conn.commit()

    return {
        "user": user,
        "count": len(data),
        "repos": [
            {
                "full_name": r.get("full_name"),
                "name": r.get("name"),
                "description": r.get("description"),
                "language": r.get("language"),
                "stars": r.get("stargazers_count", 0),
                "default_branch": r.get("default_branch", "main"),
            }
            for r in data
        ],
    }


@router.get("/content/{user}/{repo}")
async def content(user: str, repo: str, path: str = ""):
    token = await _get_saved_token(user)
    try:
        if path:
            txt = github_client.get_file_content(user, repo, path, token)
            return {"path": path, "content": txt}
        meta = github_client.get_repo(user, repo, token)
        tree = github_client.get_repo_tree(user, repo, meta.get("default_branch", "main"), token)
        return {"repo": f"{user}/{repo}", "files": [t.get("path") for t in tree if t.get("type") == "blob"]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed repo content read: {e}")


@router.delete("/connect")
async def disconnect(user: str | None = None):
    async with db() as conn:
        if user:
            await conn.execute("DELETE FROM github_tokens WHERE username=?", (user,))
        else:
            await conn.execute("DELETE FROM github_tokens")
        await conn.commit()
    return {"disconnected": True}
