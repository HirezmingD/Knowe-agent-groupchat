"""v2.2 compatibility overlay.

Worker work orders use the shared :class:`backend.runtime.TaskEnvelope` directly.  This
module intentionally contains no separate permission or tool-surface contract.
"""

from backend.runtime import TaskEnvelope

__all__ = ["TaskEnvelope"]
