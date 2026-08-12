"""
ring.py — 每项目一个环形缓冲（容量 1000）。

B-5 的根治点在 `replay_since()`：
  旧版在 since_seq == 0 时走了一条特殊分支，**淘汰之后仍然把残缺的历史当完整历史返回**，
  于是前端以为自己拿到了全量，实际上开头缺了一大截，界面永远少一块。

新规则只有一条，不分支：
  **凡是「你要的那一段已经被淘汰了」，就不要给残缺的——直接告诉前端 resync_required。**
  判据：since_seq < (最老一条的 seq - 1)  ⇒ 中间有洞 ⇒ 拒绝增量回放。
  since_seq == 0 且发生过淘汰 ⇒ 一定有洞 ⇒ 同样拒绝（这就是旧版栽的那一跤）。
"""

from __future__ import annotations

from collections import deque
from typing import Any


class RingBuffer:
    """单个项目的事件环。只存带 seq 的事件。"""

    __slots__ = ("_buf", "_capacity", "_evicted")

    def __init__(self, capacity: int = 1000) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._capacity = capacity
        self._evicted = False  # 是否发生过淘汰（丢过最老的事件）

    # ── 写 ──
    def append(self, event: dict[str, Any]) -> None:
        if "seq" not in event:
            raise ValueError(f"无 seq 事件不得进 ring: {event.get('type')}")
        if len(self._buf) == self._capacity:
            self._evicted = True
        self._buf.append(event)

    # ── 读 ──
    @property
    def evicted(self) -> bool:
        return self._evicted

    @property
    def oldest_seq(self) -> int | None:
        return self._buf[0]["seq"] if self._buf else None

    @property
    def newest_seq(self) -> int | None:
        return self._buf[-1]["seq"] if self._buf else None

    def __len__(self) -> int:
        return len(self._buf)

    def events(self) -> list[dict[str, Any]]:
        return list(self._buf)

    def structural(self, types: frozenset[str]) -> list[dict[str, Any]]:
        """快照用：只取结构事件（瞬时事件如 stream_delta 不进时间线）。"""
        return [e for e in self._buf if e.get("type") in types]

    # ── 增量回放（B-5 的家） ──
    def replay_since(self, since_seq: int) -> tuple[list[dict[str, Any]], bool]:
        """
        返回 (events, gap)：
          gap=True  → 请求的区间有一部分已被淘汰，**不返回任何残缺历史**，
                      调用方必须发 resync_required 让前端走快照重建。
          gap=False → events 是 since_seq 之后的完整增量（可能为空）。
        """
        if not self._buf:
            return [], False

        oldest = self._buf[0]["seq"]

        # since_seq 之后的第一条应该是 since_seq + 1；
        # 如果连它都已经被淘汰（oldest > since_seq + 1），中间就有洞。
        if oldest > since_seq + 1:
            return [], True

        return [e for e in self._buf if e["seq"] > since_seq], False
