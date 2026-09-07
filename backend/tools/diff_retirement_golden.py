"""Companion to capture_retirement_golden.py: diffs two golden-capture
JSON files, tolerant of tiny float noise, and reports every scenario/key
whose value materially changed. Numeric tolerance is $1 (these are
dollar-rounded fields already)."""
import json
import sys


def flatten(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def main():
    before_path, after_path = sys.argv[1], sys.argv[2]
    with open(before_path) as f:
        before = json.load(f)
    with open(after_path) as f:
        after = json.load(f)

    before_flat = dict(flatten(before))
    after_flat = dict(flatten(after))

    all_keys = set(before_flat) | set(after_flat)
    diffs = []
    for k in sorted(all_keys):
        b = before_flat.get(k, "<MISSING>")
        a = after_flat.get(k, "<MISSING>")
        if b == a:
            continue
        try:
            if abs(float(b) - float(a)) <= 1.0:
                continue
        except (TypeError, ValueError):
            pass
        diffs.append((k, b, a))

    if not diffs:
        print("NO MATERIAL DIFFERENCES (tolerance $1)")
        return
    print(f"{len(diffs)} material differences:")
    for k, b, a in diffs[:200]:
        print(f"  {k}: {b!r} -> {a!r}")
    if len(diffs) > 200:
        print(f"  ... and {len(diffs) - 200} more")


if __name__ == "__main__":
    main()
