#!/usr/bin/env python3
"""
Ought Gather 主入口
自动化信息聚合工具
"""

import argparse
import sys
import os
import copy
import re
import time
import concurrent.futures
from html import unescape
from typing import List, Tuple

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config, Config, ContentSource
from src.fetchers import BaseFetcher, FetchResult, get_fetcher_class
from src.fetchers.base import Article
from src.processors.content_processor import ContentProcessor
from src.dedup.tracker import DedupTracker
from src.epub.generator import EPUBGenerator
from src.mailer.smtp_sender import SMTPSender
from src.utils.logger import (
    get_logger,
    start_task_buffer,
    stop_task_buffer,
    flush_task_logs,
    truncate_url,
    log_stage,
    log_banner,
    log_summary_table,
)

def get_fetcher(source: ContentSource, global_limit: int = 15) -> BaseFetcher:
    """
    根据内容源类型获取对应的抓取器

    Args:
        source: 内容源配置
        global_limit: 全局抓取限制

    Returns:
        BaseFetcher: 抓取器实例
    """
    fetcher_class = get_fetcher_class(source.type)
    if not fetcher_class:
        raise ValueError(f"Unknown source type: {source.type}")

    return fetcher_class(source, global_limit=global_limit)


def has_valid_content(article: Article) -> bool:
    """
    判断文章是否具备可推送的有效正文。

    规则：
    - content 为空 / 仅空白 / 仅无意义空标签 → 无效
    - 去掉 HTML 标签后的纯文本长度 >= 1（只要有字）→ 有效
    - 纯文本为空，但存在图片（article.images 或 content 内含 <img>）→ 有效
    """
    if not article.content or not str(article.content).strip():
        return False

    plain = unescape(re.sub(r"<[^>]+>", "", article.content))
    plain = re.sub(r"\s+", " ", plain).strip()

    if len(plain) > 0:
        return True

    if article.images:
        return True

    if "<img" in article.content.lower():
        return True

    return False


def _close_fetcher(fetcher: BaseFetcher, source: ContentSource, logger, context: str) -> None:
    """Close a fetcher without hiding a cleanup failure from the task log."""
    if fetcher is None:
        return
    try:
        fetcher.close()
    except Exception as e:
        logger.exception(f"{context}关闭抓取器失败 {source.src}: {e}")


def _fresh_start_task(
    source: ContentSource, tracker: DedupTracker
) -> Tuple[FetchResult, List[Tuple[int, str]]]:
    """Fetch one source and stage all currently available items for fresh start."""
    logger = get_logger()
    start_task_buffer()
    fetcher = None
    result = None
    try:
        fetcher = get_fetcher(source, global_limit=999999)
        if not fetcher.dedup_enabled:
            logger.info(
                f"[Fresh Start] 跳过不参与去重的源: {source.type} | "
                f"{truncate_url(source.src, 40)}"
            )
            result = FetchResult(source=source, articles=[], success=True)
        elif fetcher.supports_two_phase:
            candidates = fetcher.fetch_list()
            if candidates:
                urls = [c.get("url") for c in candidates if c.get("url")]
                tracker.stage_source_snapshot(tracker.make_source_key(source), urls)
                logger.info(
                    f"[Fresh Start 两阶段] {source.type} | {truncate_url(source.src, 40)}: "
                    f"标记 {len(urls)} 条候选 URL"
                )
            result = FetchResult(source=source, articles=[], success=candidates is not None,
                                 error="Failed to fetch candidate list" if candidates is None else None)
        else:
            result = fetcher.fetch_with_retry()
            if result.success and result.articles:
                urls = [article.url for article in result.articles if article.url]
                tracker.stage_source_snapshot(tracker.make_source_key(source), urls)
                logger.info(
                    f"[Fresh Start 单阶段] {source.type} | {truncate_url(source.src, 40)}: "
                    f"标记 {len(urls)} 条文章"
                )
                result.articles = []
    except Exception as e:
        logger.error(f"[Fresh Start] 异常失败 {source.src}: {e}")
        result = FetchResult(source=source, articles=[], success=False, error=str(e))
    finally:
        _close_fetcher(fetcher, source, logger, "[Fresh Start] ")
        records = stop_task_buffer()
    return result, records


