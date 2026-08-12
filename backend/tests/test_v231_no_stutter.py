# knowe v0.23.1 — 测试：结巴不许回来
"""
v0.23 我把 stream_delta 改成实时广播，结果所有 Agent 说话都变成了结巴：

    模型想说：好的，我先查一下项目知识里有没有相关的约束
    用户看到：好的，我我先查一下项目知识知识里知识里有没有有相相关的约约束

## 为什么会重复（这是这个文件存在的理由）

不是留尾算错了，不是前端拼错了，也不是 stream_reset 打转。是**竞态**。

`engine._fire()` 对**每一帧**都 `asyncio.create_task(self.emit(payload))` ——
几十个 task 并发跑。而 v0.23 的 `_stream_advance` 干的是这么一件事：

    sent = self._stream_sent.get(agent_id, "")     # ① 读
    ...
    await self.hub.emit(..., body[len(sent):])     # ② 让出事件循环 ←★
    self._stream_sent[agent_id] = body             # ③ 写

**①②③ 之间有 await。** 于是每个 task 都在别人写回之前读到了 `sent=""`，
每个都从头发一遍自己那份 body —— 前缀一次比一次长，屏幕上就是「好、好的、好的，我…」

v0.22 的老代码为什么没事：

    self._stream_buffers.setdefault(agent_id, []).append(content)
    return dict(payload)          # ← 一个 await 都没有 = 对事件循环原子

**我把一段原子代码改成了跨 await 的读-改-写，而它跑在 N 个并发 task 里。**

## 所以这个文件守两条

  ① emit 的 stream_delta 分支里**一个 await 都不许有**（结构守）
  ② 照着 _fire 的样子并发灌一整句话，屏幕上必须一字不差（行为守）

第 ② 条就是当初该有、而我没写的那条测试：v0.23 我测了「顺序调用 emit」，
它永远是对的 —— **测试没有复现真实的调用方式，所以放过了这个 bug。**
"""
from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.engine as E                                          # noqa: E402
from backend.engine import ProjectEngine                            # noqa: E402


class FakeHub:
    """真实的 hub.emit 里有 await（websocket 发送）—— 没有它就复现不出竞态。"""

    def __init__(self) -> None:
        self.out: list[dict] = []

    async def emit(self, pid, payload):
        await asyncio.sleep(0)          # ★ 这一下就是竞态的窗口
        self.out.append(dict(payload))
        return dict(payload)


def bare() -> ProjectEngine:
    e = ProjectEngine.__new__(ProjectEngine)
    e.project_id = "p"
    e._stream_buffers = {}
    e.hub = FakeHub()
    e._public_names = lambda: []                       # type: ignore[assignment]
    e._sanitize_outbound = lambda p: dict(p)           # type: ignore[assignment]
    e.history = []
    e._trim = lambda: None                             # type: ignore[assignment]
    e._record_activity_from_event = lambda p: None     # type: ignore[assignment]
    e._fired = set()
    return e


def screen(e) -> str:
    """用户屏幕上那条气泡里最后长什么样。"""
    return "".join(x["content"] for x in e.hub.out if x["type"] == "stream_delta")


# ═══════════ ① 结构守：这条路上不许有 await ═══════════

def test_stream_delta_branch_has_no_await() -> None:
    """
    ★ 这是**根因守**，比行为守更硬。

      只要 stream_delta 的处理路径上有一个 await，`_fire` 的 N 个并发 task
      就能在中间插进来 —— 不管当时的逻辑写得多对。
      改这段代码的人，会被这条测试当场拦住。
    """
    src = Path(E.__file__).read_text("utf-8")
    tree = ast.parse(src)
    emit_fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "emit"
    )
    # 找 `if etype == "stream_delta" ...` 那个分支
    #   （不带引号地匹配：ast.unparse 会把双引号规范化成单引号，
    #     照着源码里的 '"stream_delta"' 去找是找不到的——第一版就栽在这儿。）
    branch = None
    for node in emit_fn.body:
        if isinstance(node, ast.If) and "stream_delta" in ast.unparse(node.test):
            branch = node
            break
    assert branch is not None, "emit 里找不到 stream_delta 分支了 —— 这段被改动过，请重新审视竞态"
    awaits = [n for n in ast.walk(branch) if isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))]
    assert not awaits, (
        "stream_delta 分支里出现了 await —— v0.23 的结巴就是这么来的。\n"
        "_fire 对每一帧都 create_task，这条路必须对事件循环保持原子。"
    )


def test_v023_streaming_machinery_is_gone() -> None:
    """v0.23 那套留尾/自纠的零件全部撤干净，别留半套在那儿误导人。"""
    src = Path(E.__file__).read_text("utf-8")
    for ghost in ("_STREAM_HOLDBACK", "_stream_sent", "_stream_push",
                  "_stream_advance", "_stream_flush", "_stream_visible"):
        assert ghost not in src, f"{ghost} 还在 —— v0.23 的流式广播没撤干净"


# ═══════════ ② 行为守：照 _fire 的真实样子并发灌 ═══════════

SENTENCE = ("好的，我先查一下项目知识里有没有相关的约束，然后再决定要不要加人。"
            "这件事看起来需要一个会爬虫的人来做，我先确认一下现有团队的情况。")


