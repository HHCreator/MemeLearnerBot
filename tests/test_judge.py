"""Offline tests for the judge (judging.py) and the wiki look-ups (wiki.py).

They never visit a wiki or ask a real model: a fake Ollama answers in the shapes the real
one does, and a fake wiki in the shapes of the MediaWiki API. So these check what the model
is shown and how its answers become verdicts. Whether a real model judges well, and whether
the wikis answer the bot, is only shown by a real run (py -m memelearner judge).
"""

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
from unittest import mock

import memelearner
from memelearner import cli, judging, store, surf, wiki

DAY = 86400
AYIGU = "主播的口头禅，看到好操作时喊"


class FakeModel:
    """Stands in for judging._ask: answers the explaining and the testing questions. 阿一古 is
    a meme whose meaning fits, fpx a name, 怎么翻 a meme whose meaning doesn't fit, and the
    answer about 神中神 isn't JSON."""

    ANSWERS = {
        "阿一古": {"kind": "meme", "meaning": AYIGU, "english": "a streamer's catchphrase",
                "literal": "没有", "why": "资料和句子都这么说", "used": [1, "2", 9, True, 1]},
        "fpx": {"kind": "name", "why": "FPX 是一支战队", "used": [1]},
        "怎么翻": {"kind": "meme", "meaning": "嘲笑一方已经输定了", "literal": "怎么翻盘",
                "why": "猜的"},
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


class FakeWiki:
    """Stands in for wiki._fetch: 萌娘百科 has a page on 阿一古 but makes no extracts, and
    refuses anything else; 维基百科 has a page on FPX."""

    def __init__(self):
        self.calls = []

    def __call__(self, url):
        parsed = urllib.parse.urlsplit(url)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.calls.append((parsed.netloc, params))
        if parsed.netloc == "zh.moegirl.org.cn":
            if params["action"] == "parse" and params["page"] == "阿一古":
                return json.dumps({"parse": {"title": "阿一古", "text": (
                    '<div class="mw-parser-output"><table class="infobox"><tr><td>'
                    "<p>信息框里的阿一古</p></td></tr></table><p>阿一古是主播<b>某人</b>"
                    "的口头禅<sup>[1]</sup>。</p><p>常在直播里喊。</p></div>")}},
                    ensure_ascii=False)
            if params["action"] == "query" and params["gsrsearch"] == "阿一古":
                return json.dumps({"query": {
                    "search": [{"title": "某主播", "snippet": "某主播是一位主播"},
                               {"title": "某游戏", "snippet":
                                "玩家常说<span class=\"searchmatch\">阿一古</span>&quot;"},
                               {"title": "阿一古", "snippet": "阿一古是"}],
                    "pages": [
                        {"pageid": 2, "title": "某主播", "index": 1,
                         "fullurl": "https://zh.moegirl.org.cn/某主播"},
                        {"pageid": 3, "title": "某游戏", "index": 3,
                         "fullurl": "https://zh.moegirl.org.cn/某游戏"},
                        {"pageid": 1, "title": "阿一古", "index": 2,
                         "fullurl": "https://zh.moegirl.org.cn/阿一古"}]}}, ensure_ascii=False)
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        if params["gsrsearch"] == "fpx":
            return json.dumps({"query": {"pages": [
                {"pageid": 3, "title": "FunPlus Phoenix", "index": 1,
                 "extract": "FunPlus Phoenix（简称FPX）是一家中国电子竞技俱乐部。",
                 "fullurl": "https://zh.wikipedia.org/wiki/FunPlus_Phoenix"},
                {"pageid": 4, "title": "Aqua", "index": 2, "extract": "fpxs 是另一个词。"},
            ]}}, ensure_ascii=False)
        return json.dumps({"batchcomplete": True})  # nothing found


class JudgeTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        surf_dir = os.path.join(folder.name, "surf")
        self.model, self.wiki = FakeModel(), FakeWiki()
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
                mock.patch.object(wiki, "_fetch", self.wiki),
                mock.patch.object(wiki, "REQUEST_DELAY", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        wiki.refused.clear()
        self.now = time.time()

    def write_run(self, people=None):
        """What a run read and found: lines using 阿一古, fpx, 怎么翻 and 神中神 under two LoL
        videos, and the four as candidates."""
        lines = {
            "BV1aaaaaaaaa": ["阿一古阿一古 又来了", "这波阿一古了兄弟们", "阿一古！！！", "主播又在阿一古",
                             "阿一古来了家人们", "fpx加油", "FPX 这波可以", "这局怎么翻",
                             "怎么翻盘啊", "神中神啊这操作"],
            "BV1bbbbbbbbb": ["阿一古真的绷不住", "听到阿一古就想笑", "阿一古", "阿一古",
                             "又是一句阿一古", "阿一古永远的神", "fpx要赢了", "fpx这波有点东西",
                             "还能怎么翻啊", "落后一万怎么翻", "这还怎么翻", "神中神了家人们"],
        }
        videos = {}
        for bvid, texts in lines.items():
            videos[bvid] = {"game": "lol", "title": "英雄联盟", "pubdate": int(self.now - DAY),
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
                            "people": people.get(phrase, 5), "videos": 2, "people_before": 0,
                            "rise": None, "score": 10.0,
                            "examples": [{"text": phrase, "video": "BV1aaaaaaaaa",
                                          "kind": "danmu", "sent": "2026-10-31"}]}
                           for phrase in ["阿一古", "fpx", "怎么翻", "神中神"]]})

    def judge(self, **options):
        with contextlib.redirect_stdout(io.StringIO()):
            return {c["phrase"]: c["judge"] for c in memelearner.judge(**options)}

    def test_a_meme_is_explained_from_half_the_lines_and_tested_on_the_rest(self):
        self.write_run()
        verdicts = self.judge()
        ayigu = verdicts["阿一古"]
        self.assertEqual((ayigu["verdict"], ayigu["kind"], ayigu["meaning"], ayigu["english"]),
                         ("meme", "meme", AYIGU, "a streamer's catchphrase"))
        self.assertEqual(ayigu["literal"], "")  # 没有: so the other choice was "a name"
        explained, tested = self.model.explained["阿一古"], self.model.tested["阿一古"]
        # Blind: the test saw neither the explaining lines nor the write-ups
        self.assertFalse(set(explained["lines"]) & set(tested["lines"]))
        self.assertNotIn("sources", tested)
        self.assertIn(judging.NO_LITERAL, (tested["A"], tested["B"]))
        self.assertEqual(ayigu["test"], {"lines": len(tested["lines"]),
                                         "fits": len(tested["lines"]) - 1, "misses": 0})
        # The write-up came from the page named after it, without the infobox or footnotes
        # 萌娘百科 makes no extracts: the page named after it is read, the others keep their
        # snippet, and 某主播, which doesn't name it, is left out
        self.assertEqual(explained["sources"], [
            {"id": 1, "site": "萌娘百科", "title": "阿一古", "text": "阿一古是主播某人的口头禅。 常在直播里喊。"},
            {"id": 2, "site": "萌娘百科", "title": "某游戏", "text": '玩家常说阿一古"'}])
        self.assertEqual([params["page"] for host, params in self.wiki.calls
                          if params["action"] == "parse"], ["阿一古"])
        self.assertEqual(ayigu["sources"], [  # the ones the model said it used, each once
            {"site": "萌娘百科", "title": "阿一古", "url": "https://zh.moegirl.org.cn/阿一古"},
            {"site": "萌娘百科", "title": "某游戏", "url": "https://zh.moegirl.org.cn/某游戏"}])
        self.assertEqual(explained["game"], judging.GAMES["lol"])

    def test_names_and_failed_or_unclear_meanings_are_not_memes(self):
        self.write_run()
        verdicts = self.judge()
        self.assertEqual((verdicts["fpx"]["verdict"], verdicts["fpx"]["kind"]),
                         ("not a meme", "name"))
        self.assertEqual(verdicts["fpx"]["sources"][0]["title"], "FunPlus Phoenix")
        self.assertNotIn("fpx", self.model.tested)  # a name isn't tested
        self.assertEqual(verdicts["怎么翻"]["verdict"], "watching")
        self.assertIn("fit only 0 of", verdicts["怎么翻"]["why"])
        self.assertEqual((verdicts["神中神"]["verdict"], verdicts["神中神"]["kind"]),
                         ("watching", "unclear"))  # an answer that isn't JSON
        self.assertEqual(sorted(v["phrase"] for v in memelearner.watching()), ["怎么翻", "神中神"])

    def test_a_site_that_refuses_is_left_alone_for_the_rest_of_the_run(self):
        self.write_run()
        self.judge()
        moegirl = [params.get("gsrsearch") for host, params in self.wiki.calls
                   if host == "zh.moegirl.org.cn" and params["action"] == "query"]
        self.assertEqual(moegirl, ["阿一古", "fpx"])  # refused at fpx: not asked about the rest
        self.assertIn("HTTP 403", wiki.refused["萌娘百科"])
        wikipedia = [params["gsrsearch"] for host, params in self.wiki.calls
                     if host == "zh.wikipedia.org"]
        self.assertEqual(wikipedia, ["阿一古", "fpx", "怎么翻", "神中神"])
        lookups = judging.history()["lookups"]
        self.assertEqual(list(lookups), ["阿一古"])  # only kept while every site answered

    def test_verdicts_are_kept_and_asked_again_only_with_more_to_go_on(self):
        self.write_run()
        self.judge()
        calls = self.model.calls
        self.judge()
        self.assertEqual(self.model.calls, calls)  # nothing new: nothing asked
        self.write_run(people={"怎么翻": 9})  # a watching phrase that more people use now
        self.judge()
        self.assertEqual(set(self.model.explained), {"阿一古", "fpx", "怎么翻", "神中神"})
        self.assertEqual(self.model.calls, calls + 2)  # explained and tested again
        self.judge(again=True)
        self.assertEqual(self.model.calls, calls + 2 + 6)  # everything again: 2 + 1 + 2 + 1

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
        self.assertEqual(sorted(lines[("lol", "fpx")]),
                         sorted(["fpx加油", "FPX 这波可以", "fpx要赢了", "fpx这波有点东西"]))
        self.assertNotIn(("valorant", "fpx"), lines)  # another game's lines don't count
        self.assertEqual(len(judging.usage_lines({("lol", "阿一古")}, limit=3)[("lol", "阿一古")]), 3)

    def test_a_phrase_of_letters_only_counts_as_a_whole_word(self):
        self.assertTrue(wiki.mentions("AQ战队赢了", "aq"))
        self.assertFalse(wiki.mentions("Aqua 不是 aqa", "aq"))
        self.assertTrue(wiki.mentions("绷不住了！", "绷不住了"))

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
        code, printed = self.run_cli("judge", "--no-lookup")
        self.assertEqual(code, 0)
        self.assertIn(f"meme        阿一古  {AYIGU}", printed)
        self.assertIn("1 look like memes, 1 are names or everyday words, 2 are on the watching",
                      printed)
        self.assertEqual(self.wiki.calls, [])  # --no-lookup
        code, printed = self.run_cli("candidates")
        self.assertIn(f"-> a meme: {AYIGU}", printed)
        self.assertIn("-> not a meme: a name", printed)
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
        self.assertTrue(all(c["judge"]["verdict"] in ("meme", "not a meme", "watching")
                            for c in found))


