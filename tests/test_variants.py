"""Offline tests for telling the variants of one meme (variants.py)."""

import os
import tempfile
import time
import unittest

from memelearner import variants
from tests.test_surf import use_table

# Hao's examples from the IG vs GEN report (2026-11-07), with how many people sent each
EXAMPLES = {"超威同意了": 272, "真不一样了": 153, "超威今年真不一样了": 107, "chovy同意了": 107,
            "三叔不同意": 54, "大超同意了": 42}

# What the finder listed with them in that run, as their variants
AGREED = ["超威不同意", "超威今年不同意了", "超威同意一下", "超威会同意的", "超威快同意啊",
          "超威又同意了", "chovy不同意", "chovy会同意的", "chovy同意一下", "chovy快同意",
          "rookie同意了", "ruler同意了", "今年不同意了", "三叔同意了", "kiin不同意", "ts不同意",
          "大超不同意", "大超同意一下", "大超快同意", "大超又同意了"]
DIFFERENT = ["超威真不一样了", "今年真不一样了", "超威今年真的不一样了", "超威不一样了",
             "chovy今年真不一样了", "超威真的不一样了", "chovy真不一样了", "今年不一样了",
             "大超真不一样了", "大超不一样了", "今年chovy真不一样了"]


def summary(groups):
    return [(group["pattern"], group["phrases"]) for group in groups]


class VariantTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.table = use_table(self, folder.name)  # Chovy = 超威, 大超, chooovy; Canyon = 三叔

    def test_haos_examples_are_two_memes(self):
        groups = variants.groups(EXAMPLES)
        self.assertEqual(summary(groups), [
            ("XX同意了", ["超威同意了", "chovy同意了", "三叔不同意", "大超同意了"]),
            ("XX今年真不一样了", ["真不一样了", "超威今年真不一样了"])])
        self.assertEqual(groups[0]["names"], [
            {"name": "超威", "player": "Chovy"}, {"name": "chovy", "player": "Chovy"},
            {"name": "三叔", "player": "Canyon"}, {"name": "大超", "player": "Chovy"}])
        self.assertEqual(groups[1]["names"], [{"name": "超威", "player": "Chovy"}])

    def test_the_variants_the_finder_saw_with_them_join_them(self):
        others = ["我不同意", "我们是冠军", "我们是路边", "让二追三"]
        phrases = dict(EXAMPLES, **{p: 1 for p in AGREED + DIFFERENT + others})
        groups = summary(variants.groups(phrases))
        self.assertEqual(groups[0], ("XX同意了", ["超威同意了", "chovy同意了", "三叔不同意",
                                                 "大超同意了"] + AGREED))
        self.assertEqual(groups[1], ("XX今年真不一样了", ["真不一样了", "超威今年真不一样了"] +
                                     DIFFERENT))
        # 我不同意 is everyday talk (我 is no name), and the others are memes of their own
        self.assertEqual(groups[2:], [(p, [p]) for p in others])
        names = [n["name"] for n in variants.groups(phrases)[0]["names"]]
        self.assertEqual(names, ["超威", "chovy", "三叔", "大超", "rookie", "ruler", "kiin", "ts"])

    def test_what_counts_as_the_same_meme(self):
        for a, b in [("超威同意了", "ruler同意了"),          # another player
                     ("三叔不同意", "三叔同意了"),            # turned around
                     ("超威同意了", "超威会同意的"),          # a word left out
                     ("真不一样了", "超威今年真不一样了"),    # the start left out
                     ("chooooovy同意了", "chovy同意了"),     # written longer
                     ("超威同意意意了", "超威同意了"),
                     ("超威不同意", "超威同意一下"),          # turned around and a word added
                     ("发狂了", "wei发狂了"),                 # a name added to a word
                     ("蝴蝶飞不过沧海", "蝴蝶终究飞不过沧海")]:
            self.assertTrue(variants.same_meme(a, b), (a, b))
        for a, b in [("我们是冠军", "我们是路边"),            # another meaning
                     ("目前不知道怎么赢", "目前不知道怎么输"),
                     ("同意", "超威同意一下兄弟们"),           # too little left
                     ("chooovy", "超威"),                    # two names, no meme
                     ("还在洗", "还在ig"),
                     ("我不同意", "超威同意了"),
                     ("退役", "感谢退役"),                    # a word added to a word
                     ("感谢多兰", "千言万语感谢多兰")]:        # too much added
            self.assertFalse(variants.same_meme(a, b), (a, b))

    def test_shapes_patterns_and_names(self):
        for phrase, shape in [("超威同意了", "X同意"), ("三叔不同意", "X不同意"),
                              ("ＣＨＯＯＯＯＶＹ同意意意了！", "X同意"),
                              ("真不一样了", "真不一样"), ("超威chovy同意了", "X同意")]:
            self.assertEqual(variants.shape(phrase), shape, phrase)
        self.assertEqual(variants.pattern("超威同意了"), "XX同意了")
        self.assertEqual(variants.pattern("超威chovy同意了"), "XX同意了")
        self.assertEqual(variants.pattern("真不一样了"), "真不一样了")
        self.assertEqual(variants.names("chooooovy同意了，kiin不同意"),
                         [("chooooovy", "Chovy"), ("kiin", "")])

    def test_a_chinese_name_not_in_the_table_counts_where_names_go(self):
        phrases = {"超威同意了": 10, "ruler同意了": 5, "兰子同意了": 3, "还在ig": 4,
                   "还在we": 2, "还在洗": 4}
        groups = variants.groups(phrases)
        self.assertEqual(summary(groups), [("XX同意了", ["超威同意了", "ruler同意了", "兰子同意了"]),
                                           ("还在XX", ["还在ig", "还在we"]),
                                           ("还在洗", ["还在洗"])])  # one character: a word
        self.assertIn({"name": "兰子", "player": ""}, groups[0]["names"])
        # Where only one name has gone yet, a word there may be no name
        self.assertEqual(summary(variants.groups({"超威同意了": 10, "兰子同意了": 3})),
                         [("XX同意了", ["超威同意了"]), ("兰子同意了", ["兰子同意了"])])

    def test_the_pattern_comes_from_a_phrase_sent_about_as_much(self):
        self.assertEqual(summary(variants.groups({"让二追三": 786, "ig加油让二追三": 20})),
                         [("让二追三", ["让二追三", "ig加油让二追三"])])
        self.assertEqual(summary(variants.groups(["超威同意了", "chovy同意了"])),
                         [("XX同意了", ["超威同意了", "chovy同意了"])])  # a list: in its order

    def test_a_twist_is_another_meme_on_the_same_template(self):
        self.assertEqual(variants.twists("我们是路边", ["超威同意了", "我们是冠军", "我们是路边"]),
                         ["我们是冠军"])
        self.assertEqual(variants.twists("千言万语感谢多兰", ["感谢多兰", "千言万语感谢现役"]),
                         ["千言万语感谢现役"])
        self.assertEqual(variants.twists("怎么输的", ["这怎么翻的", "怎么翻的"]), ["怎么翻的"])
        # The same meme is no twist of itself, and too little alike is none either
        self.assertEqual(variants.twists("ruler同意了", ["超威同意了", "超威会同意的"]), [])
        self.assertEqual(variants.twists("怎么翻", ["怎么回事"]), [])

    def test_a_phrase_with_two_memes_in_it_doesnt_make_them_one(self):
        # IG vs GEN: 阿一古加油 is 阿一古 with 加油 and ig加油 with 阿一古 for ig, ig加油必胜 is
        # ig加油 and ig必胜, and 正义必胜 looked like ig必胜 with 正义 for ig: so 阿一古 and
        # 正义必胜 were turned down with ig加油
        found = variants.groups({
            "ig加油": 7718, "阿一古": 3400, "正义必胜": 2206, "blg加油": 450, "t1加油": 102,
            "ig必胜": 60, "人类必胜": 56, "lpl必胜": 20, "阿一古加油": 30, "正义加油": 12,
            "ig加油必胜": 10, "恭喜gen": 180, "恭喜geng": 50, "恭喜geng夺冠": 9,
            "geng要夺冠了": 40, "超威要夺冠了": 30, "超威同意了": 274, "兰子同意了": 20,
            "ruler同意了": 15})
        group = {phrase: g["pattern"] for g in found for phrase in g["phrases"]}
        self.assertEqual(group["blg加油"], "XX加油")
        for phrase in ("阿一古", "正义必胜", "ig必胜", "人类必胜"):
            self.assertNotEqual(group[phrase], "XX加油", phrase)
        self.assertEqual(group["正义必胜"], "正义必胜")  # sent far more than ig必胜: no name in it
        self.assertEqual(group["ig必胜"], group["lpl必胜"])
        self.assertEqual(group["阿一古加油"], "阿一古")
        self.assertEqual(group["恭喜geng夺冠"], "恭喜XX")
        self.assertEqual(group["超威要夺冠了"], "XX要夺冠了")
        self.assertEqual(group["兰子同意了"], "XX同意了")
        # Everyone in a group has something in common with its first phrase
        for g in found:
            for phrase in g["phrases"]:
                self.assertTrue(variants.kin(variants.shape(g["phrases"][0]),
                                             variants.shape(phrase)), phrase)

    def test_a_word_in_letters_alone_is_no_name(self):
        self.assertTrue(variants.only_names("ggg"))
        self.assertTrue(variants.only_names("chooovy"))
        self.assertFalse(variants.only_names("啊啊啊"))
        self.assertTrue(variants.same_meme("ggg", "GG"))  # written longer
        self.assertFalse(variants.same_meme("ggg", "ig"))
        self.assertFalse(variants.same_meme("ggg", "gg加油"))
        self.assertEqual(variants.groups({"ggg": 148, "ig": 90}),
                         [{"pattern": "ggg", "phrases": ["ggg"], "names": []},
                          {"pattern": "ig", "phrases": ["ig"], "names": []}])

    def test_one_character_written_two_ways(self):
        self.assertTrue(variants.same_meme("啊一占", "阿一占"))  # 你说「啊和阿基本是同一个字」
        self.assertTrue(variants.same_meme("ig冲啊", "ig冲"))  # 啊 at the end is a mood word
        self.assertEqual(variants.shape("ig冲啊"), "X冲")
        # 开始 is a word, not a name, wherever names go
        found = variants.groups(["超威同意了", "chovy同意了", "ruler同意了", "开始同意"])
        self.assertEqual([g["phrases"] for g in found],
                         [["超威同意了", "chovy同意了", "ruler同意了"], ["开始同意"]])

    def test_more_ways_of_writing_one_meme(self):
        # Hao's 4-day report (2026-11-11..15): one meme in two rows
        for a, b in [("我先翻怎么来的", "我先来怎么翻的"),   # the same characters in another order
                     ("关闭得分通道", "得分通道关闭"),
                     ("别狐狸", "不要狐狸"),                # 别 = 不要
                     ("領會員", "领会员"),                  # traditional characters
                     ("零会员", "领会员"),                  # a typo that sounds the same
                     ("零会员了", "领会员")]:
            self.assertTrue(variants.same_meme(a, b), (a, b))
        for a, b in [("目前不知道怎么赢", "目前不知道怎么输"),  # another character, another sound
                     ("好送", "送好"),                       # too few to turn around
                     ("零会", "领会"),                       # too few for a typo
                     ("零会员呀", "领会员会")]:               # two characters differ
            self.assertFalse(variants.same_meme(a, b), (a, b))
        self.assertEqual(variants.shape("不要狐狸"), "别狐狸")
        self.assertEqual(variants.shape("領會員"), "领会员")
        self.assertTrue(variants.sound_alike("零", "领"))
        self.assertFalse(variants.sound_alike("赢", "输"))
        found = variants.groups({"领会员": 53502, "大会员": 5829, "零会员": 130, "領會員": 55,
                                 "别狐狸": 104, "不要狐狸": 56, "我先翻怎么来的": 117,
                                 "我先来怎么翻的": 113})
        self.assertEqual(summary(found), [
            ("领会员", ["领会员", "零会员", "領會員"]), ("大会员", ["大会员"]),
            ("我先翻怎么来的", ["我先翻怎么来的", "我先来怎么翻的"]),
            ("别狐狸", ["别狐狸", "不要狐狸"])])

    def test_ways_listed_in_the_table_are_one_meme(self):
        with open(variants.SAME_FILE, "w", encoding="utf-8") as f:
            f.write("# 一行一个梗\n41翻 = 41上 = 41中  # Hao 说的\n区区区 ＝ 区区 = 好多区\n")
        self.assertTrue(variants.same_meme("41翻", "41上"))
        self.assertTrue(variants.same_meme("41中", "41上"))
        self.assertTrue(variants.same_meme("好多区", "区区区区"))  # written longer too
        self.assertFalse(variants.same_meme("41翻", "好多区"))
        self.assertFalse(variants.same_meme("41下", "41上"))  # not on the line
        self.assertEqual(variants.same_table(), [["41翻", "41上", "41中"], ["区区区", "区区", "好多区"]])
        found = variants.groups({"41翻": 1714, "41上": 636, "区区": 30, "好多区": 12})
        self.assertEqual([g["phrases"] for g in found], [["41翻", "41上"], ["区区", "好多区"]])
        time.sleep(0.01)
        with open(variants.SAME_FILE, "w", encoding="utf-8") as f:
            f.write("区区区 = 区区 = 好多区\n")  # an edited table counts at once
        self.assertFalse(variants.same_meme("41翻", "41上"))
        with open(variants.SAME_FILE, "w", encoding="utf-8") as f:
            f.write("41翻 = 41上\n区区 = 好多区\n41上 = 41中\n超威同意了 = 超威点头了\n")
        self.assertTrue(variants.same_meme("41翻", "41中"))  # lines with a phrase in common
        self.assertFalse(variants.same_meme("41翻", "区区"))
        self.assertTrue(variants.same_meme("三叔同意了", "三叔点头了"))  # a name is anyone
        self.assertFalse(variants.same_meme("三叔同意了", "三叔摇头了"))
        with open(variants.SAME_FILE, "w", encoding="utf-8") as f:
            f.write("41翻 41上\n")
        with self.assertRaisesRegex(variants.TableError, "line 1 .*41翻 = 41上"):
            variants.same_meme("41翻", "41上")
        os.remove(variants.SAME_FILE)
        self.assertEqual(variants.same_table(), [])

    def test_an_edited_table_counts_at_once(self):
        with open(self.table, "w", encoding="utf-8") as f:
            f.write("Chovy = 超威\n")
        self.assertFalse(variants.same_meme("超威同意了", "三叔不同意"))
        with open(self.table, "w", encoding="utf-8") as f:
            f.write("Chovy = 超威\nCanyon = 三叔\n")
        self.assertTrue(variants.same_meme("超威同意了", "三叔不同意"))


if __name__ == "__main__":
    unittest.main()