def _fetch_source_task(
    source: ContentSource, tracker: DedupTracker, global_limit: int, collector=None
) -> Tuple[FetchResult, List[Tuple[int, str]]]:
    """Fetch one source, including two-phase filtering and dedup snapshots."""
    logger = get_logger()
    start_task_buffer()
    fetcher = None
    result = None
    try:
        fetcher = get_fetcher(source, global_limit=global_limit)
        snapshot_staged = False

        if fetcher.supports_two_phase:
            candidates = fetcher.fetch_list()
            if candidates is None:
                logger.warning(f"fetch_list 返回 None，回退单阶段抓取: {source.src}")
                if getattr(fetcher, "dedup_strategy", None) == "published":
                    fetcher._single_phase_tracker = tracker
                result = fetcher.fetch_with_retry()
                report_hook = getattr(type(fetcher), "record_fallback_outcome", None)
                if callable(report_hook):
                    report_hook(fetcher, result)
            else:
                source_key = tracker.make_source_key(source)
                published_only = getattr(fetcher, "dedup_strategy", None) == "published"
                if fetcher.dedup_enabled and candidates and not published_only:
                    tracker.stage_source_snapshot(
                        source_key, [c.get("url") for c in candidates]
                    )
                    snapshot_staged = True

                if fetcher.dedup_enabled:
                    new_candidates = []
                    candidate_urls = set()
                    for candidate in candidates:
                        url = candidate.get("url")
                        if not url:
                            candidate["_skip_reason"] = "missing_url"
                        elif tracker.is_fetched(url, source_key):
                            candidate["_skip_reason"] = "historical_dedup"
                        elif published_only and tracker.retry_status(url, source_key):
                            candidate["_skip_reason"] = tracker.retry_status(url, source_key)
                        elif published_only and url in candidate_urls:
                            candidate["_skip_reason"] = "duplicate_candidate"
                        else:
                            new_candidates.append(candidate)
                            candidate_urls.add(url)
                    if published_only:
                        fetcher._suppress_failed_metadata = {
                            c["url"] for c in new_candidates
                            if tracker.retry_count(c["url"], source_key) > 0
                        }
                    limit = fetcher.get_limit()
                    to_fetch = new_candidates if getattr(fetcher, "fills_candidate_limit", False) is True else new_candidates[:limit]
                    limit_skipped = [] if getattr(fetcher, "fills_candidate_limit", False) is True else new_candidates[limit:]
                    skipped_dedup = sum(c.get("_skip_reason") == "historical_dedup" for c in candidates)
                    logger.info(
                        f"[两阶段] {source.type} | {truncate_url(source.src, 40)}: "
                        f"{len(to_fetch)}/{len(candidates)} 篇待抓取"
                        + (f"，跳过已抓取 {skipped_dedup} 篇" if skipped_dedup else "")
                        + (f"，本次限额未抓取 {len(limit_skipped)} 篇" if limit_skipped else "")
                    )
                else:
                    new_candidates = candidates
                    limit = fetcher.get_limit()
                    to_fetch = candidates if getattr(fetcher, "fills_candidate_limit", False) is True else candidates[:limit]
                    logger.info(
                        f"[两阶段(无去重)] {source.type} | {truncate_url(source.src, 40)}: "
                        f"{len(to_fetch)}/{len(candidates)} 篇待抓取"
                    )
                if collector is not None and source.type == "rss":
                    try:
                        from urllib.parse import urlsplit
                        label = source.title or urlsplit(source.src).hostname or "RSS"
                        collector.collect(label, new_candidates, tracker.make_source_key(source),
                                          getattr(fetcher, "_cached_feed_language", None),
                                          (source.metadata or {}).get("language"))
                    except Exception:
                        logger.warning("AI candidate observation unavailable")
                result = fetcher.fetch_items(to_fetch)
                report_hook = getattr(type(fetcher), "record_candidate_outcomes", None)
                if callable(report_hook):
                    report_hook(fetcher, result, candidates, new_candidates)
        else:
            result = fetcher.fetch_with_retry()

        if getattr(fetcher, "dedup_strategy", None) == "published":
            result.source_stats["commit_on_epub"] = True
        if (fetcher.dedup_enabled and getattr(fetcher, "dedup_strategy", None) != "published"
                and not snapshot_staged and result.success and result.articles):
            tracker.stage_source_snapshot(
                tracker.make_source_key(source), [article.url for article in result.articles]
            )
    except Exception as e:
        logger.exception(f"抓取异常失败 {source.src}: {e}")
        result = FetchResult(source=source, articles=[], success=False, error=str(e))
    finally:
        _close_fetcher(fetcher, source, logger, "")
        records = stop_task_buffer()
    return result, records


