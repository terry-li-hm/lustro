from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AI-News-Bot/1.0"}


def _is_safe_url(url: str) -> bool:
    """Block URLs targeting private/reserved IP ranges (SSRF protection)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        addrs = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _type, _proto, _canonname, sockaddr in addrs:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except (socket.gaierror, ValueError, OSError):
        return False
    return True
TIMEOUT = 15
ARCHIVE_TIMEOUT = 10


def _entry_get(entry: Any, key: str, default: Any = "") -> Any:
    if hasattr(entry, "get"):
        try:
            return entry.get(key, default)
        except TypeError:
            pass
    return getattr(entry, key, default)


def _parse_feed_date(entry: Any) -> str:
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, field, None)
        if parsed is None:
            parsed = _entry_get(entry, field, None)
        if parsed:
            return f"{parsed.tm_year}-{parsed.tm_mon:02d}-{parsed.tm_mday:02d}"
    return ""


def _parse_tweet_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def _extract_summary(entry: Any) -> str:
    summary = _entry_get(entry, "summary", "")
    if not summary:
        return ""
    soup = BeautifulSoup(summary, "html.parser")
    text = soup.get_text().replace("\n", " ").strip()
    first = re.split(r"[.!?。！？]", text)[0].strip()
    return first[:120]


def fetch_rss(url: str, since_date: str, max_items: int = 5) -> list[dict[str, str]]:
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
        articles: list[dict[str, str]] = []
        for entry in feed.entries[: max_items * 2]:
            title = str(_entry_get(entry, "title", "")).strip()
            if not title:
                continue
            date_str = _parse_feed_date(entry)
            if date_str and date_str <= since_date:
                continue
            summary = _extract_summary(entry)
            link = str(_entry_get(entry, "link", ""))
            articles.append({"title": title, "date": date_str, "summary": summary, "link": link})
            if len(articles) >= max_items:
                break
        return articles
    except Exception as exc:
        print(f"  RSS error {url}: {exc}", file=sys.stderr)
        return []


def fetch_web(url: str, max_items: int = 5) -> list[dict[str, str]]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        articles: list[dict[str, str]] = []
        for tag in soup.select("article h2 a, article h3 a, h2 a, h3 a, .post-title a")[:max_items]:
            title = tag.get_text().strip()
            if title and len(title) > 10:
                link = tag.get("href", "")
                if link and not link.startswith("http"):
                    link = urljoin(url, link)
                articles.append({"title": title, "date": "", "summary": "", "link": str(link)})

        if not articles:
            for tag in soup.select("h2, h3")[:max_items]:
                title = tag.get_text().strip()
                if title and len(title) > 20:
                    articles.append({"title": title, "date": "", "summary": "", "link": ""})

        return articles
    except Exception as exc:
        print(f"  Web error {url}: {exc}", file=sys.stderr)
        return []


def fetch_x_account(
    handle: str, since_date: str, max_items: int = 5, bird_path: str | None = None
) -> list[dict[str, str]]:
    clean = handle.lstrip("@")
    bird_cli = bird_path or shutil.which("bird")
    if bird_cli is None:
        print("bird CLI not found - skipping X fetch", file=sys.stderr)
        return []

    try:
        proc = subprocess.run(
            [bird_cli, "user-tweets", clean, "-n", str(max_items * 2), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            print(f"  bird error @{clean}: {proc.stderr.strip()[:80]}", file=sys.stderr)
            return []

        tweets = json.loads(proc.stdout)
        articles: list[dict[str, str]] = []
        for tweet in tweets:
            date_str = _parse_tweet_date(tweet.get("createdAt", ""))
            if date_str and date_str <= since_date:
                continue
            text = tweet.get("text", "").strip()
            if not text or len(text) < 20:
                continue
            title = text[:120] + ("..." if len(text) > 120 else "")
            tweet_id = tweet.get("id", "")
            username = tweet.get("author", {}).get("username", clean)
            link = f"https://x.com/{username}/status/{tweet_id}" if tweet_id else ""
            articles.append({"title": title, "date": date_str, "summary": "", "link": link})
            if len(articles) >= max_items:
                break
        return articles
    except subprocess.TimeoutExpired:
        print(f"  bird timeout @{clean}", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"  bird error @{clean}: {exc}", file=sys.stderr)
        return []


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().strip())[:60].strip("-")


def _title_hash(title: str) -> str:
    return hashlib.sha256(title.encode()).hexdigest()[:8]


def archive_article(
    article: Mapping[str, str],
    source_name: str,
    tier: int,
    cache_dir: Path,
    now: datetime | None = None,
) -> None:
    if tier != 1:
        return
    link = article.get("link", "")
    if not link:
        return
    if now is None:
        now = datetime.now(timezone.utc)

    date_str = article.get("date") or now.strftime("%Y-%m-%d")
    slug = _slug(source_name)
    title = article.get("title", "")
    h = _title_hash(title)
    filename = f"{date_str}_{slug}_{h}.json"
    filepath = cache_dir / filename

    if filepath.exists():
        return

    if not _is_safe_url(link):
        print(f"  Blocked (SSRF): {link}", file=sys.stderr)
        return

    text = None
    try:
        downloaded = trafilatura.fetch_url(link)
        if downloaded:
            text = trafilatura.extract(downloaded)
    except Exception as exc:
        print(f"  Archive error {link}: {exc}", file=sys.stderr)

    record = {
        "title": title,
        "date": date_str,
        "source": source_name,
        "tier": tier,
        "link": link,
        "summary": article.get("summary", ""),
        "text": text,
        "fetched_at": now.isoformat(),
    }

    cache_dir.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    status = f"{len(text)} chars" if text else "null (extraction failed)"
    print(f"  Archived: {filename} [{status}]", file=sys.stderr)


def check_sources(
    sources: list[dict[str, Any]],
    x_accounts: list[dict[str, Any]],
    state: Mapping[str, str],
    now: datetime | None = None,
    bird_path: str | None = None,
) -> None:
    if now is None:
        now = datetime.now(timezone.utc)

    print(f"\n{'Source':<36} {'T':>1} {'HTTP':>5} {'Last Scan':>12}", file=sys.stderr)
    print("-" * 58, file=sys.stderr)

    broken: list[str] = []
    stale: list[str] = []
    for source in sources:
        name = source["name"][:35]
        tier = source.get("tier", 2)
        url = source.get("rss") or source.get("url", "")

        last_str = state.get(source["name"], "")
        if last_str:
            try:
                days = (now - datetime.fromisoformat(last_str)).days
                scan_col = f"{days}d ago"
            except (ValueError, TypeError):
                scan_col = "parse-err"
        else:
            scan_col = "never"

        if not url:
            print(f"{name:<36} {tier:>1} {'-':>5} {scan_col:>12}", file=sys.stderr)
            continue

        try:
            resp = requests.get(url, headers=HEADERS, timeout=10, stream=True)
            resp.close()
            code = str(resp.status_code)
        except requests.Timeout:
            code = "T/O"
        except Exception:
            code = "ERR"

        flag = ""
        if code not in ("200", "301", "302"):
            broken.append(source["name"])
            flag = " <-"
        elif scan_col not in ("never",) and "d ago" in scan_col:
            days_num = int(scan_col.replace("d ago", ""))
            if days_num > 60:
                stale.append(source["name"])
                flag = " (stale)"

        print(f"{name:<36} {tier:>1} {code:>5} {scan_col:>12}{flag}", file=sys.stderr)

    bird_cli = bird_path or shutil.which("bird")
    if bird_cli is not None:
        print(
            f"\n{'X Account':<25} {'T':>1} {'Status':>8} {'Last Tweet':>12}",
            file=sys.stderr,
        )
        print("-" * 50, file=sys.stderr)
        for account in x_accounts:
            handle = account["handle"].lstrip("@")
            tier = account.get("tier", 2)
            try:
                proc = subprocess.run(
                    [bird_cli, "user-tweets", handle, "-n", "1", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if proc.returncode == 0:
                    tweets = json.loads(proc.stdout)
                    if tweets:
                        last = _parse_tweet_date(tweets[0].get("createdAt", ""))
                        print(
                            f"@{handle:<24} {tier:>1} {'OK':>8} {last:>12}",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"@{handle:<24} {tier:>1} {'empty':>8} {'-':>12}",
                            file=sys.stderr,
                        )
                else:
                    err = proc.stderr.strip()[:30]
                    print(f"@{handle:<24} {tier:>1} {'FAIL':>8} {err}", file=sys.stderr)
            except Exception as exc:
                print(f"@{handle:<24} {tier:>1} {'ERR':>8} {str(exc)[:20]}", file=sys.stderr)
            time.sleep(1)
    else:
        print("\nbird CLI not found - skipping X account check", file=sys.stderr)

    print(
        f"\nTotal: {len(sources)} web/RSS + {len(x_accounts)} X accounts",
        file=sys.stderr,
    )
    if broken:
        print(f"Broken ({len(broken)}): {', '.join(broken)}", file=sys.stderr)
    if stale:
        print(f"Stale >60d ({len(stale)}): {', '.join(stale)}", file=sys.stderr)
