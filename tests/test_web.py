"""Offline tests for looking memes up on the web outside Bilibili (web.py, and its part of
explainers.py, judging.py and the sources command).

They never go on the web: made-up pages in the shapes Bing, DuckDuckGo, Baidu, 百度百科,
萌娘百科 and 小鸡词典 send stand in, and a fake model. So these check what is read out of the
pages and what becomes of it. Whether those sites still answer this way from your computer,
and what they find, only a real run shows (py -m memelearner sources 阿一古 --explain).
"""

import base64
import contextlib
import gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
from unittest import mock

import memelearner
from memelearner import cli, explainers, judging, store, surf, web

from .test_explainers import Folder, video


def bing_link(url):
    """A result link the way Bing writes it: its redirect, the address in base64."""
    wrapped = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return f"https://cn.bing.com/ck/a?!&amp;&amp;p=abc&amp;u=a1{wrapped}&amp;ntb=1"


BING = f"""<html><head><title>正义必胜是什么梗 - 搜索</title></head><body><ol id="b_results">
<li class="b_algo"><h2><a href="{bing_link('https://www.bilibili.com/video/BV1web000001')}"
 h="ID=SERP">正义必胜是什么梗？_哔哩哔哩_bilibili</a></h2>
<div class="b_caption"><p>观众一起刷的口号，支持被看好的一方……</p></div></li>
<li class="b_algo"><h2><a href="https://zh.moegirl.org.cn/正义必胜">正义必胜 - 萌娘百科</a></h2>
<div class="b_caption"><p>正义必胜是一句口号。</p></div></li>
<li class="b_algo"><h2><a href="https://www.zhihu.com/question/1">正义必胜是什么梗？ - 知乎</a></h2>
<div class="b_caption"><p>正义必胜的意思是……</p></div></li>
<li class="b_algo"><h2><a href="https://www.douyin.com/video/1">正义必胜 合集</a></h2>
<p>正义必胜名场面</p></li>
<li class="b_algo"><h2><a href="https://cn.bing.com/videos/search?q=x">正义必胜的视频</a></h2></li>
<li class="b_algo"><h2><a href="https://example.com/a">别的东西</a></h2><p>和它无关</p></li>
</ol></body></html>"""

DUCKDUCKGO = """<html><body>
<div class="result results_links results_links_deep web-result ">
<div class="links_main links_deep result__body"><h2 class="result__title">
<a rel="nofollow" class="result__a"
 href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.bilibili.com%2Fvideo%2FBV1web000002&amp;rut=1">
阿一古是什么梗 - 哔哩哔哩</a></h2>
<a class="result__snippet" href="x">阿一古是韩语<b>아이고</b>的音译</a></div></div>
<div class="result result--ad"><div class="links_main"><a class="result__a"
 href="https://ad.example.com">阿一古 广告</a></div></div>
</body></html>"""

BAIDU = """<html><body>
<div class="result c-container xpath-log new-pmd" mu="https://baike.baidu.com/item/阿一古">
<h3 class="t"><a href="http://www.baidu.com/link?url=xyz">阿一古_百度百科</a></h3>
<div class="c-abstract">阿一古是韩语感叹词的音译。</div></div>
<div class="result c-container" mu="https://www.baidu.com/s?wd=related">
<h3><a href="x">相关搜索</a></h3></div>
</body></html>"""

CAPTCHA = "<html><head><title>百度安全验证</title></head><body>请输入验证码</body></html>"

# What Bing sent the bot for 龙颜大悦 on 2026-11-11: pages about something else entirely
OFF_TOPIC = """<ol id="b_results"><li class="b_algo"><h2><a href="https://www.zhihu.com/question/9">
为什么主机隔一段时间向网关发送ARP请求？ - 知乎</a></h2><p>ARP 协议把 IP 地址解析成 MAC 地址</p></li>
<li class="b_algo"><h2><a href="https://www.spotify.com/download">Spotify – Download</a></h2>
<p>Listen to music</p></li></ol>"""


def duckduckgo(title, url, snippet=""):
    """DuckDuckGo's page without scripts with one result."""
    return (f'<div class="result results_links web-result"><div class="links_main result__body">'
            f'<h2 class="result__title"><a rel="nofollow" class="result__a" href="{url}">{title}'
            f'</a></h2><a class="result__snippet" href="x">{snippet}</a></div></div>')

# 萌娘百科's mobile site, where its search goes when it has an entry of that name
MOEGIRL = ("https://mzh.moegirl.org.cn/%E6%AD%A3%E4%B9%89%E5%BF%85%E8%83%9C", """<html><head>
<title>正义必胜 - 萌娘百科 万物皆可萌的百科全书</title></head><body><div id="mw-navigation">正义必胜
菜单</div><h1 id="firstHeading" class="firstHeading" lang="zh-CN">正义必胜</h1>
<div id="mw-content-text" class="mw-body-content"><div class="mw-parser-output"><p>别的</p>
<p><b>正义必胜</b>是一句口号。</p><p>完。</p></div></div></body></html>""")

