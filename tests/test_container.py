# -*- coding: utf-8 -*-
"""容器化与 Web 鉴权层的单元测试。

覆盖：
* ``D2A_*`` 环境变量 → serve 参数映射
* ``/api/health`` 免鉴权 + 健康语义
* Bearer / query token 鉴权，以及未授权时的 401
* 变更类 POST 之后的配置持久化
* 容器入口的自检与探测（含基线播种）

运行::

    python -m unittest tests.test_container -v
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a.data_loader import HeroBook  # noqa: E402
from d2a.engine import Assistant  # noqa: E402
from d2a.webui import ApiState, make_handler  # noqa: E402


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    """起一个临时 HTTP 服务，测试结束后务必 close()。"""

    def __init__(self, token: str = "", config_path=None):
        self.book = HeroBook.load()
        self.assistant = Assistant.create(book=self.book)
        self.state = ApiState(self.assistant, config_path=config_path)
        self.port = _free_port()
        self.token = token
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), make_handler(self.state, api_token=token))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.3)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(self, path, method="GET", body=None, token=None, parse_json=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(raw) if parse_json else raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            if not parse_json:
                return e.code, raw
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"raw": raw}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestHealthEndpoint(unittest.TestCase):
    def setUp(self):
        self.s = _Server()
        self.addCleanup(self.s.close)

    def test_health_ok_and_shape(self):
        code, body = self.s.request("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "healthy")
        self.assertGreaterEqual(body["heroes"], 100)
        self.assertGreater(body["matchup_edges"], 0)
        self.assertFalse(body["auth_required"], "没设 token 时 auth_required 应为 False")

    def test_health_reports_auth_required(self):
        s = _Server(token="t0ken")
        self.addCleanup(s.close)
        code, body = s.request("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["auth_required"])


class TestAuth(unittest.TestCase):
    def setUp(self):
        self.token = "s3cret-token"
        self.s = _Server(token=self.token)
        self.addCleanup(self.s.close)

    def test_health_is_exempt(self):
        """Docker HEALTHCHECK 依赖这一点：健康检查不能要鉴权。"""
        code, _ = self.s.request("/api/health")
        self.assertEqual(code, 200)

    def test_protected_get_without_token_is_401(self):
        code, body = self.s.request("/api/state")
        self.assertEqual(code, 401)
        self.assertFalse(body["ok"])

    def test_protected_post_without_token_is_401(self):
        code, _ = self.s.request("/api/pool", method="POST", body={"op": "set", "hero": "Axe", "games": 5})
        self.assertEqual(code, 401)

    def test_bearer_token_allows(self):
        code, body = self.s.request("/api/state", token=self.token)
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_query_token_allows(self):
        """浏览器直接打开链接时用 ?token=…，必须也能过。"""
        code, body = self.s.request(f"/api/state?token={self.token}")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_wrong_token_is_401(self):
        code, _ = self.s.request("/api/state", token="wrong")
        self.assertEqual(code, 401)

    def test_index_html_also_protected(self):
        code, _ = self.s.request("/", parse_json=False)
        self.assertEqual(code, 401)
        code, html = self.s.request(f"/?token={self.token}", parse_json=False)
        self.assertEqual(code, 200)
        self.assertIn("<!DOCTYPE html>", html)

    def test_index_html_served_without_token_when_no_auth(self):
        s = _Server()
        self.addCleanup(s.close)
        code, html = s.request("/", parse_json=False)
        self.assertEqual(code, 200)
        self.assertIn("<!DOCTYPE html>", html)

    def test_no_token_means_open(self):
        s = _Server()
        self.addCleanup(s.close)
        code, _ = s.request("/api/state")
        self.assertEqual(code, 200, "未设置 token 时应保持原来的开放行为")


class TestPersistence(unittest.TestCase):
    def test_mutation_persists_config(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = pathlib.Path(td) / "config.json"
            s = _Server(config_path=cfg)
            self.addCleanup(s.close)

            # BP 变更不应写盘（属于每局状态）
            s.request("/api/add", method="POST", body={"hero": "Axe", "side": "ally", "lane": 3})
            self.assertFalse(cfg.exists(), "BP 录入不该触发配置落盘")

            # 英雄池变更应写盘
            code, _ = s.request("/api/pool", method="POST", body={"op": "set", "hero": "斧王", "games": 30, "wins": 18})
            self.assertEqual(code, 200)
            self.assertTrue(cfg.exists(), "英雄池变更应触发落盘")
            data = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertIn("Axe", data["pool"])
            self.assertEqual(data["pool"]["Axe"]["games"], 30)

            # 权重变更应写盘
            s.request("/api/weights", method="POST", body={"proficiency": 0.5})
            data = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertAlmostEqual(data["weights"]["proficiency"], 0.5, places=6)

            # 档位变更应写盘
            s.request("/api/bracket", method="POST", body={"bracket": "divine"})
            data = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertEqual(data["bracket"], "7")

    def test_persist_without_config_path_is_noop(self):
        s = _Server(config_path=None)
        self.addCleanup(s.close)
        code, _ = s.request("/api/pool", method="POST", body={"op": "set", "hero": "Axe", "games": 5})
        self.assertEqual(code, 200, "没有配置路径时也不能报错")


class TestServeEnvMapping(unittest.TestCase):
    """D2A_* 环境变量 → serve() 参数。只验证映射，不真的起服务。"""

    def _capture(self, env: dict) -> dict:
        import d2a.webui.server as srv

        captured: dict = {}
        real_serve = srv.serve

        def fake_serve(assistant, **kw):
            captured.update(kw)

        srv.serve = fake_serve  # type: ignore[assignment]
        old = {k: os.environ.get(k) for k in env}
        try:
            for k, v in env.items():
                os.environ[k] = v
            srv.serve_from_env(Assistant.create(book=HeroBook.load()))
        finally:
            srv.serve = real_serve  # type: ignore[assignment]
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return captured

    def test_defaults(self):
        got = self._capture({"D2A_HOST": "", "D2A_PORT": "", "D2A_API_TOKEN": "", "D2A_CONFIG": ""})
        self.assertEqual(got["host"], "127.0.0.1")
        self.assertEqual(got["port"], 8787)
        self.assertEqual(got["api_token"], "")
        self.assertFalse(got["open_browser"])

    def test_container_style(self):
        got = self._capture(
            {
                "D2A_HOST": "0.0.0.0",
                "D2A_PORT": "9999",
                "D2A_API_TOKEN": "tok",
                "D2A_CONFIG": "/app/data/config.json",
                "D2A_OPEN_BROWSER": "1",
            }
        )
        self.assertEqual(got["host"], "0.0.0.0")
        self.assertEqual(got["port"], 9999)
        self.assertEqual(got["api_token"], "tok")
        self.assertEqual(str(got["config_path"]), str(pathlib.Path("/app/data/config.json")))
        self.assertTrue(got["open_browser"])

    def test_bad_port_falls_back(self):
        got = self._capture({"D2A_PORT": "not-a-number"})
        self.assertEqual(got["port"], 8787)


class TestEntrypointHelpers(unittest.TestCase):
    """容器入口的自检与播种逻辑（不起容器）。"""

    def _mod(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "d2a_entrypoint", ROOT / "tools" / "docker_entrypoint.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    def test_selfcheck_passes_on_repo_data(self):
        mod = self._mod()
        old = os.environ.get("D2A_DATA_DIR")
        os.environ["D2A_DATA_DIR"] = str(ROOT / "data")
        try:
            self.assertEqual(mod.selfcheck(), 0, "仓库自带数据必须通过自检")
        finally:
            if old is None:
                os.environ.pop("D2A_DATA_DIR", None)
            else:
                os.environ["D2A_DATA_DIR"] = old

    def test_selfcheck_fails_on_empty_dir(self):
        mod = self._mod()
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("D2A_DATA_DIR")
            os.environ["D2A_DATA_DIR"] = td
            try:
                self.assertEqual(mod.selfcheck(), 1, "空目录必须自检失败，而不是带病启动")
            finally:
                if old is None:
                    os.environ.pop("D2A_DATA_DIR", None)
                else:
                    os.environ["D2A_DATA_DIR"] = old

    def test_seed_copies_only_missing(self):
        """播种必须「只补不覆盖」：用户改过的文件不能被镜像基线冲掉。"""
        mod = self._mod()
        with tempfile.TemporaryDirectory() as td:
            target = pathlib.Path(td)
            old = os.environ.get("D2A_BASELINE_DIR")
            os.environ["D2A_BASELINE_DIR"] = str(ROOT / "data")
            try:
                # 预置一个被用户改过的文件
                (target / "heroes.json").write_text('{"heroes": [], "user_edited": true}', encoding="utf-8")
                mod.seed_data_dir(target)
                edited = json.loads((target / "heroes.json").read_text(encoding="utf-8"))
                self.assertTrue(edited.get("user_edited"), "已存在的文件不该被基线覆盖")
                # 缺失的应该被补上
                self.assertTrue((target / "aliases.json").exists(), "缺失的文件应被播种")
                self.assertTrue((target / "synergies.json").exists())
                # 第二次播种不应再动任何东西
                n = mod.seed_data_dir(target)
                self.assertEqual(n, 0, "第二次播种应当无事可做")
            finally:
                if old is None:
                    os.environ.pop("D2A_BASELINE_DIR", None)
                else:
                    os.environ["D2A_BASELINE_DIR"] = old

    def test_seed_noop_without_baseline(self):
        mod = self._mod()
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("D2A_BASELINE_DIR")
            os.environ["D2A_BASELINE_DIR"] = str(pathlib.Path(td) / "does-not-exist")
            try:
                self.assertEqual(mod.seed_data_dir(pathlib.Path(td)), 0)
            finally:
                if old is None:
                    os.environ.pop("D2A_BASELINE_DIR", None)
                else:
                    os.environ["D2A_BASELINE_DIR"] = old

    def test_healthcheck_against_live_server(self):
        """入口的 --healthcheck 必须能对真实服务返回 0。"""
        mod = self._mod()
        s = _Server()
        self.addCleanup(s.close)
        old = os.environ.get("D2A_PORT")
        os.environ["D2A_PORT"] = str(s.port)
        try:
            self.assertEqual(mod.healthcheck(), 0)
        finally:
            if old is None:
                os.environ.pop("D2A_PORT", None)
            else:
                os.environ["D2A_PORT"] = old

    def test_healthcheck_fails_when_nothing_listening(self):
        mod = self._mod()
        old = os.environ.get("D2A_PORT")
        os.environ["D2A_PORT"] = str(_free_port())  # 没人监听
        try:
            self.assertEqual(mod.healthcheck(), 1)
        finally:
            if old is None:
                os.environ.pop("D2A_PORT", None)
            else:
                os.environ["D2A_PORT"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
