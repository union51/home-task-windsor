# GitHub commits client

Pulls commit pages from the public GitHub REST API and flattens them into one shape: `sha`, `author_name`, `author_email`, `date`, `message`.

Started: 2026-10-02 10:40 AM

Finished: 2026-10-02 12:09 PM


## Run

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python github_commits.py torvalds linux --pages 5 --per-page 100
.venv\Scripts\python -m pytest
```

On Linux or macOS the venv binaries live in `bin/` rather than `Scripts\`.

I ran it against `torvalds/linux` with `--pages 5 --per-page 100`. That returned 500 commits, each with all five fields filled in. Stdout is one JSON object per line. The count goes to stderr. If we hit a rate limit we should not wait out, the process exits 1 and prints the reset time.

## Decisions

Paging uses `page` and `per_page`, which is what this task asked for. `per_page` is clamped to 100 because that is the API maximum. A short page stops the walk, so we don't keep requesting empty pages after the repo runs out. An exactly full last page costs one extra empty request. Fine for a handful of pages.

`Link` rel=next is the better way to fetch "everything", since you don't have to guess a page count. I didn't use it. Five pages don't need it, and sticking to the query params keeps the client obvious.

The record always has the same five keys. Missing values are `""`, so printing a row doesn't need a pile of null checks. Name and email come from `commit.author`, the git author. The top-level `author` is the GitHub account, and it is null on plenty of commits, including older ones in linux. `date` is left as the ISO string GitHub sent. `message` is the whole message, subject and body, with surrounding whitespace stripped.

Rate limit handling looks at 403 and 429, `X-RateLimit-Remaining: 0`, `Retry-After`, and a body that mentions a rate limit. If the wait is 30 seconds or less we sleep and try again, up to 3 attempts. If the reset is further out, we raise `GitHubRateLimitError` and include the reset timestamp. Sitting in `sleep` until the hourly window opens is a bad default for a command you are watching. A 403 that is not a rate limit (no User-Agent, blocked repo) is not retried. GitHub also returns 403 when the User-Agent header is missing, so the client always sends one.

Connection errors and HTTP 500s get the same short retry. They are not rate limits, they just show up.

Tests pass in a fake session, plus `sleeper` and `now`, so they can check the backoff without actually waiting.

## Trade-offs

No auth token. Sixty unauthenticated requests an hour is enough to pull a few pages and not enough for anything scheduled.

All pages are held in a list. Five pages of 100 is small. The full linux history would not be.

The tests will not catch GitHub renaming a JSON field. They fake the HTTP layer instead of replaying a recorded response.

The first live run died on a non-ascii author name because the Windows console is cp1252. Stdout and stderr are switched to utf-8 before we print.

## With more time

Send `GITHUB_TOKEN` when it is set.

Follow `Link` when the caller wants every commit, and write each page out as it arrives instead of buffering.

Persist `X-RateLimit-Reset` and resume later, instead of exiting.

Use `If-None-Match` so a second run doesn't spend the rate limit again.

URL-encode the owner and repo. Not required for `torvalds/linux`.
