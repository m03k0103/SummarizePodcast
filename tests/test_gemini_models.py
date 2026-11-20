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


def _load_asr_model_helpers():
    """app.py から Streamlit UI 実行を伴わずに ASR モデル優先順位ロジックを抽出"""
    app_src = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(app_src)
    target_names = {"GEMINI_ASR_FALLBACK_ORDER", "TRANSCRIBE_MAX_DURATION_SEC"}
    selected_nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in target_names:
                    selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "build_asr_candidate_models":
            selected_nodes.append(node)

    module = ast.Module(body=selected_nodes, type_ignores=[])
    ns = {}
    exec(compile(module, filename=str(PROJECT_ROOT / "app.py"), mode="exec"), ns)
    return ns["GEMINI_ASR_FALLBACK_ORDER"], ns["build_asr_candidate_models"]


class TestGeminiModelsPriority(unittest.TestCase):
    def test_asr_fallback_order_under_50_minutes(self):
        """50分未満の音声では 3.5-transcribe を3位に含む5段階順序になること"""
        order, build_fn = _load_asr_model_helpers()
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
        _, build_fn = _load_asr_model_helpers()
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


if __name__ == "__main__":
    unittest.main()
