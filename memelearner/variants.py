"""Variants of one meme: phrases that are the same meme, written differently.

    超威同意了, chovy同意了, 大超同意了, 三叔不同意     one meme: 「XX同意了」
    超威今年真不一样了, 真不一样了                     one meme: 「XX今年真不一样了」

Two phrases are the same meme when they are the same once these differences are taken away
(same_meme(); shape() is what is left of a phrase):

    Who            A player's name or nickname (data/nicknames.txt, see nicknames.py: 超威,
                   大超 and chovy are Chovy, 三叔 is Canyon), or any word in letters (ruler,
                   kiin), is just "someone", XX: 超威同意了 and ruler同意了 are both
                   XX同意了. A name in Chinese that isn't in the table (兰子, two or three
                   characters) is someone too where other phrases show that names go
                   there, two different ones at least (兰子同意了, next to 超威同意了 and
                   ruler同意了; but 还在洗 isn't 还在ig: one character is too often a word).
    Left out       One is the other with one or two parts left out: 真不一样了 is
                   超威今年真不一样了 without 超威今年, and 超威同意了 is 超威会同意的
                   without 会. What's left must be at least 2 characters that aren't a name,
                   and what's left out names and at most 2 other characters: 感谢多兰 isn't
                   千言万语感谢多兰. Two characters alone need a name with them to leave
                   other characters out: wei发狂了 is 发狂了, but 退役 isn't 感谢退役.
    Turned around  不 or 没 added: 三叔不同意 is 三叔同意了 turned around.
    Written longer A character written more than once in a row (同意意意了, chooooovy, ggg
                   and gg), and mood words at the end (了 呢 吧 啊 ...), as the finder counts
                   them. 啊 anywhere else is 阿: 啊一占 is 阿一占.
    Other order    The same characters in another order, at least ANAGRAM_KEPT of them that
                   aren't names: 我先来怎么翻的 is 我先翻怎么来的, 关闭得分通道 is 得分通道关闭.
    Same word      A word for another that means the same (SAME_WORDS): 不要狐狸 is 别狐狸.
    Traditional    A traditional character is its simplified one (data/t2s.txt): 領會員 is
                   领会员.
    A typo         One character for another that sounds the same, tones aside
                   (data/pinyin.txt), where at least SOUND_KEPT characters aren't names:
                   零会员 is 领会员 (but 目前不知道怎么赢 isn't 目前不知道怎么输).
    You said so    Ways of writing one meme listed on one line of data/same_memes.txt, a
                   table you edit yourself: 41翻 = 41上 = 41中, 区区区 = 区区 = 好多区.

Phrases that differ in anything else stay apart, however much they look alike: 我们是冠军
and 我们是路边, or 目前不知道怎么赢 and 目前不知道怎么输, mean different things. A phrase
that is only a name, or only a word in letters (ggg), is no meme's words: it is nobody's
variant. groups() puts the variants among some phrases together, each group named by a
pattern in which a name is XX (「XX同意了」), with the names in it and who they are. One
phrase can't make two memes one: 阿一古加油 is 阿一古 with 加油, and ig加油 with 阿一古 for
ig, but 阿一古 and ig加油 have nothing in common, so they stay apart (kin()).
"""

import functools
import os
import re
import unicodedata

from . import nicknames, store, surf

