"""Fetch commits from the public GitHub REST API."""

import requests

API = "https://api.github.com"
MAX_PER_PAGE = 100


def clamp_per_page(per_page):
    try:
        n = int(per_page)
    except (TypeError, ValueError):
        n = 30
    if n < 1:
        return 1
    if n > MAX_PER_PAGE:
        return MAX_PER_PAGE
    return n


def normalize_commit(raw):
    if not isinstance(raw, dict):
        raw = {}
    commit = raw.get("commit") or {}
    author = commit.get("author") or {}
    message = commit.get("message") or ""
    if not isinstance(message, str):
        message = str(message)
    return {
        "sha": raw.get("sha") or "",
        "author_name": author.get("name") or "",
        "author_email": author.get("email") or "",
        "date": author.get("date") or "",
        "message": message.strip(),
    }


class CommitClient:
    def __init__(self, owner, repo, session=None):
        self.owner = owner
        self.repo = repo
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "github-commits-client")
        self.session.headers.setdefault("Accept", "application/vnd.github+json")

    def fetch_page(self, page, per_page):
        per_page = clamp_per_page(per_page)
        url = f"{API}/repos/{self.owner}/{self.repo}/commits"
        response = self.session.get(
            url,
            params={"page": page, "per_page": per_page},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("expected a list of commits")
        return [normalize_commit(item) for item in data]
