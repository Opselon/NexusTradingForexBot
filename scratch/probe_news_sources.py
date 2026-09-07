"""Live probe of all configured news seed URLs + calendar candidates (TASK: news mission P1).

Evidence artifact: scratch/news_probe_results.json
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import httpx

UA = "NexusScalpEngine/1.0 (news intelligence)"

SEED_URLS = {
    "fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "bls": "https://www.bls.gov/feed/news.releases.rss",
    "bea": "https://www.bea.gov/news",
    "ecb": "https://www.ecb.europa.eu/rss/press.html",
    "boe": "https://www.bankofengland.co.uk/rss/news",
    "cftc": "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
    "treasury": "https://home.treasury.gov/news/press-releases",
    "reuters": "https://feeds.reuters.com/reuters/businessNews",
    "marketwatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "forexlive": "https://www.forexlive.com/feed/",
    "zerohedge": "https://feeds.feedburner.com/zerohedge/feed",
}

CANDIDATE_URLS = {
    "cnbc_top": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "fed_press_monetary": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "bls_rss_alt": "https://www.bls.gov/feed/bls_latest.rss",
    "imf_press": "https://www.imf.org/en/RSS/News",
    "ecb_euro_ref": "https://www.ecb.europa.eu/rss/fxref-usd.html",
}

CALENDAR_URLS = {
    "fomc_calendars_html": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "bls_schedule_hub": "https://www.bls.gov/schedule/news_release/bls.info.calendar.htm",
    "bls_cpi_sched": "https://www.bls.gov/schedule/news_release/cpi.htm",
    "bea_release_sched": "https://www.bea.gov/news/schedule",
}


def probe(url: str) -> dict:
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=12.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
            dt = time.monotonic() - t0
            body = resp.content or b""
            looks_rss = b"<rss" in body[:4000] or b"<feed" in body[:4000] or b"<?xml" in body[:2000]
            looks_html = b"<html" in body[:4000].lower()
            return {
                "url": url,
                "status": resp.status_code,
                "ms": round(dt * 1000),
                "bytes": len(body),
                "content_type": resp.headers.get("content-type", ""),
                "looks_rss": looks_rss,
                "looks_html": looks_html,
                "etag": resp.headers.get("etag", ""),
                "last_modified": resp.headers.get("last-modified", ""),
                "snippet": body[:220].decode("utf-8", "replace"),
            }
    except Exception as exc:
        return {"url": url, "status": None, "error": f"{type(exc).__name__}: {exc}", "ms": round((time.monotonic() - t0) * 1000)}


def main() -> None:
    out: dict[str, dict] = {"generated_at": datetime.now(UTC).isoformat(), "seed": {}, "candidates": {}, "calendar": {}}
    for group, urls in (("seed", SEED_URLS), ("candidates", CANDIDATE_URLS), ("calendar", CALENDAR_URLS)):
        for sid, url in urls.items():
            r = probe(url)
            out[group][sid] = r
            status = r.get("status")
            print(f"[{group}] {sid:22s} status={status} ms={r.get('ms')} bytes={r.get('bytes')} rss={r.get('looks_rss')} html={r.get('looks_html')} err={r.get('error','')}")
    with open(__file__.replace("probe_news_sources.py", "news_probe_results.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print("saved -> scratch/news_probe_results.json")


if __name__ == "__main__":
    main()
