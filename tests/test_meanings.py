"""Offline tests for merging your notes with the model's meanings (meanings.py).

A fake model answers in the shape a real one does, so these check what the model is shown,
what is asked again, and what is kept. Whether a real model merges well is only shown by a
real run.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import memelearner
from memelearner import cli, judging, meanings, store, surf

AGREED = "指GEN中单选手Chovy（超威）在关键比赛中发挥失常、让对手获胜；弹幕用它调侃Chovy是否“同意”输掉比赛。"
RAIN = "指比赛中某一路（常是上路或中路）频繁被击杀、崩盘，像下起雨一样不断发生。"
RAIN_NOTE = "这个梗一般只用在theshy选手或者类似theshy选手风格的选手进行比赛的场合"
WEI_NOTE = "这是梗”X出“， 出 读作 chu， 和畜生的畜同音，这个梗是咒骂某选手玩的像个畜生（表现不佳或者人品很差）"
CHOVY_NOTE = "chovy就是超威，和超威同意了是同一个梗"
DACHAO_NOTE = "”大超“是chovy选手的其中一个外号，这仍然是”XX同意了“的一个变体"


class FakeModel:
    """Stands in for judging._ask: answers the merging question with the next of answers[phrase]
    (the last one again when they run out), and keeps each question it was asked."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.asked = []  # (model, question), in order

    def __call__(self, messages, model):
        assert messages[0]["content"] == meanings.MERGE
        question = json.loads(messages[1]["content"])
        self.asked.append((model, question))
        given = self.answers.get(question["phrase"]) or [{"meaning": "", "english": ""}]
        n = sum(1 for _, q in self.asked if q["phrase"] == question["phrase"])
        return json.dumps(given[min(n, len(given)) - 1], ensure_ascii=False)

    def questions(self, phrase):
        return [question for _, question in self.asked if question["phrase"] == phrase]


class MeaningsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        surf_dir = os.path.join(folder.name, "surf")
        self.model = FakeModel()
        for patcher in (
                mock.patch.object(store, "MEMES_FILE", os.path.join(folder.name, "memes.json")),
                mock.patch.object(surf, "SURF_DIR", surf_dir),
                mock.patch.object(surf, "VIDEOS_FILE", os.path.join(surf_dir, "videos.json")),
                mock.patch.object(surf, "CANDIDATES_FILE",
                                  os.path.join(surf_dir, "candidates.json")),
                mock.patch.object(judging, "_ask", self.model),
                mock.patch.object(judging, "MODEL", "qwen3:14b"),
                mock.patch.object(judging, "_get_json",
                                  lambda path: {"models": [{"name": "qwen3:14b"}]}),
                mock.patch.object(judging, "RETRY_WAIT", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.known = {
            "超威同意了": {"meaning": AGREED},
            "chovy同意了": {"meaning": CHOVY_NOTE},
            "大超同意了": {"meaning": DACHAO_NOTE},
            "wei出": {"meaning": "这是梗X出", "notes": [WEI_NOTE]},
            "正义必胜": {"meaning": "更受观众喜爱的一方赢的时候刷"},
            "下雨了": {"meaning": RAIN},
        }

    def merge(self, phrase, note, meaning="", english="", **options):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = meanings.merge(phrase, note, meaning, english,
                                    known=options.pop("known", self.known), **options)
        self.out = out.getvalue()
        return result

    def test_a_note_says_which_learned_meme_it_is_the_same_as(self):
        self.assertEqual(meanings.same_as("chovy同意了", CHOVY_NOTE, self.known), ["超威同意了"])
        self.assertEqual(meanings.same_as("尺出", "来源同”wei出“", self.known), ["wei出"])
        # A pattern in quotes, with X standing for a name, covers the memes that fit it
        self.assertEqual(meanings.same_as("大超同意了", DACHAO_NOTE, self.known),
                         ["chovy同意了", "超威同意了"])
        # A meme a note only compares it with isn't the same meme, nor is the phrase itself
        self.assertEqual(meanings.same_as("舒服了", "像正义必胜那样，黑粉多的队伍输了的时候刷",
                                          self.known), [])
        self.assertEqual(meanings.same_as("wei出", WEI_NOTE, self.known), [])
        self.assertEqual(meanings.same_as("三叔不同意", "”三叔“ 指代的是canyon选手的外号，"
                                          "不同意是梗”XX不同意“的变体", self.known), [])

    def test_a_note_says_where_a_meme_comes_from(self):
        # Your notes in the IG vs GEN test
        self.assertEqual(meanings.source_in("阿一占", "”阿一古“的变体，占和古长得像，但是没有左边，"
                                            "用来嘲讽theshy选手有手伤"), "阿一古")
        self.assertEqual(meanings.source_in(
            "脖子捂汗", "tes曾经的战前宣言“搏至无憾”的变体，改成了同音字“脖子捂汗”调侃现在已经被打"
            "的满头大汗了。 “类似的还有”泊至武汉“，一样是同音字"), "搏至无憾")
        # Not a pattern, nor another way of writing the meme itself, nor a meme it only is
        self.assertEqual(meanings.source_in("桌子翻了", "跟皮包翻了一样，”XX翻了“的变体，XX可以是"
                                            "任何可以翻动的物品"), "")
        self.assertEqual(meanings.source_in("啊一占", "“阿一占”的变体，啊和阿基本是同一个字"), "")
        self.assertEqual(meanings.source_in("尺出", "来源同”wei出“", self.known), "")
        self.assertEqual(meanings.source_in("chovy同意了", "“chovy同意了”的变体",
                                            own=["超威同意了"]), "")
        self.assertEqual(meanings.source_in("开一局", "杜蕾斯在lpl的广告语里面有一个”开一局“"), "")
        # A learned meme a part of the note names, the longest one
        self.assertEqual(meanings.source_in("我们是路边", "从我们是冠军变来的",
                                            ["冠军", "我们是冠军"]), "我们是冠军")

    def test_learned_memes_that_look_alike_are_related(self):
        self.assertEqual(meanings.related("三叔不同意", self.known)[:1], ["大超同意了"])
        self.assertIn("超威同意了", meanings.related("三叔不同意", self.known))
        # wei出's note has the pattern "X出", which kanavi出 fits, though they share one character
        self.assertEqual(meanings.related("kanavi出", self.known), ["wei出"])
        self.assertEqual(meanings.related("下雨了", self.known), [])
        self.assertNotIn("超威同意了", meanings.related("三叔不同意", self.known,
                                                    leave_out=["超威同意了"]))

    def test_a_merged_meaning_keeps_the_names_and_quoted_words_of_the_note(self):
        self.assertEqual(meanings.kept("“三姆斯”仍然是 canyon选手的其中一个外号，canyon转会geng就像"
                                       "詹姆斯一样抱团。同时也是canyon的黑称，”X姆斯“同音”X母死“"),
                         ["canyon", "geng", "三姆斯"])
        self.assertEqual(meanings.kept(DACHAO_NOTE), ["chovy", "大超"])
        self.assertEqual(meanings.kept("just the same meaning as 卑鄙的外乡人, in short"), [])

    def test_the_model_merges_its_meaning_and_the_note(self):
        merged = "指比赛中某一路（常是上路）频繁被击杀、崩盘，像下雨一样；一般只用在theshy或打法像theshy的选手比赛时。"
        self.model.answers["下雨了"] = [{"meaning": merged, "english": "raining deaths on TheShy"}]
        result = self.merge("下雨了", RAIN_NOTE, RAIN, "raining deaths")
        self.assertEqual(result, {"phrase": "下雨了", "meaning": merged,
                                  "english": "raining deaths on TheShy", "note": RAIN_NOTE,
                                  "same": [], "model": "qwen3:14b", "missing": [], "why": ""})
        [question] = self.model.questions("下雨了")
        self.assertEqual((question["model"], question["english"], question["note"],
                          question["same"]), (RAIN, "raining deaths", RAIN_NOTE, []))

    def test_the_meaning_of_the_same_meme_goes_to_the_model(self):
        self.model.answers["chovy同意了"] = [{"meaning": "chovy就是超威：" + AGREED}]
        result = self.merge("chovy同意了", CHOVY_NOTE, "指 Chovy 认可或同意某件事")
        self.assertEqual(result["same"], ["超威同意了"])
        self.assertEqual(result["meaning"], "chovy就是超威：" + AGREED)
        [question] = self.model.questions("chovy同意了")
        self.assertEqual(question["same"], [{"phrase": "超威同意了", "meaning": AGREED}])
        self.assertNotIn("超威同意了", [r["phrase"] for r in question["related"]])

    def test_with_nothing_to_merge_with_the_note_is_the_meaning(self):
        # No meaning from the model (none, or one you marked wrong), and no same meme
        note = "就是一种对于gg的刷屏，表示想让现在正在进行的游戏赶紧结束"
        result = self.merge("ggg", note)
        self.assertEqual((result["meaning"], result["model"]), (note, ""))
        self.assertEqual(result["why"], "nothing to merge with: the note is the meaning")
        self.assertEqual(self.model.asked, [])
        # No note: the model's meaning stays as it is
        self.assertEqual(self.merge("下雨了", "", RAIN)["meaning"], RAIN)
        self.assertEqual(self.model.asked, [])

    def test_a_meaning_that_leaves_out_a_name_is_asked_for_again(self):
        self.model.answers["下雨了"] = [{"meaning": "某一路接连被击杀"},
                                     {"meaning": "某一路接连被击杀，一般只在theshy的比赛里刷"}]
        result = self.merge("下雨了", RAIN_NOTE, RAIN)
        self.assertEqual(result["meaning"], "某一路接连被击杀，一般只在theshy的比赛里刷")
        self.assertEqual(result["missing"], [])
        first, second = self.model.questions("下雨了")
        self.assertNotIn("missing", first)
        self.assertEqual(second["missing"], ["theshy"])
        # Still left out the second time: it says so
        self.model.asked.clear()
        self.model.answers["下雨了"] = [{"meaning": "某一路接连被击杀"}]
        result = self.merge("下雨了", RAIN_NOTE, RAIN)
        self.assertEqual((result["meaning"], result["missing"]), ("某一路接连被击杀", ["theshy"]))
        self.assertEqual(len(self.model.asked), 2)

    def test_a_long_answer_is_cut_at_the_end_of_a_sentence_or_clause(self):
        # The English glosses of a real run that were cut in the middle of a word at 200
        cut = 'This is the "X-chu" meme, used to curse a player for playing like a beast—i.e., ' \
              'performing badly or having a bad character; applied to "Chi," it means'
        self.assertEqual(meanings._clean(cut, 140), 'This is the "X-chu" meme, used to curse a '
                         'player for playing like a beast—i.e., performing badly or having a '
                         'bad character.')
        self.assertEqual(meanings._clean("A nickname. It is also a derogatory name, as X sounds "
                                         "like a curse", 42), "A nickname. It is also a derogatory name.")
        self.assertEqual(meanings._clean("指GEN中单选手Chovy发挥失常。弹幕用它调侃Chovy是否同意输掉比赛", 30),
                         "指GEN中单选手Chovy发挥失常。")
        self.assertEqual(meanings._clean("short enough", 40), "short enough")
        self.assertEqual(meanings._clean(["not text"], 40), "")

    def test_when_the_model_writes_no_meaning_the_note_is_kept(self):
        self.model.answers["下雨了"] = [{"meaning": ""}, {"oops": "not what was asked"}]
        result = self.merge("下雨了", RAIN_NOTE, RAIN)
        self.assertEqual((result["meaning"], result["model"]), (RAIN_NOTE, ""))
        self.assertEqual(result["why"], "the model wrote no meaning: the note is the meaning")
        self.assertEqual(len(self.model.asked), 2)

    def test_a_phrase_the_same_as_another_one_being_merged_is_merged_after_it(self):
        wei = "wei出：出读作chu，和畜同音，骂选手wei玩得像畜生。"
        self.model.answers = {"wei出": [{"meaning": wei}],
                              "尺出": [{"meaning": "和wei出同源，出读作chu：骂尺帝玩得像畜生。"}]}
        known = {"超威同意了": {"meaning": AGREED}, "wei出": {"meaning": WEI_NOTE}}
        with contextlib.redirect_stdout(io.StringIO()):
            results = meanings.merge_all([
                {"phrase": "尺出", "note": "来源同”wei出“", "meaning": ""},  # you marked it wrong
                {"phrase": "wei出", "note": WEI_NOTE, "meaning": "对选手 Wei 的调侃称呼"}],
                known=known)
        self.assertEqual([r["phrase"] for r in results], ["尺出", "wei出"])
        self.assertEqual([q["phrase"] for _, q in self.model.asked], ["wei出", "尺出"])
        self.assertEqual(self.model.questions("尺出")[0]["same"],
                         [{"phrase": "wei出", "meaning": wei}])
        self.assertEqual(results[0]["same"], ["wei出"])
        self.assertEqual(results[0]["missing"], [])

    def test_when_deepseek_cant_be_reached_qwen3_merges(self):
        self.model.answers["下雨了"] = [{"meaning": "一般只在theshy的比赛里刷：某一路接连被击杀"}]
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            result = self.merge("下雨了", RAIN_NOTE, RAIN, model="deepseek/deepseek-flash")
        self.assertEqual(result["model"], "qwen3:14b")
        self.assertEqual([model for model, _ in self.model.asked], ["qwen3:14b"])
        self.assertIn("So qwen3:14b, the model on this computer, merges the meanings instead.",
                      self.out)
        # ... and when it can't be asked either, nothing is merged
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}), \
                mock.patch.object(judging, "_get_json", lambda path: {"models": []}):
            with self.assertRaises(memelearner.ModelUnavailable):
                self.merge("下雨了", RAIN_NOTE, RAIN, model="deepseek/deepseek-flash")

    def test_accept_merges_a_note_and_keeps_it_as_you_wrote_it(self):
        merged = "某一路接连被击杀，一般只在theshy或打法像theshy的选手的比赛里刷。"
        self.model.answers["下雨了"] = [{"meaning": merged, "english": "lane keeps dying"}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["accept", "下雨了", "--meaning", RAIN, "--english", "raining deaths"])
            cli.main(["accept", "下雨了", "--note", RAIN_NOTE])
        meme = store.load()["memes"]["下雨了"]
        self.assertEqual((meme["meaning"], meme["english"], meme["notes"]),
                         (merged, "lane keeps dying", [RAIN_NOTE]))
        [question] = self.model.questions("下雨了")
        self.assertEqual(question["model"], RAIN)  # the learned meaning was merged with the note
        self.assertIn(merged, out.getvalue())
        self.assertNotIn("Check its meaning", out.getvalue())
        # Other apps get the merged meaning, without the notes
        self.assertEqual(memelearner.lookup("下雨了")["meaning"], merged)
        self.assertNotIn("notes", memelearner.lookup("下雨了"))

    def test_accept_says_what_the_meaning_leaves_out_and_learns_nothing_without_a_model(self):
        self.model.answers["下雨了"] = [{"meaning": "某一路接连被击杀"}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["accept", "下雨了", "--meaning", RAIN, "--note", RAIN_NOTE])
        self.assertIn("Check its meaning: it leaves out theshy of your note.", out.getvalue())
        with mock.patch.object(judging, "_get_json", lambda path: {"models": []}):
            with self.assertRaises(SystemExit) as stopped:
                cli.main(["accept", "超威同意了", "--meaning", AGREED, "--note", "大超也是他"])
        self.assertIn("Nothing was learned", str(stopped.exception))
        self.assertIsNone(memelearner.lookup("超威同意了"))