def _run_parallel_tasks(sources, task):
    """Run source tasks concurrently and return results with isolated logs."""
    max_workers = min(len(sources), 20) if sources else 1
    completed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_source = {executor.submit(task, source): source for source in sources}
        for future in concurrent.futures.as_completed(future_to_source):
            source = future_to_source[future]
            result, records = future.result()
            completed.append((source, result, records))
            flush_task_logs(
                f"{source.type} | {truncate_url(source.src, 40)}", records
            )
    return completed


def process_results(results: List[FetchResult], tracker: DedupTracker) -> List[FetchResult]:
    """
    处理抓取结果（去重、内容处理）。去重生命周期见 docs/DEDUP.md。

    仅当文章具备有效正文且对应抓取器开启去重时，才标记去重并纳入推送列表，
    避免全文抓取失败产生的空壳文章永久占用去重记录。

    Args:
        results: 抓取结果列表
        tracker: 去重追踪器

    Returns:
        List[FetchResult]: 处理后的结果列表
    """
    logger = get_logger()
    processed_results = []
    selected_urls = {}

    for result in results:
        short_src = truncate_url(result.source.src, 40)
        prefix = f"{result.source.type} | {short_src}"
        source_key = tracker.make_source_key(result.source)

        if not result.success:
            logger.warning(f"[{prefix}] 对应内容源之前抓取失败，已跳过去重处理")
            processed_results.append(result)
            continue

        fetcher_cls = get_fetcher_class(result.source.type)
        dedup_enabled = getattr(fetcher_cls, "dedup_enabled", True) if fetcher_cls else True
        published_only = getattr(fetcher_cls, "dedup_strategy", None) == "published"

        original_count = len(result.articles)
        new_articles = []
        skipped_empty = 0
        skipped_dedup = 0
        processor = ContentProcessor(result.source)

        for article in result.articles:
            quality = article.metadata.get("quality") if isinstance(article.metadata, dict) else None
            if not isinstance(quality, dict):
                quality = next((row for row in result.quality_report
                                if row.get("url") == article.url and row.get("title") == article.title), None)
            if isinstance(quality, dict):
                quality["included"] = False
            if published_only and article.url and article.url in selected_urls.get(source_key, set()):
                if isinstance(quality, dict):
                    quality["reason"] = (quality.get("reason", "") + "; duplicate_in_run").strip("; ")
                skipped_dedup += 1
                continue
            if dedup_enabled and tracker.is_fetched(article.url, source_key):
                logger.debug(f"[{prefix}] 跳过已抓取文章: {article.title}")
                if isinstance(quality, dict):
                    quality["reason"] = (quality.get("reason", "") + "; historical_dedup").strip("; ")
                if result.source_stats and not result.source_stats.get("_list_stage_recorded"):
                    result.source_stats["historical_dedup"] = result.source_stats.get("historical_dedup", 0) + 1
                    result.source_stats["candidates"] = max(0, result.source_stats.get("candidates", 0) - 1)
                skipped_dedup += 1
                continue

            # 先做内容处理，再判断是否有效；处理失败则保留原文再校验
            orig_article = article
            try:
                article = processor.process(copy.deepcopy(orig_article))
            except Exception as e:
                logger.error(
                    f"[{prefix}] 处理文章 '{orig_article.title}' 失败: {e}，保留原始文章内容"
                )
                article = orig_article

            if not has_valid_content(article):
                if isinstance(quality, dict):
                    quality["reason"] = (quality.get("reason", "") + "; content_processing_empty").strip("; ")
                skipped_empty += 1
                logger.warning(
                    f"[{prefix}] 跳过无效/空正文（不写入去重）: {article.title}"
                )
                continue

            # 仅在启用去重且正文有效时标记去重
            if dedup_enabled and not published_only:
                tracker.mark_as_fetched(article.url, source_key)
            if isinstance(quality, dict):
                quality["eligible"] = True
                quality["included"] = not published_only
            new_articles.append(article)
            if published_only and article.url:
                selected_urls.setdefault(source_key, set()).add(article.url)

        # 更新结果
        result.articles = new_articles
        processed_results.append(result)

        extras = []
        if skipped_dedup:
            extras.append(f"跳过已抓取 {skipped_dedup} 篇")
        if skipped_empty:
            extras.append(f"跳过空正文 {skipped_empty} 篇")
        extra_str = f"，{', '.join(extras)}" if extras else ""
        logger.info(
            f"[{prefix}] 内容处理完成: {len(new_articles)}/{original_count} 篇新文章{extra_str}"
        )

    return processed_results