class WikiTest(unittest.TestCase):
    def setUp(self):
        wiki.refused.clear()
        for patcher in (mock.patch.object(wiki, "REQUEST_DELAY", 0),):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_pages_named_after_the_phrase_come_first_and_others_must_name_it(self):
        with mock.patch.object(wiki, "_fetch", FakeWiki()):
            found = wiki.look_up("fpx", sites=["维基百科"])
        self.assertEqual([page["title"] for page in found], ["FunPlus Phoenix"])  # not fpxs
        self.assertEqual(found[0]["url"], "https://zh.wikipedia.org/wiki/FunPlus_Phoenix")

    def test_a_web_page_instead_of_data_is_a_refusal(self):
        with mock.patch.object(wiki, "_fetch", lambda url: "<html>请完成安全验证</html>"):
            self.assertEqual(wiki.look_up("阿一古"), [])
        self.assertIn("web page", wiki.refused["萌娘百科"])
        self.assertIn("web page", wiki.refused["维基百科"])
        with mock.patch.object(wiki, "_fetch", FakeWiki()) as fake:
            self.assertEqual(wiki.look_up("阿一古"), [])  # both stay skipped this run
        self.assertEqual(fake.calls, [])

    def test_a_long_write_up_is_cut_around_the_phrase(self):
        text = "前言" * 200 + "阿一古是口头禅" + "后记" * 200
        cut = wiki.excerpt(text, "阿一古")
        self.assertEqual(len(cut), wiki.TEXT)
        self.assertIn("阿一古是口头禅", cut)
        self.assertEqual(wiki.excerpt("短文", "阿一古"), "短文")


if __name__ == "__main__":
    unittest.main()
