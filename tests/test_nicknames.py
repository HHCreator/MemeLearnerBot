"""Offline tests for the players' nickname table (nicknames.py, data/nicknames.txt)."""

import os
import tempfile
import unittest
from unittest import mock

from memelearner import cli, nicknames, surf
from tests.test_surf import DAY, NOW, message, use_folder

TABLE = """# 选手 = 外号, 外号
Chovy = 超威，大超, chooovy   # GEN 中单
Canyon ＝ 三叔、三姆斯

chovy = 超大哥
"""


class NicknameTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "nicknames.txt")
        self.write(TABLE)
        patcher = mock.patch.object(nicknames, "NICKNAMES_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, text, encoding="utf-8"):
        with open(self.path, "w", encoding=encoding) as f:
            f.write(text)

    def test_the_table_says_who_each_nickname_is(self):
        # Both commas, 、, a full-width =, notes after #, and a second line for Chovy
        self.assertEqual(nicknames.load(), {
            "Chovy": ["Chovy", "超威", "大超", "chooovy", "超大哥"],
            "Canyon": ["Canyon", "三叔", "三姆斯"]})

    def test_case_width_and_a_letter_written_many_times_dont_matter(self):
        for word in ["超威", "大超", "chovy", "ＣＨＯＶＹ", "Chooooooovy", "chooovy", "超大哥"]:
            self.assertEqual(nicknames.player_of(word), "Chovy", word)
        self.assertEqual(nicknames.player_of("三叔"), "Canyon")
        for word in ["faker", "超", "chov", ""]:
            self.assertIsNone(nicknames.player_of(word), word)
        self.assertEqual(nicknames.key("ＣＨＯＯＯＶＹ！"), "chovy")
        self.assertEqual(nicknames.key("飞飞"), "飞飞")  # Chinese characters stay as they are

    def test_nicknames_are_found_in_phrases_and_lines(self):
        self.assertEqual(nicknames.find("超威同意了尺子没同意"), [("超威", "Chovy")])
        self.assertEqual(nicknames.find("chooooooooooovy同意打野不同意啊"),
                         [("chooooooooooovy", "Chovy")])
        self.assertEqual(nicknames.find("超威同意，三叔不同意啊"),
                         [("超威", "Chovy"), ("三叔", "Canyon")])
        self.assertEqual(nicknames.find("Chovy今年真不一样了"), [("chovy", "Chovy")])
        # Letters only count as a whole word
        self.assertEqual(nicknames.find("chovyx同意了"), [])
        self.assertEqual(nicknames.find("我们是冠军"), [])

    def test_the_longer_nickname_wins(self):
        self.write("Chovy = 超威, 大超\nSuper = 超\n")
        self.assertEqual(nicknames.find("大超同意了"), [("大超", "Chovy")])
        self.assertEqual(nicknames.find("超威和超"), [("超威", "Chovy"), ("超", "Super")])

    def test_a_phrase_that_is_only_a_nickname_is_a_name(self):
        for phrase in ["chooovy", "超威！", "三叔"]:
            self.assertTrue(nicknames.is_name(phrase), phrase)
        for phrase in ["超威同意了", "chovy牛逼", "", "！！"]:
            self.assertFalse(nicknames.is_name(phrase), phrase)

    def test_a_mistake_in_the_table_is_shown_with_its_line(self):
        for text, said in [("Chovy 超威\n", "line 1"), ("# a note\n= 超威\n", "line 2"),
                           ("Chovy = 超威\nCanyon = 三叔, 超威\n", "Chovy's already (line 1)")]:
            self.write(text)
            with self.assertRaises(nicknames.TableError) as caught:
                nicknames.load()
            self.assertIn(said, str(caught.exception))
            self.assertIn("nicknames.txt", str(caught.exception))
        self.write("Chovy = 超威\n", encoding="gbk")  # saved as ANSI by an old editor
        with self.assertRaises(nicknames.TableError) as caught:
            nicknames.player_of("超威")
        self.assertIn("UTF-8", str(caught.exception))
        # The command line says what's wrong, without a traceback
        with mock.patch.object(cli, "_memes", side_effect=lambda args: nicknames.load()), \
                self.assertRaises(SystemExit) as stopped:
            cli.main(["memes"])
        self.assertIn("UTF-8", str(stopped.exception.code))

    def test_the_table_is_read_again_when_it_changes_and_may_be_missing(self):
        self.assertIsNone(nicknames.player_of("兰子"))
        self.write("﻿Doran = 多兰, 兰子\n")  # with the BOM some editors add
        self.assertEqual(nicknames.player_of("兰子"), "Doran")
        os.remove(self.path)
        self.assertEqual((nicknames.load(), nicknames.find("兰子同意了")), ({}, []))
        self.assertFalse(nicknames.is_name("兰子"))


class TableInTheRepoTest(unittest.TestCase):
    def test_it_has_chovys_and_canyons_nicknames(self):
        table = nicknames.load()
        for nickname in ["Chovy", "超威", "大超", "chooovy"]:
            self.assertIn(nickname, table["Chovy"])
        self.assertIn("三叔", table["Canyon"])
        for phrase, player in [("超威同意了", "Chovy"), ("chovy同意了", "Chovy"),
                               ("大超同意了", "Chovy"), ("三叔不同意", "Canyon"),
                               ("超威今年真不一样了", "Chovy")]:
            self.assertEqual([who for _, who in nicknames.find(phrase)], [player], phrase)

    def test_the_finder_doesnt_list_a_nickname_on_its_own(self):
        use_folder(self)
        lines = [("lol", f"BV1v{i % 3}", message(f"p{i}", text, NOW - DAY))
                 for i in range(24) for text in ["chooooooovy！", "超威同意了"]]
        found, _ = surf.find_candidates(lines, days=7, now=NOW, known=set(),
                                        english_words=set())
        self.assertEqual([c["phrase"] for c in found], ["超威同意了"])


if __name__ == "__main__":
    unittest.main()
