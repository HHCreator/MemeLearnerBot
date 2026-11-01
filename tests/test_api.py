"""Offline tests for the entry point other apps use (__init__.py, store.py and cli.py).

Every test gets an empty folder for the learned memes and the cache, so the real
data/memes.json and what the bot read before are never touched.
"""

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import memelearner
from memelearner import cli, store, surf

DAY = 86400


class EntryPointTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        surf_dir = os.path.join(self.folder, "surf")
        for patcher in (
                mock.patch.object(store, "MEMES_FILE", os.path.join(self.folder, "memes.json")),
                mock.patch.object(surf, "SURF_DIR", surf_dir),
                mock.patch.object(surf, "VIDEOS_FILE", os.path.join(surf_dir, "videos.json")),
                mock.patch.object(surf, "CANDIDATES_FILE",
                                  os.path.join(surf_dir, "candidates.json")),
                mock.patch.dict(os.environ, {surf.KNOWN_ENV: ""})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_candidates(self):
        """A candidates.json like a run leaves: 阿一古 seen in two games, and 下路."""
        def example(text, video):
            return {"text": text, "video": video, "kind": "danmu", "sent": "2026-10-31"}

        surf._save_json(surf.CANDIDATES_FILE, {
            "made": "2026-10-31 13:33", "days": 7, "games": {},
            "videos": {"BV1lol": {"game": "lol", "url": "https://www.bilibili.com/video/BV1lol"},
                       "BV1val": {"game": "valorant",
                                  "url": "https://www.bilibili.com/video/BV1val"}},
            "candidates": [
                {"phrase": "阿一古", "game": "lol", "status": "first look", "people": 30,
                 "videos": 3, "people_before": 0, "rise": None, "score": 77.5,
                 "examples": [example("阿一古阿一古", "BV1lol"), example("阿一古!", "BV1lol")]},
                {"phrase": "下路", "game": "lol", "status": "first look", "people": 20,
                 "videos": 4, "people_before": 0, "rise": None, "score": 60.0,
                 "examples": [example("下路崩了", "BV1lol")]},
                {"phrase": "阿一古", "game": "valorant", "status": "new", "people": 5,
                 "videos": 2, "people_before": 0, "rise": 9.0, "score": 10.0,
                 "examples": [example("阿一古来了", "BV1val"), example("又阿一古", "BV1val")]},
            ]})

    def test_an_app_gets_nothing_before_anything_is_learned(self):
        self.assertEqual(memelearner.memes(), [])
        self.assertIsNone(memelearner.lookup("绷不住了"))
        self.assertEqual(memelearner.find("这下真的绷不住了"), [])
        self.assertEqual(memelearner.candidates(), [])

    def test_accepting_a_candidate_takes_its_games_examples_and_videos(self):
        self.write_candidates()
        meme = memelearner.accept("阿一古", meaning="A-yi-gu, 主播的口头禅")
        self.assertEqual(meme["games"], ["lol", "valorant"])
        self.assertEqual(meme["examples"], ["阿一古阿一古", "阿一古!", "阿一古来了"])
        self.assertEqual(meme["sources"], ["https://www.bilibili.com/video/BV1lol",
                                           "https://www.bilibili.com/video/BV1val"])
        self.assertEqual(meme["added"], time.strftime("%Y-%m-%d"))
        self.assertEqual(memelearner.memes(), [meme])
        self.assertEqual(memelearner.memes(game="valorant"), [meme])
        self.assertEqual(memelearner.memes(game="cs2"), [])
        self.assertEqual(memelearner.lookup("阿一古"), meme)
        # Looked at: no longer a candidate, and the bot won't list it again
        self.assertEqual([c["phrase"] for c in memelearner.candidates()], ["下路"])
        self.assertIn("阿一古", surf.known_phrases())

        memelearner.reject("下路", "an everyday word")
        self.assertEqual(memelearner.candidates(), [])
        self.assertIn("下路", surf.known_phrases())
        self.assertTrue(memelearner.forget("下路"))  # undo
        self.assertEqual([c["phrase"] for c in memelearner.candidates(game="lol")], ["下路"])
        self.assertFalse(memelearner.forget("下路"))

    def test_accepting_again_updates_and_rejecting_unlearns(self):
        memelearner.accept("ＹＹＤＳ", meaning="永远的神", game="cs2")  # any case or width
        meme = memelearner.accept("yyds", english="the greatest of all time", game="lol")
        self.assertEqual((meme["phrase"], meme["meaning"], meme["english"], meme["games"]),
                         ("yyds", "永远的神", "the greatest of all time", ["cs2", "lol"]))
        memelearner.reject("yyds", "too old to be news")
        self.assertIsNone(memelearner.lookup("yyds"))
        with open(store.MEMES_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["version"], store.VERSION)
        self.assertEqual(saved["memes"], {})
        self.assertEqual(saved["rejected"]["yyds"]["reason"], "too old to be news")
        with self.assertRaises(ValueError):
            memelearner.accept("!!!")  # nothing left of it

    def test_find_prefers_longer_memes_and_whole_words(self):
        for phrase in ["绷不住了", "不住", "yyds", "下饭"]:
            memelearner.accept(phrase)
        found = memelearner.find("这下真的绷不住了 ZYWOO YYDS，yydsx 也绷不住了")
        self.assertEqual([meme["phrase"] for meme in found], ["绷不住了", "yyds"])
        self.assertEqual([meme["phrase"] for meme in memelearner.find("太下饭了")], ["下饭"])

    def test_a_broken_memes_file_is_an_error_and_is_never_saved_over(self):
        with open(store.MEMES_FILE, "w", encoding="utf-8") as f:
            f.write('{"memes": {"绷不住了": {}')
        with self.assertRaises(ValueError):
            memelearner.memes()
        with self.assertRaises(ValueError):
            memelearner.accept("下饭")
        with open(store.MEMES_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"memes": {"绷不住了": {}')

    def write_messages(self):
        """What a run read: 4 people writing 绷不住了 under two LoL videos yesterday."""
        now = time.time()
        videos = {}
        for n, bvid in enumerate(["BV1aaaaaaaaa", "BV1bbbbbbbbb"]):
            videos[bvid] = {"game": "lol", "title": "英雄联盟", "pubdate": int(now - DAY)}
            lines = [{"id": f"d{n}{i}", "kind": "danmu", "who": f"p{n}{i}", "text": "绷不住了",
                      "sent": int(now - DAY)} for i in range(2)]
            os.makedirs(os.path.dirname(surf.messages_path(bvid)), exist_ok=True)
            with open(surf.messages_path(bvid), "w", encoding="utf-8") as f:
                f.writelines(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
        surf._save_json(surf.VIDEOS_FILE, videos)

    def test_learning_without_fetching_finds_phrases_in_what_was_read(self):
        self.write_messages()
        found = memelearner.learn(games=["LoL"], fetch=False, quiet=True)
        self.assertEqual([(c["phrase"], c["status"]) for c in found], [("绷不住了", "first look")])
        self.assertEqual(memelearner.candidates(), found)  # saved for later
        self.assertEqual(memelearner.learn(["lol"], fetch=False, known=["绷不住了"], quiet=True),
                         [])
        with self.assertRaises(ValueError):
            memelearner.learn(games=["fortnite"], fetch=False, quiet=True)

    def run_cli(self, *args):
        """(exit code, what was printed) for py -m memelearner args."""
        printed = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(printed):
            try:
                cli.main(list(args))
            except SystemExit as stop:
                code = stop.code
        return code, printed.getvalue()

    def test_the_command_line_learns_and_prints_json_for_other_programs(self):
        self.write_messages()
        code, printed = self.run_cli("learn", "--no-fetch", "--games", "lol")
        self.assertEqual(code, 0)
        self.assertIn("绷不住了", printed)
        self.assertEqual(json.loads(self.run_cli("candidates", "--json")[1])[0]["phrase"],
                         "绷不住了")

        self.run_cli("accept", "绷不住了", "--meaning", "笑得忍不住了")
        code, printed = self.run_cli("memes", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed), memelearner.memes())
        self.assertEqual(json.loads(printed)[0]["meaning"], "笑得忍不住了")
        code, printed = self.run_cli("find", "这下真的绷不住了", "--json")
        self.assertEqual([meme["phrase"] for meme in json.loads(printed)], ["绷不住了"])
        self.assertEqual(self.run_cli("lookup", "绷不住了")[0], 0)
        code, printed = self.run_cli("lookup", "下饭", "--json")
        self.assertEqual((code, json.loads(printed)), (1, None))  # not learned: exit code 1

        self.run_cli("reject", "绷不住了", "--reason", "test")
        self.assertEqual(json.loads(self.run_cli("memes", "--json")[1]), [])


if __name__ == "__main__":
    unittest.main()
