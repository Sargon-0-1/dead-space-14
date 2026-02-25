#!/usr/bin/env python3
"""
Автоматическое разрешение конфликтов в .rsi файлах и картах.

Режимы:
  python fix_rsi_conflicts.py --dry-run   — только показывает что будет делать
  python fix_rsi_conflicts.py             — применяет изменения
"""

import subprocess
import json
import sys
import os

DRY_RUN = '--dry-run' in sys.argv

def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None

def git_show(ref, path):
    result = subprocess.run(
        f'git show {ref}:"{path}"',
        shell=True, capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None

def git_checkout(side, path):
    if not DRY_RUN:
        run(f'git checkout --{side} -- "{path}"')

def git_add(path):
    if not DRY_RUN:
        run(f'git add "{path}"')

def get_conflicted_files():
    out = run('git diff --name-only --diff-filter=U')
    if not out:
        return [], [], []
    files = out.splitlines()
    metas = [f for f in files if f.endswith('.rsi/meta.json')]
    pngs = [f for f in files if '.rsi/' in f and f.endswith('.png')]
    maps = [f for f in files if f.startswith('Resources/Maps/')]
    return metas, pngs, maps

def classify_png(path):
    """Returns: 'ours_only', 'theirs_only', 'both_same', 'both_differ'"""
    ours = run(f'git ls-tree HEAD -- "{path}"')
    theirs = run(f'git ls-tree MERGE_HEAD -- "{path}"')
    ours_exists = bool(ours and ours.strip())
    theirs_exists = bool(theirs and theirs.strip())

    if ours_exists and not theirs_exists:
        return 'ours_only'
    if theirs_exists and not ours_exists:
        return 'theirs_only'
    if ours_exists and theirs_exists:
        ours_blob = ours.split()[2] if len(ours.split()) > 2 else ''
        theirs_blob = theirs.split()[2] if len(theirs.split()) > 2 else ''
        return 'both_same' if ours_blob == theirs_blob else 'both_differ'
    return 'unknown'

def merge_meta_json(path):
    """Merge meta.json: union of states, merged copyright. Returns (success, description)."""
    ours_raw = git_show('HEAD', path)
    theirs_raw = git_show('MERGE_HEAD', path)

    if ours_raw is None and theirs_raw is None:
        return False, "SKIP — both missing"

    if ours_raw is None:
        if not DRY_RUN:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(theirs_raw)
            git_add(path)
        return True, "TAKE THEIRS (ours missing)"

    if theirs_raw is None:
        if not DRY_RUN:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(ours_raw)
            git_add(path)
        return True, "TAKE OURS (theirs missing)"

    try:
        ours = json.loads(ours_raw)
        theirs = json.loads(theirs_raw)
    except json.JSONDecodeError as e:
        return False, f"SKIP — JSON parse error: {e}"

    # --- Merge copyright ---
    ours_copyright = ours.get('copyright', '')
    theirs_copyright = theirs.get('copyright', '')
    if theirs_copyright and theirs_copyright not in ours_copyright:
        merged_copyright = ours_copyright.rstrip() + ' ' + theirs_copyright
    else:
        merged_copyright = ours_copyright

    # --- Merge states (union, ours order first, then new from theirs) ---
    ours_states = ours.get('states', [])
    theirs_states = theirs.get('states', [])
    ours_state_names = {s['name'] for s in ours_states}
    theirs_state_names = {s['name'] for s in theirs_states}

    merged_states = list(ours_states)
    added_from_theirs = []
    for state in theirs_states:
        if state['name'] not in ours_state_names:
            merged_states.append(state)
            added_from_theirs.append(state['name'])

    only_in_ours = ours_state_names - theirs_state_names

    # --- Build result ---
    result = dict(ours)
    result['copyright'] = merged_copyright
    result['states'] = merged_states

    # Also merge any other top-level keys from theirs that ours doesn't have
    for key in theirs:
        if key not in result:
            result[key] = theirs[key]

    if not DRY_RUN:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=4, ensure_ascii=False)
            f.write('\n')
        git_add(path)

    # --- Build description ---
    parts = []
    if added_from_theirs:
        parts.append(f"+theirs: {added_from_theirs}")
    if only_in_ours:
        parts.append(f"kept ours: {sorted(only_in_ours)}")
    if not added_from_theirs and not only_in_ours:
        parts.append("states identical, metadata merged")
    desc = f"MERGED ({', '.join(parts)})"
    return True, desc

