"""Write per-article and per-source diagnostics for a completed gather."""
import json
import os


def write_quality_reports(results, output_dir="output"):
    os.makedirs(output_dir, exist_ok=True)
    articles, sources = [], []
    for result in results:
        rows = result.quality_report
        articles.extend(rows)
        if not result.success and not rows:
            articles.append({"title": "", "source": result.source.title or result.source.src,
                             "url": "", "language": None, "method": "source", "text_length": 0,
                             "status": "failed", "reason": result.error or "source_failed", "included": False})
        stats = result.source_stats
        selected = [row for row in rows if row.get("included") is True]
        skip_reasons = {}
        for row in rows:
            if not row.get("included") and not row.get("eligible"):
                for reason in filter(None, row.get("reason", "").split("; ")):
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
        sources.append({
            "source": result.source.title or result.source_title or result.source.src,
            "source_url": result.source.src,
            "source_type": result.source.type,
            "status": "success" if result.success else "failed",
            "reason": result.error or "",
            # None means no feed result was obtained, rather than an empty feed.
            "rss_returned": stats.get("rss_returned"),
            "rss_success": stats.get("rss_success"),
            "http_status": stats.get("http_status"),
            "parse_error": stats.get("parse_error"),
            "fetched_at": stats.get("fetched_at"),
            "feed_updated": stats.get("feed_updated"),
            "latest_published": stats.get("latest_published"),
            "stale_warning": stats.get("stale_warning"),
            "text_candidates": stats.get("text_candidates"),
            "media_candidates": stats.get("media_candidates"),
            "attempted": sum(r.get("attempted") is True for r in rows),
            "quality_passed": sum(r.get("status") in ("full_text", "rss_content", "summary") for r in rows),
            "full_text_obtained": sum(r.get("status") == "full_text" for r in rows),
            "rss_content_obtained": sum(r.get("status") == "rss_content" for r in rows),
            "summary_obtained": sum(r.get("status") == "summary" for r in rows),
            "failed": sum(r.get("status") == "failed" or "content_processing_empty" in r.get("reason", "") for r in rows),
            "skip_reasons": skip_reasons,
            "historical_dedup": stats.get("historical_dedup", 0),
            "candidates": stats.get("candidates", 0),
            "full_text": sum(r["status"] == "full_text" for r in selected),
            "rss_content": sum(r["status"] == "rss_content" for r in selected),
            "summary": sum(r["status"] == "summary" for r in selected),
            "rejected": sum(r["status"] == "rejected" and r.get("reason") not in ("historical_dedup", "quota_limit") for r in rows),
            "quota_limited": sum(r.get("reason") == "quota_limit" for r in rows),
            "selected": (len(selected) if stats.get("commit_on_epub") else len(result.articles)) if result.success else 0,
        })
    for name, content in (("quality-report.json", articles), ("quality-sources.json", sources)):
        with open(os.path.join(output_dir, name), "w", encoding="utf-8") as output:
            json.dump(content, output, ensure_ascii=False, indent=2)
