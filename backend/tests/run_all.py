"""Run every test_*.py in this folder without needing pytest:  python tests/run_all.py   (from backend/).
The tests are plain functions named test_*, so `pytest` runs them too when it is installed."""

import importlib
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))


def main(argv: list[str]) -> int:
    only = argv[1:]
    modules = sorted(p.stem for p in HERE.glob("test_*.py") if not only or any(o in p.stem for o in only))
    passed = failed = 0
    for name in modules:
        module = importlib.import_module(name)
        for fn_name in sorted(n for n in dir(module) if n.startswith("test_")):
            fn = getattr(module, fn_name)
            try:
                fn()
                passed += 1
                print(f"PASS {name}.{fn_name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}.{fn_name}")
                traceback.print_exc()
    print(f"{passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
