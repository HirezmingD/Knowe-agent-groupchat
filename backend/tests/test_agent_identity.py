# [v1.0.13][R2] Shared identity contract tests.
from __future__ import annotations

import unittest

from backend.agent_identity import identity_for


class AgentIdentityTest(unittest.TestCase):
    def test_coordinator_distinguishes_platform_and_self(self) -> None:
        block = identity_for("coordinator").system_block()
        # [v1.0.23.3] 断言对齐当前真实文案（v1.0.22.1 替换工程：总管→项目经理；
        #   早期「Knowe 是软件平台/不得说我是 Knowe」两句已随身份契约重构移除）。
        self.assertIn("你的显示名：项目经理", block)
        self.assertIn("你的角色：本项目的项目经理", block)
        self.assertIn("我是这个项目的项目经理", block)

    def test_worker_binds_display_name_role_and_id(self) -> None:
        identity = identity_for("ux_1", display_name="Kit", role_name="交互设计")
        self.assertEqual(identity.display_name, "Kit")
        self.assertEqual(identity.agent_id, "ux_1")
        self.assertIn("我是Kit，负责交互设计", identity.system_block())


if __name__ == "__main__":
    unittest.main()
