# knowe v0.6b — 测试环境隔离 + 缺 key 的人话
"""
test_v06b.py — 这一批修的两件事，各自钉一颗钉子。

  ① 缺 key 不能给用户一个空白气泡（**这是个真 bug，不只是测试问题**）
  ② 测试不能被 .env 摆布
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.config import CONFIG
from backend.engine import ProjectEngine, harness_mode
from backend.hub import Hub
from knowe_core.provider_client import ProviderClient

from .conftest import TEST_BASELINE


# ═══════════════════════════════════════════════════════════════
# ① 缺 key → 说人话（真 bug）
# ═══════════════════════════════════════════════════════════════

async def test_harness_without_api_key_says_so_instead_of_a_blank_bubble():
    """
    ★ 真 LLM 档 + 没配 key：

      修之前：ProviderClient 带一个空的 `Bearer ` 去请求 → httpx 在协议层
              LocalProtocolError → AgentLoop 把它塞进 result.error → 引擎照常发一条
              **content 为空的 message** → **用户看到一个白气泡，什么也没有。**

      修之后：一条 error 事件，写清楚是没配 key、怎么办。
    """
    object.__setattr__(CONFIG, "agent", "deepseek")
    object.__setattr__(CONFIG, "deepseek_api_key", "")

    hub = Hub()
    eng = ProjectEngine(hub, "p1")
    eng.start()
    await eng.submit("你好")

    for _ in range(200):
        await asyncio.sleep(0.01)
        if not eng.busy:
            break
    await eng.stop()

    events = hub.projects["p1"].ring.events()
    errors = [e for e in events if e["type"] == "error"]

    assert errors, "没配 key 必须说出来"
    # Runtime settings are now authoritative; the message must direct users to
    # the provider/model settings rather than treating a legacy DeepSeek env var
    # as the only valid configuration path.
    assert "设置 → 模型与提供方" in errors[0]["message"]
    assert "API Key" in errors[0]["message"]
    assert "KNOWE_AGENT=fake" in errors[0]["message"], "还得告诉他零 token 联调怎么办"

    # ★ 绝不能发一个空白气泡
    blanks = [e for e in events if e["type"] == "message" and not e["content"]]
    assert blanks == [], "空 content 的 message = 屏幕上一个白气泡，什么也没有"


def test_provider_client_omits_authorization_when_key_is_empty():
    """
    `Bearer `（后面什么也没有）不是合法的 header 值 —— httpx 会在协议层崩掉，
    报错完全看不出是「没配 key」。干脆不发这个头，让服务端回一个能读懂的 401。
    """
    c = ProviderClient(base_url="https://api.deepseek.com", api_key="")
    assert "Authorization" not in c._headers

    c2 = ProviderClient(base_url="https://api.deepseek.com", api_key="sk-x")
    assert c2._headers["Authorization"] == "Bearer sk-x"


async def test_empty_key_no_longer_raises_local_protocol_error():
    """修之前这里会抛 httpx.LocalProtocolError: Illegal header value b'Bearer '。"""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(401, content=b'{"error":"no key"}')

    client = ProviderClient(
        base_url="https://api.deepseek.com", api_key="",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    from knowe_core.errors import ProviderAuthError
    with pytest.raises(ProviderAuthError):          # 401 —— 一句能读懂的话
        async for _ in client.chat_stream([{"role": "user", "content": "hi"}]):
            pass

    assert "authorization" not in {k.lower() for k in calls[0].headers}


# ═══════════════════════════════════════════════════════════════
# ② 测试不被 .env 摆布
# ═══════════════════════════════════════════════════════════════

def test_config_is_pinned_to_the_test_baseline():
    """
    ★ 不管机器上的 .env / 环境变量写了什么，每条测试拿到的 CONFIG 都是这个基线。

      这一条是在给 conftest.py 那个 autouse fixture 站岗：
      它一旦被删掉或改坏，这里立刻红——而不是等到某人的 .env 一改，
      八条测试莫名其妙地挂掉。
    """
    assert CONFIG.agent == "fake"
    assert CONFIG.deepseek_api_key == ""
    assert CONFIG.kickoff is False
    assert CONFIG.data_dir == ""
    assert harness_mode() is False           # fake 档 → 走单 agent 老路


def test_baseline_covers_every_env_driven_field():
    """
    基线必须覆盖**所有会被环境变量喂进来的字段**——漏一个，那个字段就还是
    环境的属性，下一次 .env 一改又会炸。
    """
    import dataclasses

    env_driven = {
        f.name for f in dataclasses.fields(CONFIG)
        if f.default_factory is not dataclasses.MISSING   # type: ignore[misc]
    }
    missing = env_driven - set(TEST_BASELINE) - {
        # 这几个不影响任何测试的行为（监听地址 / 日志 / 模型名 / 前端调试开关）
        "ws_host", "ws_port", "health_host", "health_port", "log_level",
        "emit_turn_end", "deepseek_model", "deepseek_base_url",
    }
    assert missing == set(), f"这些字段还是被环境喂的，基线里没按住：{missing}"


async def test_a_test_can_still_opt_into_harness_explicitly():
    """
    基线是 fake，但要 Harness 的测试**自己显式改**就行。
    显式覆盖是好的；隐式依赖环境不是。
    """
    object.__setattr__(CONFIG, "agent", "deepseek")
    assert harness_mode() is True

    eng = ProjectEngine(Hub(), "p1")
    assert eng.agent is None, "真 LLM 档应该走 Harness"


def test_baseline_is_restored_after_each_test():
    """上一条测试把 agent 改成了 deepseek —— fixture 必须把它还原回来。"""
    assert CONFIG.agent == "fake", "CONFIG 是单例，改了不还原，下一条测试就会中毒"
