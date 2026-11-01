"""Command line entry point: py surf.py (the meme bot reads Bilibili for new memes).

The code lives in the memelearner package: py surf.py is short for py -m memelearner learn,
and memelearner/cli.py has the other commands.
"""

from memelearner.surf import main

if __name__ == "__main__":
    main()
