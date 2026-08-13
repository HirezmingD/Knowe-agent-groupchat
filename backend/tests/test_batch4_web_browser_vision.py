# knowe v0.20 — Batch 4 测试：网络 / 浏览器 / 视觉 / 运行时
"""
这一批工具全都依赖外部世界（网络、Playwright、多模态 API）。CI 里那些都没有——
**所以这个文件测的正是「没有的时候会发生什么」**，也就是 §七要的优雅降级：
不崩、不卡、回一句人话告诉用户该装什么。

纯逻辑的部分（HTML 解析、URL 白名单、快照格式化、魔数嗅探）一并在这里测，
它们不需要任何外部依赖。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import browser_tools, vision_tools, web_tools          # noqa: E402
from backend.agent_runtime import (                                 # noqa: E402
    ProjectRuntime, ToolError, clip_middle, runtime_for, shutdown_project_runtime,
)


def _has(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


# ═══════════════════════════ URL 白名单（唯一的真边界）═══════════════════════

@pytest.mark.parametrize("bad", [
    "file:///etc/shadow",
    "file:///C:/Windows/System32/config/SAM",
    "javascript:alert(1)",
    "data:text/html,<script>x</script>",
    "ftp://example.com/x",
])
def test_web_extract_only_allows_http(bad: str) -> None:
    """★ file:// 能绕开整个 resolve_in_sandbox 读任意本地文件。"""
    with pytest.raises(ToolError) as e:
        web_tools.normalize_urls(bad)
    assert "http" in str(e.value)


@pytest.mark.parametrize("bad", [
    "file:///etc/shadow",
    "javascript:alert(document.cookie)",
    "chrome://settings",
])
def test_browser_only_allows_http(bad: str) -> None:
    with pytest.raises(ToolError):
        browser_tools.check_url(bad)


def test_bare_domain_gets_https() -> None:
    """模型经常直接写 example.com —— 补上比打回去好。"""
    assert browser_tools.check_url("example.com") == "https://example.com"
    assert web_tools.normalize_urls("example.com") == ["https://example.com"]


def test_http_and_https_pass_through() -> None:
    with pytest.raises(ToolError):
        browser_tools.check_url("http://localhost:3000/x")
    assert browser_tools.check_url("http://example.com:3000/x") == "http://example.com:3000/x"
    assert browser_tools.check_url(" https://a.com/b?c=1 ") == "https://a.com/b?c=1"


def test_url_list_is_capped() -> None:
    with pytest.raises(ToolError) as e:
        web_tools.normalize_urls([f"https://e{i}.com" for i in range(9)])
    assert "最多" in str(e.value)


# ═══════════════════════════ HTML → Markdown ════════════════════════════════

_PAGE = """
<html><head><title>安装指南</title>
<style>.x{color:red}</style><script>var evil=1;</script></head>
<body>
<nav><a href="/home">首页</a><a href="/about">关于</a></nav>
<main>
  <h1>快速开始</h1>
  <p>先装 <code>foo</code>，然后运行：</p>
  <pre>pip install foo
foo --init</pre>
  <h2>参数</h2>
  <ul><li>--verbose 详细输出</li><li>--quiet 安静模式</li></ul>
  <p>更多见 <a href="https://docs.example.com/api">API 文档</a>。</p>
</main>
<footer>版权所有 2026 · <a href="/tos">服务条款</a></footer>
</body></html>
"""


def test_html_to_markdown_keeps_what_matters() -> None:
    text, title = web_tools.html_to_text(_PAGE)
    assert title == "安装指南"
    assert "# 快速开始" in text          # 标题层级：模型靠它理解结构
    assert "## 参数" in text
    assert "pip install foo" in text     # 代码块：技术文档的正文往往就是代码
    assert "- --verbose 详细输出" in text
    assert "[API 文档](https://docs.example.com/api)" in text   # 链接：它要顺着爬


def test_html_to_markdown_keeps_visible_page_chrome_but_drops_code() -> None:
    """Visible nav/footer/form text is evidence; scripts/styles/media are not."""
    text, _ = web_tools.html_to_text(_PAGE)
    assert "var evil" not in text and "color:red" not in text
    assert "[Navigation]" in text
    assert "服务条款" in text
    assert "关于" in text


