"""Reject destructive changes and any resources outside this new REIGN pilot.

Reads potentially secret plan JSON on stdin; prints only addresses and actions.
This intentionally cannot authorize changes to Workmates or existing Git High.
"""
import json
import re
import sys

EXPECTED = {
    'render_private_service.core["pilot"]': "reign-core-pilot",
    'render_private_service.temporal["pilot"]': "reign-temporal-pilot",
    'render_private_service.computer["pilot"]': "reign-computer-pilot",
    'render_web_service.ax["pilot"]': "reign-ax-pilot",
}
PROTECTED_IDS = {"srv-dbhrk6vf3r2c73c7faog", "srv-dbhrjvsipgvc73di8gd0", "srv-dbk4ff942hec73ejt9jg"}


def validate(plan):
    if not isinstance(plan, dict) or "planned_values" not in plan:
        raise ValueError("A full OpenTofu plan JSON is required")
    summaries = []
    for item in plan.get("resource_changes", []):
        address = item.get("address", "")
        change = item.get("change", {})
        actions = change.get("actions", [])
        before, after = change.get("before") or {}, change.get("after") or {}
        if address not in EXPECTED:
            raise ValueError("Plan includes a resource outside the REIGN pilot")
        if actions not in (["no-op"], ["create"], ["update"]):
            raise ValueError("Deletes, replacements, and unknown actions require a separate review")
        if change.get("importing"):
            raise ValueError("Adopting existing infrastructure requires a separate review")
        if any(value.get("id") in PROTECTED_IDS for value in (before, after)):
            raise ValueError("Plan touches an existing protected service")
        if after.get("name") != EXPECTED[address]:
            raise ValueError("Service name does not match the approved scope")
        if after.get("region") != "oregon" or after.get("num_instances") != 1:
            raise ValueError("Pilot must stay in Oregon with one instance per service")
        if after.get("plan") not in ("starter", "standard"):
            raise ValueError("Unreviewed compute plan")
        image = (after.get("runtime_source") or {}).get("image") or {}
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("digest", "")):
            raise ValueError("Image digest must be known and immutable")
        disk = after.get("disk")
        previous_disk = before.get("disk")
        if previous_disk and any(previous_disk.get(key) != (disk or {}).get(key) for key in ("name", "mount_path", "size_gb")):
            raise ValueError("Existing persistent disk changes require a separate review")
        if disk and (address != 'render_private_service.temporal["pilot"]' or disk.get("size_gb") != 1 or disk.get("mount_path") != "/data"):
            raise ValueError("Unreviewed disk configuration")
        summaries.append(f"{address}: {','.join(actions)} ({after['plan']}); image {image.get('image_url')}@{image['digest']}; disk {disk.get('size_gb') if disk else 0} GB")
    return summaries


if __name__ == "__main__":
    try:
        print("\n".join(validate(json.load(sys.stdin))) or "No infrastructure changes.")
    except (ValueError, KeyError, TypeError):
        print("Plan policy failed. Review privately; raw plans may contain secrets.", file=sys.stderr)
        sys.exit(1)
