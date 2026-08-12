# knowe v0.20 — Batch 4：浏览器（Python Playwright）
"""
browser_tools.py — 一个 headless Chromium，按 Worker 分身。

三个设计决定，都值得解释：

**① ref 是我们自己发的，不是 Playwright 给的。**

  PRD 要「accessibility tree 快照 + @e1 这样的 ref」。Playwright 的
  `page.accessibility.snapshot()` 能给树，**但给不了句柄**——拿到
  `{role: "button", name: "登录"}` 之后，你没法「点它」，只能回头去猜一个
  选择器（`text=登录`），而页面上有三个「登录」的时候就点错了。
  （Playwright 官方 MCP 用的是内部 API `_snapshotForAI`，Python 公开接口里没有。）

  所以这里注入一段 JS：自己遍历 DOM，给每个可交互元素打上
  `data-knowe-ref="e1"`，然后 `page.locator('[data-knowe-ref="e1"]')` 精确命中。
  快照上写着 `@e1`，模型说「点 e1」，我们点的**就是那一个**。
  没有猜，没有歧义，全部走 Playwright 公开 API。

**② 快照按范围给「能动的东西」+ 正文，不做全页硬顶。**

  一个新闻首页有 3000 个 DOM 节点。全 dump 给模型 = 一次调用烧掉整个上下文，
  但只给固定前缀又会让后半页永久不可见。这里对元素和 body 文本分别计算
  稳定总长度与范围，单次回执有界，后续页通过 offset 继续读取。

**③ 浏览器是全进程一个，上下文是每个 Worker 一个。**

  Chromium 进程 ~200MB，每个 BrowserContext ~5MB。按 agent 隔离要的是
  **cookie / 登录态互不串**（PRD：session 隔离），不是要 8 个 Chromium。
  一个浏览器 + N 个 context 正好给到这个语义，代价小两个数量级。

  代价是这个 Chromium 属于「谁都不属于」，于是必须有人负责关：
    · engine.stop() → BrowserPool.aclose()（切目录/关应用）
    · 空闲回收      → 十分钟没人用 → 连 Chromium 一起放掉
  这两条缺一条，用户切十次项目就是 2GB 常驻。见 agent_runtime 的模块头。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any

from .agent_runtime import ToolError, clip

log = logging.getLogger("knowe.browser")

def _hint(packaged_text: str, default: str) -> str:
    """打包态（KNOWE_PACKAGED=1/true）下给「重装 Knowe」提示，否则保留原始安装指引。"""
    if os.environ.get("KNOWE_PACKAGED", "").strip().lower() in ("1", "true"):
        return packaged_text
    return default


_PACKAGED_HINT = "浏览器组件缺失，请重新安装 Knowe。"

_INSTALL_HINT = _hint(
    _PACKAGED_HINT,
    (
        "Playwright 未安装，浏览器工具不可用。请让用户执行：\n"
        "    pip install playwright && playwright install chromium\n"
        "（这是可选依赖，不装不影响其它工具；装完不用重启 Knowe。）"
    ),
)
_CHROMIUM_HINT = _hint(
    _PACKAGED_HINT,
    "Playwright 装好了，但 Chromium 还没下载。请让用户执行：\n"
    "    playwright install chromium",
)

_ALLOWED_SCHEMES = ("http://", "https://", "about:blank")
_CONSOLE_RING = 200
_DIALOG_RING = 20
_BODY_TEXT_CHARS = 1200


def _import_playwright() -> Any:
    try:
        from playwright import async_api
    except ImportError:
        raise ToolError(_INSTALL_HINT) from None
    return async_api


def _translate(exc: Exception, *, what: str) -> ToolError:
    """
    把 Playwright 的英文异常翻译成模型能照着做点什么的中文。

    ★「Executable doesn't exist」是新用户 100% 会撞上的一个坎：
      pip install playwright 之后浏览器**并没有**装。原样把英文抛给模型，
      它多半会去 pip install 第二遍。
    """
    msg = str(exc)
    if "Executable doesn't exist" in msg or "playwright install" in msg:
        return ToolError(_CHROMIUM_HINT)
    if "Timeout" in type(exc).__name__ or "Timeout" in msg[:80]:
        return ToolError(
            f"{what}超时。页面可能没加载完、元素被遮住、或者需要先滚动到它。"
            "可以重新 browser_snapshot 看看现在页面上有什么。"
        )
    if "net::ERR_NAME_NOT_RESOLVED" in msg:
        return ToolError(f"{what}失败：域名解析不了，检查 URL 拼写或网络。")
    if "net::ERR_CONNECTION_REFUSED" in msg:
        return ToolError(f"{what}失败：连接被拒绝（服务没起来？端口对吗？）。")
    if "net::" in msg:
        net = msg.split("net::")[1].split()[0]
        return ToolError(f"{what}失败：网络错误 {net}")
    return ToolError(f"{what}失败：{msg.splitlines()[0][:300]}")


# ═══════════════════════════════════════════════════════════════
# 快照 JS
# ═══════════════════════════════════════════════════════════════

_SNAPSHOT_JS = r"""
(opts) => {
  const OFFSET = Math.max(0, Number(opts.offset || 0));
  const LIMIT = Math.max(1, Number(opts.limit || 200));
  const TEXT_OFFSET = Math.max(0, Number(opts.textOffset || 0));
  const TEXT_LIMIT = Math.max(1, Number(opts.textLimit || 1200));
  const INTERACTIVE = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea',
    'summary', '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]',
    '[role=tab]', '[role=menuitem]', '[role=switch]', '[role=option]',
    '[role=searchbox]', '[role=textbox]', '[role=combobox]',
    '[contenteditable=""]', '[contenteditable="true"]', '[onclick]',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');
  const STRUCTURE = 'h1,h2,h3,h4,[role=heading]';

  // Rebuild the complete ref index on every snapshot.  Ref numbering is based on
  // full-page DOM order, never the requested page, so offset=1200 still exposes
  // the same @e identifiers that an offset=0 scan would have assigned.
  document.querySelectorAll('[data-knowe-ref]').forEach(
    e => e.removeAttribute('data-knowe-ref')
  );

  const visible = (el) => {
    if (el.closest('[aria-hidden="true"]')) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const st = window.getComputedStyle(el);
    if (!st) return false;
    if (st.visibility === 'hidden' || st.display === 'none') return false;
    if (parseFloat(st.opacity || '1') === 0) return false;
    return true;
  };
  const clean = (value, max = 120) =>
    (value || '').replace(/\s+/g, ' ').trim().slice(0, max);
  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (['button', 'submit', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'file') return 'file-input';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return 'element';
  };
  const nameOf = (el) => {
    let value = clean(el.getAttribute('aria-label'));
    if (!value) {
      const by = el.getAttribute('aria-labelledby');
      if (by) {
        value = clean(by.split(/\s+/).map(id => {
          const node = document.getElementById(id);
          return node ? node.innerText || node.textContent : '';
        }).join(' '));
      }
    }
    if (!value && el.tagName === 'IMG') value = clean(el.getAttribute('alt'));
    if (!value) value = clean(el.innerText || el.textContent);
    if (!value) value = clean(el.getAttribute('placeholder'));
    if (!value) value = clean(el.getAttribute('title'));
    if (!value) value = clean(el.getAttribute('name'));
    if (!value && el.labels && el.labels.length) value = clean(el.labels[0].innerText);
    if (!value) value = clean(el.getAttribute('value'));
    return value;
  };

  const nodes = Array.from(document.querySelectorAll(INTERACTIVE + ',' + STRUCTURE));
  const page = [];
  let rowCount = 0;
  let interactiveCount = 0;
  for (const el of nodes) {
    if (!visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const role = roleOf(el);
    const isHeading = role === 'heading';
    const name = nameOf(el);
    if (!name && !isHeading &&
        !['textbox', 'checkbox', 'radio', 'combobox', 'file-input'].includes(role)) {
      continue;
    }

    const row = { role: role, name: name, tag: tag };
    if (isHeading) {
      if (!name) continue;
      row.level = /^h[1-6]$/.test(tag) ? parseInt(tag[1]) : 2;
    } else {
      interactiveCount += 1;
      const ref = 'e' + interactiveCount;
      el.setAttribute('data-knowe-ref', ref);
      row.ref = ref;
      if (tag === 'a') {
        const href = el.getAttribute('href') || '';
        if (href) row.href = href.slice(0, 200);
      }
      if (el.disabled) row.disabled = true;
      if (typeof el.checked === 'boolean' &&
          (role === 'checkbox' || role === 'radio')) row.checked = el.checked;
      if (role === 'textbox') row.value = clean(el.value || el.innerText);
    }

    if (rowCount >= OFFSET && page.length < LIMIT) page.push(row);
    rowCount += 1;
  }

  const body = document.body
    ? (document.body.innerText || '').replace(/\r\n?/g, '\n')
        .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
    : '';
  const bodyStart = Math.min(TEXT_OFFSET, body.length);
  const bodyEnd = Math.min(body.length, bodyStart + TEXT_LIMIT);
  return {
    url: location.href,
    title: document.title || '',
    elements: page,
    elementOffset: OFFSET,
    elementEnd: OFFSET + page.length,
    elementTotal: rowCount,
    elementHasMore: OFFSET + page.length < rowCount,
    text: body.slice(bodyStart, bodyEnd),
    bodyTextOffset: bodyStart,
    bodyTextEnd: bodyEnd,
    bodyTextTotal: body.length,
    bodyTextHasMore: bodyEnd < body.length
  };
}
"""


def format_snapshot(snap: dict[str, Any]) -> str:
    """
    快照 → 给模型看的文本。

    刻意做成**扁平的、一行一个**：模型要的是「有哪些能点的，各自叫什么」，
    缩进树只会多花 token 而不多给信息。标题行（# / ##）用来分段，
    让它知道「这一堆按钮是在哪个区域底下」。
    """
    lines: list[str] = []
    for row in snap.get("elements", []):
        role = row.get("role", "")
        name = row.get("name", "")
        if role == "heading":
            lines.append(f"{'#' * min(4, int(row.get('level', 2)))} {name}")
            continue
        bits = [f"@{row['ref']}", role]
        if name:
            bits.append(f'"{name}"')
        if row.get("value"):
            bits.append(f'= "{row["value"]}"')
        if row.get("href"):
            bits.append(f"→ {row['href']}")
        if row.get("checked") is not None:
            bits.append("[已选中]" if row["checked"] else "[未选中]")
        if row.get("disabled"):
            bits.append("[禁用]")
        lines.append(" ".join(bits))
    body = "\n".join(lines)
    if snap.get("elementHasMore") or snap.get("element_has_more") or snap.get("truncated"):
        body += "\n…（本页后还有元素；按 next_offset 继续读取）"
    return body or "（页面上没有可交互的元素——可能还在加载，或者内容在 iframe 里）"


# ═══════════════════════════════════════════════════════════════
# 会话
# ═══════════════════════════════════════════════════════════════

@dataclass
class Session:
    agent_id: str
    context: Any
    page: Any
    console: list[dict[str, Any]] = field(default_factory=list)
    dialogs: list[dict[str, Any]] = field(default_factory=list)
    dialog_policy: str = "dismiss"
    dialog_text: str | None = None
    last_used: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_used = time.monotonic()


# 全进程共用一个 Playwright + 一个 Chromium（见模块头 ③）
_pw: Any = None
_browser: Any = None
_launch_lock = asyncio.Lock()
_pools: set["BrowserPool"] = set()
_reaper: asyncio.Task[None] | None = None


def _system_chromium_executable() -> str | None:
    """Return an explicitly configured or system-installed Chromium executable.

    Playwright's Python package and browser payload are distributed separately.  Some
    production/container images intentionally provide a system Chromium instead of the
    Playwright-managed download.  Treat that as a supported deployment layout rather
    than reporting a false capability failure.
    """

    configured = os.getenv("KNOWE_CHROMIUM_EXECUTABLE", "").strip()
    if configured:
        candidate = os.path.abspath(os.path.expanduser(configured))
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        candidate = shutil.which(name)
        if candidate:
            return candidate
    return None


async def _ensure_browser(*, headless: bool) -> Any:
    global _pw, _browser
    async with _launch_lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        api = _import_playwright()
        if _pw is None:
            try:
                _pw = await api.async_playwright().start()
            except Exception as exc:
                raise _translate(exc, what="启动 Playwright") from None

        launch_args = ["--disable-dev-shm-usage", "--no-sandbox"]
        try:
            _browser = await _pw.chromium.launch(headless=headless, args=launch_args)
        except Exception as primary_exc:
            message = str(primary_exc)
            system_executable = _system_chromium_executable()
            missing_managed_browser = (
                "Executable doesn't exist" in message or "playwright install" in message
            )
            if not (missing_managed_browser and system_executable):
                raise _translate(primary_exc, what="启动浏览器") from None
            try:
                _browser = await _pw.chromium.launch(
                    headless=headless,
                    executable_path=system_executable,
                    args=launch_args,
                )
            except Exception as fallback_exc:
                raise _translate(fallback_exc, what="启动系统 Chromium") from None
            log.info("[browser] 使用系统 Chromium：%s", system_executable)
        log.info("[browser] Chromium 已启动（headless=%s）", headless)
        return _browser


async def _shutdown_browser() -> None:
    global _pw, _browser
    browser, _browser = _browser, None
    pw, _pw = _pw, None
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            log.debug("[browser] 关闭 Chromium 失败（忽略）", exc_info=True)
    if pw is not None:
        try:
            await pw.stop()
        except Exception:
            log.debug("[browser] 停止 Playwright 失败（忽略）", exc_info=True)
    log.info("[browser] Chromium 已释放")


class BrowserPool:
    """一个项目名下的浏览器会话（每个 Worker 一个 BrowserContext）。"""

    def __init__(self, project_id: str, *, headless: bool, timeout_s: float,
                 idle_s: float, max_sessions: int, snapshot_max: int) -> None:
        self.project_id = project_id
        self.headless = headless
        self.timeout_ms = int(timeout_s * 1000)
        self.idle_s = idle_s
        self.max_sessions = max_sessions
        self.snapshot_max = snapshot_max
        self._sessions: dict[str, Session] = {}
        self._active_agents: set[str] = set()
        _pools.add(self)
        _start_reaper()

    # ── 会话 ──
    def mark_active(self, agent_id: str) -> None:
        """Protect an attempt-owned session from idle reaping until cleanup."""
        self._active_agents.add(str(agent_id))

    def release_active(self, agent_id: str) -> None:
        self._active_agents.discard(str(agent_id))

    async def session(self, agent_id: str) -> Session:
        s = self._sessions.get(agent_id)
        if s is not None:
            s.touch()
            return s
        if len(self._sessions) >= self.max_sessions:
            raise ToolError(
                f"这个项目的浏览器会话已经开到上限（{self.max_sessions} 个）。"
                "让先用完的成员调 browser_close 释放，或者等空闲回收。"
            )
        browser = await _ensure_browser(headless=self.headless)
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="zh-CN",
            )
            context.set_default_timeout(self.timeout_ms)
            page = await context.new_page()
        except Exception as exc:
            raise _translate(exc, what="创建浏览器会话") from None

        s = Session(agent_id=agent_id, context=context, page=page)
        self._wire(s, page)
        self._sessions[agent_id] = s
        log.info("[%s] 浏览器会话已建立：%s", self.project_id, agent_id)
        return s

    def _wire(self, s: Session, page: Any) -> None:
        """
        console / pageerror / dialog 的监听必须在**页面出生时**就挂上。

        等模型调 browser_console 的时候再挂，前面那些报错早就过去了——
        而它想看的恰恰是「刚才那一下为什么白屏」。
        """
        def on_console(msg: Any) -> None:
            try:
                s.console.append({
                    "type": getattr(msg, "type", ""),
                    "text": str(getattr(msg, "text", ""))[:600],
                })
            except Exception:
                pass
            del s.console[:-_CONSOLE_RING]

        def on_pageerror(err: Any) -> None:
            s.console.append({"type": "pageerror", "text": str(err)[:600]})
            del s.console[:-_CONSOLE_RING]

        def on_dialog(dialog: Any) -> None:
            # ★ 对话框会把页面**整个卡住**，直到有人 accept/dismiss。
            #   Playwright 默认自动 dismiss，正是为了不让自动化挂死。
            #   这里保留这个语义（默认 dismiss），只是把「怎么处理」变成可配的策略，
            #   并且**记下来**——否则模型只会看到「点击超时」，永远不知道
            #   是一个 confirm 挡在那儿。
            record = {
                "type": getattr(dialog, "type", ""),
                "message": str(getattr(dialog, "message", ""))[:400],
                "handled_as": s.dialog_policy,
            }
            s.dialogs.append(record)
            del s.dialogs[:-_DIALOG_RING]

            async def settle() -> None:
                try:
                    if s.dialog_policy == "accept":
                        await dialog.accept(s.dialog_text or "")
                    else:
                        await dialog.dismiss()
                except Exception:
                    log.debug("[browser] 处理弹窗失败（忽略）", exc_info=True)

            asyncio.ensure_future(settle())

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("dialog", on_dialog)

    async def close_session(self, agent_id: str) -> bool:
        self.release_active(agent_id)
        s = self._sessions.pop(agent_id, None)
        if s is None:
            return False
        try:
            await s.context.close()
        except Exception:
            log.debug("[%s] 关闭浏览器上下文失败（忽略）", self.project_id, exc_info=True)
        return True

    def idle_agents(self, now: float) -> list[str]:
        return [
            agent_id for agent_id, session in self._sessions.items()
            if agent_id not in self._active_agents
            and now - session.last_used > self.idle_s
        ]

    @property
    def live(self) -> int:
        return len(self._sessions)

    async def aclose(self, *, immediate: bool = False) -> None:
        for agent_id in list(self._sessions):
            await self.close_session(agent_id)
        _pools.discard(self)
        await _maybe_release_browser()


async def _maybe_release_browser() -> None:
    """一个会话都不剩了 → 把那 200MB 还给用户。"""
    if any(p.live for p in _pools):
        return
    if _browser is not None:
        await _shutdown_browser()


def _start_reaper() -> None:
    global _reaper
    if _reaper is not None and not _reaper.done():
        return
    _reaper = asyncio.ensure_future(_reap_loop())


async def _reap_loop() -> None:
    """
    空闲回收。

    真实场景：Worker 查了一次文档，然后一整天没人再碰浏览器。没有这个循环，
    那个 Chromium 会一直躺到用户关掉 Knowe——他会在活动监视器里看到一个
    莫名其妙吃着 200MB 的 Chromium，并且合理地认为这是个 bug。
    """
    try:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for pool in list(_pools):
                for agent_id in pool.idle_agents(now):
                    log.info("[%s] 浏览器会话空闲回收：%s", pool.project_id, agent_id)
                    await pool.close_session(agent_id)
            await _maybe_release_browser()
            if not _pools:
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        log.debug("[browser] 空闲回收循环出错（下次调用会重启它）", exc_info=True)


# ═══════════════════════════════════════════════════════════════
# 动作
# ═══════════════════════════════════════════════════════════════

_SCHEME_RX = __import__("re").compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def check_url(url: Any) -> str:
    """
    ★ 这是浏览器这一侧唯一的安全边界。

    `browser_navigate("file:///etc/shadow")` 会让 Chromium 读任意本地文件，
    整个 resolve_in_sandbox 就白守了。`javascript:` 同理（在当前页注入脚本）。
    所以：**认得出的 scheme 只放 http/https**，认不出 scheme 的当域名补 https。
    """
    if not isinstance(url, str) or not url.strip():
        raise ToolError("url 不能为空")
    u = url.strip()
    low = u.lower()
    if low == "about:blank":
        return "about:blank"
    if _SCHEME_RX.match(u):
        if not low.startswith(("http://", "https://")):
            raise ToolError(
                f"只允许 http/https 地址：{url}。"
                "本地文件请用 safe_read_file，不要让浏览器去读文件系统。"
            )
        return u
    return "https://" + u                          # 模型常直接给 example.com


async def snapshot(
    s: Session,
    *,
    offset: int = 0,
    limit: int = 200,
    text_offset: int = 0,
    text_limit: int | None = None,
    max_elements: int | None = None,
) -> dict[str, Any]:
    """Return one resumable page of the DOM index and body text.

    ``max_elements`` is retained as a compatibility alias for older direct
    callers.  It controls only this response page; there is no full-page element
    ceiling.
    """
    s.touch()
    if max_elements is not None:
        limit = int(max_elements)
    offset = max(0, int(offset))
    limit = max(1, int(limit))
    text_offset = max(0, int(text_offset))
    text_limit = _BODY_TEXT_CHARS if text_limit is None else max(1, int(text_limit))
    try:
        snap = await s.page.evaluate(
            _SNAPSHOT_JS,
            {
                "offset": offset,
                "limit": limit,
                "textOffset": text_offset,
                "textLimit": text_limit,
            },
        )
    except Exception as exc:
        raise _translate(exc, what="页面快照") from None

    elements = list(snap.get("elements") or [])
    formatted = format_snapshot({
        "elements": elements,
        "elementHasMore": bool(snap.get("elementHasMore")),
    })
    return {
        "url": snap.get("url", ""),
        "title": snap.get("title", ""),
        "elements": formatted,
        "page_text": snap.get("text", ""),
        "element_count": int(snap.get("elementTotal") or 0),
        "element_returned": len(elements),
        "element_offset": int(snap.get("elementOffset") or offset),
        "element_end": int(snap.get("elementEnd") or (offset + len(elements))),
        "element_has_more": bool(snap.get("elementHasMore")),
        "body_text_offset": int(snap.get("bodyTextOffset") or 0),
        "body_text_end": int(snap.get("bodyTextEnd") or 0),
        "body_text_total_characters": int(snap.get("bodyTextTotal") or 0),
        "body_text_has_more": bool(snap.get("bodyTextHasMore")),
    }


def locator(s: Session, ref: Any) -> Any:
    if not isinstance(ref, str) or not ref.strip():
        raise ToolError("ref 不能为空——先 browser_snapshot，用返回里 @e3 这样的编号")
    r = ref.strip().lstrip("@")
    # 严格限死成 e\d+ —— ref 是要拼进 CSS 选择器的，这一句同时挡掉了选择器注入。
    if not r.startswith("e") or not r[1:].isdigit():
        raise ToolError(f"ref 格式不对：{ref}（应该形如 e3 或 @e3，来自 browser_snapshot 的返回）")
    return s.page.locator(f'[data-knowe-ref="{r}"]')


async def act(s: Session, coro: Any, *, what: str) -> None:
    s.touch()
    try:
        await coro
    except Exception as exc:
        err = _translate(exc, what=what)
        if s.dialogs and "超时" in str(err):
            # 「点了没反应」的头号真凶。把它说出来，省模型三轮猜。
            last = s.dialogs[-1]
            raise ToolError(
                f"{err}\n另外：页面弹出过一个 {last['type']} 对话框"
                f"（内容：{last['message']}），已按当前策略「{last['handled_as']}」处理。"
                "如果需要接受弹窗，先调 browser_dialog(action='accept')。"
            ) from None
        raise err from None


async def get_images(s: Session, limit: int) -> list[dict[str, Any]]:
    s.touch()
    js = r"""
    (max) => Array.from(document.images).slice(0, max).map(img => ({
      src: img.currentSrc || img.src || '',
      alt: (img.alt || '').slice(0, 200),
      width: img.naturalWidth,
      height: img.naturalHeight
    })).filter(i => i.src && !i.src.startsWith('data:'))
    """
    try:
        return await s.page.evaluate(js, limit)
    except Exception as exc:
        raise _translate(exc, what="读取页面图片") from None


async def evaluate(s: Session, script: str) -> str:
    s.touch()
    if not isinstance(script, str) or not script.strip():
        raise ToolError("script 不能为空")
    try:
        result = await s.page.evaluate(script)
    except Exception as exc:
        raise _translate(exc, what="执行 JavaScript") from None
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(result)
    out, _ = clip(text, 8000, note="返回值太长，已截断")
    return out


__all__ = [
    "BrowserPool",
    "Session",
    "act",
    "check_url",
    "evaluate",
    "format_snapshot",
    "get_images",
    "locator",
    "snapshot",
]
