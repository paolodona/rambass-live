"""``python -m rambass`` — how the console's rebuilds invoke the CLI.

The subprocess route exists so a rebuild runs exactly what a human typing the
same command would run; going through ``-m`` pins it to the interpreter and
checkout the console itself runs from, rather than whatever ``rambass``
happens to be first on PATH.
"""

from .cli import main

raise SystemExit(main())