# What Bing finds for 正义必胜是什么梗 site:bilibili.com (web.QUERY): Bilibili's pages, and one
# elsewhere (an engine doesn't always keep to site:)
BILIBILI = f"""<ol id="b_results">
<li class="b_algo"><h2><a href="{bing_link('https://www.bilibili.com/video/BV1web000001')}">
正义必胜是什么梗？_哔哩哔哩_bilibili</a></h2><div class="b_caption"><p>观众一起刷的口号</p></div></li>
<li class="b_algo"><h2><a href="https://www.bilibili.com/read/cv100">正义必胜是什么梗？ - 哔哩哔哩</a>
</h2><div class="b_caption"><p>英雄联盟比赛的弹幕里常有人刷正义必胜</p></div></li>
<li class="b_algo"><h2><a href="https://t.bilibili.com/300">正义必胜 合集</a></h2>
<p>LPL 正义必胜名场面</p></li>
<li class="b_algo"><h2><a href="https://www.bilibili.com/read/cv999">正义必胜！</a></h2>
<p>足球比赛集锦</p></li>
<li class="b_algo"><h2><a href="https://www.bilibili.com/read/cv555">电竞圈的口号大全</a></h2>
<p>正义必胜、阿一古……</p></li>
<li class="b_algo"><h2><a href="https://space.bilibili.com/1">正义必胜的个人空间</a></h2>
<p>正义必胜</p></li>
<li class="b_algo"><h2><a href="https://search.bilibili.com/all?keyword=正义必胜">
正义必胜-哔哩哔哩_bilibili搜索</a></h2></li>
<li class="b_algo"><h2><a href="https://www.zhihu.com/question/1">正义必胜是什么梗？ - 知乎</a></h2>
<p>正义必胜的意思是……</p></li></ol>"""

# A Bilibili article that explains it, and one about football
ARTICLE = """<html><head><title>正义必胜是什么梗？ - 哔哩哔哩</title></head><body>
<div>专栏</div><p>英雄联盟比赛的弹幕里常有人刷。</p>
<p>正义必胜就是给更受欢迎的一方加油，对面黑粉多的时候刷得最多。</p><p>完。</p></body></html>"""
FOOTBALL = """<html><head><title>正义必胜！ - 哔哩哔哩</title></head><body>
<p>足球比赛集锦的弹幕里，正义必胜刷满了屏。</p><p>完。</p></body></html>"""

ZHIHU = """<html><head><title>正义必胜是什么梗？ - 知乎</title></head><body>
<div>别的回答</div><p>这句口号是观众一起刷的。</p>
<p>正义必胜就是给更受欢迎的一方加油，对面黑粉多的时候刷得最多。</p>
<p>完。</p><script>var x = "正义必胜";</script></body></html>"""


