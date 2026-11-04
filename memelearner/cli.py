"""The command line: py -m memelearner COMMAND, or memelearner COMMAND once pip-installed.

    py -m memelearner learn                    read Bilibili, then list the phrases people send
    py -m memelearner judge                    a local model tells memes from names
    py -m memelearner candidates               what the last run found, to look through
    py -m memelearner watching                 what the model wasn't sure of yet
    py -m memelearner accept 阿一古 --meaning "..."   learn a phrase as a meme
    py -m memelearner reject 下路               turn a phrase down: it isn't listed again
    py -m memelearner forget 下路               undo accept or reject
    py -m memelearner memes                    the memes learned so far
    py -m memelearner lookup 绷不住了            one learned meme
    py -m memelearner find "这下真的绷不住了"     the learned memes in a text
    py -m memelearner live https://live.bilibili.com/6 --until 22:55   record a live room's danmu
    py -m memelearner live-phrases             the phrases in the last recording, and which are new

py surf.py is short for learn, with the same options (py -m memelearner learn --help), and
learn --judge also judges what it found. candidates, watching, memes, lookup and find take
--json to print JSON for another program (UTF-8) instead of lines for people, in the shapes
__init__.py describes; lookup exits with 1 when the phrase isn't a learned meme.
"""

import argparse
import json
import os
import sys
import time

from . import (ModelUnavailable, accept, candidates, find, forget, judging, lookup, memes,
               reject, store, surf, watching)


def _print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=1))


def _show_meme(meme):
    games = ", ".join(meme["games"]) or "-"
    meaning = " / ".join(part for part in (meme["meaning"], meme["english"]) if part)
    print(f"  {meme['phrase']}  [{games}]  {meaning or '(no meaning yet)'}")


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
    print(f"  {c['game']:<9} {how:<12} {c['phrase']}  ({c['people']} people{alone}, "
          f"{c['videos']} videos{also})  e.g. {example[:40]}{said}")


def _judge(games, model=judging.MODEL, again=False):
    """Judge the last run's candidates and say what to do next; False when the model
    couldn't be asked."""
    try:
        judging.judge(games, model, again)
    except ModelUnavailable as error:
        print(f"Couldn't ask the local model: {error}.")
        return False
    print('Learn one with: py -m memelearner accept PHRASE (it takes the judge\'s meaning '
          'unless it is only a guess, or give your own with --meaning); turn one down with: '
          'py -m memelearner reject PHRASE')
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
    if args.judge and found:
        print()
        if not _judge(args.games):
            sys.exit("The candidates are saved: judge them later with py -m memelearner judge")
        return
    print('Learn one with: py -m memelearner accept PHRASE --meaning "..."; '
          "turn one down with: py -m memelearner reject PHRASE")


def _judge_command(args):
    if not _judge(args.games, args.model, args.again):
        sys.exit(1)


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
    meme = accept(args.phrase, meaning=args.meaning, english=args.english, game=args.game)
    print(f"Learned (saved to {store.MEMES_FILE}):")
    _show_meme(meme)


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
    shown = 0
    for status, title in groups:
        some = [c for c in found if c["status"] == status][:args.top]
        if some:
            print(f"\n{title}:")
            for c in some:
                _show_candidate(c)
            shown += len(some)
    others = [c for c in found if c["status"] in ("steady", "older")][:args.known_top]
    if others:
        print(f"\nAlso sent a lot, but not new (the {len(others)} most sent):")
        for c in others:
            _show_candidate(c)
    keep = [c for c in found if c["status"] not in ("steady", "older")][:args.top] + others
    surf.save(keep, summary, 0, live=compared)
    print(f"\nSaved {len(keep)} phrases with example lines to {surf.CANDIDATES_FILE}, as the "
          "candidates: py -m memelearner judge (or compare) works on them now; "
          "py -m memelearner learn --no-fetch lists the usual ones again.")


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
                       help="then have the local model judge what was found (see judge)")
    learn.set_defaults(run=_learn)

    judge = sub.add_parser("judge", help="have a local model tell memes from names, game "
                           "words and everyday words, and explain them")
    judge.add_argument("--games", nargs="+", metavar="GAME", help="only these games' phrases")
    judge.add_argument("--model", default=judging.MODEL,
                       help="the Ollama model (default %(default)s; also MEMELEARNER_MODEL)")
    judge.add_argument("--again", action="store_true", help="judge again what was judged before")
    judge.set_defaults(run=_judge_command)

    show = sub.add_parser("candidates", help="the phrases the last run found, to look through")
    show.add_argument("--game", help="only this game's")
    show.add_argument("--json", action="store_true", help="print JSON")
    show.set_defaults(run=_candidates)

    unsure = sub.add_parser("watching", help="the phrases the local model wasn't sure of yet")
    unsure.add_argument("--game", help="only this game's")
    unsure.add_argument("--json", action="store_true", help="print JSON")
    unsure.set_defaults(run=_watching)

    yes = sub.add_parser("accept", help="learn a phrase as a meme")
    yes.add_argument("phrase")
    yes.add_argument("--meaning", default="",
                     help="what it means, in Chinese (default: the judge's, if it found one)")
    yes.add_argument("--english", default="", help="an English gloss (default: the judge's)")
    yes.add_argument("--game", help="a game it belongs to, besides the ones it was seen in")
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
    heard.add_argument("recordings", nargs="*", metavar="RECORDING",
                       help="recordings (files in surf/live/, like 6-20261103-1905); by "
                       "default the last day's (all of it, when the recorder was restarted)")
    heard.add_argument("--all", action="store_true", help="every recording together")
    heard.add_argument("--slice", type=int, default=60, metavar="MINUTES",
                       help="minutes of a recording that count as one video (default "
                       "%(default)s): a phrase must be sent in two")
    heard.add_argument("--people", type=int, default=5, metavar="N",
                       help="different people who must send a phrase on its own (default "
                       "%(default)s)")
    heard.add_argument("--top", type=int, default=40, metavar="N",
                       help="new phrases listed (default %(default)s)")
    heard.add_argument("--known-top", type=int, default=10, metavar="N",
                       help="phrases listed that aren't new (default %(default)s)")
    heard.set_defaults(run=_live_phrases)

    scan = sub.add_parser("find", help="the learned memes in a text")
    scan.add_argument("text")
    scan.add_argument("--json", action="store_true", help="print JSON")
    scan.set_defaults(run=_find)
    return commands


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Chinese in the Windows console and in --json
    args = parser().parse_args(argv)
    args.run(args)
