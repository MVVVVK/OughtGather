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
        sources.append({
            "source": result.source.title or result.source_title or result.source.src,
            "source_url": result.source.src,
            "source_type": result.source.type,
            "status": "success" if result.success else "failed",
            "reason": result.error or "",
            # None means no feed result was obtained, rather than an empty feed.
            "rss_returned": stats.get("rss_returned"),
            "historical_dedup": stats.get("historical_dedup", 0),
            "candidates": stats.get("candidates", 0),
            "full_text": sum(r["status"] == "full_text" for r in selected),
            "rss_content": sum(r["status"] == "rss_content" for r in selected),
            "summary": sum(r["status"] == "summary" for r in selected),
            "rejected": sum(r["status"] == "rejected" and r.get("reason") not in ("historical_dedup", "quota_limit") for r in rows),
            "quota_limited": sum(r.get("reason") == "quota_limit" for r in rows),
            "selected": len(result.articles) if result.success else 0,
        })
    for name, content in (("quality-report.json", articles), ("quality-sources.json", sources)):
        with open(os.path.join(output_dir, name), "w", encoding="utf-8") as output:
            json.dump(content, output, ensure_ascii=False, indent=2)
