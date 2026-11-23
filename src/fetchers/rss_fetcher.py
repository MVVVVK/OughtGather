"""
RSS 抓取器模块
解析 RSS/Atom 内容源
"""

from typing import List, Optional, Any
import feedparser

from src.config import ContentSource
from src.fetchers.base import BaseFetcher, FetchResult, Article
from src.utils.helpers import format_date
from src.fetchers.quality import inspect, MEDIA
from bs4 import BeautifulSoup
from urllib.parse import unquote, urlsplit
import re


class RSSFetcher(BaseFetcher):
    """RSS 抓取器"""

    type_name = "rss"
    supports_two_phase = True
    fills_candidate_limit = True
    src_placeholder = "RSS/Atom URL, 例如: https://hnrss.org/frontpage"
    config_schema = {
        "metadata.full_text": {
            "type": "select",
            "label": "全文提取",
            "options": ["", "N", "Y"],
            "hint": "抓取 RSS 页面正文"
        },
        "metadata.text_news_priority": {
            "type": "select", "label": "文字新闻优先", "options": ["", "Y", "N"],
            "hint": "优先处理没有视频、音频或直播信号的条目"
        },
        "metadata.accept_summary": {
            "type": "select", "label": "接纳 RSS 摘要", "options": ["", "Y", "N"],
            "hint": "N 时不收录只有摘要的条目"
        },
        "metadata.limit": {
            "type": "number",
            "label": "限制条目数 (limit)",
            "placeholder": "留空使用全局限制"
        }
    }
    MAX_ENTRIES = 50  # 每个 RSS 源最多抓取的条目数，可通过 metadata.limit 覆盖

    @classmethod
    def get_default_source_title(cls, source: Any, articles: List[Article], source_title: Optional[str] = None) -> str:
        return source_title or source.src

    def fetch(self) -> FetchResult:
        """
        执行 RSS 抓取

        Returns:
            FetchResult: 抓取结果
        """
        result = FetchResult(source=self.source, articles=[])

        try:
            # 通过 BaseFetcher._make_request 下载后再解析，复用统一 UA / 连接池
            feed = self._parse_feed()

            # 检查解析结果
            # 改进：即使有格式错误，只要有条目就继续处理
            if feed.bozo:
                self.logger.warning(
                    f"RSS feed has format issues (bozo): {feed.bozo_exception}"
                )
            
            result.source_stats["rss_returned"] = len(feed.entries)
            # 如果没有任何条目且有严重错误，则失败
            if not feed.entries:
                result.success = False
                error_msg = str(feed.bozo_exception) if feed.bozo else "No entries found"
                result.error = f"Failed to parse RSS feed: {error_msg}"
                self.logger.error(result.error)
                return result

            # 提取 feed 标题作为章节显示名称
            result.source_title = feed.feed.get("title", "")

            self.logger.info(f"Found {len(feed.entries)} entries in RSS feed")

            # 限制条目数量（默认使用全局限制，可通过 metadata.limit 覆盖）
            metadata = self.source.metadata or {}
            limit = min(int(metadata.get("limit", self.global_limit)), len(feed.entries))
            entries = feed.entries
            result.source_stats.update({"historical_dedup": 0, "candidates": len(entries)})
            if metadata.get("text_news_priority", "Y") != "N":
                entries = sorted(entries, key=self._is_media)

            if limit < len(feed.entries):
                self.logger.info(f"Limiting RSS entries to {limit} (feed has {len(feed.entries)})")

            # 遍历所有条目
            self._quality_report = []
            for entry in entries:
                try:
                    if self._should_delete(entry.get("title", "")):
                        self._quality_report.append(self._skipped_report(entry.get("title", ""), entry.get("link", ""), "title_filter"))
                    elif len(result.articles) >= limit:
                        self._quality_report.append(self._skipped_report(entry.get("title", ""), entry.get("link", ""), "quota_limit"))
                    else:
                        article = self._parse_entry(entry)
                        if article:
                            result.articles.append(article)
                except Exception as e:
                    self.logger.error(f"Failed to parse entry: {e}")
                    result.add_error(f"Failed to parse entry: {e}")

            result.quality_report = self._quality_report
            # 只要成功解析了至少一些条目，就认为抓取成功
            if result.articles:
                result.success = True
            
            return result

        except Exception as e:
            self.logger.error(f"RSS fetch failed: {e}")
            result.success = False
            result.error = str(e)
            return result

    # ------------------------------------------------------------------
    # 两阶段接口实现
    # ------------------------------------------------------------------

    def fetch_list(self) -> Optional[List[dict]]:
        """
        【阶段一】解析 RSS feed，仅返回候选条目元数据列表（不抓全文）。

        Returns:
            list of dict with keys: url, title, author, published, _entry
            解析失败时返回 None 以触发回退逻辑。
        """
        try:
            feed = self._parse_feed()

            if feed.bozo:
                self.logger.warning(
                    f"RSS feed has format issues (bozo): {feed.bozo_exception}"
                )

            self._rss_returned = len(feed.entries)
            if not feed.entries:
                self.logger.error(
                    f"Failed to parse RSS feed or no entries: "
                    f"{feed.bozo_exception if feed.bozo else 'No entries found'}"
                )
                return None

            # 保存 feed 级别的标题供 fetch_items 使用
            self._cached_source_title = feed.feed.get("title", "")

            self.logger.info(
                f"[fetch_list] RSS feed 共 {len(feed.entries)} 条"
            )

            self._filtered_reports = []
            candidates = []
            for entry in feed.entries:
                url = entry.get("link", "")
                title = entry.get("title", "No Title")
                # 过滤掉明显应被删除的标题，减少后续不必要处理
                if self._should_delete(title):
                    self._filtered_reports.append(self._skipped_report(title, url, "title_filter"))
                    continue
                candidates.append({
                    "url": url,
                    "title": title,
                    "author": entry.get("author", ""),
                    "published": entry.get("published", ""),
                    "_entry": entry,  # 保留原始 entry，供 fetch_items 使用摘要
                    "_media": self._is_media(entry),
                })

            return candidates

        except Exception as e:
            self.logger.error(f"[fetch_list] RSS fetch_list failed: {e}")
            return None

    def fetch_items(self, candidates: List[dict]) -> "FetchResult":
        """
        【阶段二】对去重过滤后的候选条目执行全文/摘要抓取，返回 FetchResult。

        Args:
            candidates: fetch_list() 返回并经去重过滤的条目列表。
        """
        result = FetchResult(source=self.source, articles=[])
        result.source_title = getattr(self, "_cached_source_title", "")

        if not candidates:
            self.logger.info("[fetch_items] 无新候选条目，跳过全文抓取")
            return result

        self.logger.info(f"[fetch_items] 开始抓取 {len(candidates)} 篇新文章")

        self._quality_report = []
        if (self.source.metadata or {}).get("text_news_priority", "Y") != "N":
            candidates = sorted(candidates, key=lambda c: self._is_media(c.get("_entry", {})))
        for cand in candidates:
            if len(result.articles) >= self.get_limit():
                skipped = self._skipped_report(
                    cand.get("title") or cand.get("_entry", {}).get("title", ""),
                    cand.get("url") or cand.get("_entry", {}).get("link", ""), "quota_limit")
                skipped["media_candidate"] = self._is_media(cand.get("_entry", {}))
                self._quality_report.append(skipped)
                continue
            try:
                entry = cand.get("_entry", {})
                article = self._parse_entry(entry)
                if article:
                    result.articles.append(article)
            except Exception as e:
                self.logger.error(f"[fetch_items] 解析条目失败: {e}")
                result.add_error(f"Failed to parse entry: {e}")
                self._quality_report.append({"title": cand.get("title", ""), "source": self.source.title or self.source.src, "url": cand.get("url", ""), "language": None, "method": "none", "text_length": 0, "status": "failed", "reason": "entry_parse_failed"})

        if result.articles:
            result.success = True
        result.quality_report = self._quality_report

        return result

    def _parse_entry(self, entry: dict) -> Optional[Article]:
        """
        解析单个 RSS 条目

        Args:
            entry: RSS 条目（feedparser 解析结果）

        Returns:
            Article: 文章对象
        """
        # 提取基本信息
        title = entry.get("title", "No Title")
        link = entry.get("link", "")
        author = entry.get("author", "")
        published = format_date(entry.get("published", ""))

        metadata = self.source.metadata or {}
        reasons = []
        content = ""
        raw_html = ""
        status = ""
        method = ""
        if metadata.get("full_text", "N") == "Y":
            self._last_full_text_quality_reason = None
            extracted, raw_html = self._fetch_full_text(link)
            ok, reason, _ = inspect(extracted, full=True)
            if not ok and reason == "empty_content" and self._last_full_text_quality_reason:
                reason = self._last_full_text_quality_reason
            if not ok and reason == "empty_content" and raw_html:
                _, raw_reason, _ = inspect(raw_html)
                if raw_reason not in ("", "empty_content"):
                    reason = raw_reason
            if ok:
                content, status, method = extracted, "full_text", "web"
            else:
                reasons.append("web:" + reason)
        else:
            reasons.append("web:disabled")

        rss_content = entry.get("content", [])
        rss_content = rss_content[0].get("value", "") if rss_content else ""
        if not content and rss_content:
            ok, reason, _ = inspect(rss_content, full=True)
            if ok:
                content, status, method = rss_content, "rss_content", "rss_content"
            else:
                reasons.append("rss_content:" + reason)

        summary = entry.get("summary", entry.get("description", "")) or rss_content or ""
        if not content:
            ok, reason, _ = inspect(summary)
            if ok:
                content, status, method = summary, "summary", "rss_summary"
            else:
                reasons.append("summary:" + reason)

        media = self._is_media(entry)
        if media and status not in ("full_text", "rss_content"):
            status, method, content = "rejected", "none", ""
            reasons.append("media_without_independent_text")
        elif status == "summary" and metadata.get("accept_summary", "Y") == "N":
            status, method, content = "rejected", "none", ""
            reasons.append("summary_disabled")
        elif not content:
            status, method = "failed", "metadata"
            content = "<p>Article text unavailable / Texte indisponible.</p>"
        _, _, length = inspect(content)
        if status == "summary":
            content = '<p><strong>Summary / Résumé</strong></p>' + content
        elif status == "failed":
            content = '<p><strong>Text unavailable / Texte indisponible</strong></p>'

        language = entry.get("language", "") or ""
        if not language and raw_html:
            html_tag = BeautifulSoup(raw_html, "html.parser").find("html")
            language = html_tag.get("lang", "") if html_tag else ""
        report = {"title": title, "source": self.source.title or self.source.src,
                  "url": link, "language": language or None, "method": method,
                  "text_length": length if status not in ("failed", "rejected") else 0,
                  "status": status, "reason": "; ".join(reasons),
                  "media_candidate": media, "included": False}
        if hasattr(self, "_quality_report"):
            self._quality_report.append(report)
        if status == "rejected":
            return None
        image_html = raw_html if status == "full_text" else content
        body_images = self._extract_images(image_html, base_url=link)
        og_images = self._extract_og_image(raw_html, base_url=link) if status == "full_text" else []
        images = og_images + [u for u in body_images if u not in set(og_images)]
        return Article(title=title, content=content, url=link, author=author,
                       published_date=published, images=images,
                       metadata={"categories": entry.get("tags", []), "quality": report})

    @staticmethod
    def _is_media(entry: dict) -> bool:
        """Signals identify media entries, but do not rule out a real text article."""
        title = str(entry.get("title", ""))
        path = unquote(urlsplit(str(entry.get("link", ""))).path).casefold()
        path_media = bool(re.search(r"(?:^|/)(?:vid[ée]os?|audio|podcasts?|live|direct|en-direct)(?:/|$)", path))
        attachments = list(entry.get("enclosures", []) or []) + list(entry.get("media_content", []) or [])
        media_tags = {str(tag.get("term", "")).casefold() for tag in entry.get("tags", []) or [] if isinstance(tag, dict)}
        return bool(MEDIA.search(title) or path_media or media_tags.intersection({"video", "vidéo", "audio", "podcast", "live", "en direct"}) or any(
            str(item.get("type", "")).lower().startswith(("video/", "audio/"))
            or str(item.get("medium", "")).lower() in ("video", "audio")
            for item in attachments if isinstance(item, dict)
        ))

    def _skipped_report(self, title, url, reason):
        return {"title": title, "source": self.source.title or self.source.src,
                "url": url, "language": None, "method": "none", "text_length": 0,
                "status": "rejected", "reason": reason, "included": False}

    def record_candidate_outcomes(self, result, candidates, new_candidates):
        """Describe list-stage filtering without changing the dedup snapshot."""
        new_ids = {id(candidate) for candidate in new_candidates}
        excluded = list(getattr(self, "_filtered_reports", []))
        for candidate in candidates:
            if id(candidate) not in new_ids:
                reason = "historical_dedup" if candidate.get("url") else "missing_url"
                excluded.append(self._skipped_report(candidate.get("title", ""), candidate.get("url", ""), reason))
        result.quality_report = excluded + result.quality_report
        result.source_stats.update({
            "rss_returned": getattr(self, "_rss_returned", len(candidates)),
            "historical_dedup": sum(row["reason"] == "historical_dedup" for row in excluded),
            "candidates": len(new_candidates),
            "_list_stage_recorded": True,
        })

    def record_fallback_outcome(self, result):
        # fetch_with_retry constructs a fresh failure result after retries.
        if not result.source_stats and hasattr(self, "_rss_returned"):
            result.source_stats["rss_returned"] = self._rss_returned

    def _get_summary(self, entry: dict) -> str:
        """
        从 RSS 条目中获取摘要

        Args:
            entry: RSS 条目

        Returns:
            str: 摘要 HTML
        """
        # 尝试多个可能的内容字段
        if "content" in entry and len(entry.content) > 0:
            return entry.content[0].get("value", "")

        if "summary" in entry:
            return entry.get("summary", "")

        if "description" in entry:
            return entry.get("description", "")

        return ""

    def _parse_feed(self):
        """
        通过基类 HTTP 客户端下载 RSS/Atom，再用 feedparser 解析。

        不直接把 URL 交给 feedparser，确保网络访问统一使用基类客户端。
        """
        response = self._make_request(self.source.src)
        return feedparser.parse(response.content)
