# [v1.0.13][R1] Model fingerprint, explicit apply and readiness barrier tests.
from __future__ import annotations

import asyncio
import unittest

from backend import runtime_settings


def binding(**changes: str) -> dict[str, str]:
    value = {
        "provider": "openai-api",
        "model": "gpt-test",
        "api_key": "sk-secret-value",
        "base_url": "https://example.invalid/v1/",
        "transport": "openai_chat",
    }
    value.update(changes)
    return value


class RuntimeSettingsReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        runtime_settings._state.clear()  # noqa: SLF001 - isolated module-level authority
        runtime_settings._state.update(runtime_settings._default_state())  # noqa: SLF001
        runtime_settings._loaded = True  # noqa: SLF001
        runtime_settings._model_waiters.clear()  # noqa: SLF001

    def test_fingerprint_covers_every_binding_field_and_never_exposes_key(self) -> None:
        base = binding()
        fingerprint = runtime_settings.binding_fingerprint(base)
        self.assertIsNotNone(fingerprint)
        self.assertTrue(str(fingerprint).startswith("sha256:"))
        self.assertNotIn(base["api_key"], str(fingerprint))
        for field, value in {
            "provider": "deepseek",
            "model": "another-model",
            "api_key": "sk-another",
            "base_url": "https://other.invalid/api",
            "transport": "codex_responses",
        }.items():
            changed = dict(base)
            changed[field] = value
            self.assertNotEqual(fingerprint, runtime_settings.binding_fingerprint(changed), field)

    def test_api_snapshot_never_exposes_fingerprint_key_material(self) -> None:
        main = binding()
        runtime_settings.apply({
            "main_model": main,
            "expected_fingerprint": runtime_settings.binding_fingerprint(main),
        })
        value = runtime_settings.api_snapshot(welcome_state="shown")
        self.assertNotIn("fingerprint_salt", value)
        self.assertNotIn("active_model_fingerprint", value)
        self.assertTrue(str(value["applied_fingerprint"]).startswith("sha256:"))

    def test_ordinary_save_activates_immediately(self) -> None:
        # v1.0.19.5: 指纹闸门废除——保存完整绑定即激活，不再要求测试→应用仪式。
        main = binding()
        first = runtime_settings.apply({"main_model": main, "agent_models": {}})
        self.assertTrue(runtime_settings.model_ready_for("project-1", "coordinator"))
        self.assertEqual(
            runtime_settings.model_apply_ack()["applied_fingerprint"],
            runtime_settings.binding_fingerprint(main),
        )

        changed = binding(model="gpt-test-2")
        runtime_settings.apply({
            "main_model": changed,
            "agent_models": {},
            "expected_revision": first["settings_revision"],
        })
        self.assertTrue(runtime_settings.model_ready_for("project-1", "coordinator"))
        self.assertEqual(
            runtime_settings.model_apply_ack()["applied_fingerprint"],
            runtime_settings.binding_fingerprint(changed),
        )

    def test_legacy_fingerprint_field_is_ignored_and_save_still_applies(self) -> None:
        # v1.0.19.5: 旧版 expected_fingerprint 字段不再拦截保存；配置照常生效。
        main = binding()
        before = runtime_settings.apply({"main_model": main})
        applied = runtime_settings.apply({
            "main_model": binding(model="not-tested"),
            "expected_revision": before["settings_revision"],
            "expected_fingerprint": runtime_settings.binding_fingerprint(main),
        })
        self.assertTrue(runtime_settings.model_ready_for("project-1", "coordinator"))
        self.assertEqual(applied["main_model"]["model"], "not-tested")

    def test_zinnia_compatibility_is_separate_from_generic_model_readiness(self) -> None:
        anthropic = binding(transport="anthropic_messages")
        fingerprint = runtime_settings.binding_fingerprint(anthropic)
        runtime_settings.apply({
            "main_model": anthropic,
            "expected_fingerprint": fingerprint,
        })
        self.assertTrue(runtime_settings.model_ready_for("project-1", "coordinator"))
        self.assertFalse(runtime_settings.zinnia_binding_status()[0])
        self.assertFalse(runtime_settings.model_apply_ack()["zinnia_compatible"])


class RuntimeSettingsWaiterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        runtime_settings._state.clear()  # noqa: SLF001
        runtime_settings._state.update(runtime_settings._default_state())  # noqa: SLF001
        runtime_settings._loaded = True  # noqa: SLF001
        runtime_settings._model_waiters.clear()  # noqa: SLF001

    async def test_waiter_returns_once_binding_is_complete(self) -> None:
        # v1.0.19.5: 无需指纹事务——完整绑定保存后 wait 立即放行。
        waiter = asyncio.create_task(
            runtime_settings.wait_for_model_ready("project-1", "coordinator", timeout=1.0)
        )
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        runtime_settings.apply({"main_model": binding()})
        self.assertTrue(await waiter)


if __name__ == "__main__":
    unittest.main()
