from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from lustro.fetcher import archive_article, fetch_rss, fetch_web, fetch_x_account


class Entry(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def test_fetch_rss(monkeypatch):
    entries = [
        Entry(
            title="Old post",
            link="https://example.com/old",
            summary="<p>Old summary.</p>",
            published_parsed=SimpleNamespace(tm_year=2026, tm_mon=2, tm_mday=20),
        ),
        Entry(
            title="New post",
            link="https://example.com/new",
            summary="<p>New summary sentence one. Sentence two.</p>",
            published_parsed=SimpleNamespace(tm_year=2026, tm_mon=2, tm_mday=24),
        ),
    ]
    monkeypatch.setattr(
        "lustro.fetcher.feedparser.parse",
        lambda _url, request_headers: SimpleNamespace(entries=entries, bozo=False),
    )

    articles = fetch_rss("https://example.com/feed.xml", "2026-02-23")

    assert len(articles) == 1
    assert articles[0]["title"] == "New post"
    assert articles[0]["date"] == "2026-02-24"
    assert articles[0]["summary"] == "New summary sentence one"


def test_fetch_rss_dead_feed(monkeypatch):
    monkeypatch.setattr(
        "lustro.fetcher.feedparser.parse",
        lambda _url, request_headers: SimpleNamespace(bozo=True, status=404),
    )

    articles = fetch_rss("https://example.com/dead.xml", "2026-02-23")
    assert articles is None


def test_fetch_web(monkeypatch):
    html = """
    <html><body>
      <article><h2><a href="/p/new">A proper article title here</a></h2></article>
    </body></html>
    """

    class FakeResp:
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr("lustro.fetcher.requests.get", lambda *args, **kwargs: FakeResp())

    articles = fetch_web("https://example.com")

    assert len(articles) == 1
    assert articles[0]["title"] == "A proper article title here"
    assert articles[0]["link"] == "https://example.com/p/new"


def test_archive_article(monkeypatch, tmp_path):
    monkeypatch.setattr("lustro.fetcher.trafilatura.fetch_url", lambda _url: "raw-page")
    monkeypatch.setattr("lustro.fetcher.trafilatura.extract", lambda _raw: "full text body")

    article = {
        "title": "A New Discovery in AI",
        "date": "2026-02-24",
        "summary": "A summary",
        "link": "https://example.com/article",
    }
    now = datetime(2026, 2, 24, 12, 30, tzinfo=timezone.utc)

    archive_article(article, "Example Source", 1, tmp_path, now)

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["title"] == article["title"]
    assert payload["text"] == "full text body"
    assert payload["fetched_at"] == now.isoformat()


def test_fetch_x_account(monkeypatch):
    monkeypatch.setattr("lustro.fetcher.shutil.which", lambda _name: "/usr/local/bin/bird")

    tweets = [
        {
            "id": "111",
            "createdAt": "Fri Feb 20 23:18:59 +0000 2026",
            "text": "too old tweet content that should be skipped",
            "author": {"username": "alice"},
        },
        {
            "id": "222",
            "createdAt": "Tue Feb 24 10:00:00 +0000 2026",
            "text": "A new and sufficiently long tweet update about AI agents and tooling.",
            "author": {"username": "alice"},
        },
    ]

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(tweets), stderr="")

    monkeypatch.setattr("lustro.fetcher.subprocess.run", fake_run)

    articles = fetch_x_account("@alice", "2026-02-23")

    assert len(articles) == 1
    assert articles[0]["date"] == "2026-02-24"
    assert articles[0]["link"] == "https://x.com/alice/status/222"


def test_check_sources_zeros(monkeypatch, capsys):
    from lustro.fetcher import check_sources

    sources = [{"name": "ZeroSource", "tier": 1, "url": "https://example.com"}]
    state = {"_zeros:ZeroSource": "3"}
    now = datetime(2026, 2, 24, 12, 0, tzinfo=timezone.utc)

    class FakeResp:
        status_code = 200

        def close(self):
            pass

    monkeypatch.setattr("lustro.fetcher.requests.get", lambda *args, **kwargs: FakeResp())

    check_sources(sources, [], state, now=now)
    stderr = capsys.readouterr().err
    assert "(3x0)" in stderr
