"""apiwatch - detect undocumented changes in APIs you depend on.

  python -m apiwatch snapshot          take a snapshot of every target
  python -m apiwatch snapshot stripe   just one target
  python -m apiwatch diff              compare the two most recent snapshots
  python -m apiwatch log               list snapshots on disk

Exit code 1 when a BREAKING change is found, so CI can fail loudly.
"""

import argparse
import json
import pathlib
import sys

from . import diff, probe

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "targets.json"
SNAPSHOTS = ROOT / "snapshots"


def load_targets(only: str | None = None) -> list[dict]:
    targets = json.loads(CONFIG.read_text())["targets"]
    if only:
        targets = [t for t in targets if t["name"] == only]
        if not targets:
            sys.exit(f"no target named {only!r} in {CONFIG.name}")
    return targets


def snapshot_dir(name: str) -> pathlib.Path:
    path = SNAPSHOTS / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def cmd_snapshot(args) -> int:
    for target in load_targets(args.target):
        result = probe.snapshot(target)
        stamp = result["captured_at"].replace(":", "").replace("-", "")
        out = snapshot_dir(target["name"]) / f"{stamp}.json"
        out.write_text(json.dumps(result, indent=2, sort_keys=True))

        failed = [p["probe"] for p in result["probes"] if "error" in p]
        note = f" ({len(failed)} probe(s) failed: {', '.join(failed)})" if failed else ""
        print(f"[{target['name']}] captured {len(result['probes'])} probes -> "
              f"{out.relative_to(ROOT)}{note}")
    return 0


def cmd_diff(args) -> int:
    breaking_found = False

    for target in load_targets(args.target):
        files = sorted(snapshot_dir(target["name"]).glob("*.json"))
        if len(files) < 2:
            print(f"[{target['name']}] need 2 snapshots to diff, have {len(files)}")
            continue

        old = json.loads(files[-2].read_text())
        new = json.loads(files[-1].read_text())
        findings = diff.compare(old, new)

        print(diff.render(target["name"], findings))
        print(f"  ({files[-2].stem} -> {files[-1].stem})\n")

        if diff.has_breaking(findings):
            breaking_found = True

    return 1 if breaking_found else 0


def cmd_log(args) -> int:
    for target in load_targets(args.target):
        files = sorted(snapshot_dir(target["name"]).glob("*.json"))
        print(f"[{target['name']}] {len(files)} snapshots")
        for path in files[-10:]:
            print(f"  {path.stem}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apiwatch", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler in (("snapshot", cmd_snapshot), ("diff", cmd_diff), ("log", cmd_log)):
        sp = sub.add_parser(name)
        sp.add_argument("target", nargs="?", help="limit to one target")
        sp.set_defaults(func=handler)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
