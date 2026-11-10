"""The command line: py -m memelearner COMMAND, or memelearner COMMAND once pip-installed.

    py -m memelearner learn                    read Bilibili, then list the phrases people send
    py -m memelearner judge                    a model tells memes from names
    py -m memelearner explain                  read videos that explain the memes it found
    py -m memelearner compare qwen3:14b deepseek/deepseek-flash   several models side by side
    py -m memelearner candidates               what the last run found, to look through
    py -m memelearner watching                 what the model wasn't sure of yet
    py -m memelearner accept 阿一古 --meaning "..."   learn a phrase as a meme
    py -m memelearner accept chovy同意了 --note "..."   ... merging your note with the meaning
    py -m memelearner reject 下路               turn a phrase down: it isn't listed again
    py -m memelearner forget 下路               undo accept or reject
    py -m memelearner memes                    the memes learned so far
    py -m memelearner lookup 绷不住了            one learned meme
    py -m memelearner find "这下真的绷不住了"     the learned memes in a text
    py -m memelearner live https://live.bilibili.com/6 --until 22:55   record a live room's danmu
    py -m memelearner live-phrases             the phrases in the last recording, and which are new
    py -m memelearner report                   the report in your browser: mark, decide, then 完成
    py -m memelearner live-report              live-phrases, judge and explain, then the report
    py -m memelearner sources 阿一古 --explain   which search engines and wikis explain some memes

py surf.py is short for learn, with the same options (py -m memelearner learn --help), and
learn --judge also judges what it found (learn --explain then also reads videos that explain
the memes, and judges them again). candidates, watching, memes, lookup and find take
--json to print JSON for another program (UTF-8) instead of lines for people, in the shapes
__init__.py describes; lookup exits with 1 when the phrase isn't a learned meme. With
MEMELEARNER_LOG set to a file, what a command prints goes to that file too (and only there
when there's no window, as with pythonw).
"""

import argparse
import json
import os
import sys
import time

from . import (ModelUnavailable, accept, candidates, explainers, find, forget, judging, lookup,
               meanings, memes, nicknames, reject, store, surf, watching, web)


def _print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=1))


def _show_meme(meme):
    games = ", ".join(meme["games"]) or "-"
    meaning = " / ".join(part for part in (meme["meaning"], meme["english"]) if part)
    written = meme.get("variants") or []
    also = f"  (also {', '.join(written[:3])}{' ...' if len(written) > 3 else ''})" if written else ""
    print(f"  {meme['phrase']}  [{games}]  {meaning or '(no meaning yet)'}{also}")


def _show_candidate(c):
    how = c["status"] if c["status"] != "rising" else f"rising x{c['rise']}"
    alone = f", {round(c['alone'] * 100)}% on its own" if c.get("alone") is not None else ""
    also = "".join(f", also {v['phrase']}" for v in (c.get("variants") or [])[:2])
    example = c["examples"][0]["text"] if c["examples"] else ""
    verdict = c.get("judge") or {}
    said = {"meme": f"  -> a meme: {verdict.get('meaning', '')}",
            "not a meme": "  -> not a meme: " + judging.NOT_MEMES.get(verdict.get("kind"), "?"),
            "watching": "  -> watching"}.get(verdict.get("verdict"), "")
    if verdict.get("verdict") == "untested":
        said = "  -> a meme, " + judging.untested(verdict)
    source = verdict.get("source")
    if said and source:
        said += f" (from {source['phrase']}, {'a guess' if source['how'] == 'guess' else 'found'})"
    if said and verdict.get("model") and verdict["model"] != judging.MODEL:
        said += f"  (judged by {verdict['model']})"  # like the offline model, standing in
    pattern = f" 「{c['pattern']}」" if c.get("pattern") not in (None, c["phrase"]) else ""
    print(f"  {c['game']:<9} {how:<12} {c['phrase']}{pattern}  ({c['people']} people{alone}, "
          f"{c['videos']} videos{also})  e.g. {example[:40]}{said}")


def _judge(games, model=None, again=False, hint=True):
    """Judge the last run's candidates and (with hint) say what to do next; False when the
    model couldn't be asked."""
    try:
        with judging.awake():  # a run can take a while: Windows mustn't sleep meanwhile
            judging.judge(games, model, again)
    except ModelUnavailable as error:
        print(f"Couldn't ask the model: {error}.")
        return False
    if not hint:
        return True
    print('Learn one with: py -m memelearner accept PHRASE (it takes the judge\'s meaning '
          'unless it is only a guess, or give your own with --meaning); turn one down with: '
          'py -m memelearner reject PHRASE')
    return True