def test_text_format_has_no_markdown_syntax() -> None:
    text, _ = web_tools.html_to_text(_PAGE, markdown=False)
    assert "快速开始" in text
    assert "#" not in text and "](" not in text


def test_html_parser_never_takes_the_tool_down() -> None:
    """真实网页的 HTML 什么样都有。解析器炸了也得回点东西。"""
    for junk in ("<div><p>没闭合", "<<<>>>", "", "<a href=<b>x</a>", "纯文本没有标签"):
        text, _ = web_tools.html_to_text(junk)
        assert isinstance(text, str)


# ═══════════════════════════ 降级：ddgs 没装 ════════════════════════════════

@pytest.mark.skipif(_has("ddgs") or _has("duckduckgo_search"), reason="装了 ddgs，测不到缺失路径")
def test_search_without_ddgs_says_how_to_install() -> None:
    """★ 可选依赖缺失 → 一句能照着做的中文，而不是 ImportError 把回合带走。"""
    with pytest.raises(ToolError) as e:
        asyncio.run(web_tools.search("python asyncio", limit=3, backend="ddgs"))
    msg = str(e.value)
    assert "pip install ddgs" in msg


def test_unknown_search_backend_is_a_message() -> None:
    with pytest.raises(ToolError) as e:
        asyncio.run(web_tools.search("x", limit=3, backend="google"))
    assert "KNOWE_WEB_SEARCH_BACKEND" in str(e.value)


def test_searxng_without_url_is_a_message() -> None:
    with pytest.raises(ToolError) as e:
        asyncio.run(web_tools.search("x", limit=3, backend="searxng", searxng_url=""))
    assert "KNOWE_SEARXNG_URL" in str(e.value)


def test_empty_query() -> None:
    with pytest.raises(ToolError):
        asyncio.run(web_tools.search("   ", limit=3, backend="ddgs"))


# ═══════════════════════════ 降级：浏览器 ═══════════════════════════════════

def test_browser_missing_playwright_says_how_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """在已安装 Playwright 的 CI 中也确定性模拟 ImportError，不靠 skip 制造绿灯。"""
    import builtins

    real_import = builtins.__import__

    def missing_playwright(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError("simulated missing playwright")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_playwright)
    with pytest.raises(ToolError) as e:
        browser_tools._import_playwright()
    msg = str(e.value)
    assert "pip install playwright" in msg and "playwright install chromium" in msg


def test_chromium_missing_hint_is_actionable() -> None:
    """Chromium 没下载时 Playwright 抛的是英文 Error —— 必须翻成一句能照做的话。"""
    class _FakeErr(Exception):
        pass

    err = browser_tools._translate(
        _FakeErr("BrowserType.launch: Executable doesn't exist at /root/.cache/ms-playwright/…"),
        what="启动浏览器")
    assert "playwright install chromium" in str(err)


# ── 真·端到端（不需要网络：页面用 set_content 直接灌）─────────────────────

def _browser_ready() -> bool:
    if not _has("playwright"):
        return False
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        return False
    return True


_LOGIN_PAGE = """
<html><head><title>登录页</title></head><body>
<h1>欢迎回来</h1>
<nav><a href="/skip">导航链接</a></nav>
<input id="user" placeholder="用户名">
<input id="pw" type="password" aria-label="密码">
<input id="remember" type="checkbox" checked>
<button id="go" onclick="console.log('clicked:'+document.getElementById('user').value)">登录</button>
<button id="danger" onclick="if(confirm('确定要删除吗？')){console.log('DELETED')}else{console.log('KEPT')}">删除</button>
<button id="broken" disabled>禁用按钮</button>
<div style="display:none"><button>看不见的按钮</button></div>
<script>console.warn('page-warmup');</script>
</body></html>
"""


