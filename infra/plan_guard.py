"""Fail when a tofu plan deletes the droplet, unless the commit opts in."""
import json
import os
import sys

plan = json.load(sys.stdin)
doomed = [
    rc["address"]
    for rc in plan.get("resource_changes", [])
    if rc["type"] == "digitalocean_droplet" and "delete" in rc["change"]["actions"]
]
if doomed and "[allow-droplet-replace]" not in os.environ.get("COMMIT_MESSAGE", ""):
    sys.exit(f"plan deletes {doomed}; that destroys the history. "
             "Add [allow-droplet-replace] to the commit message to proceed.")
print("plan guard: ok" + (f" (override: {doomed})" if doomed else ""))
