# -*- coding: utf-8 -*-
"""
tests/test_gemini_models.py - Gemini モデル優先順位・フォールバック・50分制限ガードの単体テスト
"""

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = PROJECT_ROOT / "Source"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import download_and_summarize_podcast as dsp


def _load_asr_model_helpers() -> dict:
    """app.py から Streamlit UI 実行を伴わずに ASR モデル優先順位・分割ロジックを抽出"""
    app_src = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(app_src)
    target_names = {
        "GEMINI_ASR_FALLBACK_ORDER",
        "TRANSCRIBE_MAX_DURATION_SEC",
        "GEMINI_MONOLOGUE_CHUNK_MINUTES",
        "DEFAULT_MONOLOGUE_CHUNK_MINUTES",
        "DIALOGUE_CHUNK_MINUTES",
        "RESPLIT_MARGIN_SEC",
        "RETRY_BACKOFF_DELAYS",
    }
    target_funcs = {
        "build_asr_candidate_models",
        "get_chunk_minutes_for_model",
        "needs_fallback_resplit",
        "format_duration",
    }
    selected_nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in target_names:
                    selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in target_funcs:
            selected_nodes.append(node)

    module = ast.Module(body=selected_nodes, type_ignores=[])
    ns = {}
    exec(compile(module, filename=str(PROJECT_ROOT / "app.py"), mode="exec"), ns)
    return ns


class TestGeminiModelsPriority(unittest.TestCase):
    def test_asr_fallback_order_under_50_minutes(self):
        """50分未満の音声では 3.5-transcribe を3位に含む5段階順序になること"""
        ns = _load_asr_model_helpers()
        order = ns["GEMINI_ASR_FALLBACK_ORDER"]
        build_fn = ns["build_asr_candidate_models"]
        expected = [
            "gemini-3.8-flash",
            "gemini-3.6-flash",
            "gemini-3.5-transcribe",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        ]
        self.assertEqual(order, expected)
        self.assertEqual(build_fn("gemini-3.8-flash", duration_sec=1500.0), expected)
        self.assertNotIn("gemini-3.5-flash", expected)

    def test_asr_fallback_order_50_minutes_or_more(self):
        """50分(3000秒)以上または長さ不明の音声では gemini-3.5-transcribe が除外されること"""
        build_fn = _load_asr_model_helpers()["build_asr_candidate_models"]
        expected_long = [
            "gemini-3.8-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        ]
        self.assertEqual(build_fn("gemini-3.8-flash", duration_sec=3000.0), expected_long)
        self.assertEqual(build_fn("gemini-3.8-flash", duration_sec=5400.0), expected_long)
        self.assertEqual(build_fn("gemini-3.8-flash", duration_sec=None), expected_long)
        # ユーザーが長尺で 3.5-transcribe を指定しても先頭・フォールバックからスキップされる
        self.assertEqual(build_fn("gemini-3.5-transcribe", duration_sec=3600.0), expected_long)

    def test_deprecated_3_5_flash_excluded_everywhere(self):
        """非推奨の gemini-3.5-flash がコード内のモデル一覧・フォールバックから除外されていること"""
        app_src = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
        dsp_src = (SOURCE_DIR / "download_and_summarize_podcast.py").read_text(encoding="utf-8")
        self.assertNotIn('"gemini-3.5-flash"', app_src)
        self.assertNotIn('"gemini-3.5-flash"', dsp_src)

    def test_summarize_with_llm_fallback_order(self):
        """要約用 LLM フォールバックが 3.8-flash -> 3.6-flash -> 3.5-flash-lite -> 3.1-flash-lite であること"""
        called_models = []

        def fake_call(prompt, api_key, model="gemini-3.8-flash"):
            called_models.append(model)
            return ""  # 全失敗させてフォールバック順を全トレース

        with patch.object(dsp, "call_gemini_api", side_effect=fake_call), \
             patch("time.sleep", return_value=None):
            dsp.summarize_with_llm(
                text="Sample podcast transcript.",
                target_language="ja",
                provider="gemini",
                gemini_key="dummy-key",
            )

        self.assertEqual(
            called_models,
            [
                "gemini-3.8-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
            ],
        )

    def test_chunk_minutes_by_model(self):
        """モデルおよび出演者構成(一人語り/対談)ごとの分割分数決定を検証"""
        get_min = _load_asr_model_helpers()["get_chunk_minutes_for_model"]

        # 一人語り (is_monologue=True)
        self.assertEqual(get_min("gemini-3.5-transcribe", True), 45)
        self.assertEqual(get_min("models/gemini-3.5-transcribe", True), 45)
        self.assertEqual(get_min("gemini-3.6-flash", True), 25)
        self.assertEqual(get_min("gemini-3.8-flash", True), 25)
        self.assertEqual(get_min("gemini-3.5-flash-lite", True), 25)
        self.assertEqual(get_min("gemini-3.1-flash-lite", True), 25)
        self.assertEqual(get_min("unknown-future-model", True), 25)

        # 複数人対談 (is_monologue=False) -> 全モデル 15分
        for model in (
            "gemini-3.5-transcribe",
            "gemini-3.6-flash",
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        ):
            self.assertEqual(get_min(model, False), 15)

    def test_needs_fallback_resplit(self):
        """フォールバック先モデルの上限超過時にサブ分割が必要と判定されるか検証"""
        needs_resplit = _load_asr_model_helpers()["needs_fallback_resplit"]

        # 一人語り 45分 (2700秒): 3.6/3.8 は再分割必要, 3.5-transcribe は不要
        self.assertTrue(needs_resplit("gemini-3.6-flash", 2700.0, True))
        self.assertTrue(needs_resplit("gemini-3.8-flash", 2700.0, True))
        self.assertFalse(needs_resplit("gemini-3.5-transcribe", 2700.0, True))

        # 一人語り 25分 (1500秒) および 末尾吸収マージン込み 27分 (1620秒): 再分割不要
        self.assertFalse(needs_resplit("gemini-3.6-flash", 1500.0, True))
        self.assertFalse(needs_resplit("gemini-3.6-flash", 1620.0, True))

        # 一人語り 35分 (2100秒): 3.6 は再分割必要, 3.5-transcribe は不要
        self.assertTrue(needs_resplit("gemini-3.6-flash", 2100.0, True))
        self.assertFalse(needs_resplit("gemini-3.5-transcribe", 2100.0, True))

        # 長さ不明 (None / 0.0): False
        self.assertFalse(needs_resplit("gemini-3.6-flash", None, True))
        self.assertFalse(needs_resplit("gemini-3.6-flash", 0.0, True))

    def test_format_duration_zero_handling(self):
        """format_duration(0) が一覧では '不明'、開始時刻(allow_zero=True)では '0分00秒' となるか検証"""
        fmt = _load_asr_model_helpers()["format_duration"]
        self.assertEqual(fmt(0), "不明")
        self.assertEqual(fmt(0, allow_zero=True), "0分00秒")
        self.assertEqual(fmt(None, allow_zero=True), "不明")
        self.assertEqual(fmt(1500, allow_zero=True), "25分00秒")

    def test_retry_backoff_delays(self):
        """503/429 リトライ待機秒数が指数バックオフ (4, 8) に設定されているか検証"""
        delays = _load_asr_model_helpers()["RETRY_BACKOFF_DELAYS"]
        self.assertEqual(delays, (4, 8))


if __name__ == "__main__":
    unittest.main()
