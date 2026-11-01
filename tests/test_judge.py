"""Offline tests for the judge (judging.py).

They never ask a real model: a fake Ollama answers in the shapes the real one does. So these
check what the model is shown and how its answers become verdicts. Whether a real model
judges well is only shown by a real run (py -m memelearner judge).
"""

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

import memelearner
from memelearner import cli, judging, store, surf

DAY = 86400
AYIGU = "主播的口头禅，看到好操作时喊"
CHANT = "观众一起刷的口号，给喜欢的队伍打气"


class FakeModel:
    """Stands in for judging._ask: answers the explaining and the testing questions. 阿一古 is
    a meme whose meaning fits, fpx a name, 怎么翻 a meme whose meaning doesn't fit, 正义必胜 a
    chant that is only ever sent on its own, and the answer about 神中神 isn't JSON."""

    ANSWERS = {
        "阿一古": {"kind": "meme", "meaning": AYIGU, "english": "a streamer's catchphrase",
                "literal": "没有", "why": "句子里都这么用"},
        "fpx": {"kind": "name", "why": "FPX 是一支战队"},
        "怎么翻": {"kind": "meme", "meaning": "嘲笑一方已经输定了", "literal": "怎么翻盘",
                "why": "猜的"},
        "正义必胜": {"kind": "meme", "meaning": CHANT, "english": "justice will win",
                 "literal": "正义的一方一定会赢", "why": "很多人一起刷"},
    }

    def __init__(self):
        self.explained = {}  # phrase: the question it was asked
        self.tested = {}
        self.calls = 0

    def __call__(self, messages, model):
        self.calls += 1
        question = json.loads(messages[1]["content"])
        phrase = question["phrase"]
        if messages[0]["content"] == judging.EXPLAIN:
            self.explained[phrase] = question
            if phrase not in self.ANSWERS:
                return "Sorry, I can't tell."
            return json.dumps(self.ANSWERS[phrase], ensure_ascii=False)
        self.tested[phrase] = question
        meaning = "A" if question["A"] == self.ANSWERS[phrase]["meaning"] else "B"
        other = "B" if meaning == "A" else "A"
        if phrase == "阿一古":  # every line fits, except one the model can't tell
            return json.dumps({"answers": [meaning] * (len(question["lines"]) - 1) + ["unsure"]})
        return json.dumps({"answers": [other] * len(question["lines"])})  # none fits


