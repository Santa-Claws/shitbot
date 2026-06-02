#!/usr/bin/env python3
"""Subreddit manager for shitbot. Reads/writes /data/subreddits.json on the LXC container."""

import json
import subprocess
import sys

PROXMOX_HOST = "root@192.168.254.201"
CONTAINER_ID = "100"
CONFIG_PATH = "/opt/shitbot/shitpost_data/subreddits.json"


def run(cmd: str) -> str:
    result = subprocess.run(
        ["ssh", PROXMOX_HOST, f"pct exec {CONTAINER_ID} -- bash -c {repr(cmd)}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def load() -> list[dict]:
    raw = run(f"cat {CONFIG_PATH}")
    return json.loads(raw)


def save(subs: list[dict]) -> None:
    data = json.dumps(subs, indent=2)
    run(f"echo {repr(data)} > {CONFIG_PATH}")


def print_list(subs: list[dict]) -> None:
    print()
    if not subs:
        print("  (no subreddits configured)")
        return
    max_len = max(len(s["name"]) for s in subs)
    for i, s in enumerate(subs, 1):
        print(f"  {i:2}. {s['name']:<{max_len}}  (weight: {s['weight']:.2f})")
    print()


def prompt_weight(default: float = 1.0) -> float:
    while True:
        raw = input(f"Weight (default {default}, min 0.1): ").strip()
        if not raw:
            return default
        try:
            w = float(raw)
            if w < 0.1:
                print("Minimum weight is 0.1")
                continue
            return round(w, 4)
        except ValueError:
            print("Enter a number")


def pick(subs: list[dict], prompt: str) -> int | None:
    while True:
        raw = input(prompt).strip()
        if not raw:
            return None
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(subs):
                return idx
            print(f"Enter a number between 1 and {len(subs)}")
        except ValueError:
            print("Enter a number")


def main() -> None:
    print("\n╔═══════════════════════════════╗")
    print("║  Shitbot Subreddit Manager    ║")
    print("╚═══════════════════════════════╝")

    while True:
        subs = load()
        print_list(subs)
        print("  [a] Add subreddit")
        print("  [r] Remove subreddit")
        print("  [w] Set weight")
        print("  [q] Quit")
        print()

        choice = input("Choice: ").strip().lower()

        if choice == "q":
            print("Bye.")
            break

        elif choice == "a":
            name = input("Subreddit name (without r/): ").strip()
            if not name:
                continue
            if any(s["name"].lower() == name.lower() for s in subs):
                print(f"'{name}' is already in the list.")
                continue
            weight = prompt_weight(1.0)
            subs.append({"name": name, "weight": weight})
            save(subs)
            print(f"Added r/{name} (weight: {weight})")

        elif choice == "r":
            if not subs:
                print("Nothing to remove.")
                continue
            idx = pick(subs, "Remove number: ")
            if idx is None:
                continue
            removed = subs.pop(idx)
            save(subs)
            print(f"Removed r/{removed['name']}")

        elif choice == "w":
            if not subs:
                print("Nothing to edit.")
                continue
            idx = pick(subs, "Set weight for number: ")
            if idx is None:
                continue
            weight = prompt_weight(subs[idx]["weight"])
            subs[idx]["weight"] = weight
            save(subs)
            print(f"Updated r/{subs[idx]['name']} → weight {weight}")

        else:
            print("Unknown option.")


if __name__ == "__main__":
    main()