class FakeWeb:
    """Stands in for web._fetch: the first (part of an address, answer) whose part is in
    the address answers it. An answer is a page, a dict (JSON), (the address it ends at,
    a page), an HTTP error status, or an exception to raise. Anything else gets an empty
    page."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, data=None, headers=None):
        url = urllib.parse.unquote(url)
        self.calls.append(url)
        answer = next((answer for part, answer in self.answers if part in url), "")
        if isinstance(answer, int):
            raise urllib.error.HTTPError(url, answer, "refused", {}, io.BytesIO(b""))
        if isinstance(answer, Exception):
            raise answer
        final, body = answer if isinstance(answer, tuple) else (url, answer)
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False)
        return 200, final, body.encode("utf-8"), "text/html; charset=utf-8"


class WebTest(Folder):
    def web(self, *answers):
        fake = FakeWeb(*answers)
        patcher = mock.patch.object(web, "_fetch", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def quietly(self, call, *args, **options):
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            found = call(*args, **options)
        return found, printed.getvalue()


class SearchTest(WebTest):
    def test_each_search_engines_results_page_is_read(self):
        results = web.parse_bing(BING)
        self.assertEqual(results[0], {
            "title": "正义必胜是什么梗？_哔哩哔哩_bilibili",
            "url": "https://www.bilibili.com/video/BV1web000001",  # Bing's redirect undone
            "snippet": "观众一起刷的口号，支持被看好的一方……", "site": "bilibili.com",
            "engine": "bing"})
        self.assertEqual(results[1]["url"], "https://zh.moegirl.org.cn/正义必胜")
        found = web.parse_duckduckgo(DUCKDUCKGO)  # its ad left out
        self.assertEqual([(r["url"], r["snippet"]) for r in found],
                         [("https://www.bilibili.com/video/BV1web000002", "阿一古是韩语아이고的音译")])
        found = web.parse_baidu(BAIDU)
        self.assertEqual(found[0]["url"], "https://baike.baidu.com/item/阿一古")  # its mu
        self.assertEqual(found[0]["snippet"], "阿一古是韩语感叹词的音译。")
        # The engines' own pages (Bing's videos, Baidu's other searches) aren't results
        self.web(("cn.bing.com/search", BING), ("baidu.com/s", BAIDU))
        found = web.search_with("bing", "正义必胜是什么梗", ["正义必胜"])
        self.assertNotIn("https://cn.bing.com/videos/search?q=x", [r["url"] for r in found])
        self.assertEqual(len(found), 5)
        self.assertEqual([r["url"] for r in web.search_with("baidu", "阿一古是什么梗")],
                         ["https://baike.baidu.com/item/阿一古"])
        # A page in a layout the parsers don't know: the links that name the phrase
        found = web.parse_any('<a href="https://a.cn/1">阿一古什么意思</a> 是韩语 <a href="/x">'
                              '阿一古</a> <a href="https://b.cn">别的</a>', "bing", ["阿一古"])
        self.assertEqual([r["url"] for r in found], ["https://a.cn/1"])

    def test_a_site_that_wants_a_captcha_or_refuses_is_left_alone_for_the_run(self):
        fake = self.web(("cn.bing.com", CAPTCHA), ("duckduckgo.com", 403),
                        ("baidu.com/s", BAIDU), ("bilibili.com/read", 412))
        found, printed = self.quietly(web.search, "阿一古是什么梗", ["阿一古"])
        self.assertEqual([r["engine"] for r in found], ["baidu"])
        self.assertIn("bing wants a captcha solved", printed)
        self.assertIn("duckduckgo refused the bot (HTTP 403)", printed)
        self.assertEqual(set(web.refused), {"bing", "duckduckgo"})
        asked = len(fake.calls)
        self.quietly(web.search, "正义必胜是什么梗", ["正义必胜"])
        self.assertEqual(len(fake.calls), asked + 1)  # only Baidu
        with self.assertRaises(web.Refused):  # Bilibili's "too many requests"
            web.get("https://www.bilibili.com/read/cv1")
        self.assertEqual(web.refused["bilibili.com"], "HTTP 412")
        # Nobody answers: None (try again next run); answered with nothing: []
        web.reset()
        self.web(("cn.bing.com", CAPTCHA), ("duckduckgo.com", 429), ("baidu.com", CAPTCHA))
        self.assertIsNone(self.quietly(web.search, "阿一古是什么梗")[0])
        web.reset()
        self.web()
        self.assertEqual(self.quietly(web.search, "阿一古是什么梗")[0], [])
        # A page with a lot to say that mentions 验证码 isn't a captcha
        web.reset()
        long = "<p>" + "阿一古是韩语感叹词。" * 400 + "登录要输入验证码</p>"
        self.web(("example.com", long), ("captcha.example.org", "<p>ok</p>"))
        self.assertIn("阿一古", web.get("https://example.com/a")[0])
        # ... but a page that sends you to a captcha is
        self.web(("example.com", ("https://wappass.baidu.com/static/captcha", "<p>x</p>")))
        with self.assertRaises(web.Refused):
            web.get("https://example.com/b")

    def test_results_that_name_the_phrase_nowhere_are_passed_over(self):
        fake = self.web(("cn.bing.com", OFF_TOPIC), ("duckduckgo.com", DUCKDUCKGO))
        found, printed = self.quietly(web.search, "阿一古是什么梗", ["阿一古"])
        self.assertEqual([r["engine"] for r in found], ["duckduckgo"])
        self.assertEqual(printed, "")
        self.assertNotIn("bing", web.refused)  # it may answer properly the next time
        self.assertEqual(len(fake.calls), 2)
        # Named nowhere by any of them: [] (nothing found), not the pages about something else
        self.web(("cn.bing.com", OFF_TOPIC), ("duckduckgo.com", ""), ("baidu.com", ""))
        self.assertEqual(self.quietly(web.search, "阿一古是什么梗", ["阿一古"])[0], [])
        self.assertFalse(web.named(web.parse_bing(OFF_TOPIC), ["龙颜大悦"]))
        self.assertTrue(web.named(web.parse_bing(BING), ["正义必胜"]))

    def test_an_error_page_is_kept_as_the_site_sent_it(self):
        web.raw = os.path.join(self.folder, "raw")
        self.addCleanup(setattr, web, "raw", None)
        page = gzip.compress("<title>百度安全验证</title>".encode("utf-8"))  # Baike's 403

        def refuse(url, data=None, headers=None):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {"Content-Encoding": "gzip"},
                                         io.BytesIO(page))

        with mock.patch.object(web, "_fetch", refuse), self.assertRaises(web.Refused):
            web.get("https://baike.baidu.com/item/阿一古")
        [kept] = [name for name in os.listdir(web.raw) if name.endswith(".html")]
        with open(os.path.join(web.raw, kept), encoding="utf-8") as f:
            self.assertEqual(f.read(), "<title>百度安全验证</title>")

    def test_a_site_that_cant_be_reached_twice_is_left_alone(self):
        fake = self.web(("duckduckgo.com", urllib.error.URLError("timed out")))
        for _ in range(web.FAILS):
            with self.assertRaises(urllib.error.URLError):
                web.get("https://html.duckduckgo.com/html/?q=1", site="duckduckgo")
        with self.assertRaises(web.Refused):
            web.get("https://html.duckduckgo.com/html/?q=1", site="duckduckgo")
        self.assertEqual(len(fake.calls), web.FAILS)

    def test_with_site_bilibili_only_bilibilis_pages_are_results(self):
        fake = self.web(("cn.bing.com", BILIBILI))
        found = web.search_with("bing", web.QUERY.format("正义必胜"), ["正义必胜"])
        self.assertIn("正义必胜是什么梗 site:bilibili.com", fake.calls[0])
        self.assertNotIn("https://www.zhihu.com/question/1", [r["url"] for r in found])
        self.assertTrue(all(web.on(r["url"], "bilibili.com") for r in found))
        self.assertEqual(len(found), 7)
        self.assertFalse(web.on("https://notbilibili.com/a", "bilibili.com"))
        # Without names, a result has to name the query's words, not its site:
        self.assertEqual(len(self.quietly(web.search, web.QUERY.format("正义必胜"))[0]), 7)
        # The pages: their titles name it (电竞圈的口号大全 only names it below); Bilibili's
        # articles are read, its other pages only used for their snippets; its searches and
        # people's spaces aren't results
        self.assertEqual([(p["url"], p["read"]) for p in web.pages(found, ["正义必胜"])],
                         [("https://www.bilibili.com/read/cv100", True),
                          ("https://www.bilibili.com/read/cv999", True),
                          ("https://t.bilibili.com/300", False)])

    def test_the_videos_and_the_pages_that_explain_a_phrase_come_first(self):
        results = web.parse_bing(BING)
        self.assertEqual(web.videos(results, ["正义必胜"]), [
            {"bvid": "BV1web000001", "title": "正义必胜是什么梗？", "how": "web"}])
        pages = web.pages(results, ["正义必胜"])
        # Explainer sites first, the one that asks what it means (是什么梗) before the one that
        # only names it; a video site's page is only used for its snippet; a result whose
        # title doesn't name it, or Bing's own page, is left out
        self.assertEqual([(p["site"], p["read"]) for p in pages],
                         [("zhihu.com", True), ("zh.moegirl.org.cn", True),
                          ("douyin.com", False)])
        self.assertEqual(web.around("一\n二\n三 正义必胜\n四\n五\n六", "正义必胜"), "二\n三 正义必胜\n四\n五")
        self.assertEqual(web.unwrap("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.cn%2F1&rut=2"),
                         "https://a.cn/1")


class WikiTest(WebTest):
    def test_baike_gives_its_entrys_summary(self):
        self.web(("baike.baidu.com/item/阿一古", (
            "https://baike.baidu.com/item/阿一古/123",
            '<html><head><title>阿一古_百度百科</title><meta name="description" content="阿一古'
            '是韩语感叹词아이고的音译，表示哎呀。"></head><body>...</body></html>')),
            ("baike.baidu.com/item/没有这个", ("https://baike.baidu.com/search/none?word=x", "")))
        self.assertEqual(web.baike("阿一古"), {
            "url": "https://baike.baidu.com/item/阿一古/123", "site": "baike.baidu.com",
            "title": "阿一古_百度百科", "text": "阿一古是韩语感叹词아이고的音译，表示哎呀。"})
        self.assertIsNone(web.baike("没有这个"))
        self.web(("baike.baidu.com", "<title>百度百科-验证</title>"))
        with self.assertRaises(web.Refused):
            web.baike("阿一古")

    def test_moegirl_gives_the_entry_its_search_goes_to(self):
        fake = self.web(("search=正义必胜", MOEGIRL))
        # The lines of the entry around the phrase (not its menus), at the address people open
        self.assertEqual(web.moegirl("正义必胜"), {
            "url": "https://zh.moegirl.org.cn/%E6%AD%A3%E4%B9%89%E5%BF%85%E8%83%9C",
            "site": "moegirl.org.cn", "title": "正义必胜", "text": "别的\n正义必胜是一句口号。\n完。"})
        self.assertIn("mzh.moegirl.org.cn/index.php?search=正义必胜&", fake.calls[0])
        self.assertIn("&go=Go", fake.calls[0])  # straight to the entry of that name
        # No entry of that name: the search stays on its own results
        self.web(("moegirl", ("https://mzh.moegirl.org.cn/index.php?search=阿一古&title=Special:"
                              "%E6%90%9C%E7%B4%A2&go=Go", '<div id="mw-content-text">阿一古</div>')))
        self.assertIsNone(web.moegirl("阿一古"))
        # What its API says to a visitor now, or a check for bots: no wiki page, so it is left
        # alone for the rest of the run
        self.web(("moegirl", ("https://mzh.moegirl.org.cn/阿一古", '{"error": {"code": '
                              '"action-notallowed", "info": "Unauthorized API call"}}')))
        with self.assertRaises(web.Refused):
            web.moegirl("阿一古")
        with self.assertRaises(web.Refused) as refused:
            web.moegirl("正义必胜")
        self.assertTrue(refused.exception.again)  # old news: explain doesn't print it again

    def test_jikipedia_gives_what_its_page_shows_about_it(self):
        self.web(("jikipedia.com", "<div>搜索 阿一古</div><div>阿一古</div>"
                                   "<div>韩语아이고的音译，哎呀的意思</div>"))
        self.assertEqual(web.jikipedia("阿一古")["text"], "搜索 阿一古\n阿一古\n韩语아이고的音译，哎呀的意思")
        self.web(("jikipedia.com", "<div>阿一古</div>"))  # only what was searched for
        self.assertIsNone(web.jikipedia("阿一古"))

    def test_check_says_which_places_answer(self):
        folder = os.path.join(self.folder, "sources")
        self.web(("cn.bing.com", BING), ("duckduckgo.com", 403), ("baidu.com/s", CAPTCHA),
                 ("baike.baidu.com", ("https://baike.baidu.com/search/none", "")),
                 ("search=正义必胜", MOEGIRL))
        report, _ = self.quietly(web.check, ["正义必胜", "阿一古"], folder=folder)
        self.assertEqual(report["places"]["bing"], {"asked": 2, "answered": 2, "found": 1,
                                                    "refused": ""})
        self.assertEqual(report["places"]["duckduckgo"]["answered"], 0)
        self.assertIn("HTTP 403", report["places"]["duckduckgo"]["refused"])
        self.assertEqual(report["places"]["baidu"]["answered"], 0)
        self.assertEqual(report["places"]["moegirl"]["found"], 1)  # no entry named 阿一古
        found = report["phrases"]["正义必胜"]["bing"]
        self.assertEqual([v["bvid"] for v in found["videos"]], ["BV1web000001"])
        # Every answer kept as it came, to fix a parser with
        with open(os.path.join(folder, "raw", "index.tsv"), encoding="utf-8") as f:
            self.assertEqual(len(f.readlines()), len(os.listdir(os.path.join(folder, "raw"))) - 1)


class FakeModel:
    """Stands in for judging._ask: a web page that says what 正义必胜 means explains it, a
    video titled 看完就懂 doesn't, and the judge takes the meaning a page or video says."""

    def __init__(self, page=2, quote="正义必胜就是给更受欢迎的一方加油"):
        self.page, self.quote = page, quote
        self.asked = []

    def __call__(self, messages, model):
        question = json.loads(messages[1]["content"])
        self.asked.append((messages[0]["content"], question))
        if messages[0]["content"] == explainers.PAGE_SUMMARY:
            return json.dumps({"about": True, "page": self.page, "meaning": "给受欢迎的一方加油",
                               "origin": "", "english": "cheering", "quote": self.quote},
                              ensure_ascii=False)
        if messages[0]["content"] == explainers.SUMMARY:
            return json.dumps({"about": False})
        said = (question["videos"] or question["pages"] or [{}])[0].get("says", "模型猜的")
        return json.dumps({"kind": "meme", "meaning": said, "english": "x", "literal": "",
                           "source": "", "why": "..."}, ensure_ascii=False)