SLOT = "X"  # someone, in a shape: store.normal() writes lowercase only, so it's never text
PATTERN_NAME = "XX"  # ... and in a pattern you read: 「XX同意了」
NEGATIONS = "不没"  # added to turn a meme around: 三叔不同意
MAX_LEFT_OUT = 2  # parts of a phrase another one may leave out
MIN_KEPT = 2  # characters, not counting names, that one that leaves parts out must keep
MAX_ADDED = 2  # characters, not counting names, that it may leave out
ALONE_KEPT = 3  # ... and that it must keep for that without a name (退役 isn't 感谢退役)
NAME_CHARS = (2, 3)  # characters of a Chinese name that isn't in the table (兰子): one
#                      alone is too often a word (还在洗, 还在送 aren't names)
SHOWN_NAMES = 2  # different names that must go where XX is before an unknown one counts
UNKNOWN_MORE = 3  # times as many people as the most sent one with a name it may have: 正义必胜
#                   (2206 people) isn't ig必胜 (60) with 正义 for ig, but a meme of its own
NOT_NAMES = {"大家", "今年", "今天", "现在", "终于", "一定", "应该", "已经", "果然", "居然",
             "竟然", "必须", "就是", "还是", "也是", "真的", "确实", "开始", "继续", "马上",
             "直接", "赶紧", "一直", "突然", "只能", "肯定", "绝对"}  # words, not names
NOT_NAME_START = "我你他她它咱您这那谁都也还就又再才别快真太很要会能可已"  # 我们, 这个, 又 ...
LETTERS = re.compile(r"[0-9a-z]*[a-z][0-9a-z]*")  # a word in letters (with digits: t1, s7)
RUNS = re.compile(r"(.)\1+")
SAME_CHARS = str.maketrans({"啊": "阿"})  # one character written two ways (but 啊 at the end
#                                          is a mood word: ig冲啊 is ig冲)
SAME_WORDS = (("不要", "别"),)  # a word for another that means the same: 不要狐狸 is 别狐狸
ANAGRAM_KEPT = 4  # characters, not counting names, that the same ones in another order need
SOUND_KEPT = 3  # ... and that a phrase with one character for another that sounds the same
T2S_FILE = os.path.join(store.HERE, "data", "t2s.txt")
PINYIN_FILE = os.path.join(store.HERE, "data", "pinyin.txt")
SAME_FILE = os.path.join(store.HERE, "data", "same_memes.txt")
SAME_SEPARATOR = re.compile(r"\s*=\s*")

_same_cache = {}  # the last data/same_memes.txt read: {"stamp", "lines": [[phrase, ...]], "shapes"}


class TableError(ValueError):
    """data/same_memes.txt has a line the bot can't read: the message names it."""


