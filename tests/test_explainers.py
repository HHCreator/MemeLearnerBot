"""Offline tests for the videos that explain memes (explainers.py, and its part of bilibili.py
and judging.py).

They never visit Bilibili, hear anything or ask a real model: made-up pages, subtitles and
audio streams in the shapes Bilibili sends, a fake Whisper and a fake model stand in. So these
check which videos are picked, what the model is shown and what becomes of its answers.
Whether Bilibili still answers this way, and whether Whisper and the model do well, only a
real run shows (py -m memelearner explain).
"""

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
import urllib.parse
from unittest import mock

import memelearner
from memelearner import bilibili, cli, explainers, judging, store, surf

DAY = 86400
FROM_VIDEO = "比赛里观众一起刷的口号，支持被看好的一方"
GUESS = "模型自己猜的意思"
QUOTE = "这句话的意思是观众一起给喜欢的队伍打气"


def video(bvid, title, plays=100, pubdate=1_700_000_000):
    """A video as bilibili.py lists it (related videos, search results)."""
    return {"bvid": bvid, "aid": 1, "title": title, "author": "up", "pubdate": pubdate,
            "plays": plays, "danmu": 0, "comments": 0,
            "url": f"https://www.bilibili.com/video/{bvid}"}


class Folder(unittest.TestCase):
    """A test with an empty cache folder of its own, and no waiting between requests."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        surf_dir = os.path.join(folder.name, "surf")
        bilibili._cookies = None
        bilibili._wbi = None
        for patcher in (
                mock.patch.object(store, "MEMES_FILE", os.path.join(folder.name, "memes.json")),
                mock.patch.object(surf, "SURF_DIR", surf_dir),
                mock.patch.object(surf, "VIDEOS_FILE", os.path.join(surf_dir, "videos.json")),
                mock.patch.object(surf, "CANDIDATES_FILE",
                                  os.path.join(surf_dir, "candidates.json")),
                mock.patch.dict(os.environ, {surf.KNOWN_ENV: ""}),
                mock.patch.object(bilibili, "REQUEST_DELAY", 0),
                mock.patch.object(bilibili, "REQUEST_JITTER", 0),
                mock.patch.object(bilibili, "_fetch", side_effect=AssertionError(
                    "a test asked Bilibili for real")),
                mock.patch.object(bilibili, "_fetch_file", side_effect=AssertionError(
                    "a test downloaded for real"))):
            patcher.start()
            self.addCleanup(patcher.stop)


# -- bilibili.py --------------------------------------------------------------------------

VISITOR = {
    "/x/frontend/finger/spi": {"code": 0, "data": {"b_3": "BUVID3", "b_4": "BUVID4"}},
    "/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket":
        {"code": 0, "data": {"ticket": "TICKET", "created_at": 1, "ttl": 259200}},
    "/x/web-interface/nav": {"code": -101, "data": {"wbi_img": {
        "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
        "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}},
}


class FakeSite:
    """Stands in for bilibili._fetch: answers each address (host and path) with a canned
    body, a dict as JSON."""

    def __init__(self, answers):
        self.answers = answers  # {"host/path" or "/api path": body}
        self.calls = []

    def __call__(self, url, post=False, cookies=None):
        parsed = urllib.parse.urlsplit(url)
        self.calls.append((parsed.netloc + parsed.path, dict(urllib.parse.parse_qsl(parsed.query)),
                           cookies))
        answer = self.answers.get(parsed.netloc + parsed.path, self.answers.get(parsed.path))
        if answer is None:
            raise AssertionError(f"nothing to answer {url}")
        return (answer if isinstance(answer, bytes) else
                json.dumps(answer, ensure_ascii=False).encode("utf-8")), ""


DETAIL = {"code": 0, "data": {
    "View": {"aid": 42, "bvid": "BV1explain01", "title": "【梗百科】正义必胜是什么梗？",
             "desc": "-", "duration": 95, "pubdate": 1_700_000_000, "cid": 7,
             "owner": {"mid": 1, "name": "梗百科"}, "stat": {"view": 12345},
             "pages": [{"cid": 7, "page": 1, "part": "p1"}],
             "subtitle": {"list": [
                 {"lan": "ai-zh", "subtitle_url": "", "ai_type": 1},  # a visitor gets no address
                 {"lan": "zh-CN", "subtitle_url": "//i0.hdslb.com/bfs/subtitle/1.json"}]}},
    "Tags": [{"tag_name": "正义必胜"}, {"tag_name": "梗百科"}],
    "Related": [{"bvid": "BV1explain02", "aid": 2, "title": "阿一古是什么梗",
                 "owner": {"name": "梗百科"}, "stat": {"view": 99}},
                {"aid": 3, "title": "a bangumi episode, which has no bvid"}]}}


class BilibiliTest(Folder):
    def site(self, answers):
        fake = FakeSite({**VISITOR, **answers})
        patcher = mock.patch.object(bilibili, "_fetch", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    fetch_file = staticmethod(bilibili._fetch_file)  # the real one, before setUp's fake

    def test_a_videos_page_gives_its_description_tags_subtitles_and_related_videos(self):
        fake = self.site({"/x/web-interface/view/detail": DETAIL})
        found = bilibili.video_detail("BV1explain01")
        self.assertEqual((found["aid"], found["title"], found["author"], found["duration"]),
                         (42, "【梗百科】正义必胜是什么梗？", "梗百科", 95))
        self.assertEqual(found["desc"], "")  # "-" is the page's "no description"
        self.assertEqual((found["tags"], found["parts"], found["plays"]),
                         (["正义必胜", "梗百科"], [7], 12345))
        # Only the track a visitor can read, with a whole address
        self.assertEqual(found["subtitles"], [
            {"lan": "zh-CN", "url": "https://i0.hdslb.com/bfs/subtitle/1.json", "ai": False}])
        self.assertEqual([v["bvid"] for v in found["related"]], ["BV1explain02"])
        self.assertEqual(fake.calls[-1][1], {"bvid": "BV1explain01"})  # unsigned, a visitor's
        self.assertEqual(fake.calls[-1][2]["buvid3"], "BUVID3")

    def test_a_deleted_video_is_an_error(self):
        self.site({"/x/web-interface/view/detail": {"code": -404, "message": "啥都木有"}})
        with self.assertRaises(bilibili.Error):
            bilibili.video_detail("BV1gone000001")

    def test_subtitles_are_read_as_text(self):
        self.site({"i0.hdslb.com/bfs/subtitle/1.json": {"body": [
            {"from": 0, "to": 2, "content": "正义必胜"}, {"from": 2, "to": 5, "content": "是什么梗"},
            {"from": 5, "to": 6, "content": ""}]}})
        self.assertEqual(bilibili.subtitle_text("https://i0.hdslb.com/bfs/subtitle/1.json"),
                         "正义必胜 是什么梗")

    def test_the_smallest_audio_stream_comes_from_the_page_or_else_the_player(self):
        playinfo = {"code": 0, "data": {"dash": {"audio": [
            {"id": 30280, "bandwidth": 190000, "baseUrl": "https://upos.example/192.m4s"},
            {"id": 30216, "bandwidth": 67000, "base_url": "https://upos.example/64.m4s",
             "backupUrl": ["https://backup.example/64.m4s"]}]}}}
        page = ("<html><script>window.__playinfo__=" + json.dumps(playinfo) +
                "</script><script>window.__INITIAL_STATE__={}</script></html>").encode()
        fake = self.site({"www.bilibili.com/video/BV1explain01/": page})
        self.assertEqual(bilibili.audio_url("BV1explain01", 7), "https://upos.example/64.m4s")
        self.assertEqual(fake.calls[-1][2]["buvid3"], "BUVID3")  # the page, as a visitor
        # A page without the player's streams: the player's API, signed
        fake = self.site({"www.bilibili.com/video/BV1explain01/": b"<html>busy</html>",
                          "/x/player/wbi/playurl": playinfo})
        self.assertEqual(bilibili.audio_url("BV1explain01", 7), "https://upos.example/64.m4s")
        path, query, _ = fake.calls[-1]
        self.assertEqual((path, query["cid"], query["fnval"]),
                         ("api.bilibili.com/x/player/wbi/playurl", "7", "16"))
        self.assertIn("w_rid", query)
        # An old video with no separate audio
        self.site({"www.bilibili.com/video/BV1explain01/": b"<html></html>",
                   "/x/player/wbi/playurl": {"code": 0, "data": {"durl": [{"url": "x"}]}}})
        self.assertIsNone(bilibili.audio_url("BV1explain01", 7))

    def test_an_audio_stream_is_saved_only_up_to_a_size(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        path = os.path.join(self.folder, "a.m4a")
        with mock.patch.object(bilibili, "_fetch_file", self.fetch_file), \
                mock.patch.object(bilibili.urllib.request, "urlopen",
                                  side_effect=lambda *a, **k: Response(b"x" * 1000)) as urlopen:
            self.assertEqual(bilibili.download("https://upos.example/64.m4s", path), 1000)
            self.assertEqual(os.path.getsize(path), 1000)
            self.assertEqual(urlopen.call_args.args[0].headers["Referer"],
                             "https://www.bilibili.com/")
            with self.assertRaisesRegex(bilibili.Error, "bigger than"):
                bilibili.download("https://upos.example/64.m4s", path, limit=999)
        self.assertFalse(os.path.exists(path))  # nothing half-saved is left behind

    def test_a_search_with_no_time_range_sends_none(self):
        fake = self.site({"/x/web-interface/wbi/search/type": {"code": 0, "data": {"result": [
            {"type": "video", "bvid": "BV1explain01", "pubdate": 1, "title": "正义必胜是什么梗"}]}}})
        found = bilibili.search_videos("正义必胜是什么梗", order="totalrank")
        self.assertEqual([v["bvid"] for v in found], ["BV1explain01"])  # posted long ago: kept
        query = fake.calls[-1][1]
        self.assertEqual(query["order"], "totalrank")
        self.assertNotIn("pubtime_begin_s", query)


# -- finding and reading explainer videos -------------------------------------------------

class FindTest(Folder):
    def test_titles_that_explain_something_and_phrases_in_them(self):
        for title in ["【梗百科】正义必胜是什么梗？", "阿一古什么意思", "千言万语感谢现役 出处", "LOL热梗盘点"]:
            self.assertTrue(explainers.looks_like_explainer(title), title)
        for title in ["英雄联盟全球总决赛 BLG vs T1", "CS2 major 决赛", ""]:
            self.assertFalse(explainers.looks_like_explainer(title), title)
        self.assertTrue(explainers.mentions("【梗百科】IG牛逼是什么梗？", "ig牛逼"))
        self.assertFalse(explainers.mentions("big牛逼是什么梗", "ig牛逼"))  # whole words only
        self.assertTrue(explainers.mentions("这怎么翻是什么梗", "怎么翻的"))  # mood words left out

    def test_explainer_videos_in_related_lists_are_remembered(self):
        new = explainers.remember([video("BV1explain01", "正义必胜是什么梗"),
                                   video("BV1match00001", "英雄联盟 S16 决赛")])
        self.assertEqual(new, 1)
        self.assertEqual(explainers.remember([video("BV1explain01", "正义必胜是什么梗")]), 0)
        self.assertEqual(list(explainers.load()["seen"]), ["BV1explain01"])
        # ... also from the collector's lists, without a request more
        with mock.patch.object(bilibili, "search_videos", return_value=[]), \
                mock.patch.object(bilibili, "related_videos", return_value=[
                    video("BV1explain02", "阿一古是什么梗", pubdate=int(time.time()))]), \
                mock.patch("builtins.print"):
            surf.find_videos("lol", {"words": ["英雄联盟"], "seeds": ["BV1seed00001"]}, {},
                             time.time(), 7, 3)
        self.assertEqual(sorted(explainers.load()["seen"]), ["BV1explain01", "BV1explain02"])

    def test_videos_are_found_in_links_then_the_ones_seen_then_a_search(self):
        explainers.remember([video("BV1explain01", "正义必输名场面 出处", plays=900),
                             video("BV1explain02", "正义必胜是什么梗", plays=10),
                             video("BV1explain03", "正义必胜的由来", plays=50),
                             video("BV1explain04", "阿一古是什么梗")])
        chant = {"phrase": "正义必胜", "game": "lol", "variants": [{"phrase": "正义必输"}]}
        with mock.patch.object(bilibili, "search_videos") as search:
            found = explainers.find(chant)
            # Made to explain one phrase first, the phrase before its variants, most played
            self.assertEqual([v["bvid"] for v in found],
                             ["BV1explain03", "BV1explain02", "BV1explain01"])
            found = explainers.find(chant, links=["https://www.bilibili.com/video/BV1linked001"])
            self.assertEqual(found[0], {"bvid": "BV1linked001", "title": "", "how": "link"})
        search.assert_not_called()  # it had videos for it

    def test_the_search_is_tried_until_it_wants_a_captcha_and_once_a_week(self):
        results = [video("BV1explain05", "千言万语感谢现役是什么梗"),
                   video("BV1explain06", "无畏契约 千言万语感谢现役 合集"),
                   video("BV1other0001", "别的视频")]
        thanks = {"phrase": "千言万语感谢现役", "game": "valorant", "variants": []}
        data, run = explainers.load(), {}
        with mock.patch.object(bilibili, "search_videos", return_value=results) as search:
            found = explainers.find(thanks, search=run, data=data)
            self.assertEqual([v["bvid"] for v in found], ["BV1explain05", "BV1explain06"])
            self.assertEqual(search.call_args.args, ("千言万语感谢现役是什么梗",))
            # Searched this week: not again (the explainer it found is remembered)
            data["phrases"]["valorant:千言万语感谢现役"].pop("searched")
            explainers._save(data)
            self.assertEqual(list(explainers.load()["seen"]), ["BV1explain05"])
        nothing = {"phrase": "空悲切", "game": "valorant", "variants": []}
        with mock.patch.object(bilibili, "search_videos",
                               side_effect=bilibili.Captcha("captcha")) as search, \
                mock.patch("builtins.print"):
            self.assertEqual(explainers.find(nothing, search=run, data=data), [])
            self.assertEqual(explainers.find(dict(nothing, phrase="马桶赛区"), search=run,
                                             data=data), [])
        self.assertEqual(search.call_count, 1)  # after a captcha, no more searches this run
        self.assertNotIn("searched", data["phrases"]["valorant:空悲切"])  # tried again next run
        with mock.patch.object(bilibili, "search_videos", return_value=[]) as search:
            explainers.find(nothing, data=data)
            explainers.find(nothing, data=data)
        self.assertEqual(search.call_count, 1)


class ReadTest(Folder):
    def fake_site(self, subtitles=True, duration=95):
        """Fakes for bilibili.py's readers; returns the list of what was asked."""
        asked = []

        def save(path):
            with open(path, "wb") as f:
                return f.write(b"audio")

        detail = {"bvid": "BV1explain01", "aid": 42, "title": "【梗百科】正义必胜是什么梗？",
                  "author": "梗百科", "pubdate": 1_700_000_000, "duration": duration,
                  "desc": "这期讲正义必胜", "tags": ["梗百科"], "plays": 12345, "parts": [7],
                  "subtitles": [{"lan": "zh-CN", "url": "https://s/1.json", "ai": False}]
                  if subtitles else [],
                  "related": [video("BV1explain02", "阿一古是什么梗")]}
        fakes = {
            "video_detail": lambda bvid: asked.append("page") or dict(detail, bvid=bvid),
            "subtitle_text": lambda url: asked.append("subtitles") or QUOTE,
            "danmu": lambda cid: asked.append("danmu") or [
                {"text": "正义必胜"}, {"text": "正义必胜"}, {"text": "原来正义必胜出自比赛"}, {"text": "哈哈"}],
            "comments": lambda aid, pages=3: asked.append("comments") or [
                {"text": "前排", "likes": 3}, {"text": "正义必胜的意思是  打气", "likes": 90},
                {"text": "前排", "likes": 1}],
            "audio_url": lambda bvid, cid: asked.append("audio") or "https://upos.example/64.m4s",
            "download": lambda url, path: asked.append("download") or save(path),
        }
        for name, fake in fakes.items():
            patcher = mock.patch.object(bilibili, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        return asked

    def test_a_video_is_read_once_with_its_subtitles_comments_and_danmu(self):
        asked = self.fake_site()
        found = explainers.read("BV1explain01", "正义必胜")
        self.assertEqual(asked, ["page", "subtitles", "danmu", "comments"])
        self.assertEqual((found["said"], found["heard"]), (QUOTE, "subtitles"))
        self.assertEqual(found["comments"], [{"text": "正义必胜的意思是 打气", "likes": 90},
                                             {"text": "前排", "likes": 3}])  # most liked first
        self.assertEqual(found["danmu"], ["正义必胜", "原来正义必胜出自比赛", "哈哈"])
        self.assertIn("BV1explain02", explainers.load()["seen"])  # its related explainer
        self.assertEqual(explainers.read("BV1explain01", "正义必胜", hearing=True), found)
        self.assertEqual(len(asked), 4)  # read once: the second time it comes from the cache

    def test_without_subtitles_a_video_is_heard_and_the_audio_deleted(self):
        asked = self.fake_site(subtitles=False)
        heard = []

        def hear(path, phrase):
            with open(path, "rb") as f:
                heard.append((os.path.basename(path), f.read(), phrase))
            return "正义必胜就是给队伍打气"

        with mock.patch.object(explainers, "hear", side_effect=hear):
            found = explainers.read("BV1explain01", "正义必胜", hearing=False)
            self.assertEqual((found["said"], found["heard"]), ("", ""))  # not wanted
            found = explainers.read("BV1explain01", "正义必胜", hearing=True)  # Whisper now
        self.assertEqual(heard, [("BV1explain01.m4a", b"audio", "正义必胜")])
        self.assertEqual((found["said"], found["heard"]), ("正义必胜就是给队伍打气", "audio"))
        self.assertEqual(asked, ["page", "danmu", "comments", "audio", "download"])
        self.assertEqual(os.listdir(os.path.join(surf.SURF_DIR, "explainers", "audio")), [])
        self.assertEqual(explainers.read("BV1explain01")["heard"], "audio")  # kept

    def test_a_long_video_isnt_heard_and_a_failure_to_hear_isnt_fatal(self):
        self.fake_site(subtitles=False, duration=3600)
        with mock.patch.object(explainers, "hear") as hear:
            self.assertEqual(explainers.read("BV1explain01", hearing=True)["said"], "")
        hear.assert_not_called()
        os.remove(explainers.video_file("BV1explain01"))
        self.fake_site(subtitles=False)
        with mock.patch.object(explainers, "hear", side_effect=RuntimeError("no model")), \
                mock.patch("builtins.print") as printed:
            self.assertEqual(explainers.read("BV1explain01", hearing=True)["said"], "")
        self.assertIn("Whisper couldn't hear BV1explain01", printed.call_args.args[0])


# -- summing up and judging ---------------------------------------------------------------

class FakeModel:
    """Stands in for judging._ask. Summing up: a video whose title asks 是什么梗 and that says
    something explains the phrase; another one doesn't. Judging: fpx is a name, and a meme's
    meaning is what the videos say, or else a guess."""

    def __init__(self, quote=QUOTE):
        self.quote = quote
        self.summed, self.explained, self.calls = {}, {}, 0

    def __call__(self, messages, model):
        self.calls += 1
        question = json.loads(messages[1]["content"])
        if messages[0]["content"] == explainers.SUMMARY:
            self.summed[question["title"]] = question
            if "是什么梗" in question["title"] and question["said"]:
                return json.dumps({"about": True, "meaning": FROM_VIDEO, "origin": "2025 年的比赛",
                                   "english": "a chant", "quote": self.quote},
                                  ensure_ascii=False)
            return json.dumps({"about": False, "meaning": "别的东西", "quote": "x"})
        if messages[0]["content"] == judging.EXPLAIN:
            self.explained[question["phrase"]] = question
            if question["phrase"] == "fpx":
                return json.dumps({"kind": "name", "why": "a team"})
            meaning = question["videos"][0]["says"] if question["videos"] else GUESS
            return json.dumps({"kind": "meme", "meaning": meaning, "english": "a chant",
                               "literal": "", "why": "..."}, ensure_ascii=False)
        return json.dumps({"answers": []})


class ExplainTest(Folder):
    def setUp(self):
        super().setUp()
        self.model = FakeModel()
        for patcher in (mock.patch.object(judging, "_ask", self.model),
                        mock.patch.object(judging, "MODEL", "deepseek-r1:14b"),
                        mock.patch.object(judging, "_get_json",
                                          lambda path: {"models": [{"name": "deepseek-r1:14b"}]}),
                        mock.patch.object(explainers, "can_hear", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.read = []

        def read(bvid, phrase="", hearing=False):
            self.read.append(bvid)
            return {"bvid": bvid, "url": f"https://www.bilibili.com/video/{bvid}",
                    "title": self.titles[bvid], "author": "up", "desc": "", "tags": [],
                    "said": f"今天讲讲{phrase}。{QUOTE}" if "梗" in self.titles[bvid] else "",
                    "heard": "subtitles", "comments": [{"text": "前排", "likes": 1}],
                    "danmu": []}

        for patcher in (mock.patch.object(explainers, "read", side_effect=read),
                        mock.patch.object(bilibili, "search_videos", return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.titles = {"BV1explain01": "正义必胜是什么梗？", "BV1explain02": "正义必胜名场面 出处",
                       "BV1linked001": "看完就懂"}
        explainers.remember([video(bvid, title) for bvid, title in self.titles.items()])

    def write_run(self):
        """What a run found: 正义必胜, a chant only ever sent on its own, and fpx, a name."""
        now = int(time.time())
        lines = {"BV1aaaaaaaaa": ["正义必胜", "正义必胜正义必胜", "fpx加油", "FPX 这波可以"],
                 "BV1bbbbbbbbb": ["正义必胜！", "fpx要赢了", "fpx这波有点东西"]}
        videos = {}
        for bvid, texts in lines.items():
            videos[bvid] = {"game": "lol", "title": "英雄联盟 比赛", "pubdate": now - DAY,
                            "url": f"https://www.bilibili.com/video/{bvid}"}
            os.makedirs(os.path.dirname(surf.messages_path(bvid)), exist_ok=True)
            with open(surf.messages_path(bvid), "w", encoding="utf-8") as f:
                f.writelines(json.dumps({"id": f"{bvid}{n}", "kind": "danmu", "who": f"{n}",
                                         "text": text, "sent": now - DAY + n},
                                        ensure_ascii=False) + "\n" for n, text in enumerate(texts))
        surf._save_json(surf.VIDEOS_FILE, videos)
        surf._save_json(surf.CANDIDATES_FILE, {"videos": videos, "candidates": [
            {"phrase": phrase, "game": "lol", "status": "first look", "people": 5, "alone": 0.9,
             "videos": 2, "score": score, "variants": [],
             "examples": [{"text": phrase, "video": "BV1aaaaaaaaa", "kind": "danmu"}]}
            for phrase, score in (("正义必胜", 10.0), ("fpx", 20.0))]})

    def quietly(self, call, *args, **options):
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            found = call(*args, **options)
        return found, printed.getvalue()

    def test_a_video_explains_a_meme_the_judge_could_only_guess_and_accept_takes_it(self):
        self.write_run()
        verdicts, _ = self.quietly(memelearner.judge)
        chant = {c["phrase"]: c["judge"] for c in verdicts}["正义必胜"]
        self.assertEqual((chant["verdict"], chant["meaning"], chant["guess"]),
                         ("untested", GUESS, True))
        found, printed = self.quietly(memelearner.explain)
        # Only the meme: no video explains a name
        self.assertEqual(list(found), ["lol:正义必胜"])
        self.assertEqual(self.read, ["BV1explain01", "BV1explain02"])
        summary, other = found["lol:正义必胜"]
        self.assertEqual((summary["about"], summary["meaning"], summary["origin"],
                          summary["quote"], summary["url"]),
                         (True, FROM_VIDEO, "2025 年的比赛", QUOTE,
                          "https://www.bilibili.com/video/BV1explain01"))
        self.assertEqual((other["about"], other["meaning"], other["quote"]), (False, "", ""))
        shown = self.model.summed["正义必胜是什么梗？"]
        self.assertEqual((shown["phrase"], shown["game"]), ("正义必胜", judging.GAMES["lol"]))
        self.assertIn(QUOTE, shown["said"])
        self.assertIn(f"正义必胜是什么梗？ [read its subtitles]: {FROM_VIDEO} (出自 2025 年的比赛)",
                      printed)
        self.assertIn("1 of 1 phrases are explained by a video now.", printed)
        # ... and judged again, with what the video says
        asked = self.model.explained["正义必胜"]
        self.assertEqual(asked["videos"], [{"title": "正义必胜是什么梗？", "says": FROM_VIDEO,
                                            "origin": "2025 年的比赛"}])
        chant = {c["phrase"]: c for c in memelearner.candidates()}["正义必胜"]["judge"]
        self.assertEqual((chant["verdict"], chant["meaning"], chant["guess"]),
                         ("untested", FROM_VIDEO, False))
        self.assertEqual(chant["videos"], [{"url": "https://www.bilibili.com/video/BV1explain01",
                                            "title": "正义必胜是什么梗？", "says": FROM_VIDEO}])
        self.assertIn("from 1 video explaining it, like 正义必胜是什么梗？", judging.untested(chant))
        self.assertEqual(memelearner.accept("正义必胜")["meaning"], FROM_VIDEO)

    def test_what_was_read_and_judged_isnt_done_again(self):
        self.write_run()
        self.quietly(memelearner.judge)
        self.quietly(memelearner.explain)
        calls = self.model.calls
        found, printed = self.quietly(memelearner.explain)
        self.assertEqual((found, self.model.calls), ({}, calls))  # explained already
        self.assertIn("Nothing to look up", printed)
        self.quietly(memelearner.judge)
        self.assertEqual(self.model.calls, calls)  # no new video: the verdicts stand
        found, _ = self.quietly(memelearner.explain, again=True)
        self.assertEqual(len(found["lol:正义必胜"]), 2)  # summed up again
        self.assertEqual(len(explainers.load()["phrases"]["lol:正义必胜"]["videos"]), 2)
        self.assertEqual(self.model.calls, calls + 2)  # the same summaries: no judging again
        # A video that says something else now is judged by again
        with mock.patch(__name__ + ".FROM_VIDEO", "后来视频改口了"):
            self.quietly(memelearner.explain, again=True)
        self.assertEqual(self.model.explained["正义必胜"]["videos"][0]["says"], "后来视频改口了")

    def test_a_link_is_read_for_its_phrase_without_looking(self):
        self.write_run()
        with mock.patch.object(bilibili, "search_videos") as search:
            found, _ = self.quietly(memelearner.explain, ["正义必胜"], judge=False,
                                    links={"正义必胜": ["https://b23.tv/x BV1linked001"]})
        search.assert_not_called()
        self.assertEqual(self.read, ["BV1linked001"])  # only the link, not the ones seen
        self.assertFalse(found["lol:正义必胜"][0]["about"])  # 看完就懂 says nothing about it
        self.assertNotIn("正义必胜", self.model.explained)  # not judged again

    def test_a_phrase_that_isnt_a_candidate_is_explained_but_not_judged(self):
        self.write_run()
        self.titles["BV1explain07"] = "雪豹是什么梗"
        explainers.remember([video("BV1explain07", "雪豹是什么梗")])
        found, printed = self.quietly(memelearner.explain, ["雪豹"], games=["lol"])
        self.assertTrue(found["lol:雪豹"][0]["about"])
        self.assertNotIn("Nothing to judge", printed)
        self.assertEqual(self.model.explained, {})

    def test_a_quote_the_video_doesnt_contain_is_dropped(self):
        self.model.quote = "视频里没有这句话"
        self.write_run()
        found, _ = self.quietly(memelearner.explain, ["正义必胜"], judge=False)
        self.assertEqual(found["lol:正义必胜"][0]["quote"], "")
        self.assertEqual(found["lol:正义必胜"][0]["meaning"], FROM_VIDEO)

    def test_the_phrases_that_need_a_video_most_come_first(self):
        surf._save_json(surf.CANDIDATES_FILE, {"candidates": [
            {"phrase": phrase, "game": "lol", "score": score, "variants": [],
             "judge": verdict} for phrase, score, verdict in (
                ("tested", 90, {"verdict": "meme", "meaning": "m"}),
                ("unjudged", 80, None),
                ("watched", 70, {"verdict": "watching"}),
                ("guessed", 60, {"verdict": "untested", "meaning": "m", "guess": True}),
                ("told", 50, {"verdict": "untested", "meaning": "m", "guess": False}),
                ("unknown", 40, {"verdict": "untested", "meaning": ""}),
                ("a name", 99, {"verdict": "not a meme", "kind": "name"}))]})
        wanted = explainers._wanted(None, None, 10, False, explainers.load())
        self.assertEqual([c["phrase"] for c in wanted],
                         ["guessed", "unknown", "watched", "unjudged", "tested", "told"])
        self.assertEqual(len(explainers._wanted(None, None, 2, False, explainers.load())), 2)
        made_up = explainers._wanted(["雪豹！"], {"lol"}, 10, False, explainers.load())
        self.assertEqual(made_up, [{"phrase": "雪豹", "game": "lol", "variants": [],
                                    "made_up": True}])

    def test_without_the_model_nothing_is_read(self):
        self.write_run()
        with mock.patch.object(judging, "_get_json", lambda path: {"models": []}):
            with self.assertRaisesRegex(memelearner.ModelUnavailable, "ollama pull"):
                self.quietly(memelearner.explain, ["正义必胜"])
        self.assertEqual(self.read, [])

    def run_cli(self, *args):
        printed = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(printed):
            try:
                cli.main(list(args))
            except SystemExit as stop:
                code = stop.code
        return code, printed.getvalue()

    def test_the_command_line(self):
        self.write_run()
        code, printed = self.run_cli("explain", "正义必胜", "--video", "BV1linked001",
                                     "--no-judge")
        self.assertEqual((code, self.read), (0, ["BV1linked001"]))
        self.assertIn("看完就懂: doesn't explain 正义必胜", printed)
        code, printed = self.run_cli("explain", "正义必胜", "fpx", "--video", "BV1linked001")
        self.assertIn("give the one phrase", str(code))
        self.run_cli("judge")  # fpx is a name now, which no video explains
        code, printed = self.run_cli("explain", "--top", "1")
        self.assertEqual(code, 0)
        self.assertIn("1 of 1 phrases are explained by a video now.", printed)
        self.assertIn("from 1 video explaining it", printed)  # judged again
        with mock.patch.object(judging, "_get_json", lambda path: {"models": []}):
            code, printed = self.run_cli("explain", "fpx")
        self.assertEqual(code, 1)
        self.assertIn("Couldn't ask the model", printed)


class HearTest(unittest.TestCase):
    def test_whisper_moves_to_the_processor_when_the_graphics_card_fails(self):
        class Segment:
            text = " 正义必胜 "

        class Model:
            def __init__(self, device):
                self.device = device

            def transcribe(self, path, **options):
                self.options = options
                if self.device == "cuda":
                    raise RuntimeError("CUDA failed with error no kernel image is available")
                return iter([Segment(), Segment()]), None

        loaded = []

        def load(device):
            loaded.append(device)
            return Model(device)

        with mock.patch.object(explainers, "_whisper", None), \
                mock.patch.object(explainers, "_load_whisper", side_effect=load), \
                mock.patch("builtins.print"):
            self.assertEqual(explainers.hear("a.m4a", "正义必胜"), "正义必胜 正义必胜")
            self.assertEqual(explainers._whisper[1], "cpu")
            self.assertEqual(explainers._whisper[0].options["language"], "zh")
            self.assertIn("正义必胜", explainers._whisper[0].options["initial_prompt"])
        self.assertEqual(loaded, ["cuda", "cpu"])


if __name__ == "__main__":
    unittest.main()