def _published_strategy(result):
    cls = get_fetcher_class(result.source.type)
    return bool(cls and getattr(cls, "dedup_strategy", None) == "published")


def record_article_failures(results, tracker):
    """Throttle only attempted failures; quota and policy exclusions stay eligible."""
    for result in results:
        if not _published_strategy(result):
            continue
        key = tracker.make_source_key(result.source)
        for row in result.quality_report:
            if row.get("url") and (row.get("status") == "failed" or "content_processing_empty" in row.get("reason", "")):
                tracker.record_failure(row["url"], key)


def mark_epub_articles(results, tracker):
    """Commit RSS publication only after the generator successfully returns."""
    for result in results:
        if not _published_strategy(result):
            continue
        key = tracker.make_source_key(result.source)
        for article in result.articles:
            quality = article.metadata.get("quality", {})
            if quality.get("status") != "failed":
                tracker.mark_as_fetched(article.url, key)
                tracker.clear_retry(article.url, key)
            # The processor deep-copies articles, so update the report row as well.
            for row in result.quality_report:
                if row.get("url") == article.url and row.get("title") == article.title and row.get("eligible"):
                    row["included"] = True
                    break


def has_new_content(results: List[FetchResult]) -> bool:
    """
    检查是否有新内容

    Args:
        results: 抓取结果列表

    Returns:
        bool: 是否有新内容
    """
    return any(result.success and len(result.articles) > 0 for result in results)