def _explain(games, phrases=None, links=(), model=None, top=explainers.TOP,
             videos=explainers.VIDEOS, again=False, hearing=True, judge=True):
    """Read videos that explain memes and judge those memes again; False when no model
    could be asked."""
    try:
        with judging.awake():  # hearing and summing up take a while too
            explainers.explain(phrases, games, {phrases[0]: links} if links else None, model,
                               videos, top, again, hearing, judge)
    except ModelUnavailable as error:
        print(f"Couldn't ask the model: {error}. What was read so far is kept.")
        return False
    return True


def _learn(args):
    missing = [path for path in args.known if not os.path.exists(path)]
    if missing:
        sys.exit(f"--known: there's no {', '.join(missing)}")
    try:
        found, summary = surf.learn(args.games, args.days, args.videos, args.people,
                                    not args.no_fetch, known_files=args.known)
    except ValueError as error:
        sys.exit(str(error))
    for game in [game.lower() for game in args.games] if args.games else list(surf.sources()):
        s = summary.get(game)
        if not s:
            print(f"{game}: nothing read yet")
            continue
        print(f"{game}: {s['videos']} videos, {s['recent']} lines from the last {args.days} "
              f"days, {s['before']} from before" + (
                  "" if s["baseline"] else " (too few to compare with: all are a first look)"))
    print(f"\n{len(found)} phrases people send on their own (at least {args.people} people, "
          f"in at least {surf.MIN_VIDEOS} videos):")
    for c in found:
        _show_candidate(c)
    print(f"\nSaved them with example lines to {surf.CANDIDATES_FILE}")
    if (args.judge or args.explain) and found:
        print()
        if not _judge(args.games):
            sys.exit("The candidates are saved: judge them later with py -m memelearner judge")
        if args.explain:
            print()
            if not _explain(args.games):
                sys.exit(1)
        return
    print('Learn one with: py -m memelearner accept PHRASE --meaning "..."; '
          "turn one down with: py -m memelearner reject PHRASE")


def _judge_command(args):
    if not _judge(args.games, args.model, args.again):
        sys.exit(1)


def _explain_command(args):
    if args.video and len(args.phrases) != 1:
        sys.exit("--video: give the one phrase the video explains, like: "
                 "py -m memelearner explain 阿一古 --video https://b23.tv/...")
    if not _explain(args.games, args.phrases or None, args.video, args.model, args.top,
                    args.videos, args.again, not args.no_audio, not args.no_judge):
        sys.exit(1)


def _compare(args):
    try:
        judging.compare(args.models, args.games, args.top, args.add)
    except ModelUnavailable as error:
        sys.exit(f"Couldn't compare: {error}.")
    except KeyboardInterrupt:
        sys.exit(f"Stopped. What was judged so far is in {judging.comparison_file('md')}")


def _watching(args):
    found = watching(args.game)
    if args.json:
        return _print_json(found)
    if not found:
        print("Nothing on the watching list.")
    for verdict in found:
        print(f"  {verdict['game']:<9} {verdict['phrase']}  {verdict.get('why', '')}  "
              f"(judged {verdict.get('judged', '?')})")


def _candidates(args):
    found = candidates(args.game)
    if args.json:
        return _print_json(found)
    if not found:
        print("No candidates: run py -m memelearner learn first, or all of them are looked at.")
    for c in found:
        _show_candidate(c)


def _accept(args):
    try:
        meme = accept(args.phrase, meaning=args.meaning, english=args.english, game=args.game,
                      note=args.note, model=args.model)
    except ModelUnavailable as error:
        sys.exit(f"Couldn't ask the model to merge your note: {error}. Nothing was learned.")
    print(f"Learned (saved to {store.MEMES_FILE}):")
    _show_meme(meme)
    left_out = [word for word in meanings.kept(args.note)
                if store.normal(word) not in store.normal(meme["meaning"])]
    if left_out:
        print(f"Check its meaning: it leaves out {', '.join(left_out)} of your note.")


def _reject(args):
    reject(args.phrase, args.reason)
    print(f"Turned down {store.normal(args.phrase)}: it won't be listed again.")


