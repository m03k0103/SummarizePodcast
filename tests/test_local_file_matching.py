# -*- coding: utf-8 -*-
"""
tests/test_local_file_matching.py - ローカル Output ファイルの Unicode 正規化照合および実態スキャン回帰テスト
"""

import ast
import re
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = PROJECT_ROOT / "Source"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from download_and_summarize_podcast import normalize_text


def _load_matching_helpers() -> dict:
    """app.py から Streamlit UI 実行を伴わずにローカルファイル照合ヘルパーを抽出"""
    app_src = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(app_src)
    target_funcs = {
        "sanitize_filename",
        "canonical_filename_key",
        "strip_episode_prefix",
        "_split_output_suffix",
        "_suffix_to_kind",
        "build_local_output_index",
        "lookup_local_output_file",
    }
    selected_nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in target_funcs
    ]
    module = ast.Module(body=selected_nodes, type_ignores=[])
    ns = {
        "re": re,
        "Path": Path,
        "normalize_text": normalize_text,
        "OUTPUT_DIR": PROJECT_ROOT / "Output",
    }
    exec(compile(module, filename=str(PROJECT_ROOT / "app.py"), mode="exec"), ns)
    return ns


class TestLocalFileMatching(unittest.TestCase):
    def test_canonical_filename_key_nfd_and_fullwidth(self):
        """NFD濁点分離・全角記号(?/!)・大文字小文字の違いが同一正規化キーへ収束すること"""
        ns = _load_matching_helpers()
        canon = ns["canonical_filename_key"]

        # 濁点分離 (NFD) vs NFC
        nfd_title = "20261008_産業革命の時には一度、手作業のニース\u3099か\u3099増えた"
        nfc_title = "20261008_産業革命の時には一度、手作業のニーズが増えた"
        self.assertEqual(canon(nfd_title), canon(nfc_title))

        # NFD + 全角記号 (？/！) vs NFKC + sanitize
        nfd_full_q = "20261009_SaaS_is_Deadには意外にならないんし\u3099ゃないか？と\u3099うなんた\u3099？！"
        sanitized_api = "20261009_SaaS_is_Deadには意外にならないんじゃないかどうなんだ!"
        self.assertEqual(canon(nfd_full_q), canon(sanitized_api))

    def test_build_and_lookup_nfd_disk_files(self):
        """NFD実ファイル解決・0バイト除外・.md優先・UUIDプレフィックス吸収を検証"""
        ns = _load_matching_helpers()
        build_idx = ns["build_local_output_index"]
        lookup = ns["lookup_local_output_file"]
        sanitize = ns["sanitize_filename"]

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            show_dir = root / "けんすうスピーク"
            show_dir.mkdir(parents=True)

            # 1. NFD MP3 & NFD 要約 (.md)
            nfd_base = "20261009_SaaS_is_Deadには意外にならないんし\u3099ゃないか？と\u3099うなんた\u3099？！"
            (show_dir / f"{nfd_base}.mp3").write_bytes(b"fake-mp3-data")
            (show_dir / f"{nfd_base}.summary.ja.md").write_text("要約本文", encoding="utf-8")

            # 2. 0バイト空ファイルは未済扱い
            (show_dir / "20261120_空ファイルテスト.mp3").write_bytes(b"")

            # 3. .summary.ja.txt と .summary.ja.md が両方ある場合は .md を優先
            (show_dir / "20261119_二重形式テスト.summary.ja.txt").write_text("old", encoding="utf-8")
            (show_dir / "20261119_二重形式テスト.summary.ja.md").write_text("new", encoding="utf-8")

            # 4. 旧 UUID プレフィックスのファイル
            legacy_uuid = "12345678-1234-1234-1234-123456789abc"
            (show_dir / f"{legacy_uuid}_過去エピソード.mp3").write_bytes(b"legacy-mp3")

            idx = build_idx(podcast_name="けんすうスピーク", output_root=root)

            # NFC/NFKC のベース名で NFD ファイルが解決されること
            nfc_title = normalize_text("SaaS is Deadには意外にならないんじゃないか？どうなんだ？！", single_line=True)
            api_base = f"20261009_{sanitize(nfc_title)}"
            hit_mp3 = lookup(idx, api_base, ".mp3", title_only=sanitize(nfc_title))
            hit_ja = lookup(idx, api_base, ".summary.ja.md", title_only=sanitize(nfc_title))
            self.assertIsNotNone(hit_mp3)
            self.assertEqual(hit_mp3.name, f"{nfd_base}.mp3")
            self.assertIsNotNone(hit_ja)
            self.assertEqual(hit_ja.name, f"{nfd_base}.summary.ja.md")

            # 0バイト空ファイルは None
            self.assertIsNone(lookup(idx, "20261120_空ファイルテスト", ".mp3"))

            # .md が .txt より優先される
            hit_md = lookup(idx, "20261119_二重形式テスト", ".summary.ja.md")
            self.assertIsNotNone(hit_md)
            self.assertTrue(hit_md.name.endswith(".md"))

            # UUID プレフィックスでもタイトル一致で解決
            hit_legacy = lookup(idx, "20260101_過去エピソード", ".mp3", title_only="過去エピソード")
            self.assertIsNotNone(hit_legacy)
            self.assertEqual(hit_legacy.name, f"{legacy_uuid}_過去エピソード.mp3")

    def test_real_kensu_speak_folder_if_present(self):
        """ローカル実環境の Output/けんすうスピーク が存在する場合に実ファイルが正しく照合されることを検証"""
        target_dir = PROJECT_ROOT / "Output" / "けんすうスピーク"
        if not target_dir.exists():
            self.skipTest("Output/けんすうスピーク ディレクトリが存在しないためスキップ")

        ns = _load_matching_helpers()
        build_idx = ns["build_local_output_index"]
        lookup = ns["lookup_local_output_file"]
        sanitize = ns["sanitize_filename"]

        idx = build_idx(podcast_name="けんすうスピーク", output_root=PROJECT_ROOT / "Output")

        # 実在する代表的NFDファイル群が通常のNFKCキーから解決できること
        expected_both = [
            "20261008_産業革命の時には一度、手作業のニーズが増えた",
            "20261009_SaaS_is_Deadには意外にならないんじゃないかどうなんだ!",
            "20261105_商人・預言者・指導者・王_リーダー",
        ]
        for base in expected_both:
            with self.subTest(base=base):
                self.assertIsNotNone(lookup(idx, base, ".mp3"), f"{base}.mp3 が検出されませんでした")
                self.assertIsNotNone(lookup(idx, base, ".summary.ja.md"), f"{base}.summary.ja.md が検出されませんでした")

        # フォルダ内の全実在ファイル（.mp3 / .summary.ja.md）が NFKC 正規化ベース名から 100% 検出できること
        checked_count = 0
        for f in sorted(target_dir.iterdir()):
            if not f.is_file() or f.stat().st_size <= 0:
                continue
            for suffix in (".mp3", ".summary.ja.md"):
                if f.name.endswith(suffix):
                    raw_base = f.name[:-len(suffix)]
                    norm_base = sanitize(normalize_text(raw_base, single_line=True))
                    with self.subTest(file=f.name):
                        hit = lookup(idx, norm_base, suffix)
                        self.assertIsNotNone(hit, f"{f.name} が正規化照合で検出されませんでした")
                        self.assertEqual(hit.resolve(), f.resolve())
                    checked_count += 1
                    break
        self.assertGreater(checked_count, 0)


if __name__ == "__main__":
    unittest.main()