def main(argv=None):
    """主函数"""
    parser = argparse.ArgumentParser(description="Ought Gather - 自动化信息聚合工具")
    parser.add_argument("--config", help="配置文件路径", default="config.json")
    parser.add_argument("--fresh-start", action="store_true", help="清空去重日志并全量标记当前所有 URL，不进行推送")
    parser.add_argument("--test-mode", action="store_true", help="使用独立临时去重记录，不发送邮件或上传")
    parser.add_argument("--retry-failed", action="store_true", help="重置失败文章的重试计数，不清除已出版记录")
    args, _ = parser.parse_known_args(argv)
    if args.test_mode and args.fresh_start:
        parser.error("--test-mode cannot be combined with --fresh-start")

    logger = get_logger()
    start_time = time.time()

    log_banner("Ought Gather - 自动化信息聚合工具")

    try:
        # Ensure a recoverable encrypted backup can be made before fetching.
        if not args.test_mode and not args.fresh_start and os.getenv("WEBDAV_ENABLED", "false").lower() == "true":
            from src.delivery.service import backup_key
            backup_key()
        # 1. 加载配置与初始化去重追踪器
        log_stage(1, 5, "加载配置与初始化去重追踪器")
        config = load_config(args.config)
        logger.info(f"成功加载 {len(config.body)} 个内容源配置")
        if args.test_mode:
            import tempfile
            test_dir = tempfile.TemporaryDirectory(prefix="oughtgather-test-")
            tracker = DedupTracker(data_file=os.path.join(test_dir.name, "dedup.txt"))
        else:
            tracker = DedupTracker()
        dedup_start_count = tracker.get_stats()["total_fetched"]
        if args.retry_failed:
            tracker.reset_retries()

        # ------------------------------------------------------------------
        # Fresh Start 工作流处理
        # ------------------------------------------------------------------
        if args.fresh_start:
            log_stage(2, 5, "执行 Fresh Start 全量标记去重记录")
            tracker.clear(persist=False)

            fresh_results = _run_parallel_tasks(
                config.body, lambda source: _fresh_start_task(source, tracker)
            )
            if any(not result.success for _, result, _ in fresh_results):
                raise RuntimeError("Fresh Start failed for one or more sources; existing cache was not replaced")

            tracker.save()
            stats = tracker.get_stats()
            logger.info(f"[Fresh Start 完成] 已全量标记当前内容源，共入库 {stats['total_fetched']} 条记录，未生成或发送 EPUB。")
            return

        # ------------------------------------------------------------------
        # 常规抓取流程
        # ------------------------------------------------------------------
        # 2. 抓取内容（并发处理 + 任务日志隔离缓冲）
        log_stage(2, 5, "并发抓取内容源（开启独立日志缓冲）")
        results: List[FetchResult] = []
        error_log: List[str] = []
        raw_counts = {}

        collector = None
        benchmark = None
        benchmark_key = None
        if os.getenv("AI_EDITOR_MODE", "off") == "shadow" and (not args.test_mode or os.getenv("AI_TEST_OVERRIDE") == "true"):
            try:
                from src.ai.candidates import CandidateCollector
                collector = CandidateCollector(int(os.getenv("AI_MAX_EXCERPT_CHARS") or "600"))
            except Exception:
                logger.warning("AI candidate observation unavailable")
        if os.getenv("EDITORIAL_BENCHMARK_ENABLED", "false") == "true" and not args.test_mode:
            try:
                from pathlib import Path
                Path('output/.benchmark-stage.enc').unlink(missing_ok=True)
                from src.editorial_benchmark.collector import BenchmarkCollector, parse_permissions
                from src.editorial_benchmark.crypto import key_from_env
                benchmark_key = key_from_env()
                benchmark = BenchmarkCollector(parse_permissions(os.environ['EDITORIAL_BENCHMARK_PERMISSIONS']))
            except Exception:
                logger.warning("Benchmark capture unavailable")
        observer = collector
        if os.getenv("EDITORIAL_BENCHMARK_ENABLED", "false") == "true" and not args.test_mode:
            from src.editorial_benchmark.collector import Fanout
            observer = Fanout(collector, benchmark)
        completed = _run_parallel_tasks(
            config.body,
            lambda source: (_fetch_source_task(source, tracker, config.limit, observer) if observer is not None
                            else _fetch_source_task(source, tracker, config.limit)),
        )
        if benchmark is not None:
            from datetime import datetime, timezone
            benchmark_cutoff = datetime.now(timezone.utc).isoformat()
        for source, result, _records in completed:
            results.append(result)
            raw_counts[source] = len(result.articles) if result.success else 0
            if not result.success:
                error_log.append(f"[{source.type}] {source.src}: {result.error}")

        # 3. 处理结果（去重、内容过滤处理）
        log_stage(3, 5, "去重与内容过滤处理")
        processed_results = process_results(results, tracker)
        record_article_failures(processed_results, tracker)

        # Write both reports before EPUB generation or delivery.
        from src.quality_report import write_quality_reports
        write_quality_reports(processed_results)

        # 4. 检查是否有新内容并生成 EPUB
        log_stage(4, 5, "检查新内容并生成 EPUB")
        if not has_new_content(processed_results):
            logger.info("未发现新文章内容，跳过 EPUB 生成。")
        else:
            epub_generator = EPUBGenerator(config)
            epub_path = epub_generator.generate(processed_results, error_log, start_time=start_time)
            mark_epub_articles(processed_results, tracker)
            tracker.save()
            write_quality_reports(processed_results)
            edition = None
            backup_failed = False
            if not args.test_mode and os.getenv("WEBDAV_ENABLED", "false").lower() == "true":
                from src.delivery.service import make_backup, backup_key, write_status
                try:
                    edition = make_backup(epub_path, backup_key())
                except Exception:
                    backup_failed = True
                    logger.error("Encrypted delivery backup could not be created")
                    write_status({'webdav': 'failed', 'error': 'backup_unavailable', 'email': 'not_sent'})
                    error_log.append("Encrypted delivery backup could not be created")

            # 5. WebDAV is primary; email is a legacy delivery or failure fallback.
            log_stage(5, 5, "推送分发与保存记录")
            if args.test_mode:
                logger.info("Test mode: all delivery disabled")
            elif backup_failed:
                logger.error("WebDAV delivery skipped because no recovery backup is available")
            elif edition:
                from src.delivery.service import deliver, write_status, BACKUP_PATH
                try:
                    delivery = deliver(epub_path, edition, sender_factory=SMTPSender, cleanup_backup=BACKUP_PATH)
                    if delivery['webdav'] != 'success':
                        error_log.append("WebDAV delivery failed: " + str(delivery.get('error')))
                except Exception:
                    logger.error("WebDAV delivery failed (configuration or unexpected error)")
                    write_status({'webdav': 'failed', 'error': 'configuration_or_unexpected', 'email': 'not_sent'})
                    error_log.append("WebDAV delivery failed")
            else:
                try:
                    sender = SMTPSender()
                    if not sender.send_epub(epub_path, config.title.get_plain_text()):
                        error_log.append("Email sending failed")
                except Exception as e:
                    logger.error("Email sending failed: %s", type(e).__name__)
                    error_log.append("Email sending failed")

        # 始终保存去重数据库（确保 limit 截断标记以及新增抓取的记录得到持久化）
        logger.info("保存去重数据库...")
        tracker.save()

        if collector is not None:
            try:
                import json
                from hashlib import sha256
                selected = set()
                outcomes = {}
                reasons = {}
                for res in processed_results:
                    if res.source.type == "rss":
                        key = tracker.make_source_key(res.source)
                        for row in res.quality_report:
                            if row.get("url"):
                                candidate_id = sha256((str(key) + "\0" + row["url"]).encode("utf-8")).hexdigest()
                                outcomes[candidate_id] = row.get("status") if row.get("status") in ("full_text", "rss_content", "summary", "failed", "rejected") else "unknown"
                                raw_reason = row.get("reason") or ""
                                for known in ("quota_limit", "media_without_independent_text", "content_processing_empty", "historical_dedup", "retry_cooldown", "retry_exhausted", "summary_disabled"):
                                    if known in raw_reason:
                                        reasons[candidate_id] = known
                                        break
                                if row.get("included"):
                                    selected.add(candidate_id)
                snapshot = collector.snapshot(1000000)
                if os.getenv('EDITORIAL_BENCHMARK_ENABLED', '') == 'true' and not args.test_mode:
                    # Fail closed for external Shadow use when benchmark permissions are enabled.
                    snapshot = [item for item in snapshot if benchmark is not None and item['candidate_id'] in benchmark.b1_ids]
                    for item in snapshot:
                        if item['candidate_id'] not in benchmark.excerpt_ids:
                            item['rss_excerpt'] = ''
                for item in snapshot:
                    item["traditional_selected"] = item["candidate_id"] in selected
                    item["traditional_status"] = outcomes.get(item["candidate_id"], "unknown")
                    item["traditional_reason"] = reasons.get(item["candidate_id"], "")
                os.makedirs("output", exist_ok=True)
                from src.delivery.service import _write_private
                _write_private("output/.ai-candidates.json", json.dumps(snapshot, ensure_ascii=False).encode("utf-8"))
            except Exception:
                logger.warning("AI candidate snapshot unavailable")

        if benchmark is not None:
            try:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                from src.delivery.service import _write_private
                from src.editorial_benchmark.crypto import encrypt
                from src.editorial_benchmark.snapshot import build_stage
                target = datetime.fromtimestamp(start_time, ZoneInfo('Asia/Shanghai')).replace(hour=6, minute=30, second=0, microsecond=0).isoformat()
                # The snapshot above is the exact order passed to Shadow, after permission filtering.
                order = [item['candidate_id'] for item in snapshot] if collector is not None else None
                stage = build_stage(benchmark, processed_results, tracker, order, target, benchmark_cutoff)
                _write_private('output/.benchmark-stage.enc', encrypt(stage, benchmark_key))
            except Exception:
                logger.warning("Benchmark capture unavailable")

        # 6. 输出执行数据汇总表格
        summary_headers = ["#", "类型", "内容源 / URL", "状态", "抓取数", "新增数", "错误数", "备注 / 错误"]
        summary_rows = []
        for idx, res in enumerate(processed_results, start=1):
            s = res.source
            raw_c = raw_counts.get(s, len(res.articles) if res.success else 0)
            new_c = len(res.articles) if res.success else 0
            error_count = res.error_count if isinstance(res.error_count, int) else 0
            if not res.success:
                status = "FAILED"
                note = truncate_url(res.error or "Unknown error", 30)
            elif error_count > 0:
                status = "PARTIAL"
                note = res.error or f"部分处理失败 {error_count} 条"
            elif new_c > 0:
                status = "SUCCESS"
                note = ""
            else:
                status = "SKIPPED"
                note = "无新文章"

            summary_rows.append([
                idx,
                s.type,
                truncate_url(s.src, 35),
                status,
                raw_c,
                new_c,
                error_count,
                note
            ])

        log_banner("执行数据汇总表")
        log_summary_table(summary_headers, summary_rows)

        stats = tracker.get_stats()
        logger.info(
            f"去重数据库保存完成: 新增={stats['snapshot_added']} 条, "
            f"移除={stats['snapshot_removed']} 条, "
            f"当前={stats['total_fetched']} 条"
        )
        logger.info(f"运行结束后去重记录={stats['total_fetched']} 条")
        logger.info(
            f"去重记录变化: {dedup_start_count} → {stats['total_fetched']} "
            f"(本次新增入库={stats['new_fetched']} 条)"
        )

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
