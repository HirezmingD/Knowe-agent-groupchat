# knowe v0.20 — Batch 4：网络
"""
web_tools.py — `web_search` · `web_extract`。

**一个必选依赖都不加**（硬约束）。所以：

  · 搜索：ddgs（免费、无 key）。**没装也不报错**——它是可选的，
    没装就回一句「怎么装」。另留 searxng 后端给被 DDG 限流的用户。
  · 抓取：httpx 有就用（异步、快），没有就退回 stdlib 的 urllib（丢线程里）。
  · HTML→Markdown：**自己写**。markdownify / html2text / trafilatura 都很好，
    但它们都是新的必选依赖。二百行 HTMLParser 换零依赖，划算。

两条防线值得单独说：

  ★ **只认 http/https。** `web_extract("file:///etc/shadow")` 会让 Worker
    绕开 resolve_in_sandbox 读任意本地文件 —— 沙箱守着前门，这里就是后窗。
    scheme 白名单是这个模块里唯一一条真正的安全边界。

  ★ **下载有上限。** 一个 URL 指向 4GB 的 ISO，httpx 会很乐意把它读进内存。
    边读边数，超了就掐。
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable

from .agent_runtime import ToolError
from .network_policy import (
    NetworkPolicyError,
    PublicEgressProxy,
    assert_public_http_url,
    normalize_public_http_url,
)

log = logging.getLogger("knowe.web")

#: 用一个普通浏览器的 UA。
#: 不是为了伪装 —— 是因为大量站点对 `Python-urllib/3.12` 直接回 403，
#: 而这次抓取本来就是用户让他的 Agent 代他去看一眼那个页面。
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Knowe/0.20")

_MAX_URLS = 5
_MAX_BYTES = 3_000_000
_ALLOWED_SCHEMES = ("http", "https")


# ═══════════════════════════════════════════════════════════════
# 搜索
# ═══════════════════════════════════════════════════════════════

def _ddgs_search(query: str, limit: int) -> list[dict[str, str]]:
    """
    ddgs 是**同步阻塞**库 —— 调用方必须把它丢进线程（见 search()）。

    包名换过一次（duckduckgo_search → ddgs），字段名也有版本差异。
    两个都试，字段都兜住 —— 用户的 pip 里是哪个版本不该由他来操心。
    """
    DDGS = None
    try:
        from ddgs import DDGS as _D            # 新包名
        DDGS = _D
    except ImportError:
        try:
            from duckduckgo_search import DDGS as _D   # 旧包名
            DDGS = _D
        except ImportError:
            raise ToolError(
                "网络搜索需要 ddgs（DuckDuckGo，免费、不用 API key）。"
                "请让用户执行：pip install ddgs"
            ) from None

    try:
        with DDGS() as ddgs:
            rows = list(ddgs.text(query, max_results=limit))
    except Exception as exc:
        name = type(exc).__name__
        if "atelimit" in name or "429" in str(exc):
            raise ToolError(
                "DuckDuckGo 把这次搜索限流了（短时间搜太多次）。"
                "等一会儿再试，或者让用户把 KNOWE_WEB_SEARCH_BACKEND 换成 searxng "
                "并配置 KNOWE_SEARXNG_URL。"
            ) from None
        raise ToolError(f"搜索失败（{name}）：{exc}") from None

    out: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("href") or row.get("url") or row.get("link") or ""
        if not url:
            continue
        out.append({
            "url": url,
            "title": (row.get("title") or "").strip(),
            "snippet": (row.get("body") or row.get("snippet") or row.get("description") or "").strip(),
        })
    return out


def _searxng_search(query: str, limit: int, instance: str) -> list[dict[str, str]]:
    if not instance:
        raise ToolError(
            "KNOWE_WEB_SEARCH_BACKEND=searxng，但没配 KNOWE_SEARXNG_URL —— "
            "请让用户填一个 SearXNG 实例地址（如 https://searx.example.com）"
        )
    qs = urllib.parse.urlencode({"q": query, "format": "json"})
    url = f"{instance.rstrip('/')}/search?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            import json
            data = json.loads(resp.read(_MAX_BYTES).decode("utf-8", errors="replace"))
    except Exception as exc:
        raise ToolError(f"SearXNG 搜索失败（{instance}）：{exc}") from None
    rows = data.get("results") or []
    return [
        {
            "url": r.get("url", ""),
            "title": (r.get("title") or "").strip(),
            "snippet": (r.get("content") or "").strip(),
        }
        for r in rows[:limit] if isinstance(r, dict) and r.get("url")
    ]


async def search(query: str, *, limit: int, backend: str, searxng_url: str = "") -> list[dict[str, str]]:
    """
    后端是**一张表**，不是一串 if —— PRD 要「预留扩展点」，那扩展点就该是
    「加一个函数、加一行表」，而不是「回来改这个函数」。
    """
    query = (query or "").strip()
    if not query:
        raise ToolError("query 不能为空")
    limit = max(1, min(20, int(limit)))
    backend = (backend or "ddgs").strip().lower()

    if backend in ("ddgs", "duckduckgo", "ddg"):
        return await asyncio.to_thread(_ddgs_search, query, limit)
    if backend == "searxng":
        return await asyncio.to_thread(_searxng_search, query, limit, searxng_url)
    raise ToolError(
        f"不认识的搜索后端：{backend}。当前支持 ddgs（默认）和 searxng；"
        "改 KNOWE_WEB_SEARCH_BACKEND 即可。"
    )


# ═══════════════════════════════════════════════════════════════
# 抓取
# ═══════════════════════════════════════════════════════════════

def normalize_urls(raw: Any) -> list[str]:
    if isinstance(raw, str):
        items: Iterable[Any] = [raw]
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        raise ToolError("urls 要么是一个 URL 字符串，要么是 URL 数组")

    out: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        url = item.strip()
        try:
            out.append(normalize_public_http_url(url))
        except NetworkPolicyError as exc:
            raise ToolError(
                f"URL 被安全策略拒绝（只支持 http/https 公网地址）：{exc}。"
                "本地文件请用 safe_read_file；本机和局域网服务默认不向 Agent 开放。"
            ) from None
    if not out:
        raise ToolError("一个有效的 URL 都没有")
    if len(out) > _MAX_URLS:
        raise ToolError(f"一次最多抓 {_MAX_URLS} 个 URL，这次给了 {len(out)} 个——分几次抓")
    return out


@dataclass
class Fetched:
    url: str
    ok: bool
    status: int | None = None
    content_type: str = ""
    title: str = ""
    content: str = ""
    error: str = ""
    truncated: bool = False
    bytes_downloaded: int = 0


async def _fetch_one(url: str, timeout_s: float) -> Fetched:
    try:
        import httpx
    except ImportError:
        return Fetched(url=url, ok=False, error="安全抓取需要 httpx 依赖")

    try:
        async with PublicEgressProxy(connect_timeout_s=timeout_s) as egress_proxy:
            # Explicit proxy + trust_env=False prevents HTTP(S)_PROXY/NO_PROXY
            # from creating an unreviewed alternate path.  For HTTPS, httpx does
            # TLS over CONNECT using the original hostname and its normal CA
            # verification; the local proxy never terminates TLS.
            async with httpx.AsyncClient(
                timeout=timeout_s,
                follow_redirects=False,
                proxy=egress_proxy.proxy_url,
                trust_env=False,
                headers={
                    "User-Agent": _UA,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            ) as cli:
                current = url
                for _redirect in range(6):
                    # Validate every redirect hop.  The proxy repeats DNS policy
                    # at connection time and pins the resulting numeric address,
                    # so this friendly preflight is never treated as authority.
                    current = await assert_public_http_url(current)
                    async with cli.stream("GET", current) as resp:
                        if resp.status_code in {301, 302, 303, 307, 308}:
                            location = resp.headers.get("location", "").strip()
                            if not location:
                                return Fetched(
                                    url=current, ok=False, status=resp.status_code,
                                    error="重定向缺少 Location",
                                )
                            current = urllib.parse.urljoin(current, location)
                            continue
                        ctype = resp.headers.get("content-type", "")
                        buf = bytearray()
                        transport_truncated = False
                        async for chunk in resp.aiter_bytes():
                            remaining = _MAX_BYTES - len(buf)
                            if remaining <= 0:
                                transport_truncated = True
                                break
                            if len(chunk) > remaining:
                                buf.extend(chunk[:remaining])
                                transport_truncated = True
                                break
                            buf.extend(chunk)
                        if resp.status_code >= 400:
                            return Fetched(
                                url=current,
                                ok=False,
                                status=resp.status_code,
                                content_type=ctype,
                                error=f"HTTP {resp.status_code}",
                            )
                        return _decode(
                            current,
                            bytes(buf),
                            ctype,
                            resp.status_code,
                            truncated=transport_truncated,
                        )
                return Fetched(url=current, ok=False, error="重定向次数超过安全上限")
    except NetworkPolicyError as exc:
        return Fetched(url=url, ok=False, error=f"网络安全策略：{exc}")
    except Exception as exc:
        return Fetched(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")


def _fetch_urllib(url: str, timeout_s: float) -> Fetched:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw_with_probe = resp.read(_MAX_BYTES + 1)
            transport_truncated = len(raw_with_probe) > _MAX_BYTES
            raw = raw_with_probe[:_MAX_BYTES]
            ctype = resp.headers.get("content-type", "")
            return _decode(
                url, raw, ctype, resp.status, truncated=transport_truncated
            )
    except urllib.error.HTTPError as exc:
        return Fetched(url=url, ok=False, status=exc.code, error=f"HTTP {exc.code}")
    except Exception as exc:
        return Fetched(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")


_CHARSET_RX = re.compile(rb"""<meta[^>]+charset=['"]?([\w\-]+)""", re.I)


def _decode(
    url: str, raw: bytes, ctype: str, status: int, *, truncated: bool = False
) -> Fetched:
    low = ctype.lower()
    if low and not (low.startswith("text/") or "html" in low or "xml" in low
                    or "json" in low or "javascript" in low):
        return Fetched(
            url=url, ok=False, status=status, content_type=ctype,
            error=f"不是文本内容（{ctype.split(';')[0]}），web_extract 不处理。"
                  "PDF/图片请让用户下载后用别的工具处理。",
        )
    charset = ""
    m = re.search(r"charset=([\w\-]+)", ctype, re.I)
    if m:
        charset = m.group(1)
    if not charset:
        m2 = _CHARSET_RX.search(raw[:4096])
        if m2:
            charset = m2.group(1).decode("ascii", errors="ignore")
    for enc in (charset, "utf-8"):
        if not enc:
            continue
        try:
            return Fetched(
                url=url, ok=True, status=status, content_type=ctype,
                content=raw.decode(enc), bytes_downloaded=len(raw),
                truncated=truncated,
            )
        except (UnicodeDecodeError, LookupError):
            continue
    return Fetched(
        url=url, ok=True, status=status, content_type=ctype,
        content=raw.decode("utf-8", errors="replace"), bytes_downloaded=len(raw),
        truncated=truncated,
    )


async def fetch_many(urls: list[str], *, timeout_s: float) -> list[Fetched]:
    """并发抓，但别把对面打了 —— 3 路够快，也不至于让人觉得被爬。"""
    sem = asyncio.Semaphore(3)

    async def one(u: str) -> Fetched:
        async with sem:
            return await _fetch_one(u, timeout_s)

    return list(await asyncio.gather(*(one(u) for u in urls)))


# ═══════════════════════════════════════════════════════════════
# HTML → Markdown / 纯文本
# ═══════════════════════════════════════════════════════════════

_DROP_ENTIRELY = frozenset({
    "script", "style", "noscript", "svg", "canvas", "head", "meta", "link",
    "iframe", "template", "object", "embed", "audio", "video", "picture",
})
#: Visible page structure is evidence, not noise.  Navigation, sidebars, forms,
#: buttons and selects can contain the only wording needed to complete a task, so
#: they stay in the authoritative extraction.  These labels make their role
#: explicit without deleting their text.
_STRUCTURE_LABELS = {
    "nav": "Navigation",
    "aside": "Aside",
    "footer": "Footer",
    "form": "Form",
}
_BLOCK = frozenset({
    "p", "div", "section", "article", "main", "header", "ul", "ol", "li", "tr",
    "table", "thead", "tbody", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5",
    "h6", "dl", "dt", "dd", "figure", "figcaption", "hr", "br", "details",
    "nav", "footer", "aside", "form", "fieldset", "legend", "label", "button",
    "select", "option", "textarea",
})


class _Markdownifier(HTMLParser):
    """
    够用的 HTML→Markdown。**不追求完美**，追求：
      · 标题层级在（模型靠它理解文档结构）
      · 链接在（模型要顺着爬下一页）
      · 代码块在（技术文档的正文往往就是代码）
      · 导航、侧栏和表单等可见文本保留并标注结构
    """

    def __init__(self, *, markdown: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.markdown = markdown
        self.parts: list[str] = []
        self.title = ""
        self._drop_depth = 0
        self._in_title = False
        self._in_pre = False
        self._list_stack: list[str] = []
        self._link: str | None = None
        self._link_text: list[str] = []

    # ── 工具 ──
    def _emit(self, text: str) -> None:
        if self._link is not None:
            self._link_text.append(text)
        else:
            self.parts.append(text)

    def _nl(self, n: int = 1) -> None:
        self._emit("\n" * n)

    # ── 标签 ──
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        # ★ <title> 必须在丢弃判断**之前**处理：它住在 <head> 里，而 <head> 整个是丢的。
        #   先判丢弃的话，页面标题会跟着 head 一起被扔掉——而那是我们要回给模型的
        #   「这是哪一页」。只取第一个（<svg><title> 也叫 title，但它不是页面标题）。
        if tag == "title":
            if not self.title:
                self._in_title = True
            return
        if tag in _DROP_ENTIRELY:
            self._drop_depth += 1
            return
        if self._drop_depth:
            return
        if tag in _STRUCTURE_LABELS:
            self._nl(2)
            label = _STRUCTURE_LABELS[tag]
            self._emit(f"[{label}]" if self.markdown else f"{label}:")
            self._nl()
            return
        if tag == "input":
            input_type = (a.get("type") or "text").strip().lower()
            if input_type == "hidden":
                return
            label = (
                a.get("aria-label") or a.get("placeholder") or a.get("title")
                or a.get("name") or a.get("value") or input_type
            ).strip()
            self._nl()
            self._emit(f"[Input: {label}]" if self.markdown else f"Input: {label}")
            self._nl()
            return
        if tag in {"button", "select", "textarea"}:
            label = {"button": "Button", "select": "Select", "textarea": "Textarea"}[tag]
            self._nl()
            self._emit(f"[{label}] " if self.markdown else f"{label}: ")
            return
        if tag == "option":
            self._nl()
            self._emit("- " if self.markdown else "· ")
            return
        if tag == "pre":
            self._in_pre = True
            self._nl(2)
            if self.markdown:
                self._emit("```\n")
            return
        if tag == "code" and not self._in_pre and self.markdown:
            self._emit("`")
            return
        if tag == "a":
            href = a.get("href", "").strip()
            if href and not href.startswith(("javascript:", "#")) and self.markdown:
                self._link = href
                self._link_text = []
            return
        if tag == "img":
            alt = (a.get("alt") or "").strip()
            src = (a.get("src") or "").strip()
            if self.markdown and src and not src.startswith("data:"):
                self._emit(f"![{alt}]({src})")
            elif alt:
                self._emit(alt)
            return
        if tag in ("ul", "ol"):
            self._list_stack.append(tag)
            self._nl()
            return
        if tag == "li":
            self._nl()
            depth = max(0, len(self._list_stack) - 1)
            marker = "- " if (not self._list_stack or self._list_stack[-1] == "ul") else "1. "
            self._emit("  " * depth + (marker if self.markdown else "· "))
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._nl(2)
            if self.markdown:
                self._emit("#" * int(tag[1]) + " ")
            return
        if tag in ("strong", "b") and self.markdown:
            self._emit("**")
            return
        if tag in ("em", "i") and self.markdown:
            self._emit("*")
            return
        if tag == "hr":
            self._nl()
            self._emit("---" if self.markdown else "")
            self._nl()
            return
        if tag == "br":
            self._nl()
            return
        if tag in ("td", "th"):
            self._emit(" | ")
            return
        if tag in _BLOCK:
            self._nl(2 if tag in ("p", "div", "section", "blockquote", "table") else 1)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag in _DROP_ENTIRELY:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if self._drop_depth:
            return
        if tag in _STRUCTURE_LABELS:
            self._nl(2)
            return
        if tag in {"button", "select", "textarea", "option"}:
            self._nl()
            return
        if tag == "pre":
            self._in_pre = False
            if self.markdown:
                self._nl()
                self._emit("```")
            self._nl(2)
            return
        if tag == "code" and not self._in_pre and self.markdown:
            self._emit("`")
            return
        if tag == "a" and self._link is not None:
            text = "".join(self._link_text).strip()
            href, self._link = self._link, None
            self._link_text = []
            if text:
                self.parts.append(f"[{text}]({href})" if self.markdown else text)
            return
        if tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._nl()
            return
        if tag in ("strong", "b") and self.markdown:
            self._emit("**")
            return
        if tag in ("em", "i") and self.markdown:
            self._emit("*")
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._nl(2)
            return
        if tag in _BLOCK:
            self._nl(2 if tag in ("p", "div", "section", "blockquote", "table") else 1)

    def handle_data(self, data: str) -> None:
        if self._in_title:                  # 同上：标题在 <head> 里，比丢弃判断更早
            self.title += data.strip()
            return
        if self._drop_depth:
            return
        if self._in_pre:
            self._emit(data)
            return
        text = re.sub(r"[ \t\r\f\v]+", " ", data.replace("\n", " "))
        if text.strip() or (self.parts and not self.parts[-1].endswith((" ", "\n"))):
            self._emit(text)

    def result(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text(html: str, *, markdown: bool = True) -> tuple[str, str]:
    """返回 (正文, <title>)。解析器炸了也不许把工具带走 —— 退回粗暴去标签。"""
    p = _Markdownifier(markdown=markdown)
    try:
        p.feed(html)
        p.close()
        return p.result(), p.title.strip()
    except Exception:
        log.debug("HTML 解析失败，退回去标签模式", exc_info=True)
        stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        return re.sub(r"\s+", " ", stripped).strip(), ""


def render(fetched: Fetched, fmt: str, max_chars: int | None = None) -> Fetched:
    """Render the complete downloaded source.

    ``max_chars`` remains an accepted compatibility argument for legacy callers,
    but pagination belongs at the tool response boundary.  It is intentionally not
    applied here: cropping before ``offset`` is false pagination because later
    source characters become unreachable.  ``Fetched.truncated`` therefore means
    only that the explicit network byte boundary was reached.
    """
    del max_chars
    fmt = (fmt or "markdown").strip().lower()
    if fmt not in ("markdown", "html", "text"):
        raise ToolError(f"format 只能是 markdown / html / text，收到：{fmt}")
    if not fetched.ok:
        return fetched
    body = fetched.content
    looks_html = "html" in fetched.content_type.lower() or "<html" in body[:2000].lower()
    if fmt == "html" or not looks_html:
        text, title = body, ""
    else:
        text, title = html_to_text(body, markdown=(fmt == "markdown"))
    fetched.content = text
    fetched.title = title or fetched.title
    return fetched


def SUMMARY_SYSTEM() -> str:
    """[v1.0.21.3] 提取指令按当前语言；函数化避免模块级语言固化。"""
    from .i18n_backend import msg  # 局部导入：避免模块级 msg() 固化语言
    return msg("web.001")


__all__ = [
    "Fetched",
    "SUMMARY_SYSTEM",
    "fetch_many",
    "html_to_text",
    "normalize_urls",
    "render",
    "search",
]
