"""PyInstaller entry point. Same effect as `python -m crm`.

A wrapper is required because passing crm/__main__.py directly to
PyInstaller drops the package context, breaking the relative import.
Invoking from this top-level script puts the repo root on sys.path,
so `crm.cli` resolves cleanly during analysis and at runtime.
"""

from crm.cli import main

if __name__ == "__main__":
    main()