class JudgeTest(unittest.TestCase):
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
                mock.patch.dict(os.environ, {surf.KNOWN_ENV: ""}),
                mock.patch.object(judging, "_ask", self.model),
                mock.patch.object(judging, "_get_json",
                                  lambda path: {"models": [{"name": "deepseek-r1:14b"}]})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.now = time.time()

    def write_run(self, people=None):
        """What a run read and found: lines using 阿一古, fpx, 怎么翻, 正义必胜 and 神中神 under
        two LoL videos, and the five as candidates."""
        lines = {
            "BV1aaaaaaaaa": ["阿一古阿一古 又来了", "这波阿一古了兄弟们", "阿一古！！！", "主播又在阿一古",
                             "阿一古来了家人们", "fpx加油", "FPX 这波可以", "这局怎么翻",
                             "怎么翻盘啊", "神中神啊这操作", "正义必胜", "正义必胜"],
            "BV1bbbbbbbbb": ["阿一古真的绷不住", "听到阿一古就想笑", "阿一古", "阿一古",
                             "又是一句阿一古", "阿一古永远的神", "fpx要赢了", "fpx这波有点东西",
                             "fpxs是谁", "还能怎么翻啊", "落后一万怎么翻", "这还怎么翻",
                             "神中神了家人们", "正义必胜正义必胜", "正义必胜！"],
        }
        videos = {}
        for bvid, texts in lines.items():
            videos[bvid] = {"game": "lol", "title": f"英雄联盟 {bvid[-1]} 组比赛",
                            "pubdate": int(self.now - DAY),
                            "url": f"https://www.bilibili.com/video/{bvid}"}
            messages = [{"id": f"{bvid}{n}", "kind": "danmu", "who": f"{bvid}{n}", "text": text,
                         "sent": int(self.now - DAY) + n} for n, text in enumerate(texts)]
            os.makedirs(os.path.dirname(surf.messages_path(bvid)), exist_ok=True)
            with open(surf.messages_path(bvid), "w", encoding="utf-8") as f:
                f.writelines(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
        surf._save_json(surf.VIDEOS_FILE, videos)
        people = people or {}
        surf._save_json(surf.CANDIDATES_FILE, {
            "made": "2026-11-01 05:00", "days": 7, "games": {}, "videos": videos,
            "candidates": [{"phrase": phrase, "game": "lol", "status": "first look",
                            "people": people.get(phrase, 5), "people_recent": 5,
                            "people_before": 0, "alone": 0.9, "videos": 2, "rise": None,
                            "score": 10.0,
                            "variants": [{"phrase": "正义必输", "people": 3}]
                            if phrase == "正义必胜" else [],
                            "examples": [{"text": phrase, "video": bvid, "kind": "danmu",
                                          "sent": "2026-10-31"} for bvid in videos]}
                           for phrase in ["阿一古", "fpx", "怎么翻", "正义必胜", "神中神"]]})

    def judge(self, **options):
        with contextlib.redirect_stdout(io.StringIO()):
            return {c["phrase"]: c["judge"] for c in memelearner.judge(**options)}

    def test_a_meme_is_explained_from_half_the_longer_lines_and_tested_on_the_rest(self):
        self.write_run()
        verdicts = self.judge()
        ayigu = verdicts["阿一古"]
        self.assertEqual((ayigu["verdict"], ayigu["kind"], ayigu["meaning"], ayigu["english"]),
                         ("meme", "meme", AYIGU, "a streamer's catchphrase"))
        self.assertEqual(ayigu["literal"], "")  # 没有: so the other choice was "a name"
        explained, tested = self.model.explained["阿一古"], self.model.tested["阿一古"]
        # Blind: the test saw none of the explaining lines, only longer lines
        self.assertFalse(set(explained["lines"]) & set(tested["lines"]))
        self.assertEqual(len(tested["lines"]), 4)  # 8 longer lines: half of them
        self.assertNotIn("阿一古", tested["lines"])
        self.assertIn("阿一古", explained["lines"])  # the bare line shows how it is sent
        self.assertIn(judging.NO_LITERAL, (tested["A"], tested["B"]))
        self.assertEqual(ayigu["test"], {"lines": 4, "fits": 3, "misses": 0})
        self.assertEqual(explained["game"], judging.GAMES["lol"])
        # The video it is used most in first
        self.assertEqual(explained["titles"], ["英雄联盟 b 组比赛", "英雄联盟 a 组比赛"])
        self.assertIn("5 个人单独发过，在 2 个视频里", explained["how"])
        self.assertIn("90%", explained["how"])

    def test_a_meme_only_ever_sent_on_its_own_is_untested(self):
        self.write_run()
        chant = self.judge()["正义必胜"]
        self.assertEqual((chant["verdict"], chant["meaning"]), ("untested", CHANT))
        self.assertNotIn("正义必胜", self.model.tested)  # no longer lines to test it on
        self.assertNotIn("test", chant)
        self.assertEqual(self.model.explained["正义必胜"]["variants"], ["正义必输"])
        # 正义必胜 is the same line as the newer 正义必胜！
        self.assertEqual(sorted(self.model.explained["正义必胜"]["lines"]),
                         ["正义必胜正义必胜", "正义必胜！"])

    def test_names_and_failed_or_unclear_meanings_are_not_memes(self):
        self.write_run()
        verdicts = self.judge()
        self.assertEqual((verdicts["fpx"]["verdict"], verdicts["fpx"]["kind"]),
                         ("not a meme", "name"))
        self.assertNotIn("fpx", self.model.tested)  # a name isn't tested
        self.assertEqual(verdicts["怎么翻"]["verdict"], "watching")
        self.assertIn("fit only 0 of", verdicts["怎么翻"]["why"])
        self.assertEqual((verdicts["神中神"]["verdict"], verdicts["神中神"]["kind"]),
                         ("watching", "unclear"))  # an answer that isn't JSON
        self.assertEqual(sorted(v["phrase"] for v in memelearner.watching()), ["怎么翻", "神中神"])

    def test_verdicts_are_kept_and_asked_again_only_with_more_to_go_on(self):
        self.write_run()
        self.judge()
        calls = self.model.calls
        self.judge()
        self.assertEqual(self.model.calls, calls)  # nothing new: nothing asked
        # A watching and an untested phrase that more people send now
        self.write_run(people={"怎么翻": 9, "正义必胜": 8})
        self.judge()
        self.assertEqual(set(self.model.explained), {"阿一古", "fpx", "怎么翻", "正义必胜", "神中神"})
        self.assertEqual(self.model.calls, calls + 2 + 1)  # explained (and tested) again
        self.judge(again=True)
        self.assertEqual(self.model.calls, calls + 3 + 7)  # everything again: 2 + 1 + 2 + 1 + 1
        with open(judging.judge_file(), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "judged": {"lol:fpx": {"verdict": "not a meme",
                                                            "model": "deepseek-r1:14b"}}}, f)
        self.assertEqual(judging.history()["judged"], {})  # an older version's: asked again

    def test_verdicts_go_with_the_candidates_and_accept_takes_the_meaning(self):
        self.write_run()
        self.judge()
        found = {c["phrase"]: c for c in memelearner.candidates()}
        self.assertEqual(found["阿一古"]["judge"]["verdict"], "meme")
        meme = memelearner.accept("阿一古")
        self.assertEqual((meme["meaning"], meme["english"]), (AYIGU, "a streamer's catchphrase"))
        self.assertEqual(memelearner.accept("阿一古", meaning="我自己的意思")["meaning"], "我自己的意思")
        self.assertEqual(memelearner.accept("阿一古")["meaning"], "我自己的意思")  # yours stays
        self.assertEqual(memelearner.accept("fpx")["meaning"], "")  # not a meme: no meaning
        self.assertEqual(memelearner.accept("正义必胜")["meaning"], CHANT)  # untested: you decide
        memelearner.reject("怎么翻", "an everyday phrase")
        self.assertEqual([v["phrase"] for v in memelearner.watching()], ["神中神"])

    def test_without_ollama_nothing_is_judged(self):
        self.write_run()
        before = surf._load_json(surf.CANDIDATES_FILE, {})

        def no_ollama(path):
            raise urllib.error.URLError("connection refused")

        with mock.patch.object(judging, "_get_json", no_ollama):
            with self.assertRaisesRegex(memelearner.ModelUnavailable, "isn't running"):
                self.judge()
        with self.assertRaisesRegex(memelearner.ModelUnavailable, "ollama pull qwen3:14b"):
            self.judge(model="qwen3:14b")
        self.assertEqual(self.model.calls, 0)
        self.assertEqual(surf._load_json(surf.CANDIDATES_FILE, {}), before)

    def test_the_lines_say_more_first_are_newest_first_and_take_turns_between_videos(self):
        self.write_run()
        lines = judging.usage_lines({("lol", "阿一古"), ("lol", "fpx"), ("valorant", "fpx")})
        self.assertEqual(lines[("lol", "阿一古")], [  # newest first, the videos taking turns
            "阿一古永远的神", "阿一古来了家人们", "又是一句阿一古", "主播又在阿一古", "听到阿一古就想笑",
            "这波阿一古了兄弟们", "阿一古真的绷不住", "阿一古阿一古 又来了",
            "阿一古"])  # then the bare lines: 阿一古 and 阿一古！！！ are the same line
        self.assertEqual(sorted(lines[("lol", "fpx")]),  # whole words only: not fpxs是谁
                         sorted(["fpx加油", "FPX 这波可以", "fpx要赢了", "fpx这波有点东西"]))
        self.assertNotIn(("valorant", "fpx"), lines)  # another game's lines don't count
        self.assertEqual(len(judging.usage_lines({("lol", "阿一古")}, limit=3)[("lol", "阿一古")]), 3)

    def test_the_longer_lines_are_split_between_explaining_and_testing(self):
        self.assertEqual(judging._split(["1", "2", "3", "4"]), (["1", "3"], ["2", "4"]))
        self.assertEqual(judging._split(["1", "2", "3"]), (["1"], ["2", "3"]))
        self.assertEqual(judging._split(["1", "2"]), (["1", "2"], []))  # too few to test
        self.assertTrue(judging._says_more("这还怎么翻", "怎么翻的"))
        self.assertFalse(judging._says_more("怎么翻的啊！", "怎么翻的"))

    def test_the_blind_test_reads_answers_written_in_different_ways(self):
        self.assertEqual([judging._pick(a) for a in ["A", " b", "A：这句是这个意思", "Both",
                                                     "（B）", "选A", "neither", None, 1]],
                         ["a", "b", "a", "both", "b", "unsure", "neither", "unsure", "unsure"])
        self.assertTrue(judging.passed({"lines": 4, "fits": 3, "misses": 1}))
        self.assertFalse(judging.passed({"lines": 4, "fits": 2, "misses": 1}))
        self.assertFalse(judging.passed({"lines": 4, "fits": 1, "misses": 0}))

    def run_cli(self, *args):
        printed = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(printed):
            try:
                cli.main(list(args))
            except SystemExit as stop:
                code = stop.code
        return code, printed.getvalue()

    def test_the_command_line_judges_and_shows_the_verdicts(self):
        self.write_run()
        code, printed = self.run_cli("judge")
        self.assertEqual(code, 0)
        self.assertIn(f"meme        阿一古  {AYIGU}", printed)
        self.assertIn("1 look like memes, 1 look like memes whose meaning couldn't be tested, "
                      "1 are names, game words or everyday words, 2 are on the watching list",
                      printed)
        code, printed = self.run_cli("candidates")
        self.assertIn(f"-> a meme: {AYIGU}", printed)
        self.assertIn(f"-> a meme, meaning not tested: {CHANT}", printed)
        self.assertIn("-> not a meme: a name", printed)
        self.assertIn("90% on its own", printed)
        self.assertIn("also 正义必输", printed)
        code, printed = self.run_cli("watching", "--json")
        self.assertEqual(sorted(v["phrase"] for v in json.loads(printed)), ["怎么翻", "神中神"])
        with mock.patch.object(judging, "_get_json", lambda path: {"models": []}):
            code, printed = self.run_cli("judge", "--again")
        self.assertEqual(code, 1)
        self.assertIn("ollama pull deepseek-r1:14b", printed)

    def test_learning_can_judge_what_it_found(self):
        self.write_run()
        found = memelearner.learn(games=["lol"], fetch=False, judge=True, quiet=True)
        self.assertTrue(found)
        self.assertIn("正义必胜", [c["phrase"] for c in found])  # sent on its own by 4 people
        self.assertTrue(all(c["judge"]["verdict"] in ("meme", "untested", "not a meme",
                                                      "watching") for c in found))


if __name__ == "__main__":
    unittest.main()
