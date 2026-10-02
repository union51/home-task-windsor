import pytest
import requests

from github_commits import CommitClient, GitHubRateLimitError, main, normalize_commit


SAMPLE = {
    "sha": "abc123",
    "commit": {
        "author": {
            "name": "Linus Torvalds",
            "email": "torvalds@linux-foundation.org",
            "date": "2026-01-02T03:04:05Z",
        },
        "message": "subject line\n\nmore detail\n",
    },
    "author": {"login": "torvalds"},
}


class FakeResponse:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        if not self._responses:
            raise AssertionError("unexpected extra request")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(responses, now=None, max_retries=3):
    sleeps = []
    session = FakeSession(responses)
    client = CommitClient(
        "torvalds",
        "linux",
        session=session,
        max_retries=max_retries,
        sleeper=sleeps.append,
        now=now or (lambda: 1000),
    )
    return client, session, sleeps


def test_normalize_commit_uses_git_author():
    assert normalize_commit(SAMPLE) == {
        "sha": "abc123",
        "author_name": "Linus Torvalds",
        "author_email": "torvalds@linux-foundation.org",
        "date": "2026-01-02T03:04:05Z",
        "message": "subject line\n\nmore detail",
    }


def test_normalize_commit_fills_missing_fields():
    got = normalize_commit({"commit": None})
    assert got == {
        "sha": "",
        "author_name": "",
        "author_email": "",
        "date": "",
        "message": "",
    }
    assert normalize_commit("nope")["message"] == ""


def test_fetch_commits_stops_on_a_short_page():
    pages = [
        FakeResponse(200, [SAMPLE, SAMPLE]),
        FakeResponse(200, [SAMPLE]),
        FakeResponse(200, [SAMPLE]),
    ]
    client, session, sleeps = make_client(pages)
    commits = client.fetch_commits(pages=5, per_page=2)

    assert len(commits) == 3
    assert [call["params"]["page"] for call in session.calls] == [1, 2]
    assert session.calls[0]["params"]["per_page"] == 2
    assert "torvalds/linux/commits" in session.calls[0]["url"]
    assert sleeps == []


def test_per_page_is_capped_at_100():
    client, session, _ = make_client([FakeResponse(200, [])])
    assert client.fetch_commits(pages=3, per_page=250) == []
    assert session.calls[0]["params"]["per_page"] == 100
    assert len(session.calls) == 1


def test_rate_limit_backs_off_and_retries():
    limited = FakeResponse(
        403,
        {"message": "API rate limit exceeded for 1.2.3.4."},
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1010"},
    )
    client, session, sleeps = make_client([limited, FakeResponse(200, [SAMPLE])])
    commits = client.fetch_page(1, 1)

    assert commits[0]["sha"] == "abc123"
    assert sleeps == [11]
    assert len(session.calls) == 2


def test_retry_after_is_waited_out():
    limited = FakeResponse(
        429,
        {"message": "You have exceeded a secondary rate limit."},
        headers={"Retry-After": "2"},
    )
    client, _, sleeps = make_client([limited, FakeResponse(200, [])])
    assert client.fetch_page(4, 10) == []
    assert sleeps == [2]


def test_far_rate_limit_reset_is_not_slept_through():
    limited = FakeResponse(
        403,
        {"message": "API rate limit exceeded"},
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9000"},
    )
    client, session, sleeps = make_client([limited])
    with pytest.raises(GitHubRateLimitError) as caught:
        client.fetch_page(1, 10)

    assert caught.value.reset_at == 9000
    assert sleeps == []
    assert len(session.calls) == 1


def test_unrelated_403_is_not_treated_as_rate_limit():
    denied = FakeResponse(403, {"message": "Repository access blocked"})
    client, session, sleeps = make_client([denied])
    with pytest.raises(requests.HTTPError):
        client.fetch_page(1, 10)
    assert sleeps == []
    assert len(session.calls) == 1


def test_main_prints_json_lines(capsys, monkeypatch):
    def fake_fetch(self, pages=5, per_page=100):
        assert (pages, per_page) == (2, 10)
        return [normalize_commit(SAMPLE)]

    monkeypatch.setattr(CommitClient, "fetch_commits", fake_fetch)
    code = main(["torvalds", "linux", "--pages", "2", "--per-page", "10"])
    out = capsys.readouterr()
    assert code == 0
    assert '"sha": "abc123"' in out.out
    assert "1 commits" in out.err


def test_main_exits_quietly_on_rate_limit(capsys, monkeypatch):
    def fake_fetch(self, pages=5, per_page=100):
        raise GitHubRateLimitError("GitHub rate limit hit.", reset_at=123)

    monkeypatch.setattr(CommitClient, "fetch_commits", fake_fetch)
    code = main(["torvalds", "linux"])
    out = capsys.readouterr()
    assert code == 1
    assert "rate limit" in out.err
    assert "123" in out.err
    assert out.out == ""