def _fire_all(e, text: str, chunk: int = 1):
    """★ 照抄 engine._fire：每一帧一个 create_task，并发。"""
    async def go():
        for i in range(0, len(text), chunk):
            e._fire({"type": "stream_delta", "agent_id": "c", "content": text[i:i + chunk]})
        while e._fired:
            await asyncio.gather(*list(e._fired))
        await e.emit({"type": "message", "agent_id": "c", "content": text})
    asyncio.run(go())


@pytest.mark.parametrize("chunk", [1, 2, 5, 13])
def test_concurrent_frames_do_not_stutter(chunk: int) -> None:
    """
    ★ **这就是 v0.23 当初该有、而我没写的那条测试。**

      我那时测的是「顺序 await emit」—— 那样永远是对的。
      测试没有复现真实的调用方式（_fire 的并发 task），所以放过了这个 bug。
      现在按真实方式灌：一个字一个字、两个两个……屏幕上都必须一字不差。
    """
    e = bare()
    _fire_all(e, SENTENCE, chunk)
    got = screen(e)
    assert got == SENTENCE, (
        f"结巴回来了（chunk={chunk}）：\n"
        f"  想说 {len(SENTENCE)} 字：{SENTENCE[:40]}…\n"
        f"  看到 {len(got)} 字：{got[:60]}…"
    )


def test_the_exact_reported_sentence() -> None:
    """用户报的那一句，原样跑一遍。"""
    e = bare()
    真话 = "好的，我先查一下项目知识里有没有相关的约束"
    _fire_all(e, 真话, 1)
    assert screen(e) == 真话
    assert "我我" not in screen(e)
    assert "知识知识" not in screen(e)


def test_newline_is_not_printed_as_text() -> None:
    """
    问题二里冒出过字面的「（换行）」。换行符必须原样是 \\n，
    不能在任何环节被转义成可见文字。
    """
    e = bare()
    text = "好，这个活需要有人去搜图下载。\n\n队里现在只有宋精（技术写作），他不适合做爬虫。"
    _fire_all(e, text, 3)
    assert "（换行）" not in screen(e)
    assert screen(e) == text


# ═══════════ ③ 问题四：推理不许混进正文 ═══════════

def test_reasoning_never_reaches_the_final_bubble() -> None:
    """
    ★ 问题四（最严重的一条）：模型的中间推理被带进了最终 message。

      根因是 v0.23 把推理实时灌进了 `item.text` —— 而 `item.text` 正是最终答案
      要落的那个字段。两者共用一个格子，隔离自然就破了。

      撤掉实时广播之后：整轮的推理只在 `_stream_buffers` 里躺着，
      只有在「它和 final 一字不差」时才会补一条整段 delta（v0.22 的老行为）。
      长任务里推理 ≠ final，所以**一个字都不会流出去**。
    """
    e = bare()
    推理 = "好的，我先读一下之前的大纲文件，确认哪些章节需要重写。\n"
    正文 = "写完了，三章都改好了。"
    async def go():
        for ch in 推理:
            e._fire({"type": "stream_delta", "agent_id": "c", "content": ch})
        while e._fired:
            await asyncio.gather(*list(e._fired))
        await e.emit({"type": "message", "agent_id": "c", "content": 正文})
    asyncio.run(go())

    assert screen(e) == "", "推理流出去了 —— 它会被前端累进 item.text，然后混进正文"
    msgs = [x for x in e.hub.out if x["type"] == "message"]
    assert len(msgs) == 1 and msgs[0]["content"] == 正文


def test_matching_text_still_replays_once(monkeypatch) -> None:
    """
    v0.22 的老行为要原样保住：整段流和 final 一字不差时，补一条整段 delta
    让前端气泡正常落定。撤销不能把这个也撤掉。
    """
    monkeypatch.setattr(E, "sanitize_text", lambda t, n=None: t)
    e = bare()
    text = "这句话流出来的和最终一模一样。"
    async def go():
        for ch in text:
            e._fire({"type": "stream_delta", "agent_id": "c", "content": ch})
        while e._fired:
            await asyncio.gather(*list(e._fired))
        await e.emit({"type": "message", "agent_id": "c", "content": text})
    asyncio.run(go())
    assert screen(e) == text
    monkeypatch.undo()


# ═══════════ ④ v0.23 里**保留**的那部分（派活话术）═══════════

def test_dispatch_tool_result_still_free_of_jargon() -> None:
    """
    现行协议由可见派单卡承担“谁去做、做什么”，工具回执不得再诱导总管
    复述卡片或承诺后台报告/审阅流程。
    """
    from backend import tools_knowe
    src = Path(tools_knowe.__file__).read_text("utf-8")
    assert 'message=f"任务已派给' not in src
    block = src.rsplit('engine.record_committed_action("propose_next")', 1)[1][:12000]
    message_block = block.split("message=(", 1)[1].split(").replace", 1)[0]
    assert "卡把话说完了" in message_block
    assert "NOTHING_TO_ADD" in message_block
    assert "谁去做、做什么" not in message_block


def test_coordinator_soul_uses_structured_state_not_a_word_blacklist() -> None:
    soul = Path(E.__file__).parent.joinpath("souls", "coordinator.txt").read_text("utf-8")
    assert "roster、completion" in soul
    assert "普通自然语言不是系统状态协议" in soul
    assert "称呼、代词、时态或措辞" in soul
    assert "正文不要机械复述卡片" in soul
    assert "然后一句「谁去干什么」" not in soul