def _forget(args):
    phrase = store.normal(args.phrase)
    if forget(args.phrase):
        print(f"Forgot {phrase}: it is neither learned nor turned down now.")
    else:
        print(f"{phrase} was neither learned nor turned down.")


def _memes(args):
    found = memes(args.game)
    if args.json:
        return _print_json(found)
    if not found:
        print("No memes learned yet: look through py -m memelearner candidates and accept some.")
    for meme in found:
        _show_meme(meme)


def _lookup(args):
    meme = lookup(args.phrase)
    if args.json:
        _print_json(meme)
    elif meme:
        _show_meme(meme)
    else:
        print(f"{store.normal(args.phrase)} isn't a learned meme.")
    if meme is None:
        sys.exit(1)


def _find(args):
    found = find(args.text)
    if args.json:
        return _print_json(found)
    if not found:
        print("No learned memes in it.")
    for meme in found:
        _show_meme(meme)


def _live(args):
    from . import bilibili, live
    try:
        until = live.end_time(args.until, args.hours)
        live.record(args.room, until, game=args.game.lower())
    except ValueError as error:
        sys.exit(str(error))
    except live.NeedBrotli as error:
        sys.exit(str(error))
    except (bilibili.Error, bilibili.Blocked, OSError, KeyError, TypeError) as error:
        sys.exit(f"Couldn't open the live room: {error!r}")


def _live_phrases(args):
    from . import live
    paths = []
    for path in args.recordings:
        if not os.path.exists(path):
            path = os.path.join(live.live_dir(), path if path.endswith(".jsonl")
                                else path + ".jsonl")
        if not os.path.exists(path):
            sys.exit(f"There's no recording {path}")
        paths.append(path)
    if args.all:
        paths = live.recordings()
    try:
        found, summary, compared = live.find(paths, args.slice, args.people)
    except ValueError as error:
        sys.exit(str(error))
    start = time.strftime("%Y-%m-%d %H:%M", time.localtime(compared["start"]))
    print(f"{', '.join(compared['recordings'])}: {compared['danmu']} danmu from "
          f"{compared['senders']} senders since {start}, in {compared['parts']} parts of "
          f"{args.slice} minutes (each counts as a video)")
    print(f"Compared with {compared['before']} lines read before it (replays, earlier "
          "recordings)")
    groups = (("new", "New: nobody sent these in what was read before"),
              ("rising", "Rising: sent before, but much more now"),
              ("first look", "First look (too little to compare with)"))
    keep = []  # what's listed, in the same order (new first), is what judge and compare get
    for status, title in groups:
        some = [c for c in found if c["status"] == status][:args.top]
        if some:
            print(f"\n{title}:")
            for c in some:
                _show_candidate(c)
            keep += some
    others = [c for c in found if c["status"] in ("steady", "older")][:args.known_top]
    if others:
        print(f"\nAlso sent a lot, but not new (the {len(others)} most sent):")
        for c in others:
            _show_candidate(c)
    keep += others
    surf.save(keep, summary, 0, live=compared)
    print(f"\nSaved {len(keep)} phrases with example lines to {surf.CANDIDATES_FILE}, as the "
          "candidates: py -m memelearner judge (or compare) works on them now; "
          "py -m memelearner learn --no-fetch lists the usual ones again.")


def _report(args):
    from . import review
    rows = review.rows()
    if not rows:
        print("Nothing to report: run py -m memelearner learn or live-phrases, and judge, "
              "first; or every phrase found is learned or turned down.")
        return
    try:
        report = review.Report(rows, args.model, args.port, review.about())
    except OSError as error:
        sys.exit(f"Couldn't open the report on port {args.port}: {error}")
    result = report.run(not args.no_browser)
    if result:
        print("\n".join(review.summary(result)))


def _live_report(args):
    """live-phrases, judge and explain, then the report: a live recording in one command."""
    _live_phrases(args)
    print()
    if not _judge(None, args.model, hint=False):
        sys.exit("The phrases are saved: judge them later with py -m memelearner judge, then "
                 "open the report with py -m memelearner report")
    if not args.no_explain:
        print()
        if not _explain(None, model=args.model, top=args.explain_top,
                        hearing=not args.no_audio):
            print("So the report shows what the judge said before that.")
    print()
    _report(args)


