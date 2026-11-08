# -*- coding: utf-8 -*-
"""
tests/test_start_server.py - start_server.py の単体テスト (unittest)
"""

import http.server
import socket
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import start_server


class TestStartServer(unittest.TestCase):
    def test_load_orchestration_config_real(self):
        """実環境の ports.json から SummarizePodcast 設定が読み取れるかテスト"""
        cfg = start_server.load_orchestration_config()
        self.assertEqual(cfg.get("port"), 8005)
        self.assertEqual(cfg.get("fallback_port"), 8055)
        self.assertEqual(cfg.get("health_path"), "/_stcore/health")

    def test_load_orchestration_config_fallback(self):
        """ports.json が存在しない場合のデフォルト値フォールバック確認"""
        with patch("start_server.PORTS_JSON_PATH", Path("non_existent_path.json")):
            cfg = start_server.load_orchestration_config()
            self.assertEqual(cfg.get("port"), 8005)
            self.assertEqual(cfg.get("fallback_port"), 8055)
            self.assertEqual(cfg.get("health_path"), "/_stcore/health")

    def test_state_files_lifecycle(self):
        """状態ファイルの保存とクリーンアップのライフサイクル確認"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_port_file = tmp_path / ".server_port"
            mock_pid_file = tmp_path / ".server_pid"

            with patch("start_server.PORT_FILE", mock_port_file), patch("start_server.PID_FILE", mock_pid_file):
                # 存在しない初期状態
                port, pid = start_server.get_existing_server_info()
                self.assertIsNone(port)
                self.assertIsNone(pid)

                # 保存
                start_server.save_state_files(8005, 99999)
                self.assertEqual(mock_port_file.read_text(encoding="utf-8").strip(), "8005")
                self.assertEqual(mock_pid_file.read_text(encoding="utf-8").strip(), "99999")

                # 読込
                port, pid = start_server.get_existing_server_info()
                self.assertEqual(port, 8005)
                self.assertEqual(pid, 99999)

                # クリーンアップ
                start_server.cleanup_state_files()
                self.assertFalse(mock_port_file.exists())
                self.assertFalse(mock_pid_file.exists())

    def test_detect_available_port_primary(self):
        """8005 が空いている場合は 8005 が選択される"""
        with patch("start_server.is_port_in_use", side_effect=lambda p: False):
            port = start_server.detect_available_port(8005, 8055)
            self.assertEqual(port, 8005)

    def test_detect_available_port_fallback(self):
        """8005 が使用中の場合は 8055 が選択される"""
        def mock_in_use(p):
            return p == 8005

        with patch("start_server.is_port_in_use", side_effect=mock_in_use):
            port = start_server.detect_available_port(8005, 8055)
            self.assertEqual(port, 8055)

    def test_detect_available_port_both_in_use(self):
        """8005, 8055 の双方が使用中の場合は 8056 以降から空きを探索する"""
        def mock_in_use(p):
            return p in (8005, 8055, 8056)

        with patch("start_server.is_port_in_use", side_effect=mock_in_use):
            port = start_server.detect_available_port(8005, 8055)
            self.assertEqual(port, 8057)

    def test_is_http_alive_with_mock_server(self):
        """ローカル疑似HTTPサーバーに対する is_http_alive 応答確認"""
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/_stcore/health":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass

        server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertTrue(start_server.is_http_alive(port, "/_stcore/health"))
            self.assertFalse(start_server.is_http_alive(port, "/wrong_path"))
        finally:
            server.shutdown()
            server.server_close()

    def test_main_reuse_existing_server(self):
        """二重起動時: 稼働中ならブラウザを開いて sys.exit(0) する【挙動1】の検証"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_port_file = tmp_path / ".server_port"
            mock_pid_file = tmp_path / ".server_pid"
            mock_port_file.write_text("8005", encoding="utf-8")
            mock_pid_file.write_text("12345", encoding="utf-8")

            with patch("start_server.PORT_FILE", mock_port_file), \
                 patch("start_server.PID_FILE", mock_pid_file), \
                 patch("start_server.is_http_alive", return_value=True), \
                 patch("webbrowser.open") as mock_open:

                with self.assertRaises(SystemExit) as cm:
                    start_server.main()

                self.assertEqual(cm.exception.code, 0)
                mock_open.assert_called_once_with("http://localhost:8005/")

    def test_real_port_conflict_fallback(self):
        """実ソケットでプライマリポートを占有した際にフォールバックポートが正しく検出されるかテスト"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied_sock:
            occupied_sock.bind(("127.0.0.1", 0))
            occupied_port = occupied_sock.getsockname()[1]
            occupied_sock.listen(1)

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as free_sock:
                free_sock.bind(("127.0.0.1", 0))
                free_port = free_sock.getsockname()[1]

            detected = start_server.detect_available_port(occupied_port, free_port)
            self.assertEqual(detected, free_port)


if __name__ == "__main__":
    unittest.main()
