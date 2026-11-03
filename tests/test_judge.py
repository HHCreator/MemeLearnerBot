"""Offline tests for the judge (judging.py).

They never ask a real model: a fake Ollama, and a fake DeepSeek API, answer in the shapes
the real ones do. So these check what the model is shown and how its answers become
verdicts. Whether a real model judges well is only shown by a real run (py -m memelearner
judge, or compare to see several side by side).
"""

import collections
import contextlib
import io
import itertools
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

import memelearner
from memelearner import cli, judging, store, surf

ASK = judging._ask  # the real one, which the tests of online models use
KEY = "sk-test-0123456789"  # a made-up DeepSeek API key
DAY = 86400
AYIGU = "主播的口头禅，看到好操作时喊"
CHANT = "观众一起刷的口号，给喜欢的队伍打气"
SNOW = "「这是雪豹」的谐音，拿来开玩笑"


class FakeModel:
    """Stands in for judging._ask: answers the explaining and the testing questions. 阿一古 is
    a meme whose meaning fits, fpx a name, 怎么翻 a meme whose meaning doesn't fit, 正义必胜 a
    chant that is only ever sent on its own, 芝士雪豹 a meme someone explains, 吓哭了 a meme
    the model can't explain, and the answer about 神中神 isn't JSON."""

    ANSWERS = {
        "阿一古": {"kind": "meme", "meaning": AYIGU, "english": "a streamer's catchphrase",
                "literal": "没有", "why": "句子里都这么用"},
        "fpx": {"kind": "name", "why": "FPX 是一支战队"},
        "怎么翻": {"kind": "meme", "meaning": "嘲笑一方已经输定了", "literal": "怎么翻盘",
                "why": "猜的"},
        "正义必胜": {"kind": "meme", "meaning": CHANT, "english": "justice will win",
                 "literal": "正义的一方一定会赢", "why": "很多人一起刷"},
        "芝士雪豹": {"kind": "meme", "meaning": SNOW, "english": "a pun on 'this is a snow leopard'",
                 "literal": "奶酪做的雪豹", "why": "有人解释了它是谐音"},
        "吓哭了": {"kind": "meme", "meaning": "", "english": "scared to tears", "literal": "",
                "why": "大家一起刷，但看不出在说什么"},
    }

    def __init__(self):
        self.explained = {}  # phrase: the question it was asked
        self.tested = {}
        self.calls = 0
        self.asked = []  # (model, "explain" or "test", phrase, question), in order
        self.answers_for = {}  # {model: {phrase: answer}}: what another model says instead
        self.stop_after = {}  # {model: n}: that model stops answering after n questions,
        self.stop_error = judging.ModelUnavailable("Ollama answered 500: out of memory")  # so

    def __call__(self, messages, model):
        self.calls += 1
        if len([m for m, *_ in self.asked if m == model]) >= self.stop_after.get(model, 1e9):
            raise self.stop_error
        question = json.loads(messages[1]["content"])
        phrase = question["phrase"]
        answers = dict(self.ANSWERS, **self.answers_for.get(model, {}))
        explaining = messages[0]["content"] == judging.EXPLAIN
        self.asked.append((model, "explain" if explaining else "test", phrase, question))
        if explaining:
            self.explained[phrase] = question
            if phrase not in answers:
                return "Sorry, I can't tell."
            return json.dumps(answers[phrase], ensure_ascii=False)
        self.tested[phrase] = question
        meaning = "A" if question["A"] == answers[phrase]["meaning"] else "B"
        other = "B" if meaning == "A" else "A"
        if phrase == "阿一古":  # every line fits, except one the model can't tell
            return json.dumps({"answers": [meaning] * (len(question["lines"]) - 1) + ["unsure"]})
        return json.dumps({"answers": [other] * len(question["lines"])})  # none fits


