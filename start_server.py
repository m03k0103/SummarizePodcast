# -*- coding: utf-8 -*-
"""
start_server.py - Podcast AI Summarizer サーバー起動管理スクリプト

機能:
1. 二重起動時の再利用【挙動1】:
   - 起動時に .server_port を確認し、すでに Streamlit サーバーが応答（/_stcore/health）している場合は新規起動を行わずブラウザで開く。
2. ポート設定と競合回避:
   - D:\\dev\\Orchestration\\ports.json があれば "SummarizePodcast" のポート定義を参照。
   - 未指定なら 8005（使用中なら 8055）を検出。
3. 起動オプション:
   - python -m streamlit run app.py --server.port <検出ポート> --server.runOnSave true
   - バインドしたポートを .server_port、PIDを .server_pid に記録。
   - 終了時に状態ファイルをクリーンアップ。
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
PORT_FILE = SCRIPT_DIR / ".server_port"
PID_FILE = SCRIPT_DIR / ".server_pid"
PORTS_JSON_PATH = Path("D:/dev/Orchestration/ports.json")

DEFAULT_PRIMARY_PORT = 8005
DEFAULT_FALLBACK_PORT = 8055
DEFAULT_HEALTH_PATH = "/_stcore/health"


def load_orchestration_config() -> Dict[str, Any]:
    """Orchestration/ports.json から SummarizePodcast の設定を読み込む"""
    if PORTS_JSON_PATH.exists():
        try:
            with open(PORTS_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                projects = data.get("projects", {})
                for name, cfg in projects.items():
                    if name.lower() == "summarizepodcast":
                        return cfg
        except Exception as e:
            print(f"[WARN] ports.json 読み込み失敗: {e}", file=sys.stderr)
    return {
        "port": DEFAULT_PRIMARY_PORT,
        "fallback_port": DEFAULT_FALLBACK_PORT,
        "health_path": DEFAULT_HEALTH_PATH,
    }


def is_http_alive(port: int, health_path: str = DEFAULT_HEALTH_PATH, timeout: float = 1.2) -> bool:
    """指定ポートのヘルスチェックエンドポイントが正常応答(200 OK)するか確認"""
    if not health_path.startswith("/"):
        health_path = "/" + health_path
    url = f"http://localhost:{port}{health_path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        return 200 <= e.code < 300
    except Exception:
        return False


def is_port_in_use(port: int) -> bool:
    """ポートが使用中（リッスン中またはバインド不可）か判定"""
    # 1. 接続可能かテスト
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return True

    # 2. Windows での排他バインドテスト
    for bind_host in ("127.0.0.1", "0.0.0.0"):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE") and sys.platform == "win32":
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                except OSError:
                    pass
            try:
                s.bind((bind_host, port))
            except OSError:
                return True
    return False


def detect_available_port(primary_port: int, fallback_port: int) -> int:
    """空きポートを検出 (プライマリ -> フォールバック -> 連番範囲)"""
    if not is_port_in_use(primary_port):
        return primary_port

    print(f"[INFO] プライマリポート {primary_port} は使用中です。フォールバックポート {fallback_port} を確認します...")
    if not is_port_in_use(fallback_port):
        return fallback_port

    print(f"[INFO] フォールバックポート {fallback_port} も使用中です。空きポートを探索します...")
    for p in range(8056, 8100):
        if not is_port_in_use(p):
            return p

    raise RuntimeError("利用可能な空きポートが見つかりませんでした (8005, 8055, 8056〜8099 はすべて使用中)。")


def get_existing_server_info() -> Tuple[Optional[int], Optional[int]]:
    """既存の状態ファイルから port と pid を取得"""
    port = None
    pid = None
    if PORT_FILE.exists():
        try:
            content = PORT_FILE.read_text(encoding="utf-8").strip()
            if content.isdigit():
                port = int(content)
        except Exception:
            pass
    if PID_FILE.exists():
        try:
            content = PID_FILE.read_text(encoding="utf-8").strip()
            if content.isdigit():
                pid = int(content)
        except Exception:
            pass
    return port, pid


def cleanup_state_files() -> None:
    """状態ファイルをクリーンアップ"""
    for file_path in (PORT_FILE, PID_FILE):
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass


def save_state_files(port: int, pid: int) -> None:
    """バインドポートとプロセスPIDを記録"""
    PORT_FILE.write_text(str(port), encoding="utf-8")
    PID_FILE.write_text(str(pid), encoding="utf-8")


def main() -> None:
    cfg = load_orchestration_config()
    primary_port = int(cfg.get("port", DEFAULT_PRIMARY_PORT))
    fallback_port = int(cfg.get("fallback_port", DEFAULT_FALLBACK_PORT))
    health_path = cfg.get("health_path", DEFAULT_HEALTH_PATH)

    # 1. 二重起動時の再利用【挙動1】
    saved_port, saved_pid = get_existing_server_info()
    if saved_port and is_http_alive(saved_port, health_path):
        url = f"http://localhost:{saved_port}/"
        print("=" * 60)
        print("  Podcast AI Summarizer Web Dashboard")
        print("=" * 60)
        print(f"[INFO] 既に Streamlit サーバーがポート {saved_port} で稼働中です。")
        print(f"[INFO] ブラウザでダッシュボードを開きます: {url}")
        print("=" * 60)
        webbrowser.open(url)
        sys.exit(0)

    # 2. ポート検出
    port = detect_available_port(primary_port, fallback_port)

    # 3. 起動コマンド構築
    app_py_path = SCRIPT_DIR / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_py_path),
        "--server.port",
        str(port),
        "--server.runOnSave",
        "true",
    ]

    print("=" * 60)
    print("  Podcast AI Summarizer Web Dashboard")
    print("=" * 60)
    print(f" バインドポート   : {port}")
    print(f" ホットリロード   : runOnSave=true (有効)")
    print(f" 起動コマンド     : {' '.join(cmd)}")
    print(" Webアプリを起動しています... ブラウザが自動的に開きます。")
    print(" 終了するにはこのウィンドウで Ctrl + C を押してください。")
    print("=" * 60)
    print()

    # 4. プロセス起動
    proc = subprocess.Popen(cmd)
    save_state_files(port, proc.pid)

    # 終了ハンドリング（クリーンアップ）
    def handle_signal(sig, frame):
        print("\n[INFO] 終了シグナルを受信しました。サーバーを停止します...")
        try:
            proc.terminate()
        except Exception:
            pass
        cleanup_state_files()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n[INFO] Ctrl + C を検知しました。")
    finally:
        cleanup_state_files()


if __name__ == "__main__":
    main()
