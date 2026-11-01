"""Offline tests for the meme bot (bilibili.py and surf.py). Run them with: py -m unittest

They never visit Bilibili. The inputs are made-up danmu, comments and search results in the
shapes Bilibili's API sends, so these check the signing, the parsing and the phrase finding.
Whether Bilibili still answers the way they assume is only shown by a real run of surf.py.
"""

import json
import os
import tempfile
import unittest
import urllib.parse
import zlib
from unittest import mock

from sbtranslator import bilibili, settings, surf

NOW = 1_800_000_000
DAY = 86400


class FakeBilibili:
    """Stands in for bilibili._fetch: answers each API path with a canned body."""

    def __init__(self, answers):
        self.answers = answers  # {path: body}
        self.calls = []

    def __call__(self, url, post=False, cookies=None):
        parsed = urllib.parse.urlsplit(url)
        self.calls.append((parsed.path, dict(urllib.parse.parse_qsl(parsed.query)), cookies))
        answer = self.answers[parsed.path]
        if isinstance(answer, bytes):
            return answer, "deflate"
        return json.dumps(answer).encode("utf-8"), ""


VISITOR = {
    "/x/frontend/finger/spi": {"code": 0, "data": {"b_3": "BUVID3", "b_4": "BUVID4"}},
    "/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket":
        {"code": 0, "data": {"ticket": "TICKET", "created_at": NOW, "ttl": 259200}},
    "/x/web-interface/nav": {"code": -101, "message": "账号未登录", "data": {"wbi_img": {
        "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
        "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}},
}


class BilibiliTest(unittest.TestCase):
    def setUp(self):
        bilibili._cookies = None
        bilibili._wbi = None
        for name in ("REQUEST_DELAY", "REQUEST_JITTER"):
            patcher = mock.patch.object(bilibili, name, 0)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake(self, answers):
        fake = FakeBilibili({**VISITOR, **answers})
        patcher = mock.patch.object(bilibili, "_fetch", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_signature_matches_the_documented_example(self):
        # The worked example in the community docs of Bilibili's web API (bilibili-API-collect)
        key = bilibili.mixin_key("7cd084941338484aae1ad9425b84077c",
                                 "4932caff0ff746eab6f01bf08b70ac45")
        self.assertEqual(key, "ea1db124af3c7062474693fa704f4ff8")
        signed = bilibili.sign({"foo": "114", "bar": "514", "zab": 1919810}, key, now=1702204169)
        self.assertEqual(signed["w_rid"], "8f6f2b5b3d485fe1886cec6a0be8c5d4")
        self.assertEqual(signed["wts"], "1702204169")

    def test_signature_ignores_the_characters_the_page_removes(self):
        a = bilibili.sign({"keyword": "it's (cs2)!"}, "k" * 32, now=1)
        b = bilibili.sign({"keyword": "its cs2"}, "k" * 32, now=1)
        self.assertEqual(a["w_rid"], b["w_rid"])

    def test_danmu_are_read_from_the_deflated_xml(self):
        xml = ('<?xml version="1.0" encoding="UTF-8"?><i><chatserver>chat.bilibili.com'
               '</chatserver><maxlimit>3000</maxlimit>'
               '<d p="12.5,1,25,16777215,1761800000,0,1a2b3c4d,111,10">绷不住了</d>'
               '<d p="30.0,1,25,16777215,1761800100,0,5e6f7a8b,222,10">a &amp; b\x01</d>'
               '<d p="broken">skipped</d></i>')
        deflate = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        self.fake({"/x/v1/dm/list.so": deflate.compress(xml.encode()) + deflate.flush()})
        found = bilibili.danmu(123)
        self.assertEqual([m["text"] for m in found], ["绷不住了", "a & b\x01"])
        self.assertEqual(found[0]["id"], "d111")
        self.assertEqual(found[0]["sent"], 1761800000)
        self.assertEqual(found[0]["who"], bilibili.user_hash("1a2b3c4d"))
        self.assertNotIn("1a2b3c4d", json.dumps(found))  # the sender hash itself isn't kept

    def test_comments_include_replies_and_the_same_hash_as_danmu(self):
        fake = self.fake({"/x/v2/reply/wbi/main": {"code": 0, "data": {
            "cursor": {"is_end": True, "pagination_reply": {}},
            "replies": [{"rpid": 1, "mid": 42, "ctime": 1761800000, "like": 9,
                         "content": {"message": "绷不住了[doge]"},
                         "replies": [{"rpid": 2, "mid": 43, "ctime": 1761800050, "like": 1,
                                      "content": {"message": "确实绷不住了"}}]}]}}})
        found = bilibili.comments(456)
        self.assertEqual([m["id"] for m in found], ["c1", "c2"])
        self.assertEqual(found[0]["who"], bilibili.user_hash(bilibili.mid_hash(42)))
        path, query, cookies = fake.calls[-1]
        self.assertIn("w_rid", query)  # the comment list is a signed request
        self.assertEqual(cookies["buvid3"], "BUVID3")
        self.assertEqual(cookies["bili_ticket"], "TICKET")

    def test_search_cleans_titles_reads_counts_and_keeps_the_time_range(self):
        self.fake({"/x/web-interface/wbi/search/type": {"code": 0, "data": {"result": [
            {"type": "video", "bvid": "BV1aaaaaaaaa", "aid": 1, "pubdate": NOW - DAY,
             "title": '<em class="keyword">CS2</em> 名场面 &amp; 集锦', "author": "up",
             "play": 12000, "video_review": "1.2万", "review": 35},
            {"type": "video", "bvid": "BV1bbbbbbbbb", "aid": 2, "pubdate": NOW - 40 * DAY,
             "title": "too old", "video_review": 99999},
            {"type": "ketang", "bvid": "BV1cccccccccc"}]}}})
        videos = bilibili.search_videos("CS2", NOW - 7 * DAY, NOW)
        self.assertEqual([v["bvid"] for v in videos], ["BV1aaaaaaaaa"])
        self.assertEqual(videos[0]["title"], "CS2 名场面 & 集锦")
        self.assertEqual(videos[0]["danmu"], 12000)

    def test_related_videos_are_read_like_search_results(self):
        self.fake({"/x/web-interface/archive/related": {"code": 0, "data": [
            {"bvid": "BV1aaaaaaaaa", "aid": 1, "title": "CS2 &amp; 名场面", "pubdate": NOW - DAY,
             "owner": {"mid": 9, "name": "up"}, "stat": {"view": 5000, "danmaku": 321}},
            {"aid": 2, "title": "a bangumi episode, which has no bvid"}]}})
        videos = bilibili.related_videos("BV1sssssssss")
        self.assertEqual([v["title"] for v in videos], ["CS2 & 名场面"])
        self.assertEqual((videos[0]["danmu"], videos[0]["author"], videos[0]["pubdate"]),
                         (321, "up", NOW - DAY))

    def test_a_link_gives_its_bvid_and_a_short_link_is_followed(self):
        self.assertEqual(bilibili.bvid_of("https://www.bilibili.com/video/BV1xx411c7mD/?p=2"),
                         "BV1xx411c7mD")
        self.assertEqual(bilibili.bvid_of("BV1xx411c7mD"), "BV1xx411c7mD")
        self.assertIsNone(bilibili.bvid_of("not a link"))
        response = mock.MagicMock()
        response.__enter__.return_value.geturl.return_value = \
            "https://m.bilibili.com/video/BV1yy411c7mE?share_source=copy"
        with mock.patch.object(bilibili.urllib.request, "urlopen",
                               return_value=response) as urlopen:
            self.assertEqual(bilibili.bvid_of("【CS2 名场面-哔哩哔哩】 https://b23.tv/AbC1234"),
                             "BV1yy411c7mE")
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://b23.tv/AbC1234")

    def test_risk_control_waits_retries_then_gives_up(self):
        self.fake({"/x/web-interface/view": {"code": -352, "message": "风控校验失败"}})
        with mock.patch.object(bilibili.time, "sleep") as sleep, \
                mock.patch("builtins.print"):
            with self.assertRaises(bilibili.Blocked):
                bilibili.video_info("BV1aaaaaaaaa")
        waits = [call.args[0] for call in sleep.call_args_list if call.args[0] >= 1]
        self.assertEqual(waits, [bilibili.RETRY_WAIT, bilibili.RETRY_WAIT * 2])


    def test_a_captcha_is_never_retried(self):
        fake = self.fake({"/x/web-interface/wbi/search/type":
                          {"code": 0, "data": {"v_voucher": "v"}}})
        with mock.patch.object(bilibili.time, "sleep") as sleep:
            with self.assertRaises(bilibili.Captcha):  # a kind of Blocked
                bilibili.search_videos("CS2", NOW - DAY, NOW)
        self.assertFalse([call for call in sleep.call_args_list if call.args[0] >= 1])
        searches = [call for call in fake.calls if call[0].endswith("/search/type")]
        self.assertEqual(len(searches), 1)

    def test_an_answer_that_isnt_json_is_an_error(self):
        self.fake({"/x/web-interface/view": b"<html>busy</html>"})
        with self.assertRaises(bilibili.Error):
            bilibili.video_info("BV1aaaaaaaaa")

def message(who, text, sent, n=[0]):
    n[0] += 1
    return {"id": f"d{n[0]}", "kind": "danmu", "who": who, "text": text, "sent": sent}


class PhraseTest(unittest.TestCase):
    def setUp(self):
        # Entries from your own ⚙ window count as known phrases: test with none
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(settings.reload)  # runs last, after the real settings file is back
        patcher = mock.patch.dict(os.environ, {
            "SBTRANSLATOR_SETTINGS": os.path.join(folder.name, "settings.json")})
        patcher.start()
        self.addCleanup(patcher.stop)
        settings.reload()

    def test_your_own_entries_count_as_known(self):
        settings.set_entry("memes", "g2", "鸡兔")
        self.assertIn("鸡兔", surf.known_phrases())

    def test_clean(self):
        self.assertEqual(surf.clean("回复 @某人 :绷不住了[doge]"), "绷不住了")
        self.assertEqual(surf.clean("ＹＹＤＳ"), "yyds")
        self.assertEqual(surf.clean("哈哈哈哈哈哈哈"), "哈哈哈")
        self.assertEqual(surf.clean("看 https://b23.tv/abc @小明 好"), "看 好")

    def test_freedom(self):
        self.assertGreater(surf.freedom({None: 5}), 2)  # always written on its own
        self.assertEqual(surf.freedom({"绷": 5}), 0)  # always attached to the same character

    def test_new_and_rising_phrases_are_found_and_the_rest_left_out(self):
        recent, before = NOW - DAY, NOW - 20 * DAY
        lines = []
        # Before: 300 people; 4 of them already say 下饭, and 老马又来了 is old news
        for i in range(300):
            text = "这把真下饭" if i < 4 else ["这波操作太秀了", "老马又来了", "哈哈哈", "好家伙"][i % 4]
            lines.append(("cs2", f"BV1old{i % 3}", message(f"b{i}", text, before)))
        # Recent: 300 people in 4 videos
        for i in range(300):
            video = f"BV1new{i % 4}"
            if i < 30:
                text = "绷不住了"  # new, written on its own
            elif i < 45:
                text = "这下真的绷不住了"  # and inside longer lines
            elif i < 65:
                text = "zywoo yyds"  # yyds: a new Latin-letter word (zywoo is known)
            elif i < 125:
                text = ["这把下饭了", "太下饭了吧", "下饭操作", "真下饭"][i % 4]  # rising
            elif i < 150:
                text, video = "这个视频第一", "BV1new0"  # one video's own thing
            elif i < 225:
                text = "老马又来了"  # as common as before: not news
            else:
                text = "nice 哈哈哈"  # an English word and an everyday danmu
            lines.append(("cs2", video, message(f"r{i}", text, recent)))

        found, summary = surf.find_candidates(lines, days=7, now=NOW, min_before=50)
        by_phrase = {c["phrase"]: c for c in found}
        self.assertEqual(by_phrase["绷不住了"]["status"], "new")
        self.assertEqual(by_phrase["绷不住了"]["people"], 45)
        self.assertEqual(by_phrase["yyds"]["status"], "new")
        self.assertEqual(by_phrase["下饭"]["status"], "rising")
        self.assertGreaterEqual(by_phrase["下饭"]["rise"], surf.RISE)
        for left_out in ["不住了", "绷不住", "老马又来了", "这个视频第一", "zywoo", "nice",
                         "哈哈哈", "这把", "下饭了", "真的", "下饭操作"]:
            self.assertNotIn(left_out, by_phrase)
        examples = by_phrase["绷不住了"]["examples"]
        self.assertGreater(len({e["video"] for e in examples}), 1)  # from several videos
        self.assertEqual(examples[0]["text"], "这下真的绷不住了")  # context first
        self.assertTrue(summary["cs2"]["baseline"])

    def test_a_handful_of_new_users_is_not_enough_to_call_it_new(self):
        lines = [("cs2", "BV1old", message(f"b{i}", "好家伙", NOW - 20 * DAY)) for i in range(300)]
        lines += [("cs2", f"BV1new{i % 2}", message(f"r{i}", "好家伙" if i > 3 else "神中神",
                                                    NOW - DAY)) for i in range(300)]
        found, _ = surf.find_candidates(lines, days=7, now=NOW, min_before=50)
        self.assertEqual(found, [])  # 4 people: could be chance, so it waits for more

    def test_surprise(self):
        self.assertAlmostEqual(surf.surprise(10, 0, 100, 100), 10 * 0.30103, places=3)
        self.assertLess(surf.surprise(10, 10, 100, 100), 1)  # just as common before

    def test_without_earlier_lines_everything_is_a_first_look(self):
        lines = [("lol", f"BV1v{i % 2}", message(f"p{i}", "绷不住了", NOW - DAY))
                 for i in range(4)]
        found, summary = surf.find_candidates(lines, days=7, now=NOW)
        self.assertEqual([(c["phrase"], c["status"]) for c in found], [("绷不住了", "first look")])
        self.assertFalse(summary["lol"]["baseline"])


class FindVideosTest(unittest.TestCase):
    """Finding a game's videos without the search: by following related videos."""

    SOURCE = {"words": ["CS2", "CSGO"], "seeds": ["https://www.bilibili.com/video/BV1seed00001"]}

    def video(self, bvid, title, days_ago, danmu):
        return {"bvid": bvid, "aid": 1, "title": title, "author": "up",
                "pubdate": NOW - days_ago * DAY, "plays": 0, "danmu": danmu, "comments": 0}

    def test_it_follows_related_videos_from_the_seeds_to_the_newest_ones(self):
        related = {
            "BV1seed00001": [self.video("BV1new000001", "CS2 major 决赛", 1, 500),
                             self.video("BV1old000001", "CSGO 回忆", 60, 9000),
                             self.video("BV1lol000001", "英雄联盟 S16", 1, 99999)],
            "BV1new000001": [self.video("BV1new000002", "cs2 名场面", 2, 800),
                             self.video("BV1mid000001", "CS2 老比赛", 20, 300)]}
        followed = []

        def related_videos(bvid):
            followed.append(bvid)
            return related.get(bvid, [])

        search = {}
        with mock.patch.object(bilibili, "search_videos",
                               side_effect=bilibili.Captcha("captcha")) as searched, \
                mock.patch.object(bilibili, "related_videos", side_effect=related_videos), \
                mock.patch("builtins.print"):
            recent, older, seeds = surf.find_videos("cs2", self.SOURCE, {}, NOW, 7, 3, search)
            surf.find_videos("valorant", {"words": ["无畏契约"], "seeds": []}, {}, NOW, 7, 3,
                             search)
        self.assertEqual(searched.call_count, 1)  # after a captcha the search is skipped
        self.assertEqual(followed, ["BV1seed00001", "BV1new000001", "BV1new000002"])
        # The week's busiest first, topped up from the last 30 days; other games are left out
        self.assertEqual([v["bvid"] for v in recent],
                         ["BV1new000002", "BV1new000001", "BV1mid000001"])
        self.assertEqual([v["bvid"] for v in older], ["BV1old000001"])
        self.assertEqual(seeds, ["BV1seed00001"])

    def test_without_seeds_it_starts_from_the_games_videos_read_before(self):
        videos = {"BV1read00001": {"game": "cs2", "pubdate": NOW - 9 * DAY},
                  "BV1read00002": {"game": "cs2", "pubdate": NOW - 3 * DAY},
                  "BV1read00003": {"game": "lol", "pubdate": NOW - DAY}}
        followed = []
        with mock.patch.object(bilibili, "search_videos", return_value=[]), \
                mock.patch.object(bilibili, "related_videos",
                                  side_effect=lambda bvid: followed.append(bvid) or []), \
                mock.patch("builtins.print"):
            surf.find_videos("cs2", {"words": ["CS2"], "seeds": []}, videos, NOW, 7, 3)
        self.assertEqual(followed, ["BV1read00002", "BV1read00001"])  # newest first

    def test_fetch_reads_the_week_some_older_videos_and_new_seeds(self):
        recent = [self.video("BV1new000001", "CS2", 1, 500)]
        older = [self.video("BV1old000001", "CS2", 60, 900)]
        read = []

        def read_video(video, game, videos):
            read.append((video["bvid"], video.get("baseline", False)))
            videos[video["bvid"]] = {"game": game, "title": "t", "read": []}
            return 1, 0

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(surf, "VIDEOS_FILE", os.path.join(folder, "videos.json")), \
                mock.patch.object(surf, "find_videos",
                                  return_value=(recent, older, ["BV1seed00001"])), \
                mock.patch.object(surf, "read_video", side_effect=read_video), \
                mock.patch("builtins.print"):
            surf.fetch(["cs2"], {"cs2": self.SOURCE}, 7, 3, NOW)
        self.assertEqual(read, [("BV1new000001", False), ("BV1old000001", True),
                                ("BV1seed00001", False)])

    def test_the_first_version_of_the_sources_file_still_works(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "sources.json")
            with open(path, "w", encoding="utf-8") as f:
                lol = {"words": ["LPL"], "seeds": ["BV1xx411c7mD"]}
                json.dump({"bilibili": {"_comment": "", "cs2": ["CS2"], "lol": lol}}, f)
            with mock.patch.object(surf, "SOURCES_FILE", path):
                self.assertEqual(surf.sources(), {
                    "cs2": {"words": ["CS2"], "seeds": []},
                    "lol": {"words": ["LPL"], "seeds": ["BV1xx411c7mD"]}})


class ReadVideoTest(unittest.TestCase):
    def test_reading_a_video_twice_only_adds_new_messages(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(surf, "SURF_DIR", folder), \
                mock.patch.object(bilibili, "video_info", return_value={
                    "bvid": "BV1x", "aid": 1, "title": "t", "author": "a", "pubdate": NOW,
                    "danmu": 2, "comments": 1, "parts": [10, 11]}), \
                mock.patch.object(bilibili, "danmu", side_effect=lambda cid: [
                    {"id": f"d{cid}", "kind": "danmu", "who": "w", "text": "x", "sent": NOW}]), \
                mock.patch.object(bilibili, "comments", side_effect=bilibili.Error("closed")):
            videos = {}
            self.assertEqual(surf.read_video({"bvid": "BV1x"}, "cs2", videos), (2, 0))
            self.assertEqual(surf.read_video({"bvid": "BV1x"}, "cs2", videos), (0, 0))
            self.assertEqual(len(list(surf.load_messages(videos))), 2)
            self.assertEqual(videos["BV1x"]["game"], "cs2")
            self.assertEqual(len(videos["BV1x"]["read"]), 2)


if __name__ == "__main__":
    unittest.main()
