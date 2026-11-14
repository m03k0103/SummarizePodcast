# -*- coding: utf-8 -*-
"""
tests/test_design_harness.py - Design.md (第3層・第4層) Design Quality Gate スモークテスト
"""

import ast
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = PROJECT_ROOT / "Source"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from download_and_summarize_podcast import normalize_text


class TestDesignHarness(unittest.TestCase):
    def test_python_syntax_all_files(self):
        """app.py および Source/*.py 全ファイルの構文エラーがないことを検証"""
        target_files = [PROJECT_ROOT / "app.py"] + sorted(SOURCE_DIR.glob("*.py"))
        for py_file in target_files:
            with self.subTest(file=py_file.name):
                source = py_file.read_text(encoding="utf-8")
                ast.parse(source, filename=str(py_file))

    def test_streamlit_config_toml_slate_blue(self):
        """Design.md §1.1: .streamlit/config.toml が Light Slate & Blue パレットであることを検証"""
        config_path = PROJECT_ROOT / ".streamlit" / "config.toml"
        self.assertTrue(config_path.exists(), ".streamlit/config.toml が存在しません")
        content = config_path.read_text(encoding="utf-8")

        expected_pairs = [
            ('base', '"light"'),
            ('primaryColor', '"#2563eb"'),
            ('backgroundColor', '"#f8fafc"'),
            ('secondaryBackgroundColor', '"#f1f5f9"'),
            ('textColor', '"#0f172a"'),
        ]
        for key, val in expected_pairs:
            pattern = rf"^\s*{key}\s*=\s*{re.escape(val)}\s*$"
            self.assertRegex(
                content,
                re.compile(pattern, re.MULTILINE),
                f".streamlit/config.toml に {key} = {val} が設定されていません",
            )

    def test_styles_separated_from_app_py(self):
        """Design.md BAN-09: styles.css へのスタイル分離と app.py 内インライン CSS 排除を検証"""
        css_path = PROJECT_ROOT / "styles.css"
        self.assertTrue(css_path.exists(), "styles.css が存在しません")
        css_content = css_path.read_text(encoding="utf-8")

        for token in ("--bg-primary: #f8fafc;", "--accent-primary: #2563eb;", ".status-strip", ".podcast-card"):
            self.assertIn(token, css_content, f"styles.css に必須トークン/クラス {token} が含まれていません")

        app_content = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('load_css(PROJECT_ROOT / "styles.css")', app_content)
        # 長大なインライン <style> ブロックやインライン style= 属性が直書きされていないこと
        self.assertEqual(app_content.count("<style>"), 1, "app.py に load_css 以外の <style> タグが存在します")
        self.assertIn("<style>{css_content}</style>", app_content)
        inline_style_attrs = re.findall(r'style="[^"]+"', app_content)
        self.assertEqual(len(inline_style_attrs), 0, "app.py 内にインライン style=\"...\" 属性が残存しています")
        # 1行ステータスバーの配置確認
        self.assertIn('class="status-strip"', app_content)

    def test_normalize_text_nfkc_and_nbsp(self):
        """Design.md §2.2: NFKC正規化（康煕部首・全角英数）および不可分空白(\xa0)クレンジングを検証"""
        # 康煕部首 '⼀' (\u2f00 -> '一'), '⼾' (\u2f3e -> '戸'), 全角英数
        raw_title = "⼀番目の\xa0エピソード　ＡＢＣ\n第１回\u200b"
        cleaned_title = normalize_text(raw_title, single_line=True)
        self.assertEqual(cleaned_title, "一番目の エピソード ABC 第1回")
        self.assertNotIn("\xa0", cleaned_title)
        self.assertNotIn("\n", cleaned_title)

        # 複数行 Markdown 要約の段落改行保持 & \xa0 除去
        raw_summary = "## 概要\n\n・項目\xa01　テスト\n\n\n\n・項目2\xa0詳細"
        cleaned_summary = normalize_text(raw_summary, single_line=False)
        self.assertEqual(cleaned_summary, "## 概要\n\n・項目 1 テスト\n\n・項目2 詳細")
        self.assertNotIn("\xa0", cleaned_summary)

    def test_no_prohibited_copy_tone_or_ban03(self):
        """Design.md §1.5 BAN-03 / §2.2: 主観的誇張表現や禁止中黒併記が UI に含まれないことを検証"""
        app_content = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

        prohibited_terms = ["劇的", "画期的", "圧倒的", "最速", "対談・複数人", "一人語り・単独", "最新の要約・文字起こし"]
        for term in prohibited_terms:
            self.assertNotIn(term, app_content, f"app.py に禁止表現または非推奨コピー '{term}' が含まれています")


if __name__ == "__main__":
    unittest.main()
