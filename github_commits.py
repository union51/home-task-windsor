"""Fetch commits from the public GitHub REST API."""

import time

import requests

API = "https://api.github.com"
MAX_PER_PAGE = 100
MAX_INLINE_WAIT = 30


class GitHubRateLimitError(Exception):
    def __init__(self, message, reset_at=None):
        super().__init__(message)
        self.reset_at = reset_at


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


def _header(headers, name):
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _is_rate_limited(response):
    if response.status_code not in (403, 429):
        return False
    remaining = _header(response.headers, "X-RateLimit-Remaining")
    if remaining is not None and str(remaining).strip() == "0":
        return True
    if response.status_code == 429:
        return True
    if _header(response.headers, "Retry-After") is not None:
        return True
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    message = (body.get("message") or "").lower()
    return "rate limit" in message


def _reset_at(headers):
    raw = _header(headers, "X-RateLimit-Reset")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _backoff_seconds(headers, now):
    """How long to sleep. None means the window is too far out to wait here."""
    retry_after = _header(headers, "Retry-After")
    if retry_after is not None:
        try:
            secs = int(float(retry_after))
        except (TypeError, ValueError):
            secs = 5
        if secs > MAX_INLINE_WAIT:
            return None
        return max(secs, 0)

    reset_at = _reset_at(headers)
    if reset_at is not None:
        secs = reset_at - int(now())
        if secs > MAX_INLINE_WAIT:
            return None
        # +1 so we don't retry in the same second the window opens
        return max(secs, 0) + 1

    return 5


class CommitClient:
    def __init__(self, owner, repo, session=None, max_retries=3, sleeper=None, now=None):
        self.owner = owner
        self.repo = repo
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.sleeper = sleeper or time.sleep
        self.now = now or time.time
        self.session.headers.setdefault("User-Agent", "github-commits-client")
        self.session.headers.setdefault("Accept", "application/vnd.github+json")

    def fetch_page(self, page, per_page):
        per_page = clamp_per_page(per_page)
        url = f"{API}/repos/{self.owner}/{self.repo}/commits"
        params = {"page": page, "per_page": per_page}

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.session.get(url, params=params, timeout=20)
            except requests.RequestException:
                if attempt >= self.max_retries:
                    raise
                self.sleeper(min(2 ** attempt, MAX_INLINE_WAIT))
                continue

            if _is_rate_limited(response):
                wait = _backoff_seconds(response.headers, self.now)
                if wait is None or attempt >= self.max_retries:
                    raise GitHubRateLimitError(
                        "GitHub rate limit hit. Try again after the reset, or send a token.",
                        reset_at=_reset_at(response.headers),
                    )
                self.sleeper(wait)
                continue

            if response.status_code >= 500:
                if attempt >= self.max_retries:
                    response.raise_for_status()
                self.sleeper(min(2 ** attempt, MAX_INLINE_WAIT))
                continue

            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError("expected a list of commits")
            return [normalize_commit(item) for item in data]

    def fetch_commits(self, pages=5, per_page=100):
        if pages < 1:
            return []
        per_page = clamp_per_page(per_page)
        commits = []
        for page in range(1, pages + 1):
            batch = self.fetch_page(page, per_page)
            if not batch:
                break
            commits.extend(batch)
            if len(batch) < per_page:
                break
        return commits
