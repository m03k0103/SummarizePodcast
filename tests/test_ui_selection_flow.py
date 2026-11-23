# -*- coding: utf-8 -*-
"""
tests/test_ui_selection_flow.py - st.data_editor チェックボックス選択から1回目ボタンクリックで即実行される回帰テスト
"""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


_MOCK_SELECTION_APP = """
import pandas as pd
import streamlit as st

podcast_key = "test_pod"
episodes = [{"title": "Ep1"}, {"title": "Ep2"}]

select_state_key = f"selected_rows_{podcast_key}"
editor_ver_key = f"ep_editor_ver_{podcast_key}"
if select_state_key not in st.session_state:
    st.session_state[select_state_key] = [False] * len(episodes)
if editor_ver_key not in st.session_state:
    st.session_state[editor_ver_key] = 0
if "ran" not in st.session_state:
    st.session_state["ran"] = 0
if "last_selected" not in st.session_state:
    st.session_state["last_selected"] = []

if st.button("最新回を選択", key=f"q_latest_{podcast_key}"):
    st.session_state[select_state_key] = [True, False]
    st.session_state[editor_ver_key] += 1
    st.rerun()

df = pd.DataFrame({
    "選択": st.session_state[select_state_key],
    "_idx": list(range(len(episodes))),
})
editor_key = f"ep_editor_{podcast_key}_{st.session_state[editor_ver_key]}"
edited_df = st.data_editor(df, disabled=["_idx"], key=editor_key)
selected_indices = edited_df[edited_df["選択"] == True]["_idx"].tolist()

if st.button("選択エピソードの要約を実行", key=f"btn_summarize_{podcast_key}", disabled=len(selected_indices) == 0):
    st.session_state["ran"] += 1
    st.session_state["last_selected"] = selected_indices
"""


class TestUiSelectionFlow(unittest.TestCase):
    def test_app_py_no_data_editor_input_writeback(self):
        """app.py で手動選択の入力 DataFrame 書き戻しが削除され、バージョン付きキーと一意ボタン key が使われていること"""
        app_src = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn(
            'st.session_state[select_state_key] = edited_df["選択"].tolist()',
            app_src,
            "st.data_editor への入力書き戻しが残存しています",
        )
        self.assertIn('key=f"ep_editor_{podcast_key}_{st.session_state[editor_ver_key]}"', app_src)
        self.assertIn('key=f"btn_summarize_{podcast_key}"', app_src)
        self.assertIn('key=f"btn_audio_only_{podcast_key}"', app_src)

    def test_first_click_executes_after_manual_checkbox_edit(self):
        """手動チェック編集後の1回目クリックで即座に要約実行ロジックが発火すること"""
        edit_payload = {
            "edited_rows": {0: {"選択": True}},
            "added_rows": [],
            "deleted_rows": [],
        }
        at = AppTest.from_string(_MOCK_SELECTION_APP, default_timeout=15).run()
        self.assertTrue(at.button(key="btn_summarize_test_pod").disabled)
        self.assertEqual(at.session_state["ran"], 0)

        # Run 2: 0行目のチェックボックスを手動で True に変更
        at.session_state["ep_editor_test_pod_0"] = edit_payload
        at.run()
        self.assertFalse(at.button(key="btn_summarize_test_pod").disabled)

        # Run 3: 実ブラウザと同様にフロントエンドの編集状態を保持したまま1回目の実行ボタンクリック
        at.session_state["ep_editor_test_pod_0"] = edit_payload
        at.button(key="btn_summarize_test_pod").click().run()
        self.assertEqual(at.session_state["ran"], 1)
        self.assertEqual(at.session_state["last_selected"], [0])
        self.assertEqual(at.session_state["ep_editor_test_pod_0"]["edited_rows"], {0: {"選択": True}})

    def test_first_click_executes_after_quick_select(self):
        """クイック選択ボタン押下後の1回目クリックで即座に要約実行ロジックが発火すること"""
        at = AppTest.from_string(_MOCK_SELECTION_APP, default_timeout=15).run()
        at.button(key="q_latest_test_pod").click().run()
        self.assertEqual(at.session_state["ep_editor_ver_test_pod"], 1)
        self.assertFalse(at.button(key="btn_summarize_test_pod").disabled)

        at.button(key="btn_summarize_test_pod").click().run()
        self.assertEqual(at.session_state["ran"], 1)
        self.assertEqual(at.session_state["last_selected"], [0])


if __name__ == "__main__":
    unittest.main()
