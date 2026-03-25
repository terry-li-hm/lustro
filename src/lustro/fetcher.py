from __future__ import annotations

import atexit
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
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

# ---------------------------------------------------------------------------
# Browser process leak guard
# ---------------------------------------------------------------------------
# nodriver's browser.stop() sends SIGTERM to the parent Chrome process but
# does not wait for it, and Chrome's child processes (renderer, GPU, etc.)
# can survive.  We track every PID spawned by fetch_stealth_* here and
# forcefully kill any stragglers at process exit.

_browser_pids: set[int] = set()


def _kill_browser_pid(pid: int) -> None:
    """Kill a Chrome parent process and all its children, best-effort."""
    # Kill children first (macOS/Linux: pkill -P <pid>)
    try:
        subprocess.run(
            ["pkill", "-9", "-P", str(pid)],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass
    # Then kill the parent
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # already dead — fine
    except Exception:
        pass


def _atexit_kill_browsers() -> None:
    for pid in list(_browser_pids):
        _kill_browser_pid(pid)
    _browser_pids.clear()


atexit.register(_atexit_kill_browsers)

# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Lustro/0.2; +https://github.com/terry-li-hm/lustro)"
}


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


def _parse_feed_datetime(entry: Any) -> str:
    """Return ISO 8601 UTC datetime string from an RSS entry, or '' if unavailable.

    Uses the time.struct_time fields (published_parsed / updated_parsed /
    created_parsed) which feedparser normalises to UTC.  Falls back to the raw
    string fields so we can try email-format parsing when the structured form is
    absent.
    """
    import calendar

    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, field, None)
        if parsed is None:
            parsed = _entry_get(entry, field, None)
        if parsed:
            try:
                # calendar.timegm interprets struct_time as UTC
                ts = calendar.timegm(parsed)
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return dt.isoformat()
            except (TypeError, OverflowError, OSError):
                continue

    # Fallback: raw string fields (RFC 2822 / ISO 8601)
    for field in ("published", "updated", "created"):
        raw = _entry_get(entry, field, "")
        if not raw:
            continue
        # Try common formats
        for fmt in (
            "%a, %d %b %Y %H:%M:%S %z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
        ):
            try:
                dt = datetime.strptime(str(raw).strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except ValueError:
                continue

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


def fetch_stealth_url(url: str, profile_dir: Path) -> str | None:
    """Fetch URL using nodriver (stealth Chrome) to bypass Cloudflare.

    Returns extracted text or None on failure.
    profile_dir: persistent Chrome user-data-dir so logins persist across runs.
    """
    try:
        import asyncio

        import nodriver as uc

        async def _fetch() -> str:
            browser = await uc.start(
                headless=True,
                user_data_dir=str(profile_dir),
            )
            pid: int | None = getattr(browser, "_process_pid", None)
            if pid is not None:
                _browser_pids.add(pid)
            try:
                page = await browser.get(url)
                await asyncio.sleep(4)
                text = await page.evaluate("document.body.innerText")
                return str(text) if text is not None else ""
            finally:
                browser.stop()
                if pid is not None:
                    _kill_browser_pid(pid)
                    _browser_pids.discard(pid)

        return asyncio.run(_fetch())
    except ImportError:
        print("  nodriver not installed — skipping stealth fetch", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  stealth_fetch error {url}: {exc}", file=sys.stderr)
        return None


def fetch_stealth_html(url: str, profile_dir: Path) -> str | None:
    """Fetch URL using nodriver and return rendered body HTML for link extraction."""
    try:
        import asyncio

        import nodriver as uc

        async def _fetch() -> str:
            browser = await uc.start(
                headless=True,
                user_data_dir=str(profile_dir),
            )
            pid: int | None = getattr(browser, "_process_pid", None)
            if pid is not None:
                _browser_pids.add(pid)
            try:
                page = await browser.get(url)
                await asyncio.sleep(4)
                html = await page.evaluate("document.body.outerHTML")
                return str(html) if html is not None else ""
            finally:
                browser.stop()
                if pid is not None:
                    _kill_browser_pid(pid)
                    _browser_pids.discard(pid)

        return asyncio.run(_fetch())
    except ImportError:
        print("  nodriver not installed — skipping stealth_web", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  stealth_web error {url}: {exc}", file=sys.stderr)
        return None


# JS to extract post body text from LinkedIn company posts page
_LINKEDIN_JS = """
(function() {
  var arts = document.querySelectorAll('[data-urn]');
  var result = Array.from(arts).map(function(a) {
    var text = a.innerText;
    var idx = text.indexOf('\\nFollow\\n');
    if (idx < 0) { idx = text.indexOf('\\nFollow '); }
    var content = idx >= 0 ? text.slice(idx + 8).trim() : '';
    var lines = content.split('\\n');
    var title = lines[0].slice(0, 140);
    return {title: title, summary: lines.slice(1, 3).join(' ').slice(0, 200)};
  }).filter(function(p) { return p.title && p.title.length > 15; });
  return JSON.stringify(result);
})()
"""


def internalize_linkedin(
    slug: str,
    since_date: str,
    max_items: int = 5,
    agent_browser_bin: str = "agent-browser",
) -> list[dict[str, str]] | None:
    """Internalize LinkedIn company posts via agent-browser (requires active session).

    slug: LinkedIn company URL slug (e.g. 'the-hong-kong-institute-of-bankers')
    Requires agent-browser daemon running with a logged-in LinkedIn session.
    """
    url = f"https://www.linkedin.com/company/{slug}/posts/?feedView=all"
    try:
        # Navigate to the page (agent-browser daemon must be running)
        nav = subprocess.run(
            [agent_browser_bin, "open", url],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if nav.returncode != 0:
            print(f"  linkedin: open failed for {slug}: {nav.stderr[:80]}", file=sys.stderr)
            return None

        # Extract posts via JS eval
        result = subprocess.run(
            [agent_browser_bin, "eval", _LINKEDIN_JS],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            print(f"  linkedin: eval failed for {slug}: {result.stderr[:80]}", file=sys.stderr)
            return None

        raw = result.stdout.strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = json.loads(raw)  # agent-browser wraps string results in quotes
        posts = json.loads(raw) if isinstance(raw, str) else raw

        articles: list[dict[str, str]] = []
        for post in posts[:max_items]:
            title = str(post.get("title", "")).strip()
            if not title or len(title) < 15:
                continue
            articles.append(
                {
                    "title": title,
                    "summary": str(post.get("summary", "")),
                    "date": "",
                    "link": url,
                }
            )
        print(f"  linkedin: {slug} → {len(articles)} posts", file=sys.stderr)
        return articles
    except subprocess.TimeoutExpired:
        print(f"  linkedin: timeout for {slug}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  linkedin: error for {slug}: {exc}", file=sys.stderr)
        return None


def internalize_json_api(
    url: str,
    since_date: str,
    title_key: str = "title",
    link_key: str = "link",
    date_key: str = "date",
    records_path: tuple[str, ...] = ("result", "records"),
    max_items: int = 10,
) -> list[dict[str, str]] | None:
    """Internalize cargo from a JSON API (e.g. HKMA press releases).

    Args:
        url: API endpoint URL.
        since_date: ISO date string; entries on or before this date are skipped.
        records_path: Tuple of keys to drill into the response to reach the list of records.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  JSON API error: {url} — {exc}", file=sys.stderr)
        return None

    records = data
    for key in records_path:
        if isinstance(records, dict):
            records = records.get(key, [])
        else:
            records = []
            break

    if not isinstance(records, list):
        return None

    articles: list[dict[str, str]] = []
    for record in records[: max_items * 3]:
        title = str(record.get(title_key, "")).strip()
        if not title:
            continue
        date_str = str(record.get(date_key, ""))[:10]  # ISO date, trim time if present
        if date_str and date_str < since_date:
            continue
        link = str(record.get(link_key, ""))
        articles.append({"title": title, "link": link, "date": date_str, "summary": ""})
        if len(articles) >= max_items:
            break

    return articles


def internalize_rss(
    url: str,
    since_date: str,
    max_items: int = 5,
    full_fetch: bool = False,
    stealth_fetch: bool = False,
    profile_dir: Path | None = None,
) -> list[dict[str, str]] | None:
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)

        # Dead-feed detection
        status = getattr(feed, "status", None)
        if getattr(feed, "bozo", False):
            if isinstance(status, int) and status >= 400:
                print(f"  RSS dead (HTTP {status}): {url}", file=sys.stderr)
                return None
            if not hasattr(feed, "entries") or not feed.entries:
                print(f"  RSS dead (bozo + no entries): {url}", file=sys.stderr)
                return None

        if not hasattr(feed, "entries"):
            return None

        articles: list[dict[str, str]] = []
        for entry in feed.entries[: max_items * 2]:
            title = str(_entry_get(entry, "title", "")).strip()
            if not title:
                continue
            date_str = _parse_feed_date(entry)
            if date_str and date_str < since_date:
                continue
            link = str(_entry_get(entry, "link", ""))
            summary = _extract_summary(entry)

            # Extract full article text from RSS content field if available
            # (e.g. Wechat2RSS proxies full article HTML in content[0].value)
            rss_text = ""
            content_list = getattr(entry, "content", None) or _entry_get(entry, "content", [])
            if content_list:
                raw = content_list[0]
                html = raw.get("value", "") if hasattr(raw, "get") else getattr(raw, "value", "")
                if html:
                    rss_text = BeautifulSoup(html, "html.parser").get_text(separator=" ").strip()

            if stealth_fetch and link and _is_safe_url(link):
                try:
                    text = fetch_stealth_url(
                        link,
                        profile_dir or Path.home() / ".config" / "lustro" / "nodriver-profile",
                    )
                    if text:
                        summary = text.strip().replace("\n", " ")
                        print(f"  stealth_fetch: {link} [{len(summary)} chars]", file=sys.stderr)
                    else:
                        print(f"  stealth_fetch: {link} [failed]", file=sys.stderr)
                except Exception:
                    print(f"  stealth_fetch: {link} [failed]", file=sys.stderr)
            elif full_fetch and link and _is_safe_url(link):
                try:
                    downloaded = trafilatura.fetch_url(link)
                    extracted = trafilatura.extract(downloaded) if downloaded else None
                    if extracted:
                        summary = extracted.strip().replace("\n", " ")
                        print(f"  full_fetch: {link} [{len(summary)} chars]", file=sys.stderr)
                    else:
                        print(f"  full_fetch: {link} [failed]", file=sys.stderr)
                except Exception:
                    print(f"  full_fetch: {link} [failed]", file=sys.stderr)

            published_at = _parse_feed_datetime(entry)
            article: dict[str, str] = {
                "title": title,
                "date": date_str,
                "published_at": published_at,
                "summary": summary,
                "link": link,
            }
            if rss_text:
                article["text"] = rss_text
            articles.append(article)
            if len(articles) >= max_items:
                break
        return articles
    except Exception as exc:
        print(f"  RSS error {url}: {exc}", file=sys.stderr)
        return []


def internalize_web(
    url: str,
    max_items: int = 5,
    selector: str | None = None,
    stealth: bool = False,
    profile_dir: Path | None = None,
) -> list[dict[str, str]]:
    try:
        if stealth and _is_safe_url(url):
            html = fetch_stealth_html(
                url,
                profile_dir or Path.home() / ".config" / "lustro" / "nodriver-profile",
            )
            if html is None:
                return []
            soup = BeautifulSoup(html, "html.parser")
            print(f"  stealth_web: {url} [{len(html)} chars]", file=sys.stderr)
        else:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

        articles: list[dict[str, str]] = []

        if selector:
            for tag in soup.select(selector)[:max_items]:
                # Prefer heading child over full card text (avoids category label noise)
                heading = tag.find(["h2", "h3", "h4"]) if tag.name == "a" else None
                title = heading.get_text().strip() if heading else tag.get_text().strip()
                if title and len(title) > 10:
                    link = tag.get("href", "") if tag.name == "a" else ""
                    if not link:
                        a = tag.find("a")
                        link = a.get("href", "") if a else ""
                    if link and not link.startswith("http"):
                        link = urljoin(url, link)
                    articles.append({"title": title, "date": "", "summary": "", "link": str(link)})
            return articles

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
        return None


def internalize_x_account(
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


def internalize_x_bookmarks(
    since_date: str, max_items: int = 10, bird_path: str | None = None
) -> list[dict[str, str]]:
    bird_cli = bird_path or shutil.which("bird")
    if bird_cli is None:
        print("bird CLI not found - skipping bookmarks fetch", file=sys.stderr)
        return []

    try:
        proc = subprocess.run(
            [bird_cli, "bookmarks", "-n", str(max_items * 2), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            print(f"  bird bookmarks error: {proc.stderr.strip()[:80]}", file=sys.stderr)
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
            username = tweet.get("author", {}).get("username", "")
            link = f"https://x.com/{username}/status/{tweet_id}" if tweet_id and username else ""
            articles.append(
                {
                    "title": title,
                    "date": date_str,
                    "summary": "",
                    "link": link,
                    "_tweet_id": tweet_id,
                }
            )
            if len(articles) >= max_items:
                break
        return articles
    except subprocess.TimeoutExpired:
        print("  bird bookmarks timeout", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"  bird bookmarks error: {exc}", file=sys.stderr)
        return []


def unbookmark_tweets(tweet_ids: list[str], bird_path: str | None = None) -> None:
    if not tweet_ids:
        return
    bird_cli = bird_path or shutil.which("bird")
    if bird_cli is None:
        return
    try:
        subprocess.run(
            [bird_cli, "unbookmark", *tweet_ids],
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(f"  Unbookmarked {len(tweet_ids)} tweets", file=sys.stderr)
    except Exception as exc:
        print(f"  Unbookmark error: {exc}", file=sys.stderr)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().strip())[:60].strip("-")


def _title_hash(title: str) -> str:
    return hashlib.sha256(title.encode()).hexdigest()[:8]


def archive_cargo(
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

    text = article.get("text") or None
    if not text:
        try:
            downloaded = trafilatura.fetch_url(link)
            if downloaded:
                text = trafilatura.extract(downloaded)
        except Exception as exc:
            print(f"  Archive error {link}: {exc}", file=sys.stderr)

    # Skip articles with insufficient text (scrape failures, paywalled stubs)
    min_text = 100
    if not text or len(text) < min_text:
        status = f"{len(text)} chars" if text else "null"
        print(f"  Skipped (too short): {filename} [{status}]", file=sys.stderr)
        return

    # Content-hash dedup: skip if identical text already archived for same date+source
    content_hash = hashlib.md5(text.encode()).hexdigest()
    prefix = f"{date_str}_{slug}_"
    if cache_dir.exists():
        for existing in cache_dir.glob(f"{prefix}*.json"):
            try:
                existing_data = json.loads(existing.read_text(encoding="utf-8"))
                existing_text = existing_data.get("text") or ""
                if hashlib.md5(existing_text.encode()).hexdigest() == content_hash:
                    print(f"  Skipped (duplicate content): {filename}", file=sys.stderr)
                    return
            except (OSError, json.JSONDecodeError):
                continue

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
    print(f"  Archived: {filename} [{len(text)} chars]", file=sys.stderr)


def check_receptors(
    sources: list[dict[str, Any]],
    x_accounts: list[dict[str, Any]],
    state: Mapping[str, str],
    now: datetime | None = None,
    bird_path: str | None = None,
    x_bookmarks: list[dict[str, Any]] | None = None,
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

        zeros = int(state.get(f"_zeros:{source['name']}", 0))

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

        if zeros >= 3:
            flag += f" ({zeros}x0)"

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

    if x_bookmarks and bird_cli is not None:
        print(f"\n{'X Bookmarks':<25} {'T':>1} {'Status':>8}", file=sys.stderr)
        print("-" * 38, file=sys.stderr)
        for bm in x_bookmarks:
            name = bm.get("name", "X Bookmarks")[:24]
            tier = bm.get("tier", 2)
            try:
                proc = subprocess.run(
                    [bird_cli, "bookmarks", "-n", "1", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                status = "OK" if proc.returncode == 0 else "FAIL"
                print(f"{name:<25} {tier:>1} {status:>8}", file=sys.stderr)
            except Exception as exc:
                print(f"{name:<25} {tier:>1} {'ERR':>8} {str(exc)[:20]}", file=sys.stderr)

    wechat_sources = [s for s in sources if s.get("rss", "").startswith("http://localhost:8001")]
    if wechat_sources:
        try:
            resp = requests.get("http://localhost:8001/", timeout=5)
            resp.close()
            w_status = str(resp.status_code)
        except Exception:
            w_status = "DOWN"
        print(
            f"\n{'Wechat2RSS (localhost:8001)':<36} {'—':>1} {w_status:>5} {f'({len(wechat_sources)} feeds)':>12}",
            file=sys.stderr,
        )

    bm_count = len(x_bookmarks) if x_bookmarks else 0
    parts = [f"{len(sources)} web/RSS", f"{len(x_accounts)} X accounts"]
    if bm_count:
        parts.append(f"{bm_count} bookmarks")
    print(f"\nTotal: {' + '.join(parts)}", file=sys.stderr)
    if broken:
        print(f"Broken ({len(broken)}): {', '.join(broken)}", file=sys.stderr)
    if stale:
        print(f"Stale >60d ({len(stale)}): {', '.join(stale)}", file=sys.stderr)