class FakeDeepSeek:
    """Stands in for urllib.request.urlopen when the judge asks DeepSeek's API: it lists the
    models and answers chats the way DeepSeek does, with a FakeModel's answers. failures are
    how it answers the first requests instead: an HTTP error code, "blank" (a blank answer,
    which DeepSeek sends when the model didn't write JSON), "filtered" (turned down by its
    content filter) or None (the usual answer)."""

    def __init__(self, model, failures=(), models=("deepseek-flash", "deepseek-v4-pro")):
        self.model, self.failures, self.models = model, list(failures), models
        self.requests = []  # (url, headers, body sent)

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append((request.full_url, dict(request.header_items()), body))
        failure = self.failures.pop(0) if self.failures else None
        if isinstance(failure, int):
            raise urllib.error.HTTPError(request.full_url, failure, "no", {}, io.BytesIO(
                json.dumps({"error": {"message": f"it said {failure}"}}).encode("utf-8")))
        if failure == "blank":
            return self.answer({"choices": [{"message": {"content": "   "}}]})
        if failure == "filtered":  # what DeepSeek's content filter turns down
            return self.answer({"choices": [{"message": {"content": ""},
                                             "finish_reason": "content_filter"}]})
        if request.full_url.endswith("/models"):
            return self.answer({"object": "list", "data": [
                {"id": name, "object": "model", "owned_by": "deepseek"} for name in self.models]})
        return self.answer({
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": self.model(body["messages"], body["model"])}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                      "prompt_cache_hit_tokens": 600, "prompt_cache_miss_tokens": 400}})

    @staticmethod
    def answer(data):
        return io.BytesIO(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def chats(self):
        return [body for url, _, body in self.requests if url.endswith("/chat/completions")]


class JudgeTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        surf_dir = os.path.join(folder.name, "surf")
        self.model = FakeModel()
        self.awake = []  # what Windows was told: stay awake, may sleep
        for patcher in (
                mock.patch.object(store, "MEMES_FILE", os.path.join(folder.name, "memes.json")),
                mock.patch.object(surf, "SURF_DIR", surf_dir),
                mock.patch.object(surf, "VIDEOS_FILE", os.path.join(surf_dir, "videos.json")),
                mock.patch.object(surf, "CANDIDATES_FILE",
                                  os.path.join(surf_dir, "candidates.json")),
                mock.patch.dict(os.environ, {surf.KNOWN_ENV: ""}),
                mock.patch.object(judging, "_ask", self.model),
                mock.patch.object(judging, "_get_json",
                                  lambda path: {"models": [{"name": "deepseek-r1:14b"}]}),
                mock.patch.object(judging, "usage", collections.defaultdict(collections.Counter)),
                mock.patch.object(judging, "_no_json_mode", set()),
                mock.patch.object(judging, "RETRY_WAIT", 0),
                mock.patch.object(judging, "_execution_state", self.awake.append)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.now = time.time()

    def write_run(self, people=None, more=None):
        """What a run read and found: lines using 阿一古, fpx, 怎么翻, 正义必胜 and 神中神 under
        two LoL videos, and the five as candidates; more: {phrase: (its lines in the first
        video, in the second)} for more candidates."""
        lines = {
            "BV1aaaaaaaaa": ["阿一古阿一古 又来了", "这波阿一古了兄弟们", "阿一古！！！", "主播又在阿一古",
                             "阿一古来了家人们", "fpx加油", "FPX 这波可以", "这局怎么翻",
                             "怎么翻盘啊", "神中神啊这操作", "正义必胜", "正义必胜"],
            "BV1bbbbbbbbb": ["阿一古真的绷不住", "听到阿一古就想笑", "阿一古", "阿一古",
                             "又是一句阿一古", "阿一古永远的神", "fpx要赢了", "fpx这波有点东西",
                             "fpxs是谁", "还能怎么翻啊", "落后一万怎么翻", "这还怎么翻",
                             "神中神了家人们", "正义必胜正义必胜", "正义必胜！"],
        }
        for in_a, in_b in (more or {}).values():
            lines["BV1aaaaaaaaa"] += in_a
            lines["BV1bbbbbbbbb"] += in_b
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
                           for phrase in ["阿一古", "fpx", "怎么翻", "正义必胜", "神中神"]
                           + list(more or {})]})

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
        # Nothing showed the model what it means: the meaning is only a guess
        self.assertEqual((chant["guess"], chant["explanations"]), (True, []))
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
        self.assertEqual(memelearner.accept("正义必胜")["meaning"], "")  # only a guess: not taken
        memelearner.reject("怎么翻", "an everyday phrase")
        self.assertEqual([v["phrase"] for v in memelearner.watching()], ["神中神"])

    def test_lines_that_explain_a_phrase_are_told_apart_from_questions_and_opinions(self):
        for line in ["芝士雪豹是这是雪豹的谐音", "阿一古的意思是韩语的哎呦", "出自去年的世界赛",
                     "yyds=永远的神", "阿一古是什么意思？韩语哎呦的意思"]:
            self.assertTrue(judging.explains(line), line)
        for line in ["芝士雪豹是什么梗？", "求科普阿一古", "阿一古的意思是？", "这就是阿一古的意思吗",
                     "我的意思是阿一古太强了", "阿一古！这波是指挥的问题", "阿一古阿一古 又来了"]:
            self.assertFalse(judging.explains(line), line)

    def test_a_meaning_someone_explains_goes_to_the_model_and_accept_takes_it(self):
        self.write_run(more={"芝士雪豹": (["芝士雪豹是这是雪豹的谐音", "芝士雪豹", "芝士雪豹是什么梗？"],
                                       ["芝士雪豹！", "我的意思是芝士雪豹太好笑了"])})
        lines = judging.usage_lines({("lol", "芝士雪豹")})[("lol", "芝士雪豹")]
        self.assertEqual(lines, ["芝士雪豹是这是雪豹的谐音",  # the line that explains it first
                                 "我的意思是芝士雪豹太好笑了", "芝士雪豹是什么梗？", "芝士雪豹！"])
        snow = self.judge()["芝士雪豹"]
        asked = self.model.explained["芝士雪豹"]
        self.assertEqual(asked["explanations"], ["芝士雪豹是这是雪豹的谐音"])
        self.assertNotIn("芝士雪豹是这是雪豹的谐音", asked["lines"])
        # Two other longer lines are too few to test on, but the meaning isn't just a guess
        self.assertEqual((snow["verdict"], snow["meaning"], snow["guess"]),
                         ("untested", SNOW, False))
        self.assertIn("from 1 line explaining it, like 芝士雪豹是这是雪豹的谐音",
                      judging.untested(snow))
        self.assertEqual(memelearner.accept("芝士雪豹")["meaning"], SNOW)

    def test_a_meme_the_model_cant_explain_has_no_meaning(self):
        self.write_run(more={"吓哭了": (["吓哭了", "吓哭了吓哭了"], ["吓哭了！", "真的吓哭了"])})
        scared = self.judge()["吓哭了"]
        self.assertEqual((scared["verdict"], scared["meaning"], scared["english"], scared["guess"]),
                         ("untested", "", "", False))
        self.assertEqual(judging.untested(scared),
                         "meaning unknown: nobody explains it in what the bot read")
        self.assertEqual(memelearner.accept("吓哭了")["meaning"], "")

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
        self.assertIn("1 look like memes, 1 look like memes whose meaning couldn't be tested (1 "
                      "of them with only a guess or no meaning), 1 are names, game words or "
                      "everyday words, 2 are on the watching list", printed)
        self.assertIn(f"untested    正义必胜  only the model's guess: {CHANT}", printed)
        code, printed = self.run_cli("candidates")
        self.assertIn(f"-> a meme: {AYIGU}", printed)
        self.assertIn(f"-> a meme, only the model's guess: {CHANT}", printed)
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

    # -- online models --------------------------------------------------------------------

    @contextlib.contextmanager
    def online(self, api, key=KEY):
        """Judge with the real _ask(), against a fake DeepSeek API, with key as the key."""
        with mock.patch.object(judging, "_ask", ASK), \
                mock.patch.object(urllib.request, "urlopen", api), \
                mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": key}):
            yield

    def test_an_online_model_is_asked_through_its_api_with_the_key_from_the_environment(self):
        self.write_run()
        api = FakeDeepSeek(self.model)
        with self.online(api):
            verdicts = self.judge(model="deepseek/deepseek-flash")
        # The same verdicts as from Ollama, as the same answers came back
        self.assertEqual((verdicts["阿一古"]["verdict"], verdicts["阿一古"]["meaning"]),
                         ("meme", AYIGU))
        self.assertEqual(verdicts["fpx"]["verdict"], "not a meme")
        self.assertEqual(verdicts["阿一古"]["model"], "deepseek/deepseek-flash")
        # First it checks the key and the model, then it asks
        self.assertEqual(api.requests[0][0], "https://api.deepseek.com/models")
        self.assertEqual({url for url, _, _ in api.requests[1:]},
                         {"https://api.deepseek.com/chat/completions"})
        self.assertTrue(all(headers["Authorization"] == f"Bearer {KEY}"
                            for _, headers, _ in api.requests))
        chat = api.chats()[0]
        self.assertEqual(chat["model"], "deepseek-flash")  # its name at DeepSeek
        self.assertEqual(chat["response_format"], {"type": "json_object"})
        self.assertEqual(chat["thinking"], {"type": "disabled"})
        self.assertEqual(chat["messages"][0]["content"], judging.EXPLAIN)
        self.assertEqual(json.loads(chat["messages"][1]["content"]),
                         self.model.explained["阿一古"])
        answers = len(api.chats())
        self.assertEqual(dict(judging.usage["deepseek/deepseek-flash"]), {
            "calls": answers, "input": 1000 * answers, "cached": 600 * answers,
            "output": 100 * answers})
        # The key is never written down
        for path in (judging.judge_file(), surf.CANDIDATES_FILE):
            with open(path, encoding="utf-8") as f:
                self.assertNotIn(KEY, f.read())

    def test_an_online_model_needs_a_key_that_works_and_a_model_its_api_has(self):
        self.write_run()
        api = FakeDeepSeek(self.model)
        with self.online(api, key=" "):
            with self.assertRaisesRegex(memelearner.ModelUnavailable, "put your API key in "
                                        "the DEEPSEEK_API_KEY environment variable"):
                self.judge(model="deepseek/deepseek-flash")
        self.assertEqual(api.requests, [])  # nothing is sent without a key
        for failures, said in (([401], "the API key in DEEPSEEK_API_KEY was refused"),
                               ([None, 402], "the balance has run out"),
                               ([503] * 4, "api.deepseek.com is busy .HTTP 503: .*asked 4 "
                                           "times")):
            with self.online(FakeDeepSeek(self.model, failures)):
                with self.assertRaisesRegex(memelearner.ModelUnavailable, said):
                    self.judge(model="deepseek/deepseek-flash")
        with self.online(FakeDeepSeek(self.model)):
            with self.assertRaisesRegex(memelearner.ModelUnavailable, "has no model deepseek-v9; "
                                        "it has deepseek-flash, deepseek-v4-pro"):
                self.judge(model="deepseek/deepseek-v9")
        # A DeepSeek name without the provider is an Ollama model that isn't there
        with self.assertRaisesRegex(memelearner.ModelUnavailable, "ollama pull deepseek-flash "
                                    r"\(or, for DeepSeek's online model, --model "
                                    r"deepseek/deepseek-flash\)"):
            self.judge(model="deepseek-flash")
        self.assertEqual(self.model.calls, 0)

    def test_a_busy_online_model_or_a_blank_answer_is_asked_again(self):
        self.write_run()
        # The model list: busy once; the first question: busy, then a blank answer
        api = FakeDeepSeek(self.model, [503, None, 429, "blank"])
        with self.online(api):
            verdicts = self.judge(model="deepseek/deepseek-flash")
        self.assertEqual(verdicts["阿一古"]["verdict"], "meme")
        self.assertEqual(len(api.requests), 2 + 3 + self.model.calls - 1)
        # What its content filter turns down isn't asked again: it would be turned down again
        api = FakeDeepSeek(self.model, [None, "filtered"])
        with self.online(api):
            verdicts = self.judge(model="deepseek/deepseek-flash", again=True)
        self.assertEqual((verdicts["阿一古"]["verdict"], verdicts["阿一古"]["kind"]),
                         ("watching", "unclear"))
        self.assertEqual(verdicts["fpx"]["verdict"], "not a meme")
        self.assertEqual(len(api.chats()), 1 + 1 + 2 + 1 + 1)  # 阿一古 once, then as before

    def test_an_api_that_turns_json_mode_down_is_asked_without_it(self):
        self.write_run()
        api = FakeDeepSeek(self.model, [None, 400])
        with self.online(api):
            verdicts = self.judge(model="deepseek/deepseek-flash")
        self.assertEqual(verdicts["阿一古"]["verdict"], "meme")
        chats = api.chats()
        self.assertIn("response_format", chats[0])
        self.assertTrue(all("response_format" not in chat for chat in chats[1:]))

    def test_judging_with_an_online_model_says_how_many_tokens_it_used(self):
        self.write_run()
        with self.online(FakeDeepSeek(self.model)):
            code, printed = self.run_cli("judge", "--model", "deepseek/deepseek-flash")
        self.assertEqual(code, 0)
        self.assertIn(f"meme        阿一古  {AYIGU}", printed)
        answers = self.model.calls
        self.assertIn(f"deepseek/deepseek-flash read {1000 * answers:,} tokens "
                      f"({600 * answers:,} of them cached, which cost less) and wrote "
                      f"{100 * answers:,}, in {answers} answers.", printed)

    # -- comparing models -----------------------------------------------------------------

    def two_models(self):
        return mock.patch.object(judging, "_get_json", lambda path: {"models": [
            {"name": "deepseek-r1:14b"}, {"name": "qwen3:14b"}]})

    def test_compare_asks_each_model_about_the_same_lines_and_leaves_the_verdicts_alone(self):
        self.write_run()
        self.judge()  # judge.json and the candidates' verdicts, from deepseek-r1:14b
        kept = {}
        for path in (judging.judge_file(), surf.CANDIDATES_FILE):
            with open(path, encoding="utf-8") as f:
                kept[path] = f.read()
        self.model.asked.clear()
        self.model.answers_for["qwen3:14b"] = {"阿一古": {"kind": "everyday", "why": "就是感叹"}}
        with self.two_models(), contextlib.redirect_stdout(io.StringIO()) as printed:
            result = judging.compare(["deepseek-r1:14b", "qwen3:14b", "llama9:70b",
                                      "qwen3:14b"])
        self.assertIn("Leaving out llama9:70b: the model llama9:70b isn't downloaded",
                      printed.getvalue())
        self.assertEqual(result["models"], ["deepseek-r1:14b", "qwen3:14b"])
        # One model after the other, each about every phrase, from the same lines
        self.assertEqual([model for model, _ in itertools.groupby(m for m, *_ in self.model.asked)],
                         ["deepseek-r1:14b", "qwen3:14b"])
        questions = collections.defaultdict(dict)
        for model, kind, phrase, question in self.model.asked:
            questions[model][(kind, phrase)] = question
        self.assertEqual(len([k for k in questions["qwen3:14b"] if k[0] == "explain"]), 5)
        for (kind, phrase), question in questions["qwen3:14b"].items():
            self.assertEqual(question, questions["deepseek-r1:14b"][(kind, phrase)])
        verdicts = result["verdicts"]
        self.assertEqual(verdicts["deepseek-r1:14b"]["lol:阿一古"]["verdict"], "meme")
        self.assertEqual(verdicts["qwen3:14b"]["lol:阿一古"]["verdict"], "not a meme")
        self.assertEqual(result["stats"]["qwen3:14b"]["calls"], 0)  # Ollama's count; faked here
        # judge's verdicts are as they were
        for path, text in kept.items():
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), text)
        self.assertEqual(surf._load_json(judging.comparison_file(), {})["models"],
                         ["deepseek-r1:14b", "qwen3:14b"])
        with open(judging.comparison_file("md"), encoding="utf-8") as f:
            report = f.read()
        self.assertIn("| deepseek-r1:14b | 1 | 1 | 1 | 2 |", report)
        self.assertIn("| qwen3:14b | 0 | 1 | 2 | 2 |", report)
        self.assertIn("The models call 1 of the 5 phrases different kinds of phrase", report)
        self.assertIn("### 阿一古 ≠", report)
        self.assertIn("### fpx\n", report)
        self.assertIn(f"- **deepseek-r1:14b**: meme, {AYIGU} / a streamer's catchphrase  "
                      "(blind test: 3 of 4 lines)", report)
        self.assertIn("- **qwen3:14b**: not a meme, an everyday word: 就是感叹", report)
        self.assertIn("5 people send it on its own (90% of those who use it), in 2 videos; "
                      "also 正义必输", report)

    def test_the_command_line_compares_the_top_phrases_and_keeps_them_when_a_model_stops(self):
        self.write_run()
        self.model.stop_after["qwen3:14b"] = 2
        with self.two_models():
            code, printed = self.run_cli("compare", "deepseek-r1:14b", "qwen3:14b", "--top", "3")
        self.assertEqual(code, 0)
        self.assertIn("Judging 3 phrases with deepseek-r1:14b, qwen3:14b", printed)
        self.assertIn("qwen3:14b stopped answering: Ollama answered 500", printed)
        self.assertIn(f"side by side: {judging.comparison_file('md')}", printed)
        result = surf._load_json(judging.comparison_file(), {})
        self.assertEqual([c["phrase"] for c in result["candidates"]], ["阿一古", "fpx", "怎么翻"])
        self.assertEqual(len(result["verdicts"]["deepseek-r1:14b"]), 3)
        self.assertEqual(list(result["verdicts"]["qwen3:14b"]), ["lol:阿一古"])  # before it stopped
        with open(judging.comparison_file("md"), encoding="utf-8") as f:
            self.assertIn("qwen3:14b stopped answering: Ollama answered 500", f.read())
        code, printed = self.run_cli("compare", "llama9:70b")
        self.assertEqual(code, "Couldn't compare: none of the models can be asked.")
        self.assertIn("Leaving out llama9:70b", printed)

    def test_compare_can_add_models_to_the_last_comparison_of_the_same_phrases(self):
        self.write_run()
        with self.two_models():
            self.run_cli("compare", "deepseek-r1:14b")
            self.model.asked.clear()
            code, printed = self.run_cli("compare", "qwen3:14b", "--add")
        self.assertEqual({model for model, *_ in self.model.asked}, {"qwen3:14b"})
        result = surf._load_json(judging.comparison_file(), {})
        self.assertEqual(result["models"], ["deepseek-r1:14b", "qwen3:14b"])
        self.assertEqual(len(result["verdicts"]["deepseek-r1:14b"]), 5)
        self.assertIn("deepseek-r1:14b", printed.split("minutes")[1])  # in the summary too
        with open(judging.comparison_file("md"), encoding="utf-8") as f:
            self.assertIn("# The judge with 2 models, side by side", f.read())
        # Other phrases: a new comparison
        with self.two_models():
            code, printed = self.run_cli("compare", "qwen3:14b", "--add", "--top", "2")
        self.assertIn("The last comparison was of other phrases, so it isn't kept.", printed)
        self.assertEqual(surf._load_json(judging.comparison_file(), {})["models"], ["qwen3:14b"])

    def test_a_stopped_comparison_carries_on_where_it_stopped_and_windows_stays_awake(self):
        self.write_run()
        self.model.stop_after["qwen3:14b"] = 3
        self.model.stop_error = KeyboardInterrupt()  # the run is stopped, or the computer is
        with self.two_models():
            code, printed = self.run_cli("compare", "deepseek-r1:14b", "qwen3:14b")
        self.assertIn("Stopped. What was judged so far is in", str(code))
        self.assertEqual(self.awake, [judging.STAY_AWAKE, judging.MAY_SLEEP])
        saved = surf._load_json(judging.comparison_file(), {})
        self.assertEqual(len(saved["verdicts"]["deepseek-r1:14b"]), 5)
        self.assertEqual(list(saved["verdicts"]["qwen3:14b"]), ["lol:阿一古", "lol:fpx"])
        # Again with --add: deepseek-r1:14b has judged them all, and qwen3:14b carries on
        self.model.asked.clear()
        self.model.stop_after.clear()
        with self.two_models():
            code, printed = self.run_cli("compare", "deepseek-r1:14b", "qwen3:14b", "--add")
        self.assertEqual(code, 0)
        self.assertIn("qwen3:14b: 2 phrases judged before, 3 to go", printed)
        self.assertEqual({(model, phrase) for model, kind, phrase, _ in self.model.asked
                          if kind == "explain"},
                         {("qwen3:14b", phrase) for phrase in ("怎么翻", "正义必胜", "神中神")})
        saved = surf._load_json(judging.comparison_file(), {})
        self.assertEqual(saved["models"], ["deepseek-r1:14b", "qwen3:14b"])
        self.assertEqual(len(saved["verdicts"]["qwen3:14b"]), 5)
        self.assertNotIn("stopped", saved["stats"]["qwen3:14b"])

    def test_judging_keeps_windows_awake_until_it_is_done(self):
        self.write_run()
        self.run_cli("judge")
        self.assertEqual(self.awake, [judging.STAY_AWAKE, judging.MAY_SLEEP])

    def test_compare_works_with_online_models_too(self):
        self.write_run()
        with self.online(FakeDeepSeek(self.model)), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            result = judging.compare(["deepseek/deepseek-flash", "deepseek/deepseek-v4-pro"],
                                     top=2)
        self.assertEqual(result["models"], ["deepseek/deepseek-flash", "deepseek/deepseek-v4-pro"])
        stats = result["stats"]["deepseek/deepseek-v4-pro"]
        self.assertEqual((stats["calls"], stats["input"], stats["cached"], stats["output"]),
                         (3, 3000, 1800, 300))  # 阿一古 explained and tested, fpx explained
        self.assertIn("deepseek/deepseek-v4-pro read 3,000 tokens", printed.getvalue())



class AwakeTest(unittest.TestCase):
    def test_telling_windows_to_stay_awake_works_and_does_nothing_elsewhere(self):
        # On Windows (as on GitHub's Windows runner) this really calls Windows
        judging._execution_state(judging.STAY_AWAKE)
        judging._execution_state(judging.MAY_SLEEP)
        with judging.awake():
            pass


if __name__ == "__main__":
    unittest.main()