@pytest.mark.skipif(not _browser_ready(), reason="没有 Playwright/Chromium")
def test_browser_end_to_end() -> None:
    """
    快照 → @ref → 点击/输入 → console → 弹窗 → 关闭，一条龙走一遍真浏览器。
    **这是 ref 机制唯一能被证明的地方**：注入 data-knowe-ref、按它精确定位。
    """
    async def go() -> None:
        pool = browser_tools.BrowserPool(
            "e2e", headless=True, timeout_s=15, idle_s=600,
            max_sessions=2, snapshot_max=100)
        try:
            s = await pool.session("fe_1")
            await s.page.set_content(_LOGIN_PAGE)

            # ── 快照 ──
            snap = await browser_tools.snapshot(s, max_elements=100)
            assert snap["title"] == "登录页"
            body = snap["elements"]
            assert "# 欢迎回来" in body                    # 标题给结构
            assert "看不见的按钮" not in body               # display:none 不列
            assert "导航链接" in body                       # 快照要动手，导航也能点
            assert "[禁用]" in body                        # 禁用状态告诉模型别白点
            assert "[已选中]" in body                      # checkbox 状态

            refs = {}
            for line in body.splitlines():
                if line.startswith("@"):
                    ref = line.split()[0][1:]
                    refs[line] = ref
            user_ref = next(r for l, r in refs.items() if "用户名" in l)
            pw_ref = next(r for l, r in refs.items() if "密码" in l)
            go_ref = next(r for l, r in refs.items() if '"登录"' in l)
            danger_ref = next(r for l, r in refs.items() if '"删除"' in l)

            # ── 输入（ref 必须精确命中那一个，不是"某个输入框"）──
            await browser_tools.act(s, browser_tools.locator(s, user_ref).fill("alice"),
                                    what="输入")
            await browser_tools.act(s, browser_tools.locator(s, pw_ref).fill("secret"),
                                    what="输入")
            assert await s.page.input_value("#user") == "alice"
            assert await s.page.input_value("#pw") == "secret"

            # ── 点击 + console ──
            await browser_tools.act(s, browser_tools.locator(s, go_ref).click(), what="点击")
            await s.page.wait_for_timeout(300)
            texts = [c["text"] for c in s.console]
            assert "clicked:alice" in texts                # 点中的是「登录」，且拿到了输入值
            assert "page-warmup" in texts                  # 监听从页面出生就挂着

            # ── 弹窗：默认 dismiss，页面不会卡死 ──
            await browser_tools.act(s, browser_tools.locator(s, danger_ref).click(), what="点击")
            await s.page.wait_for_timeout(300)
            assert s.dialogs and "确定要删除吗？" in s.dialogs[-1]["message"]
            assert s.dialogs[-1]["handled_as"] == "dismiss"
            assert "KEPT" in [c["text"] for c in s.console]     # 点了取消

            # ── 改策略 → accept ──
            s.dialog_policy = "accept"
            await browser_tools.act(s, browser_tools.locator(s, danger_ref).click(), what="点击")
            await s.page.wait_for_timeout(300)
            assert "DELETED" in [c["text"] for c in s.console]

            # ── evaluate ──
            assert "登录页" in await browser_tools.evaluate(s, "document.title")

            # ── 会话隔离：另一个 Worker 是另一个 context ──
            s2 = await pool.session("qa_1")
            assert s2.context is not s.context
            assert pool.live == 2

            # ── 关闭 ──
            assert await pool.close_session("fe_1") is True
            assert pool.live == 1
            assert await pool.close_session("fe_1") is False    # 关两次不报错
        finally:
            await pool.aclose(immediate=True)
            if browser_tools._reaper:
                browser_tools._reaper.cancel()

    asyncio.run(go())


@pytest.mark.skipif(not _browser_ready(), reason="没有 Playwright/Chromium")
def test_stale_ref_gives_a_clear_message() -> None:
    """
    ★ 页面一变，旧编号就失效了。这时候模型看到的必须是「重新 snapshot」，
      而不是一句英文 TimeoutError —— 否则它会拿着同一个死 ref 重试三轮。
    """
    async def go() -> None:
        pool = browser_tools.BrowserPool(
            "stale", headless=True, timeout_s=2, idle_s=600,
            max_sessions=1, snapshot_max=50)
        try:
            s = await pool.session("fe_1")
            await s.page.set_content("<button>老按钮</button>")
            await browser_tools.snapshot(s, max_elements=50)
            await s.page.set_content("<p>页面换了</p>")        # 所有 ref 随之蒸发
            with pytest.raises(ToolError) as e:
                await browser_tools.act(
                    s, browser_tools.locator(s, "e1").click(timeout=1500), what="点击")
            assert "超时" in str(e.value) and "browser_snapshot" in str(e.value)
        finally:
            await pool.aclose(immediate=True)
            if browser_tools._reaper:
                browser_tools._reaper.cancel()

    asyncio.run(go())