def main():
    os.chdir(run('git rev-parse --show-toplevel'))

    metas, pngs, maps = get_conflicted_files()

    print(f"{'[DRY RUN] ' if DRY_RUN else ''}RSI Conflict Resolver")
    print(f"  meta.json: {len(metas)}")
    print(f"  PNG:       {len(pngs)}")
    print(f"  Maps:      {len(maps)}")
    print()

    # ============ META.JSON ============
    print("=" * 60)
    print("META.JSON")
    print("=" * 60)
    meta_ok = 0
    meta_fail = 0
    for path in metas:
        ok, desc = merge_meta_json(path)
        prefix = "  OK " if ok else "  FAIL"
        print(f"  {prefix}  {desc}")
        print(f"        {path}")
        if ok:
            meta_ok += 1
        else:
            meta_fail += 1

    # ============ PNG ============
    print()
    print("=" * 60)
    print("PNG FILES")
    print("=" * 60)

    png_stats = {'ours_only': [], 'theirs_only': [], 'both_same': [], 'both_differ': [], 'unknown': []}
    for path in pngs:
        kind = classify_png(path)
        png_stats[kind].append(path)

    # ours_only → take ours
    if png_stats['ours_only']:
        print(f"\n  OURS_ONLY ({len(png_stats['ours_only'])}) -> take ours:")
        for p in png_stats['ours_only']:
            print(f"    {p}")
            git_checkout('ours', p)
            git_add(p)

    # theirs_only → take theirs
    if png_stats['theirs_only']:
        print(f"\n  THEIRS_ONLY ({len(png_stats['theirs_only'])}) -> take theirs:")
        for p in png_stats['theirs_only']:
            print(f"    {p}")
            git_checkout('theirs', p)
            git_add(p)

    # both_same → take ours (identical)
    if png_stats['both_same']:
        print(f"\n  BOTH_SAME ({len(png_stats['both_same'])}) -> take ours (identical):")
        for p in png_stats['both_same']:
            print(f"    {p}")
            git_checkout('ours', p)
            git_add(p)

    # both_differ → take ours but WARN
    if png_stats['both_differ']:
        print(f"\n  WARNING: BOTH_DIFFER ({len(png_stats['both_differ'])}) -> take ours (REVIEW NEEDED!):")
        for p in png_stats['both_differ']:
            print(f"    {p}")
            git_checkout('ours', p)
            git_add(p)

    # ============ MAPS ============
    if maps:
        print()
        print("=" * 60)
        print("MAPS (all -> take ours)")
        print("=" * 60)
        for path in maps:
            print(f"  {path}")
            git_checkout('ours', path)
            git_add(path)

    # ============ SUMMARY ============
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total_resolved = meta_ok + len(pngs) + len(maps)
    print(f"  meta.json:  {meta_ok}/{len(metas)} resolved (failed: {meta_fail})")
    print(f"  PNG:        {len(pngs)}/{len(pngs)} resolved")
    print(f"    ours_only:   {len(png_stats['ours_only'])}")
    print(f"    theirs_only: {len(png_stats['theirs_only'])}")
    print(f"    both_same:   {len(png_stats['both_same'])}")
    print(f"    both_differ: {len(png_stats['both_differ'])} <- review these!")
    print(f"  Maps:       {len(maps)}/{len(maps)} resolved (took ours)")
    print(f"  TOTAL:      {total_resolved} files resolved")

    if DRY_RUN:
        print(f"\n  This was a DRY RUN. No files were changed.")
        print(f"  Run without --dry-run to apply.")
    else:
        remaining = run('git diff --name-only --diff-filter=U | wc -l') or '?'
        print(f"\n  Remaining conflicts: {remaining}")

if __name__ == '__main__':
    main()
