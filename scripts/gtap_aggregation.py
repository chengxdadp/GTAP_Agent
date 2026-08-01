from __future__ import annotations

import re
from typing import Any


SECTION_MARKER = "= = = = = ="
TARGET_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def normalize_label(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def parse_mapping_sections(text: str) -> list[list[str]]:
    sections: list[list[str]] = []
    current: list[str] | None = None
    in_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("="):
            if not in_section:
                current = []
                in_section = True
            else:
                sections.append(current or [])
                current = None
                in_section = False
            continue
        if not in_section or current is None or not line or line.startswith("!"):
            continue
        current.append(line)
    if in_section:
        raise ValueError("GTAPAgg mapping has an unclosed section.")
    if len(sections) != 6:
        raise ValueError(f"Expected 6 GTAPAgg mapping sections, found {len(sections)}.")
    return sections


def split_target(line: str) -> tuple[str, str]:
    if "&" not in line:
        raise ValueError(f"Invalid GTAPAgg target line: {line}")
    name, description = line.split("&", 1)
    return name.strip(), description.strip()


def split_member(line: str) -> dict[str, str]:
    if "&" not in line:
        raise ValueError(f"Invalid GTAPAgg member line: {line}")
    left, target = line.rsplit("&", 1)
    left = left.strip()
    parts = left.split(None, 1)
    return {
        "code": parts[0],
        "description": parts[1].strip() if len(parts) > 1 else "",
        "left": left,
        "target": target.strip(),
    }


def validate_target_name(name: object, label: str) -> str:
    value = str(name or "").strip()
    if not value:
        raise ValueError(f"{label} group name is required.")
    if len(value) > 12:
        raise ValueError(f"{label} group name {value!r} exceeds the GTAPAgg 12-character limit.")
    if not TARGET_NAME_PATTERN.fullmatch(value):
        raise ValueError(
            f"{label} group name {value!r} must start with a letter and contain only letters, digits, or underscores."
        )
    return value


def resolve_member(selector: object, members: list[dict[str, str]], label: str) -> dict[str, str]:
    raw = str(selector or "").strip()
    key = normalize_label(raw)
    if not key:
        raise ValueError(f"Empty {label} member selector.")

    exact = [
        member
        for member in members
        if key
        in {
            normalize_label(member["code"]),
            normalize_label(member["description"]),
            normalize_label(member["left"]),
        }
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        choices = ", ".join(member["code"] for member in exact)
        raise ValueError(f"Ambiguous {label} member {raw!r}: {choices}.")

    fuzzy = [
        member
        for member in members
        if len(key) >= 4
        and (
            key in normalize_label(member["description"])
            or normalize_label(member["description"]) in key
        )
    ]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        choices = ", ".join(f"{member['code']} ({member['description']})" for member in fuzzy)
        raise ValueError(f"Ambiguous {label} member {raw!r}: {choices}.")

    sample = ", ".join(f"{member['code']} ({member['description']})" for member in members[:12])
    raise ValueError(f"Unknown {label} member {raw!r}. Use an original GTAP code or name, for example: {sample}.")


def customize_dimension(
    target_lines: list[str],
    member_lines: list[str],
    group_specs: object,
    label: str,
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    targets = [split_target(line) for line in target_lines]
    members = [split_member(line) for line in member_lines]
    canonical_targets = {normalize_label(name): name for name, _ in targets}
    for member in members:
        member["target"] = canonical_targets.get(normalize_label(member["target"]), member["target"])
    specs = group_specs or []
    if not isinstance(specs, list):
        raise ValueError(f"{label}_groups must be an array.")

    descriptions = {name: description for name, description in targets}
    target_order = [name for name, _ in targets]
    canonical_group_names = {normalize_label(name): name for name in target_order}
    assigned_codes: dict[str, str] = {}
    changes: list[dict[str, str]] = []

    for index, spec in enumerate(specs, start=1):
        if not isinstance(spec, dict):
            raise ValueError(f"{label}_groups item {index} must be an object.")
        requested_name = validate_target_name(spec.get("name"), label)
        group_key = normalize_label(requested_name)
        group_name = canonical_group_names.get(group_key, requested_name)
        canonical_group_names[group_key] = group_name
        description = str(spec.get("description") or descriptions.get(group_name) or group_name).strip()
        if len(description) > 30:
            raise ValueError(f"Description for {group_name!r} exceeds the GTAPAgg 30-character limit.")
        selectors = spec.get("members")
        if not isinstance(selectors, list) or not selectors:
            raise ValueError(f"{label} group {group_name!r} requires a non-empty members array.")

        descriptions[group_name] = description
        if group_name not in target_order:
            target_order.append(group_name)

        for selector in selectors:
            member = resolve_member(selector, members, label)
            code_key = normalize_label(member["code"])
            if code_key in assigned_codes:
                raise ValueError(
                    f"{label} member {member['code']!r} is assigned more than once: "
                    f"{assigned_codes[code_key]!r} and {group_name!r}."
                )
            old_target = member["target"]
            assigned_codes[code_key] = group_name
            member["target"] = group_name
            changes.append(
                {
                    "code": member["code"],
                    "description": member["description"],
                    "from": old_target,
                    "to": group_name,
                }
            )

    used_targets = {member["target"] for member in members}
    missing_targets = sorted(used_targets - set(descriptions))
    if missing_targets:
        raise ValueError(f"{label} mappings reference undefined targets: {', '.join(missing_targets)}.")

    rendered_targets = [
        f"{name:<12} & {descriptions[name]}"
        for name in target_order
        if name in used_targets
    ]
    rendered_members = [f"{member['left']} & {member['target']}" for member in members]
    return rendered_targets, rendered_members, changes


def build_custom_mapping(base_text: str, spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(spec, dict):
        raise ValueError("Custom aggregation spec must be an object.")
    region_groups = spec.get("region_groups") or []
    sector_groups = spec.get("sector_groups") or []
    if not region_groups and not sector_groups:
        raise ValueError("Custom aggregation requires at least one region_groups or sector_groups override.")

    sections = parse_mapping_sections(base_text)
    sector_targets, sector_members, sector_changes = customize_dimension(
        sections[0], sections[1], sector_groups, "sector"
    )
    region_targets, region_members, region_changes = customize_dimension(
        sections[2], sections[3], region_groups, "region"
    )
    output_sections = [
        sector_targets,
        sector_members,
        region_targets,
        region_members,
        sections[4],
        sections[5],
    ]

    lines = [
        "! Custom GTAPAgg mapping generated by GTAPAgent.",
        "! Unspecified original regions and sectors retain their default aggregation.",
        "",
    ]
    labels = [
        "new aggregated sectors",
        "original sector to aggregate mapping",
        "new aggregated regions",
        "original region to aggregate mapping",
        "new aggregated endowments",
        "original endowment to aggregate mapping",
    ]
    for label, section in zip(labels, output_sections):
        lines.extend([f"! Section: {label}", SECTION_MARKER, *section, SECTION_MARKER, ""])

    metadata = {
        "aggregation_name": str(spec.get("aggregation_name") or "custom aggregation"),
        "region_count": len(region_targets),
        "sector_count": len(sector_targets),
        "region_names": [split_target(line)[0] for line in region_targets],
        "sector_names": [split_target(line)[0] for line in sector_targets],
        "region_changes": region_changes,
        "sector_changes": sector_changes,
    }
    return "\n".join(lines).rstrip() + "\n", metadata