@pytest.mark.skipif(not _browser_ready(), reason="没有 Playwright/Chromium")
def test_browser_released_when_last_session_closes() -> None:
    """★ 没人用了就把那 200MB 还给用户——这是「Knowe 是内存黑洞」投诉的解药。"""
    async def go() -> None:
        pool = browser_tools.BrowserPool(
            "mem", headless=True, timeout_s=10, idle_s=600,
            max_sessions=2, snapshot_max=50)
        await pool.session("fe_1")
        assert browser_tools._browser is not None
        await pool.aclose(immediate=True)
        assert browser_tools._browser is None, "最后一个会话关了，Chromium 还留着"
        assert browser_tools._pw is None
        if browser_tools._reaper:
            browser_tools._reaper.cancel()

    asyncio.run(go())


def test_ref_must_look_like_a_ref() -> None:
    """★ ref 会被拼进 CSS 选择器 —— 限死 e\\d+ 同时挡掉了选择器注入。"""
    class _FakePage:
        def locator(self, sel: str) -> str:
            return sel

    s = browser_tools.Session(agent_id="a", context=None, page=_FakePage())
    assert browser_tools.locator(s, "e3") == '[data-knowe-ref="e3"]'
    assert browser_tools.locator(s, "@e12") == '[data-knowe-ref="e12"]'
    for bad in ('e1"] , [href', "button", "", "e", "../e1", "e1; drop"):
        with pytest.raises(ToolError):
            browser_tools.locator(s, bad)


def test_snapshot_formatting_is_readable() -> None:
    snap = {
        "elements": [
            {"role": "heading", "name": "登录", "level": 1},
            {"ref": "e1", "role": "textbox", "name": "用户名", "value": "", "tag": "input"},
            {"ref": "e2", "role": "textbox", "name": "密码", "tag": "input"},
            {"ref": "e3", "role": "checkbox", "name": "记住我", "checked": False, "tag": "input"},
            {"ref": "e4", "role": "button", "name": "提交", "disabled": True, "tag": "button"},
            {"ref": "e5", "role": "link", "name": "忘记密码", "href": "/reset", "tag": "a"},
        ],
        "truncated": False,
    }
    text = browser_tools.format_snapshot(snap)
    assert "# 登录" in text
    assert '@e1 textbox "用户名"' in text
    assert "[未选中]" in text
    assert "[禁用]" in text
    assert "→ /reset" in text


def test_empty_snapshot_explains_itself() -> None:
    text = browser_tools.format_snapshot({"elements": []})
    assert "没有可交互的元素" in text


# ═══════════════════════════ 视觉 ══════════════════════════════════════════