def _sources(args):
    """Ask every search engine and wiki about some phrases (web.check()), and with --explain
    look them up as explain does; write what each found to a folder (sources.md for people,
    sources.json, links.json: the Bilibili videos found for each), beside what memes.json
    says the learned ones mean."""
    phrases = list(args.phrases)
    if args.file:
        with open(args.file, encoding="utf-8-sig") as f:
            phrases += [line.strip() for line in f if line.strip() and not line.startswith("#")]
    phrases = [p for p in dict.fromkeys(store.normal(p) for p in phrases) if p]
    if not phrases:
        sys.exit("Give some phrases, or --file with one a line")
    folder = args.out or os.path.join(surf.SURF_DIR, "sources")
    os.makedirs(folder, exist_ok=True)
    print(f"Asking {len(web.ENGINES)} search engines and {len(web.SITES)} wikis about "
          f"{len(phrases)} phrases (read only, a few seconds between two requests to the same "
          f"site) ...")
    checked = web.check(phrases, folder=folder if args.raw else None)
    looked = {}
    if args.explain:
        print()
        try:
            with judging.awake():
                explainers.explain(phrases, None, None, args.model, args.videos, len(phrases),
                                   again=True, hearing=not args.no_audio, judge=False)
        except ModelUnavailable as error:
            print(f"Couldn't ask the model: {error}. What was read so far is kept.")
        entries = explainers.load()["phrases"]
        looked = {phrase: next((e for k, e in entries.items() if k.split(":", 1)[1] == phrase), {})
                  for phrase in phrases}
    memes_now = store.load()["memes"]
    links = {}
    for phrase in phrases:
        for answer in checked["phrases"][phrase].values():
            for video in answer.get("videos") or []:
                links.setdefault(phrase, []).append(
                    f"https://www.bilibili.com/video/{video['bvid']}")
        for video in (looked.get(phrase) or {}).get("videos") or []:
            links.setdefault(phrase, []).append(f"https://www.bilibili.com/video/{video['bvid']}")
        if phrase in links:
            links[phrase] = list(dict.fromkeys(links[phrase]))
    report = {"made": time.strftime("%Y-%m-%d %H:%M"), "phrases": phrases,
              "places": checked["places"], "checked": checked["phrases"], "explained": looked,
              "learned": {p: memes_now[p].get("meaning", "") for p in phrases if p in memes_now}}
    for name, value in (("sources.json", report), ("links.json", links)):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=1)
    with open(os.path.join(folder, "sources.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(_sources_text(report)) + "\n")
    told = [p for p in phrases if explainers.told(looked.get(p) or {})]
    print()
    if args.explain:
        print(f"{len(told)} of {len(phrases)} phrases are explained by a video or a web page.")
    print(f"What each place found is in {os.path.join(folder, 'sources.md')}")


def _sources_text(report):
    """sources.md: for each phrase, what memes.json says, what each place found, and what
    the videos and web pages explain read said."""
    lines = [f"# Where explanations come from ({report['made']})", ""]
    for place, counted in report["places"].items():
        lines.append(f"- {place}: answered {counted['answered']} of {counted['asked']}, found "
                     f"something for {counted['found']}" +
                     (f"; refused: {counted['refused']}" if counted["refused"] else ""))
    for phrase in report["phrases"]:
        lines += ["", f"## {phrase}", ""]
        if phrase in report["learned"]:
            lines.append(f"memes.json: {report['learned'][phrase]}")
            lines.append("")
        for place, answer in report["checked"][phrase].items():
            if not answer.get("ok"):
                lines.append(f"- {place}: {answer.get('error', '')}")
            elif "entry" in answer:
                entry = answer["entry"]
                lines.append(f"- {place}: " + (f"[{entry['title']}]({entry['url']}) "
                                               f"{' '.join(entry['text'].split())[:150]}"
                                               if entry else "no entry"))
            else:
                lines.append(f"- {place}: {len(answer['results'])} results, "
                             f"{len(answer['videos'])} Bilibili videos, "
                             f"{len(answer['pages'])} other pages that name it")
                for video in answer["videos"][:3]:
                    lines.append(f"  - video [{video['title']}]"
                                 f"(https://www.bilibili.com/video/{video['bvid']})")
                for page in answer["pages"][:3]:
                    lines.append(f"  - page [{page['title']}]({page['url']}): {page['snippet'][:80]}")
        entry = report["explained"].get(phrase)
        if entry is None:
            continue
        said = explainers._summaries(entry)
        lines.append("")
        lines.append("Looked up as explain does: " + ("nothing found" if not said else ""))
        for summary in said:
            where = (f"page {summary.get('site', '')} [{summary.get('title', '')}]"
                     f"({summary.get('url', '')})" if summary.get("kind") == "page" else
                     f"video [{summary.get('title', '')}]({summary.get('url', '')})")
            lines.append(f"- {where}: " + (
                summary["meaning"] + (f" (出自 {summary['origin']})" if summary.get("origin")
                                      else "") +
                (f" 「{summary['quote']}」" if summary.get("quote") else "")
                if summary.get("about") else "doesn't explain it"))
    return lines


def _recording_options(command):
    """The options of live-phrases, which live-report has too."""
    command.add_argument("recordings", nargs="*", metavar="RECORDING",
                         help="recordings (files in surf/live/, like 6-20261103-1905); by "
                         "default the last day's (all of it, when the recorder was restarted)")
    command.add_argument("--all", action="store_true", help="every recording together")
    command.add_argument("--slice", type=int, default=60, metavar="MINUTES",
                         help="minutes of a recording that count as one video (default "
                         "%(default)s): a phrase must be sent in two")
    command.add_argument("--people", type=int, default=5, metavar="N",
                         help="different people who must send a phrase on its own (default "
                         "%(default)s)")
    command.add_argument("--top", type=int, default=40, metavar="N",
                         help="new phrases listed (default %(default)s)")
    command.add_argument("--known-top", type=int, default=10, metavar="N",
                         help="phrases listed that aren't new (default %(default)s)")


def _report_options(command, model_help):
    command.add_argument("--model", default=judging.MODEL, help=model_help)
    command.add_argument("--port", type=int, default=0, metavar="N",
                         help="the port the page is served on, on this computer only "
                         "(default: a free one)")
    command.add_argument("--no-browser", action="store_true",
                         help="don't open the browser: open the link it prints yourself")


def parser():
    commands = argparse.ArgumentParser(
        prog="memelearner", description="The meme bot: finds the newest gaming memes on "
        "Bilibili, and gives other programs the memes it learned.")
    sub = commands.add_subparsers(dest="command", required=True, metavar="COMMAND")

    learn = sub.add_parser("learn", help="read Bilibili, then list the phrases people send on "
                           "their own")
    learn.add_argument("--games", nargs="+", metavar="GAME",
                       help="only these games (cs2, lol, valorant, dota2); all by default")
    learn.add_argument("--videos", type=int, default=surf.VIDEOS_PER_GAME, metavar="N",
                       help="videos read per game (default %(default)s)")
    learn.add_argument("--days", type=int, default=surf.DAYS, metavar="N",
                       help='"new" means the last N days (default %(default)s)')
    learn.add_argument("--people", type=int, default=surf.MIN_PEOPLE, metavar="N",
                       help="different people who must send a phrase on its own (default "
                       "%(default)s)")
    learn.add_argument("--no-fetch", action="store_true",
                       help="don't visit Bilibili; find phrases again in what was read")
    learn.add_argument("--known", nargs="+", action="extend", default=[], metavar="PATH",
                       help="files or folders whose phrases aren't news, like another app's "
                       "word tables (.json or .txt; also MEMELEARNER_KNOWN)")
    learn.add_argument("--judge", action="store_true",
                       help="then have the model judge what was found (see judge)")
    learn.add_argument("--explain", action="store_true",
                       help="then judge, read videos that explain the memes found, and judge "
                       "those again (see explain)")
    learn.set_defaults(run=_learn)

    judge = sub.add_parser("judge", help="have a model tell memes from names, game words and "
                           "everyday words, and explain them")
    judge.add_argument("--games", nargs="+", metavar="GAME", help="only these games' phrases")
    judge.add_argument("--model", default=judging.MODEL,
                       help="an online model like deepseek/deepseek-v4-pro, or an Ollama "
                       "model on this computer like qwen3:14b (default %(default)s; also "
                       f"MEMELEARNER_MODEL). When an online model can't be reached, "
                       f"{judging.LOCAL_MODEL} judges instead")
    judge.add_argument("--again", action="store_true", help="judge again what was judged before")
    judge.set_defaults(run=_judge_command)

    explain = sub.add_parser("explain", help="find and read videos that explain the memes the "
                             "judge found (阿一古是什么梗), sum them up, and judge those again")
    explain.add_argument("phrases", nargs="*", metavar="PHRASE",
                         help="only these phrases (default: the memes the judge found, the "
                         "ones whose meaning is least sure first)")
    explain.add_argument("--video", action="append", default=[], metavar="LINK",
                         help="a video that explains the one phrase given, read instead of "
                         "looking for one (can be given more than once)")
    explain.add_argument("--games", nargs="+", metavar="GAME", help="only these games' phrases")
    explain.add_argument("--top", type=int, default=explainers.TOP, metavar="N",
                         help="phrases looked up in one run (default %(default)s)")
    explain.add_argument("--videos", type=int, default=explainers.VIDEOS, metavar="N",
                         help="videos read for each phrase (default %(default)s)")
    explain.add_argument("--model", default=judging.MODEL,
                         help="the model that sums the videos up and judges, as for judge "
                         "(default %(default)s; also MEMELEARNER_MODEL). When an online model "
                         f"can't be reached, {judging.LOCAL_MODEL} does instead")
    explain.add_argument("--again", action="store_true",
                         help="also phrases looked up before, and sum their videos up again")
    explain.add_argument("--no-audio", action="store_true",
                         help="don't hear the videos, even with Whisper installed")
    explain.add_argument("--no-judge", action="store_true",
                         help="don't judge the phrases again afterwards")
    explain.set_defaults(run=_explain_command)

    side = sub.add_parser("compare", help="judge the same phrases with several models, side by "
                          "side, to pick one (the verdicts judge keeps don't change)")
    side.add_argument("models", nargs="+", metavar="MODEL",
                      help="Ollama models (qwen3:14b) or online ones (deepseek/deepseek-flash)")
    side.add_argument("--games", nargs="+", metavar="GAME", help="only these games' phrases")
    side.add_argument("--top", type=int, metavar="N", help="only each game's N best phrases")
    side.add_argument("--add", action="store_true", help="keep the last comparison of the same "
                      "phrases: add these models to it, and let a model in it that stopped carry "
                      "on where it stopped")
    side.set_defaults(run=_compare)

    show = sub.add_parser("candidates", help="the phrases the last run found, to look through")
    show.add_argument("--game", help="only this game's")
    show.add_argument("--json", action="store_true", help="print JSON")
    show.set_defaults(run=_candidates)

    unsure = sub.add_parser("watching", help="the phrases the model wasn't sure of yet")
    unsure.add_argument("--game", help="only this game's")
    unsure.add_argument("--json", action="store_true", help="print JSON")
    unsure.set_defaults(run=_watching)

    yes = sub.add_parser("accept", help="learn a phrase as a meme")
    yes.add_argument("phrase")
    yes.add_argument("--meaning", default="",
                     help="what it means, in Chinese (default: the judge's, if it found one)")
    yes.add_argument("--english", default="", help="an English gloss (default: the judge's)")
    yes.add_argument("--game", help="a game it belongs to, besides the ones it was seen in")
    yes.add_argument("--note", default="",
                     help="your own note on it (what to add, or 和X是同一个梗): the model merges it "
                     "with the meaning into one whole meaning, in which your note wins, and "
                     "keeps it as you wrote it")
    yes.add_argument("--model", default=judging.MODEL,
                     help="the model that merges a --note (default %(default)s; also "
                     f"MEMELEARNER_MODEL). When an online model can't be reached, "
                     f"{judging.LOCAL_MODEL} does instead")
    yes.set_defaults(run=_accept)

    no = sub.add_parser("reject", help="turn a phrase down, so it isn't listed again")
    no.add_argument("phrase")
    no.add_argument("--reason", default="", help="why, like 'a player name'")
    no.set_defaults(run=_reject)

    undo = sub.add_parser("forget", help="undo accept or reject")
    undo.add_argument("phrase")
    undo.set_defaults(run=_forget)

    learned = sub.add_parser("memes", help="the memes learned so far")
    learned.add_argument("--game", help="only this game's")
    learned.add_argument("--json", action="store_true", help="print JSON")
    learned.set_defaults(run=_memes)

    one = sub.add_parser("lookup", help="one learned meme (exits with 1 when there's none)")
    one.add_argument("phrase")
    one.add_argument("--json", action="store_true", help="print JSON (null when there's none)")
    one.set_defaults(run=_lookup)

    watch = sub.add_parser("live", help="record a live room's danmu as a visitor (no login, "
                           "sends nothing), to find phrases in later")
    watch.add_argument("room", help="the room's link or number, like "
                       "https://live.bilibili.com/6")
    watch.add_argument("--until", metavar="HH:MM",
                       help="stop at this time on this computer's clock, like 22:55")
    watch.add_argument("--hours", type=float, metavar="N", help="stop after N hours")
    watch.add_argument("--game", default="lol",
                       help="the game the room shows, for finding phrases (default lol)")
    watch.set_defaults(run=_live)

    heard = sub.add_parser("live-phrases", help="the phrases people send on their own in a "
                           "recording, and which are new next to what was read before")
    _recording_options(heard)
    heard.set_defaults(run=_live_phrases)

    page = sub.add_parser("report", help="the last run's phrases and the judge's answers as a "
                          "page in your browser: mark them, decide, and 完成 learns them")
    _report_options(page, "the model that merges your My meaning with the judge's meaning "
                    f"(default %(default)s; {judging.LOCAL_MODEL} when it can't be reached)")
    page.set_defaults(run=_report)

    whole = sub.add_parser("live-report", help="live-phrases, judge and explain for the last "
                           "live recording, then open the report")
    _recording_options(whole)
    _report_options(whole, "the model that judges, sums up the videos and merges your notes "
                    f"(default %(default)s; {judging.LOCAL_MODEL} when it can't be reached)")
    whole.add_argument("--no-explain", action="store_true",
                       help="don't look for videos that explain the memes (that reads Bilibili "
                       "for some minutes)")
    whole.add_argument("--explain-top", type=int, default=explainers.TOP, metavar="N",
                       help="memes whose explaining videos are read (default %(default)s)")
    whole.add_argument("--no-audio", action="store_true",
                       help="don't hear the videos, even with Whisper installed")
    whole.set_defaults(run=_live_report)

    look = sub.add_parser("sources", help="ask every search engine and wiki about some "
                          "phrases, to see which ones explain memes from this computer")
    look.add_argument("phrases", nargs="*", metavar="PHRASE")
    look.add_argument("--file", metavar="PATH", help="more phrases, one a line (UTF-8)")
    look.add_argument("--out", metavar="FOLDER",
                      help="where to write what was found (default: sources in the cache)")
    look.add_argument("--raw", action="store_true",
                      help="also keep every page as it came, in the folder's raw/")
    look.add_argument("--explain", action="store_true",
                      help="then look them up as explain does (videos, then web pages) and "
                      "sum up what they say with the model")
    look.add_argument("--videos", type=int, default=explainers.VIDEOS, metavar="N",
                      help="videos read for each phrase with --explain (default %(default)s)")
    look.add_argument("--model", default=judging.MODEL,
                      help=f"the model that sums up (default %(default)s; "
                      f"{judging.LOCAL_MODEL} when it can't be reached)")
    look.add_argument("--no-audio", action="store_true", help="don't hear videos (--explain)")
    look.set_defaults(run=_sources)

    scan = sub.add_parser("find", help="the learned memes in a text")
    scan.add_argument("text")
    scan.add_argument("--json", action="store_true", help="print JSON")
    scan.set_defaults(run=_find)
    return commands


class _Log:
    """What a command prints, to the screen (when there is one) and to a file too."""

    encoding = "utf-8"

    def __init__(self, stream, file):
        self.stream, self.file = stream, file

    def write(self, text):
        if self.stream is not None:
            try:
                self.stream.write(text)
            except (OSError, ValueError):
                pass  # the window was closed
        self.file.write(text)
        self.file.flush()
        return len(text)

    def flush(self):
        for stream in (self.stream, self.file):
            try:
                stream and stream.flush()
            except (OSError, ValueError):
                pass

    def isatty(self):
        return False


def _log_to(path, argv):
    """Have what this command prints go to the file path too (MEMELEARNER_LOG)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    file = open(path, "a", encoding="utf-8")
    file.write(f"\n== {time.strftime('%Y-%m-%d %H:%M:%S')}  py -m memelearner "
               f"{' '.join(sys.argv[1:] if argv is None else argv)}\n")
    sys.stdout, sys.stderr = _Log(sys.stdout, file), _Log(sys.stderr, file)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Chinese in the Windows console and in --json
    if os.environ.get("MEMELEARNER_LOG"):
        _log_to(os.environ["MEMELEARNER_LOG"], argv)
    args = parser().parse_args(argv)
    try:
        args.run(args)
    except nicknames.TableError as error:  # a line of data/nicknames.txt it can't read
        sys.exit(str(error))
