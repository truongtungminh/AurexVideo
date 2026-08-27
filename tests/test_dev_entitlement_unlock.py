from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from web_server import (  # noqa: E402
    DESKTOP_BUILD_PROFILE_ENV,
    DEV_ENTITLEMENT_UNLOCK_ENV,
    development_entitlement_unlock_enabled,
    entitlement_is_active_pro,
    reserve_trial_export,
    trial_branding_required,
)


class DevelopmentEntitlementUnlockTests(unittest.TestCase):
    def test_unlock_requires_explicit_flag_and_debug_build_profile(self) -> None:
        cases = (
            ({}, False),
            ({DEV_ENTITLEMENT_UNLOCK_ENV: "1"}, False),
            ({DESKTOP_BUILD_PROFILE_ENV: "debug"}, False),
            ({DEV_ENTITLEMENT_UNLOCK_ENV: "true", DESKTOP_BUILD_PROFILE_ENV: "debug"}, False),
            ({DEV_ENTITLEMENT_UNLOCK_ENV: "1", DESKTOP_BUILD_PROFILE_ENV: "release"}, False),
            ({DEV_ENTITLEMENT_UNLOCK_ENV: "1", DESKTOP_BUILD_PROFILE_ENV: "debug"}, True),
        )

        for environment, expected in cases:
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                self.assertIs(development_entitlement_unlock_enabled(), expected)

    def test_desktop_marker_alone_no_longer_bypasses_gating(self) -> None:
        with patch.dict(os.environ, {"AUREXVIDEO_DESKTOP": "1"}, clear=True):
            self.assertFalse(entitlement_is_active_pro({}))
            self.assertTrue(trial_branding_required({"entitlement": {}}))
            with self.assertRaises(RuntimeError):
                reserve_trial_export("local-project")

    def test_debug_unlock_bypasses_gates_without_entitlement_data(self) -> None:
        environment = {
            DEV_ENTITLEMENT_UNLOCK_ENV: "1",
            DESKTOP_BUILD_PROFILE_ENV: "debug",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertTrue(entitlement_is_active_pro({}))
            self.assertFalse(trial_branding_required({"entitlement": {}}))
            self.assertEqual(reserve_trial_export("local-project"), (None, None))

    def test_release_profile_preserves_normal_entitlement_behavior(self) -> None:
        environment = {
            DEV_ENTITLEMENT_UNLOCK_ENV: "1",
            DESKTOP_BUILD_PROFILE_ENV: "release",
        }
        active_pro = {"product_id": "aurexvideo-pro", "status": "active"}
        with patch.dict(os.environ, environment, clear=True):
            self.assertFalse(entitlement_is_active_pro({}))
            self.assertTrue(entitlement_is_active_pro(active_pro))
            self.assertTrue(trial_branding_required({"entitlement": {}}))


class LauncherReleaseGuardTests(unittest.TestCase):
    def test_launcher_compile_time_guards_debug_unlock(self) -> None:
        source = (ENGINE_ROOT / "tauri-src" / "src" / "main.rs").read_text(encoding="utf-8")

        debug_start = source.index(
            "#[cfg(debug_assertions)]\nfn configure_development_entitlement_unlock"
        )
        release_start = source.index(
            "#[cfg(not(debug_assertions))]\nfn configure_development_entitlement_unlock"
        )
        spawn_start = source.index("fn spawn_server", release_start)
        debug_guard = source[debug_start:release_start]
        release_guard = source[release_start:spawn_start]

        self.assertIn('command.env(DESKTOP_BUILD_PROFILE_ENV, "debug");', debug_guard)
        self.assertIn('command.env(DEV_ENTITLEMENT_UNLOCK_ENV, "1");', debug_guard)
        self.assertNotIn('command.env(DEV_ENTITLEMENT_UNLOCK_ENV, "1");', release_guard)
        self.assertIn('command.env(DESKTOP_BUILD_PROFILE_ENV, "release");', release_guard)
        self.assertIn("command.env_remove(DEV_ENTITLEMENT_UNLOCK_ENV);", release_guard)
        self.assertEqual(source.count('command.env(DEV_ENTITLEMENT_UNLOCK_ENV, "1");'), 1)
        self.assertNotIn('.env("AUREXVIDEO_DESKTOP", "1")', source)
        self.assertIn('.env("AUREXVIDEO_EMBEDDED_DESKTOP", "1")', source)
        self.assertIn("configure_development_entitlement_unlock(&mut command);", source[spawn_start:])

    def test_launcher_rejects_a_backend_with_the_wrong_profile(self) -> None:
        source = (ENGINE_ROOT / "tauri-src" / "src" / "main.rs").read_text(encoding="utf-8")
        watchdog_start = source.index("fn run_server_watchdog")
        watchdog = source[watchdog_start:]

        self.assertIn("fn backend_matches_runtime_profile()", source)
        self.assertIn("/api/health", source)
        self.assertIn("if backend_matches_runtime_profile()", watchdog)
        self.assertIn("existing backend profile mismatch; replacing it", watchdog)
        self.assertIn("try_wait()", watchdog)
        self.assertIn("stop_requested.store(true, Ordering::Release)", source)

    def test_health_contract_exposes_build_profile_and_dev_unlock_state(self) -> None:
        source = (ENGINE_ROOT / "web_server.py").read_text(encoding="utf-8")

        self.assertIn('"desktop_build_profile": desktop_build_profile(),', source)
        self.assertIn(
            '"development_entitlement_unlock": development_entitlement_unlock_enabled(),',
            source,
        )

if __name__ == "__main__":
    unittest.main()