def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_vision_sniffs_by_magic_not_extension(tmp_path: Path) -> None:
    """用户把 jpeg 存成 .png 是家常便饭；按扩展名猜会换来一个 400。"""
    f = tmp_path / "shot.png"
    f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)          # 其实是 JPEG
    part = vision_tools.build_image_part("shot.png", lambda rel: tmp_path / rel)
    assert part["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_vision_accepts_png_and_urls(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(_png())
    part = vision_tools.build_image_part("a.png", lambda rel: tmp_path / rel)
    assert part["image_url"]["url"].startswith("data:image/png;base64,")

    passthrough = vision_tools.build_image_part("https://x.com/a.png", lambda rel: tmp_path / rel)
    assert passthrough["image_url"]["url"] == "https://x.com/a.png"


def test_vision_goes_through_the_sandbox_resolver(tmp_path: Path) -> None:
    """★ 这个模块**不自己拼路径**：沙箱是调用方注入的，绕不过去。"""
    seen: list[str] = []

    def resolver(rel: str) -> Path:
        seen.append(rel)
        raise ToolError("越界了")           # 模拟 resolve_in_sandbox 拒绝

    with pytest.raises(ToolError) as e:
        vision_tools.build_image_part("../../etc/passwd", resolver)
    assert seen == ["../../etc/passwd"]
    assert "越界" in str(e.value)


def test_vision_rejects_file_scheme(tmp_path: Path) -> None:
    with pytest.raises(ToolError):
        vision_tools.build_image_part("file:///etc/passwd", lambda rel: tmp_path / rel)


def test_vision_unknown_format(tmp_path: Path) -> None:
    (tmp_path / "x.png").write_bytes(b"this is not an image at all")
    with pytest.raises(ToolError) as e:
        vision_tools.build_image_part("x.png", lambda rel: tmp_path / rel)
    assert "认不出" in str(e.value)


def test_vision_missing_key_is_a_message(tmp_path: Path) -> None:
    """没配 key 是常态，不是故障——要说清楚配什么。"""
    (tmp_path / "a.png").write_bytes(_png())
    with pytest.raises(ToolError) as e:
        asyncio.run(vision_tools.analyze(
            "a.png", "这是什么", resolve_local=lambda rel: tmp_path / rel,
            api_key="", base_url="https://api.deepseek.com", model="deepseek-chat",
        ))
    assert "API key" in str(e.value)


def test_vision_empty_prompt(tmp_path: Path) -> None:
    with pytest.raises(ToolError):
        asyncio.run(vision_tools.analyze(
            "a.png", "  ", resolve_local=lambda rel: tmp_path / rel,
            api_key="k", base_url="https://x", model="m",
        ))


# ═══════════════════════════ 运行时寿命管理 ════════════════════════════════

def test_runtime_slot_is_created_once() -> None:
    rt = ProjectRuntime("proj-1")
    made: list[int] = []

    class Thing:
        def __init__(self) -> None:
            made.append(1)

        async def aclose(self, *, immediate: bool = False) -> None:
            made.append(2)

    a = rt.slot("x", Thing)
    b = rt.slot("x", Thing)
    assert a is b and made == [1]


def test_runtime_aclose_closes_every_slot() -> None:
    closed: list[str] = []

    class Thing:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self, *, immediate: bool = False) -> None:
            closed.append(f"{self.name}:{immediate}")

    async def go() -> None:
        rt = runtime_for("proj-2")
        rt.slot("processes", lambda: Thing("processes"))
        rt.slot("browser", lambda: Thing("browser"))
        await shutdown_project_runtime("proj-2", immediate=True)

    asyncio.run(go())
    assert sorted(closed) == ["browser:True", "processes:True"]


def test_one_bad_slot_does_not_block_shutdown() -> None:
    """★ 关不掉浏览器，不能连累「切项目目录」这个动作本身。"""
    closed: list[str] = []

    class Bad:
        async def aclose(self, *, immediate: bool = False) -> None:
            raise RuntimeError("浏览器卡死了")

    class Good:
        async def aclose(self, *, immediate: bool = False) -> None:
            closed.append("good")

    async def go() -> None:
        rt = runtime_for("proj-3")
        rt.slot("bad", Bad)
        rt.slot("good", Good)
        await shutdown_project_runtime("proj-3")      # 不许抛

    asyncio.run(go())
    assert closed == ["good"]


def test_shutdown_unknown_project_is_free() -> None:
    """项目没用过这些工具 → 直接返回，绝不为了「万一」去 import Playwright。"""
    asyncio.run(shutdown_project_runtime("never-existed"))


def test_clip_middle_keeps_both_ends() -> None:
    """构建日志的信息全在两头：头上是在装什么，尾巴上是为什么挂了。"""
    text = "START\n" + ("x" * 5000) + "\nTHE ERROR IS HERE"
    out, truncated = clip_middle(text, 200)
    assert truncated is True
    assert out.startswith("START")
    assert out.endswith("THE ERROR IS HERE")
    assert "中间省略" in out


def test_clip_middle_leaves_short_text_alone() -> None:
    out, truncated = clip_middle("short", 200)
    assert out == "short" and truncated is False