@functools.lru_cache(maxsize=1)
def _simplified():
    """The str.translate() table from data/t2s.txt: traditional -> simplified character."""
    table = {}
    with open(T2S_FILE, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#"):
                table.update((ord(pair[0]), pair[1]) for pair in line.split() if len(pair) == 2)
    return table


@functools.lru_cache(maxsize=1)
def _sounds():
    """{character: the syllables it is read as, tones aside} from data/pinyin.txt."""
    found = {}
    with open(PINYIN_FILE, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            syllable, _, chars = line.strip().partition(" ")
            for char in chars:
                found.setdefault(char, set()).add(syllable)
    return {char: frozenset(syllables) for char, syllables in found.items()}


def sound_alike(a, b):
    """Whether two characters can be read the same, tones aside: 零 and 领 (ling)."""
    sounds = _sounds()
    return bool(sounds.get(a, frozenset()) & sounds.get(b, frozenset()))


def parse_same(text, name="same_memes.txt"):
    """The lines of data/same_memes.txt in a text: [[phrase, ...], ...], the ways of writing
    one meme a line, as written. Raises TableError naming a line without = between two."""
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = unicodedata.normalize("NFKC", raw).split("#", 1)[0].strip()
        if not line:
            continue
        phrases = [p for p in SAME_SEPARATOR.split(line) if store.normal(p)]
        if len(phrases) < 2:
            raise TableError(f"{name} line {number} ({raw.strip()}): write two ways of "
                             "writing one meme or more, with = between them (41翻 = 41上)")
        lines.append(phrases)
    return lines


def same_table(path=None):
    """data/same_memes.txt (or path): [[phrase, ...], ...] as parse_same() gives them; empty
    when there's no file. Read again only when the file changed."""
    return [list(line) for line in _same(path)["lines"]]


def _same(path=None):
    path = path or SAME_FILE
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return {"lines": [], "shapes": {}}
    stamp = (path, stat.st_mtime_ns, stat.st_size, nicknames._read().get("stamp"))
    if _same_cache.get("stamp") != stamp:
        try:
            with open(path, encoding="utf-8-sig") as f:
                lines = parse_same(f.read(), os.path.basename(path))
        except UnicodeDecodeError:
            raise TableError(f"{path} isn't UTF-8: save it as UTF-8 (Notepad: Save as, "
                             "Encoding UTF-8)") from None
        meme = list(range(len(lines)))  # lines with a phrase in common are one meme

        def first(number):
            while meme[number] != number:
                number = meme[number]
            return number

        shapes = {}
        for number, line in enumerate(lines):
            for phrase in line:
                was = first(shapes.setdefault(shape(phrase), number))
                meme[max(was, first(number))] = min(was, first(number))
        shapes = {found: first(number) for found, number in shapes.items()}
        _same_cache.update(stamp=stamp, lines=lines, shapes=shapes)
    return _same_cache


def _listed_together(a, b):
    """Whether two shapes are one meme by data/same_memes.txt: on one line, or on two lines
    that have a phrase in common (41翻 = 41上, then 41上 = 41中)."""
    shapes = _same()["shapes"]
    return a in shapes and shapes.get(b) == shapes[a]


def names(phrase):
    """The names in a phrase, in the order they come: [(name, player)], player from the
    nickname table, or "" for a word in letters that isn't in it (ruler). As
    store.normal() writes them: chooooovy."""
    text = store.normal(phrase)
    return [(text[start:end], player) for start, end, player in _names(text)]


def _names(text):
    """Where the names are in a text that store.normal() wrote: [(start, end, player)]."""
    found = nicknames.spans(text)
    for word in LETTERS.finditer(text):
        if not any(start < word.end() and word.start() < end for start, end, _ in found):
            found.append((word.start(), word.end(), ""))
    return sorted(found)


def _with(text, found, name):
    """text with each name in it as name, one for names that come one after another."""
    pieces, at = [], 0
    for start, end, _ in found:
        pieces.append(text[at:start])
        if not (pieces[-1] == "" and len(pieces) > 1):  # one XX for 超威chovy
            pieces.append(name)
        at = end
    return "".join(pieces) + text[at:]


def shape(phrase):
    """What is left of a phrase once names are someone (X), each character is written once
    and the mood words at its end are left out: 超威同意了 -> X同意, 三叔不同意 -> X不同意,
    chooooovy同意意意了 -> X同意, 真不一样了 -> 真不一样, 啊一占 -> 阿一占, and traditional
    characters and words that mean the same are written one way: 領會員 -> 领会员,
    不要狐狸 -> 别狐狸."""
    text = store.normal(phrase).translate(_simplified())
    shape = RUNS.sub(r"\1", _with(text, _names(text), SLOT))
    for word, same in SAME_WORDS:
        shape = shape.replace(word, same)
    while shape and shape[-1] in surf.PARTICLES:
        shape = shape[:-1]
    return RUNS.sub(r"\1", shape.translate(SAME_CHARS))


def only_names(phrase):
    """Whether a phrase is only names, or only a word in letters (chooovy, ggg): no meme's
    words are in it."""
    found = shape(phrase)
    return SLOT in found and not _kept(found)


def pattern(phrase):
    """A phrase with each name in it as XX: 超威同意了 -> XX同意了; one without names is as
    it is (真不一样了)."""
    text = store.normal(phrase)
    return _with(text, _names(text), PATTERN_NAME)


def _kept(shape):
    """The characters of a shape that aren't someone."""
    return len(shape) - shape.count(SLOT)


@functools.lru_cache(maxsize=65536)
def _left_out(short, long):
    """The fewest parts of long to leave out so that what's left is short, or None when
    leaving parts out can't make it short."""
    @functools.lru_cache(maxsize=None)
    def fewest(i, j, leaving):
        if i == len(short):
            return 0 if j == len(long) or leaving else 1
        if len(long) - j < len(short) - i:
            return None
        best = []
        if short[i] == long[j]:
            best.append(fewest(i + 1, j + 1, False))
        skip = fewest(i, j + 1, True)
        if skip is not None:
            best.append(skip + (0 if leaving else 1))
        best = [b for b in best if b is not None]
        return min(best) if best else None
    return fewest(0, 0, False)


def _leaves_out(a, b):
    """Whether one shape is the other with up to MAX_LEFT_OUT parts left out: it keeps at
    least MIN_KEPT characters that aren't names, and leaves out names and up to MAX_ADDED
    other characters; to leave out any of those, it must keep ALONE_KEPT characters or a
    name (see the top of this file)."""
    short, long = sorted((a, b), key=len)
    kept, added = _kept(short), _kept(long) - _kept(short)
    if kept < MIN_KEPT or added > MAX_ADDED or added and kept < ALONE_KEPT and SLOT not in short:
        return False
    parts = _left_out(short, long)
    return parts is not None and parts <= MAX_LEFT_OUT


def _turned(shape):
    """A shape without the 不 or 没 that turn it around."""
    return "".join(char for char in shape if char not in NEGATIONS)


def _reordered(a, b):
    """Whether two shapes are the same characters in another order, at least ANAGRAM_KEPT
    that aren't names: 我先来怎么翻的 and 我先翻怎么来的."""
    return sorted(a) == sorted(b) and _kept(a) >= ANAGRAM_KEPT


def _misheard(a, b):
    """Whether one shape is the other with one character for another that sounds the same
    (a typo: 零会员 for 领会员), at least SOUND_KEPT characters that aren't names."""
    if len(a) != len(b) or min(_kept(a), _kept(b)) < SOUND_KEPT:
        return False
    differ = [(x, y) for x, y in zip(a, b) if x != y]
    return len(differ) == 1 and SLOT not in differ[0] and sound_alike(*differ[0])


def _same_shapes(a, b):
    """same_meme() for two shapes, without names in Chinese that aren't in the table."""
    if a == b:
        return _kept(a) >= 1  # not two names on their own (chooovy and ruler)
    if _turned(a) == _turned(b):
        return _kept(_turned(a)) >= 1
    if _listed_together(a, b) or _reordered(a, b) or _misheard(a, b):
        return True
    return _leaves_out(a, b) or _leaves_out(_turned(a), _turned(b))


def same_meme(a, b):
    """Whether two phrases are the same meme (see the top of this file). An unknown name in
    Chinese only counts in groups(), which sees where other names go. Two that are only a
    word in letters are the same when one is the other written longer (ggg and gg)."""
    if only_names(a) and only_names(b):
        return RUNS.sub(r"\1", store.normal(a)) == RUNS.sub(r"\1", store.normal(b))
    return _same_shapes(shape(a), shape(b))


def _common(a, b):
    """How many characters two shapes have in common in the same order, names, 不 and 没
    left out."""
    a, b = (_turned(s).replace(SLOT, "") for s in (a, b))
    row = [0] * (len(b) + 1)
    for char in a:
        before = 0  # row[j - 1] as it was for the character before
        for j in range(1, len(b) + 1):
            was = row[j]
            row[j] = before + 1 if char == b[j - 1] else max(row[j], row[j - 1])
            before = was
    return row[-1]


def kin(a, b):
    """Whether two shapes may be in one group: they have in common, in the same order, the
    characters that aren't names of the one with fewer, up to MIN_KEPT of them. A phrase
    that has two memes in it doesn't put them together: 阿一古 and ig加油 (阿一古加油), or
    恭喜gen and gen要夺冠了 (恭喜gen夺冠) have nothing in common."""
    need = min(MIN_KEPT, _kept(_turned(a)), _kept(_turned(b)))
    return need > 0 and _common(a, b) >= need


def _fill(slotted, other):
    """The Chinese name that other has where slotted has someone (X同意 and 兰子同意: 兰子),
    or None. Without 不 and 没, so 兰子不同意 fits X同意 with 兰子 too."""
    slotted, other = _turned(slotted), _turned(other)
    if slotted.count(SLOT) != 1 or SLOT in other or _kept(slotted) < MIN_KEPT:
        return None
    before, after = slotted.split(SLOT)
    low, high = NAME_CHARS
    found = re.fullmatch(re.escape(before) + f"([一-鿿]{{{low},{high}}})" + re.escape(after),
                         other)
    if not found:
        return None
    name = found.group(1)
    return None if name in NOT_NAMES or name[0] in NOT_NAME_START else name


def groups(phrases):
    """The phrases that are the same meme, together: [{"pattern": "XX同意了", "phrases":
    [超威同意了, chovy同意了, ...], "names": [{"name": "超威", "player": "Chovy"}, ...]}].

    phrases: {phrase: people} (how many people sent it), or the phrases in a list, the most
    sent first. In a group, the most sent phrase comes first. The pattern is the most sent
    phrase that has a name, with the name as XX, when at least half as many people send it
    as the first one (「XX今年真不一样了」: 107 people, next to 153 for 真不一样了), else the
    first one (让二追三, not XX加油让二追三). The group with the most sent phrase comes
    first. names: the names in its phrases, each once, player "" for one that isn't in the
    table (ruler, and 兰子 where others show a name goes there). Every phrase in a group is
    kin() to its first one."""
    people = dict(phrases) if isinstance(phrases, dict) else {p: 0 for p in phrases}
    order = sorted(people, key=lambda p: -people[p])  # stable: a list keeps its order
    rank = {phrase: i for i, phrase in enumerate(order)}
    shapes = {phrase: shape(phrase) for phrase in order}
    found = {phrase: _names(store.normal(phrase)) for phrase in order}
    leader = {phrase: phrase for phrase in order}
    members = {phrase: [phrase] for phrase in order}  # each group's, under the one leading it

    def lead(phrase):
        while leader[phrase] != phrase:
            leader[phrase] = leader[leader[phrase]]
            phrase = leader[phrase]
        return phrase

    def join(a, b, listed=False):
        """Put the groups of a and b together, the one sent more leading, unless a phrase of
        the other one isn't kin() to it, or listed: data/same_memes.txt says a and b are one
        meme (大的来了 has only 来 in common with 要来力, but goes with 大的要来了 = 要来力).
        Returns whether they are one group now."""
        a, b = lead(a), lead(b)
        if a == b:
            return True
        first, second = sorted((a, b), key=rank.get)
        if not listed and not all(kin(shapes[first], shapes[phrase])
                                  for phrase in members[second]):
            return False
        leader[second] = first
        members[first] += members.pop(second)
        return True

    # What two phrases must have for _same_shapes() to be worth asking: about as many
    # characters, and one's characters all among the other's (without names, 不 and 没);
    # or as many, but for one (_misheard()); or a line of data/same_memes.txt
    quick = {}
    for phrase in order:
        turned = _turned(shapes[phrase])
        quick[phrase] = (_kept(turned), frozenset(turned) - {SLOT})
    for i, a in enumerate(order):
        kept_a, chars_a = quick[a]
        for b in order[i + 1:]:
            kept_b, chars_b = quick[b]
            maybe = abs(kept_a - kept_b) <= MAX_ADDED and (chars_a <= chars_b or
                                                         chars_b <= chars_a)
            misheard = kept_a == kept_b and len(chars_a - chars_b) <= 1 and \
                len(chars_b - chars_a) <= 1  # one character for another: _misheard()
            listed = _listed_together(shapes[a], shapes[b])
            if not (maybe or misheard or listed):
                continue
            if _same_shapes(shapes[a], shapes[b]):
                join(a, b, listed)

    # Names in Chinese that aren't in the table: where two different names go already
    who = {}  # {shape with someone: the names that go there}
    for phrase in order:
        if shapes[phrase].count(SLOT) == 1:
            who.setdefault(_turned(shapes[phrase]), set()).update(
                player or store.normal(phrase)[start:end]
                for start, end, player in found[phrase])
    unknown = {}  # {phrase: the name in it that isn't in the table}
    for slotted, names_there in sorted(who.items(), key=lambda item: -len(item[1])):
        if len(names_there) < SHOWN_NAMES:  # where the most names go first: XX同意了 has
            continue  # 超威, chovy, 三叔 ..., 多兰XX only mvp and nb
        named = [p for p in order if _turned(shapes[p]) == slotted]  # the most sent first
        for phrase in order:
            if phrase in unknown:  # one name a phrase: 多兰同意了 is 多兰 in XX同意了, so
                continue  # it isn't 同意 in 多兰XX too (that put 多兰是人啊 with 超威同意了)
            name = _fill(slotted, shapes[phrase])
            if name and people[phrase] <= UNKNOWN_MORE * people[named[0]] and \
                    join(phrase, named[0]):
                unknown[phrase] = name

    together = {}
    for phrase in order:
        together.setdefault(lead(phrase), []).append(phrase)
    for phrase, name in unknown.items():  # where it is, as names() gives them
        text = store.normal(phrase)
        if name in text:
            found[phrase] = sorted(found[phrase] + [(text.index(name),
                                                     text.index(name) + len(name), "")])

    result = []
    for phrases_there in together.values():
        worded = [p for p in phrases_there if _kept(shapes[p])]  # not ggg, which is no name
        named = [p for p in worded if found[p] and 2 * people[p] >= people[phrases_there[0]]]
        if named:
            head = store.normal(named[0])
            name = _with(head, found[named[0]], PATTERN_NAME)
        else:
            name = phrases_there[0]
        listed, seen = [], set()
        for phrase in worded:
            text = store.normal(phrase)
            for start, end, player in found[phrase]:
                if nicknames.key(text[start:end]) not in seen:
                    seen.add(nicknames.key(text[start:end]))
                    listed.append({"name": text[start:end], "player": player})
        result.append({"pattern": name, "phrases": phrases_there, "names": listed})
    return result


@functools.lru_cache(maxsize=65536)
def _units(phrase):
    return tuple(surf.units(surf.clean(phrase)))


def twists(phrase, others):
    """The phrases among others that are another meme on the same template as phrase, in
    their order: 我们是冠军 for 我们是路边, 千言万语感谢现役 for 千言万语感谢多兰. They share
    their start and end, more than half of the longer one (surf.variant()), but aren't the
    same meme (same_meme()): one may come from the other."""
    mine, key, found = list(_units(phrase)), store.normal(phrase), []
    for other in others:
        if store.normal(other) == key or other in found:
            continue
        if surf.variant(mine, list(_units(other))) and not same_meme(phrase, other):
            found.append(other)
    return found


def learned_of(groups_of, memes):
    """Which learned memes some groups of phrases are (a candidate: its phrase and its
    variants): {name: [learned phrase, ...]} for groups_of {name: [phrase, ...]}, the learned
    memes (memes: memes.json's {phrase: entry}) in groups() order, and only for the groups
    that are one. Not a meme whose "apart" holds the group's first phrase: you said it isn't
    that one."""
    owner = store.owners(memes)
    asked = [phrase for phrases in groups_of.values() for phrase in phrases]
    together = groups(list(dict.fromkeys(list(owner) + asked)))
    group_of = {phrase: i for i, group in enumerate(together) for phrase in group["phrases"]}
    found = {}
    for name, phrases in groups_of.items():
        if not phrases:
            continue
        hits = []
        for phrase in phrases:
            for other in together[group_of[phrase]]["phrases"] if phrase in group_of else ():
                meme = owner.get(other)
                if meme and meme not in hits and \
                        store.normal(phrases[0]) not in (memes[meme].get("apart") or []):
                    hits.append(meme)
        if hits:
            found[name] = hits
    return found
