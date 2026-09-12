from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

import m3_backend as m3  # noqa: E402
import web_server  # noqa: E402
import social_upload.config as social_config  # noqa: E402


class BrandLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-brand-library-")
        self.root = Path(self.temp_dir.name)
        self.projects = self.root / "project"
        self.config = self.root / "config"
        self.social = self.config / "social-upload.json"
        self.library = self.config / "brand-library.json"
        self.patches = [
            patch.object(m3, "PROJECTS_ROOT", self.projects),
            patch.object(m3, "OUTPUT_ROOT", self.root / "output"),
            patch.object(m3, "CONFIG_ROOT", self.config),
            patch.object(m3, "PROJECT_DEFAULTS_PATH", self.config / "project-defaults.json"),
            patch.object(m3, "SOCIAL_CONFIG_PATH", self.social),
            patch.object(m3, "BRAND_LIBRARY_PATH", self.library),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _write_topic(self, project: str, brand: str) -> None:
        topic_path = self.projects / project / "topic.json"
        topic_path.parent.mkdir(parents=True, exist_ok=True)
        topic_path.write_text(json.dumps({"id": project, "brand": brand}), encoding="utf-8")

    def _write_social(self, payload: dict) -> None:
        self.social.parent.mkdir(parents=True, exist_ok=True)
        self.social.write_text(json.dumps(payload), encoding="utf-8")

    def test_normalizes_and_persists_migrated_project_and_social_brands(self) -> None:
        self._write_topic("legacy-project", "Brand Cũ")
        self._write_social({
            "brand_routes": {"social_brand": {"youtube": {"channel_id": "channel-1"}}},
            "zernio": {"connections": {"connection-1": {
                "brand": "route.brand", "api_key": "must-not-be-copied",
            }}},
        })

        brands = m3.list_brands()

        self.assertEqual(m3.normalize_brand_id(" Cà phê Việt "), "ca-phe-viet")
        self.assertEqual({item["id"] for item in brands}, {"brand-cu", "social_brand", "route.brand"})
        self.assertEqual(
            next(item for item in brands if item["id"] == "brand-cu")["displayName"],
            "Brand Cũ",
        )
        persisted = self.library.read_text(encoding="utf-8")
        self.assertIn('"version": 1', persisted)
        self.assertNotIn("must-not-be-copied", persisted)

    def test_crud_and_usage_guard_cover_projects_and_social_connections(self) -> None:
        created = m3.create_brand({"displayName": "  Cà phê Việt  "})
        self.assertEqual(created, {"id": "ca-phe-viet", "displayName": "Cà phê Việt"})
        with self.assertRaises(FileExistsError):
            m3.create_brand({"id": "ca-phe-viet", "displayName": "Duplicate"})
        self.assertEqual(m3.delete_brand("ca-phe-viet"), {"id": "ca-phe-viet"})

        m3.create_brand({"name": "Brand đang dùng"})
        self._write_topic("used-project", "brand-dang-dung")
        with self.assertRaises(m3.BrandInUseError) as guarded:
            m3.delete_brand("brand-dang-dung")
        self.assertEqual(guarded.exception.usage["projects"], ["used-project"])

        m3.create_brand({"name": "Brand social"})
        self._write_social({"brand_routes": {"brand-social": {"facebook": {"page_id": "page-1"}}}})
        with self.assertRaises(m3.BrandInUseError) as guarded:
            m3.delete_brand("brand-social")
        self.assertEqual(guarded.exception.usage["social"], ["route:facebook"])

        m3.create_brand({"name": "Brand connection"})
        self._write_social({"zernio": {"connections": {"account-1": {
            "brand": "brand-connection", "api_key": "secret-not-returned",
        }}}})
        with self.assertRaises(m3.BrandInUseError) as guarded:
            m3.delete_brand("brand-connection")
        self.assertEqual(guarded.exception.usage["social"], ["connection:zernio/account-1"])

    def test_quiz_and_custom_projects_are_brand_only_and_survive_editor_save(self) -> None:
        m3.create_brand({"name": "Quiz Team"})
        with patch.object(m3, "character_manifest", side_effect=FileNotFoundError):
            for project_type in ("quiz", "custom"):
                project_id = f"{project_type}-brand-project"
                summary = m3.create_project({
                    "id": project_id,
                    "projectType": project_type,
                    "brand": "Quiz Team",
                })
                topic = m3.read_topic(project_id)
                saved = m3.save_topic(project_id, topic)
                reloaded = m3.read_topic(project_id)
                self.assertEqual(summary["brand"], "quiz-team")
                self.assertEqual(topic["brand"], "quiz-team")
                self.assertEqual(saved["brand"], "quiz-team")
                self.assertEqual(reloaded["brand"], "quiz-team")
                self.assertEqual(reloaded["characterId"], "human-presenter")
                self.assertEqual(reloaded["poseAssets"], m3.DEFAULT_POSE_ASSETS)

    def test_cascade_delete_removes_owned_projects_and_routes_but_preserves_connections(self) -> None:
        m3.create_brand({"id": "delete-me", "name": "Delete Me"})
        m3.create_brand({"id": "keep-me", "name": "Keep Me"})
        self._write_topic("delete-project", "delete-me")
        self._write_topic("keep-project", "keep-me")
        self._write_social({
            "brand_routes": {
                "delete-me": {"facebook": {"page_id": "page-delete"}},
                "keep-me": {"youtube": {"channel_id": "channel-keep"}},
            },
            "zernio": {
                "connections": {
                    "delete-connection": {
                        "brand": "delete-me", "api_key": "must-stay",
                    },
                    "keep-connection": {
                        "brand": "keep-me", "api_key": "keep-key",
                    },
                },
            },
        })

        with (
            patch.object(web_server, "PROJECT_ROOT", self.projects),
            patch.object(web_server, "REPO_ROOT", self.root),
            patch.object(web_server, "SOURCE_ROOT_IS_PROJECT", False),
            patch.object(web_server, "JOBS", {}),
            patch.object(social_config, "SOCIAL_UPLOAD_CONFIG", self.social),
        ):
            result = web_server.delete_brand_cascade("delete-me")

        self.assertEqual(result["deleted"], "delete-me")
        self.assertEqual(result["projects"], ["delete-project"])
        self.assertEqual(result["social"], ["route:facebook"])
        self.assertEqual(result["connectionsDetached"], ["connection:zernio/delete-connection"])
        self.assertFalse((self.projects / "delete-project").exists())
        self.assertTrue((self.projects / "keep-project").is_dir())
        self.assertNotIn("delete-me", {item["id"] for item in m3.list_brands()})

        social = json.loads(self.social.read_text(encoding="utf-8"))
        self.assertNotIn("delete-me", social["brand_routes"])
        self.assertNotIn("brand", social["zernio"]["connections"]["delete-connection"])
        self.assertEqual(social["zernio"]["connections"]["delete-connection"]["api_key"], "must-stay")
        self.assertEqual(social["zernio"]["connections"]["keep-connection"]["brand"], "keep-me")

    def test_cascade_delete_preflights_running_projects_without_mutating_data(self) -> None:
        m3.create_brand({"id": "busy-brand", "name": "Busy Brand"})
        self._write_topic("busy-project", "busy-brand")
        self._write_social({"brand_routes": {"busy-brand": {"facebook": {"page_id": "page"}}}})

        with (
            patch.object(web_server, "PROJECT_ROOT", self.projects),
            patch.object(web_server, "REPO_ROOT", self.root),
            patch.object(web_server, "SOURCE_ROOT_IS_PROJECT", False),
            patch.object(web_server, "JOBS", {"job-1": {"project": "busy-project", "status": "running"}}),
            patch.object(social_config, "SOCIAL_UPLOAD_CONFIG", self.social),
            self.assertRaisesRegex(RuntimeError, "đang render"),
        ):
            web_server.delete_brand_cascade("busy-brand")

        self.assertTrue((self.projects / "busy-project").is_dir())
        self.assertIn("busy-brand", {item["id"] for item in m3.list_brands()})
        social = json.loads(self.social.read_text(encoding="utf-8"))
        self.assertIn("busy-brand", social["brand_routes"])

    def test_new_project_ui_uses_explicit_brand_cascade_delete(self) -> None:
        source = (ENGINE_ROOT / "webui" / "new-project.html").read_text(encoding="utf-8")
        self.assertIn("Xoá brand và dữ liệu liên quan", source)
        self.assertIn("?cascade=true", source)


class BrandLibraryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-brand-library-api-")
        root = Path(self.temp_dir.name)
        config = root / "config"
        self.projects = root / "project"
        self.social = config / "social-upload.json"
        self.library = config / "brand-library.json"
        self.patches = [
            patch.object(m3, "PROJECTS_ROOT", self.projects),
            patch.object(m3, "OUTPUT_ROOT", root / "output"),
            patch.object(m3, "CONFIG_ROOT", config),
            patch.object(m3, "PROJECT_DEFAULTS_PATH", config / "project-defaults.json"),
            patch.object(m3, "SOCIAL_CONFIG_PATH", self.social),
            patch.object(m3, "BRAND_LIBRARY_PATH", self.library),
            patch.object(social_config, "SOCIAL_UPLOAD_CONFIG", self.social),
            patch.object(web_server, "PROJECT_ROOT", self.projects),
            patch.object(web_server, "REPO_ROOT", root),
            patch.object(web_server, "SOURCE_ROOT_IS_PROJECT", False),
            patch.object(web_server, "JOBS", {}),
        ]
        for item in self.patches:
            item.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.WebHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _request(self, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8"))
            finally:
                error.close()

    def test_endpoint_crud_and_clear_client_errors(self) -> None:
        status, payload = self._request("/api/brands")
        self.assertEqual((status, payload), (200, {"brands": []}))

        status, payload = self._request("/api/brands", "POST", {"name": "API Brand"})
        self.assertEqual(status, 201)
        self.assertEqual(payload["brand"], {"id": "api-brand", "displayName": "API Brand"})

        with patch.object(m3, "character_manifest", side_effect=FileNotFoundError):
            for project_type in ("quiz", "custom"):
                project_id = f"api-{project_type}-brand-only"
                status, created = self._request("/api/projects", "POST", {
                    "id": project_id,
                    "projectType": project_type,
                    "brand": "api-brand",
                })
                self.assertEqual(status, 201)
                self.assertEqual(created["project"]["brand"], "api-brand")

                status, loaded = self._request(f"/api/projects/{project_id}/topic")
                self.assertEqual(status, 200)
                self.assertEqual(loaded["topic"]["brand"], "api-brand")
                self.assertEqual(loaded["topic"]["characterId"], "human-presenter")

                status, saved = self._request(
                    f"/api/projects/{project_id}/topic", "PUT", loaded["topic"],
                )
                self.assertEqual(status, 200)
                self.assertEqual(saved["topic"]["brand"], "api-brand")

                status, reloaded = self._request(f"/api/projects/{project_id}/topic")
                self.assertEqual(status, 200)
                self.assertEqual(reloaded["topic"]["brand"], "api-brand")

        with (
            patch.object(web_server, "social_status", return_value={"brand_route_records": {}, "platforms": {}}),
            patch.object(web_server, "read_social_config", return_value={}),
        ):
            status, social_payload = self._request("/api/social/brands")
        self.assertEqual(status, 200)
        self.assertEqual(social_payload["brands"][0]["id"], "api-brand")
        self.assertEqual(social_payload["brands"][0]["name"], "API Brand")
        self.assertNotIn("access_token", json.dumps(social_payload))

        status, payload = self._request("/api/brands", "POST", {"name": "API Brand"})
        self.assertEqual(status, 409)
        self.assertIn("đã tồn tại", payload["error"])

        status, payload = self._request("/api/brands/missing", "DELETE")
        self.assertEqual(status, 404)
        self.assertIn("Không tìm thấy", payload["error"])

        topic_path = self.projects / "api-project" / "topic.json"
        topic_path.parent.mkdir(parents=True)
        topic_path.write_text(json.dumps({"brand": "api-brand"}), encoding="utf-8")
        status, payload = self._request("/api/brands/api-brand", "DELETE")
        self.assertEqual(status, 409)
        self.assertEqual(
            payload["usage"]["projects"],
            ["api-custom-brand-only", "api-project", "api-quiz-brand-only"],
        )

    def test_cascade_delete_endpoint_removes_exact_project_and_route(self) -> None:
        status, payload = self._request("/api/brands", "POST", {"id": "cascade-brand", "name": "Cascade Brand"})
        self.assertEqual(status, 201)

        project = self.projects / "cascade-project"
        project.mkdir(parents=True)
        (project / "topic.json").write_text(json.dumps({"brand": "cascade-brand"}), encoding="utf-8")
        self.social.parent.mkdir(parents=True, exist_ok=True)
        self.social.write_text(json.dumps({
            "brand_routes": {"cascade-brand": {"facebook": {"page_id": "page"}}},
            "zernio": {"connections": {
                "cascade-connection": {"brand": "cascade-brand", "api_key": "preserve"},
            }},
        }), encoding="utf-8")

        status, deleted = self._request("/api/brands/cascade-brand?cascade=true", "DELETE")

        self.assertEqual(status, 200)
        self.assertEqual(deleted["deleted"], "cascade-brand")
        self.assertEqual(deleted["projects"], ["cascade-project"])
        self.assertEqual(deleted["social"], ["route:facebook"])
        self.assertFalse(project.exists())
        social = json.loads(self.social.read_text(encoding="utf-8"))
        self.assertNotIn("cascade-brand", social["brand_routes"])
        self.assertNotIn("brand", social["zernio"]["connections"]["cascade-connection"])
        self.assertEqual(social["zernio"]["connections"]["cascade-connection"]["api_key"], "preserve")


if __name__ == "__main__":
    unittest.main()
