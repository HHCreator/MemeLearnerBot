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

py surf.py is short for learn, with the same options (py -m memelearner learn --help), and
learn --judge also judges what it found. candidates, watching, memes, lookup and find take
--json to print JSON for another program (UTF-8) instead of lines for people, in the shapes
__init__.py describes; lookup exits with 1 when the phrase isn't a learned meme.
"""

import argparse
import json
import os
import sys

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
            "untested": f"  -> a meme, meaning not tested: {verdict.get('meaning', '')}",
            "not a meme": "  -> not a meme: " + judging.NOT_MEMES.get(verdict.get("kind"), "?"),
            "watching": "  -> watching"}.get(verdict.get("verdict"), "")
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
          'unless you give --meaning); turn one down with: py -m memelearner reject PHRASE')
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