class LookUpTest(WebTest):
    def setUp(self):
        super().setUp()
        store.accept("正义必胜", meaning="观众一起刷的口号", games=["lol"])
        store.accept("阿一古", meaning="韩语感叹词的音译", games=["lol"])
        self.model = FakeModel()
        for patcher in (mock.patch.object(judging, "_ask", self.model),
                        mock.patch.object(judging, "MODEL", "deepseek-r1:14b"),
                        mock.patch.object(judging, "_get_json",
                                          lambda path: {"models": [{"name": "deepseek-r1:14b"}]}),
                        mock.patch.object(explainers, "can_hear", return_value=False),
                        mock.patch.object(explainers.bilibili, "search_videos", return_value=[]),
                        mock.patch.object(explainers, "read", side_effect=lambda bvid, *a: {
                            "bvid": bvid, "url": f"https://www.bilibili.com/video/{bvid}",
                            "title": "看完就懂", "zone": "电子竞技", "said": "",
                            "comments": [], "danmu": []})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_videos_a_search_engine_finds_are_read_once_a_week(self):
        explainers.remember([video("BV1explain03", "正义必胜的由来")])
        fake = self.web(("cn.bing.com", BILIBILI))
        chant = {"phrase": "正义必胜", "game": "lol", "variants": []}
        data = explainers.load()
        found = explainers.find(chant, data=data)
        # The ones seen first, then the search engine's, then (none found) no Bilibili search
        self.assertEqual([(v["bvid"], v["how"]) for v in found],
                         [("BV1explain03", "related"), ("BV1web000001", "web")])
        entry = data["phrases"]["lol:正义必胜"]
        # Only Bilibili was searched, and only its pages are kept, for step 4
        self.assertEqual(entry["web"]["query"], "正义必胜是什么梗 site:bilibili.com")
        self.assertEqual(len(entry["web"]["results"]), 7)
        self.assertTrue(all(web.on(r["url"], "bilibili.com") for r in entry["web"]["results"]))
        explainers.find(chant, data=data)
        self.assertEqual(len([c for c in fake.calls if "bing.com" in c]), 1)  # once a week
        explainers.bilibili.search_videos.assert_not_called()

    def test_with_no_video_explaining_it_bilibilis_pages_are_read_and_judged(self):
        fake = self.web(("cn.bing.com", BILIBILI), ("bilibili.com/read/cv100", ARTICLE),
                        ("bilibili.com/read/cv999", FOOTBALL), ("search=正义必胜", MOEGIRL))
        found, printed = self.quietly(memelearner.explain, ["正义必胜"])
        summary = found["lol:正义必胜"][-1]
        # The model said page 2 (a post's snippet), but quoted page 1 (the article): page 1
        self.assertEqual({k: summary[k] for k in ("kind", "url", "site", "about", "meaning",
                                                   "quote", "snippet", "pages")},
                         {"kind": "page", "url": "https://www.bilibili.com/read/cv100",
                          "site": "bilibili.com", "about": True, "meaning": "给受欢迎的一方加油",
                          "quote": "正义必胜就是给更受欢迎的一方加油", "snippet": False, "pages": 2})
        shown = next(q for prompt, q in self.model.asked if prompt == explainers.PAGE_SUMMARY)
        self.assertEqual(shown["game"], judging.GAMES["lol"])  # 正义必胜 is a learned LoL meme
        # The article, then the post (only its snippet); not the article about football
        self.assertEqual([(p["n"], p["site"]) for p in shown["pages"]],
                         [(1, "bilibili.com"), (2, "t.bilibili.com")])
        self.assertIn("英雄联盟比赛的弹幕里常有人刷", shown["pages"][0]["text"])  # around it
        self.assertEqual(shown["pages"][1]["text"], "LPL 正义必胜名场面")
        self.assertIn("bilibili.com  正义必胜是什么梗？ - 哔哩哔哩: 给受欢迎的一方加油", printed)
        self.assertIn("1 more are explained by a web page.", printed)
        entry = explainers.load()["phrases"]["lol:正义必胜"]
        self.assertTrue(explainers.told(entry))
        # The football article is written down with its link and why it wasn't used
        self.assertEqual([(d["url"], d["title"], d["why"]) for d in entry["dropped"]],
                         [("https://www.bilibili.com/read/cv999", "正义必胜！",
                           "not about games: no game word in its title or text")])
        self.assertIn("skipped 正义必胜！: not about games", printed)
        # A Bilibili page explains it: the wikis aren't asked
        self.assertFalse([c for c in fake.calls if "moegirl" in c or "baike" in c])
        # A quote the page doesn't contain is dropped; one from another page names that page
        self.model.quote = "这句话哪里都没有"
        summary = explainers.summarize_pages("正义必胜", "lol", [
            {"url": "u1", "site": "a", "title": "t", "text": "x"},
            {"url": "u2", "site": "b", "title": "t", "text": "正义必胜是口号"}], "m")
        self.assertEqual((summary["url"], summary["quote"]), ("u2", ""))
        self.model.quote, self.model.page = "正义必胜是口号", 1
        summary = explainers.summarize_pages("正义必胜", "lol", [
            {"url": "u1", "site": "a", "title": "t", "text": "x"},
            {"url": "u2", "site": "b", "title": "t", "text": "正义必胜是口号"}], "m")
        self.assertEqual((summary["url"], summary["quote"]), ("u2", "正义必胜是口号"))

    def test_the_wikis_are_asked_when_nothing_on_bilibili_explains_it(self):
        transformers = ("https://baike.baidu.com/item/汽车人/1", '<html><head><title>汽车人_百度'
                        '百科</title><meta name="description" content="汽车人是《变形金刚》里的一族'
                        '机器人。"></head></html>')
        lol = ("https://mzh.moegirl.org.cn/汽车人", '<h1 id="firstHeading">汽车人</h1><div id='
               '"mw-content-text"><p>汽车人是英雄联盟弹幕里的梗：漏了炮车的最后一刀，就是弃车人。</p>'
               '</div>')
        fake = self.web(("baike.baidu.com/item/汽车人", transformers), ("search=汽车人", lol))
        found, printed = self.quietly(memelearner.explain, ["汽车人"], games=["lol"])
        [summary] = found["lol:汽车人"]
        self.assertEqual((summary["url"], summary["about"]),
                         ("https://zh.moegirl.org.cn/汽车人", True))
        shown = next(q for prompt, q in self.model.asked if prompt == explainers.PAGE_SUMMARY)
        self.assertEqual([p["site"] for p in shown["pages"]], ["moegirl.org.cn"])
        entry = explainers.load()["phrases"]["lol:汽车人"]
        self.assertEqual([(d["url"], d["why"]) for d in entry["dropped"]],
                         [("https://baike.baidu.com/item/汽车人/1",
                           "not about games: no game word in its title or text")])
        self.assertIn("Bilibili's pages: nothing that names it and is about games", printed)
        self.assertTrue([c for c in fake.calls if "site:bilibili.com" in c])
        # An entry whose title doesn't name it (a wiki's redirect to another entry)
        self.web(("search=汽车人", ("https://mzh.moegirl.org.cn/变形金刚", '<h1 id="firstHeading">'
                                    '变形金刚</h1><div id="mw-content-text"><p>汽车人的游戏</p></div>')))
        web.reset()
        dropped = []
        self.assertEqual(explainers.read_wikis("汽车人", ["汽车人"], ["moegirl"], dropped), [])
        self.assertEqual([d["why"] for d in dropped], ["its title doesn't name it"])

    def test_a_phrase_whose_search_named_it_nowhere_is_looked_up_again(self):
        # Run 1 on 2026-11-11 kept Bing's pages about something else for 龙颜大悦, and marked
        # the phrase looked up: it is wanted again, and searched again
        now = time.strftime("%Y-%m-%d %H:%M")
        surf._save_json(surf.CANDIDATES_FILE, {"candidates": [
            {"phrase": phrase, "game": "lol", "score": 1, "variants": [],
             "judge": {"verdict": "untested", "meaning": "m", "guess": True}}
            for phrase in ("龙颜大悦", "滔一下")]})
        data = explainers.load()
        data["phrases"]["lol:龙颜大悦"] = {"looked": now, "web": {
            "searched": now, "query": web.QUERY.format("龙颜大悦"),
            "results": web.parse_bing(OFF_TOPIC)}}
        data["phrases"]["lol:滔一下"] = {"looked": now, "web": {
            "searched": now, "query": web.QUERY.format("滔一下"), "results": []}}
        wanted = explainers._wanted(None, None, 10, False, data)
        self.assertEqual([c["phrase"] for c in wanted], ["龙颜大悦"])  # 滔一下: nothing found
        # A search of the whole web, from before the bot searched only Bilibili: again
        data["phrases"]["lol:滔一下"]["web"].pop("query")
        wanted = explainers._wanted(None, None, 10, False, data)
        self.assertEqual([c["phrase"] for c in wanted], ["龙颜大悦", "滔一下"])
        fake = self.web(("cn.bing.com", OFF_TOPIC), ("duckduckgo.com", duckduckgo(
            "龙颜大悦是什么梗_哔哩哔哩_bilibili", "https://www.bilibili.com/video/BV1web000009")))
        entry = data["phrases"]["lol:龙颜大悦"]
        found = explainers._web("龙颜大悦", ["龙颜大悦"], entry, "web", False)
        self.assertEqual([v["bvid"] for v in found], ["BV1web000009"])
        self.assertEqual({r["engine"] for r in entry["web"]["results"]}, {"duckduckgo"})
        asked = len(fake.calls)
        explainers._web("龙颜大悦", ["龙颜大悦"], entry, "web", False)
        self.assertEqual(len(fake.calls), asked)  # this time it named it: kept for a week

    def test_a_site_that_refused_the_bot_is_named_once_in_a_run(self):
        self.web(("baike.baidu.com", 403), ("cn.bing.com", BING), ("zhihu.com", ZHIHU))
        _, printed = self.quietly(memelearner.explain, ["正义必胜", "阿一古"])
        self.assertEqual(printed.count("baike.baidu.com refused the bot (HTTP 403)"), 1)
        self.assertNotIn("earlier in this run", printed)
        self.assertIn("Left alone for the rest of the run: baike.baidu.com (HTTP 403)", printed)

    def test_the_judge_is_shown_what_the_web_pages_say(self):
        page = {"kind": "page", "url": "https://www.zhihu.com/question/1", "site": "zhihu.com",
                "title": "正义必胜是什么梗？", "about": True, "meaning": "给受欢迎的一方加油",
                "origin": "", "quote": ""}
        chant = {"phrase": "正义必胜", "game": "lol", "people": 5, "variants": []}
        verdict = judging.judge_one(chant, ["正义必胜"], [], "m", [page])
        question = self.model.asked[-1][1]
        self.assertEqual(question["pages"], [{"site": "zhihu.com", "title": "正义必胜是什么梗？",
                                              "says": "给受欢迎的一方加油", "origin": ""}])
        self.assertEqual(question["videos"], [])
        self.assertEqual((verdict["verdict"], verdict["meaning"], verdict["guess"]),
                         ("untested", "给受欢迎的一方加油", False))  # a page backs it
        self.assertEqual(verdict["pages"], [{"url": "https://www.zhihu.com/question/1",
                                             "site": "zhihu.com", "title": "正义必胜是什么梗？",
                                             "says": "给受欢迎的一方加油"}])
        self.assertIn("from a web page explaining it, zhihu.com", judging.untested(verdict))
        # The verdict stands until a page says something new
        self.assertTrue(judging._still_good(verdict, chant, "m", [page]))
        self.assertFalse(judging._still_good(verdict, chant, "m", [dict(page, meaning="新的")]))
        # Where it comes from, when a page says so
        roadside = {"phrase": "我们是路边", "game": "lol", "variants": []}
        shown = {"videos": [], "explanations": [], "learned": [],
                 "pages": [{"site": "zhihu.com", "title": "我们是路边", "says": "来自我们是冠军"}]}
        self.assertEqual(judging._source({"source": "我们是冠军", "source_how": "page"},
                                         roadside, shown),
                         {"phrase": "我们是冠军", "how": "page"})

    def test_the_sources_command_writes_what_each_place_found(self):
        self.web(("cn.bing.com", BILIBILI), ("bilibili.com/read/cv100", ARTICLE),
                 ("bilibili.com/read/cv999", FOOTBALL), ("duckduckgo.com", 403))
        folder = os.path.join(self.folder, "out")
        phrases = os.path.join(self.folder, "phrases.txt")
        with open(phrases, "w", encoding="utf-8-sig") as f:
            f.write("# the memes to try\n正义必胜\n\n阿一古\n")
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            cli.main(["sources", "--file", phrases, "--out", folder, "--raw", "--explain",
                      "--no-audio"])
        self.assertIn("1 of 2 phrases are explained by a video or a web page.",
                      printed.getvalue())
        with open(os.path.join(folder, "sources.json"), encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["phrases"], ["正义必胜", "阿一古"])
        self.assertEqual(report["learned"]["阿一古"], store.load()["memes"]["阿一古"]["meaning"])
        self.assertEqual(report["explained"]["正义必胜"]["pages"][0]["site"], "bilibili.com")
        with open(os.path.join(folder, "links.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f),
                             {"正义必胜": ["https://www.bilibili.com/video/BV1web000001"]})
        with open(os.path.join(folder, "sources.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## 正义必胜", text)
        self.assertIn("- duckduckgo: duckduckgo refused the bot (HTTP 403)", text)
        self.assertIn("- page bilibili.com [正义必胜是什么梗？ - 哔哩哔哩](https://www.bilibili.com/read/"
                      "cv100): 给受欢迎的一方加油 「正义必胜就是给更受欢迎的一方加油」", text)
        self.assertIn("- skipped [正义必胜！](https://www.bilibili.com/read/cv999): not about games: "
                      "no game word in its title or text", text)
        self.assertTrue(os.listdir(os.path.join(folder, "raw")))

    def test_a_log_file_gets_what_a_command_prints(self):
        log = os.path.join(self.folder, "log", "log.txt")
        with mock.patch.dict(os.environ, {"MEMELEARNER_LOG": log}), \
                mock.patch("sys.stdout", io.StringIO()) as screen, \
                mock.patch("sys.stderr", io.StringIO()):
            cli.main(["lookup", "阿一古"])
            shown = screen.getvalue()
            sys.stdout.file.close()
        with open(log, encoding="utf-8") as f:
            logged = f.read()
        self.assertIn("py -m memelearner lookup 阿一古", logged)
        self.assertIn("阿一古", shown)
        self.assertTrue(logged.endswith(shown))
