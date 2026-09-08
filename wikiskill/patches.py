"""Validate and apply bounded text changes before touching workspace files."""

from __future__ import annotations

import re
from copy import deepcopy
import yaml

Skills = dict[str, dict[str, str]]


def apply_edits(text: str, edits: list[dict]) -> str:
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must be a non-empty list")
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("Each edit must be an object")
        op, content = edit.get("op"), edit.get("content")
        if not isinstance(content, str):
            raise ValueError("Edit content must be text")
        if op == "append":
            if set(edit) != {"op", "content"}:
                raise ValueError("append takes only op and content")
            text += content
        elif op in ("replace", "insert_after"):
            if set(edit) != {"op", "target", "content"}:
                raise ValueError(f"{op} requires op, target and content")
            target = edit["target"]
            if not isinstance(target, str) or not target or text.count(target) != 1:
                raise ValueError("A patch target must match exactly once")
            replacement = content if op == "replace" else target + content
            text = text.replace(target, replacement, 1)
        else:
            raise ValueError(f"Unsupported edit operation: {op!r}")
    return text


def validate_skill(name: str, skill_md: str, purpose_md: str) -> None:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
        raise ValueError("Skill directory names must use lower-case snake_case")
    if not isinstance(skill_md, str) or not isinstance(purpose_md, str):
        raise ValueError("Skill and purpose content must be text")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", skill_md, re.DOTALL)
    if not match or not match[2].strip():
        raise ValueError("SKILL.md requires YAML frontmatter followed by instructions")
    try:
        fields = yaml.safe_load(match[1])
    except yaml.YAMLError as exc:
        raise ValueError("Invalid skill YAML frontmatter") from exc
    if not isinstance(fields, dict) or fields.get("name") != name:
        raise ValueError("Frontmatter needs a matching name")
    if not isinstance(fields.get("description"), str) or not fields["description"].strip():
        raise ValueError("Frontmatter needs a non-empty description")
    if not purpose_md.strip():
        raise ValueError("PURPOSE.md must describe the skill's origin")


def apply_proposal(skills: Skills, proposal: dict, iteration: int) -> Skills:
    if not isinstance(proposal, dict):
        raise ValueError("A proposal must be an object")
    candidate = deepcopy(skills)
    action = proposal.get("action")
    if action == "no_action":
        if set(proposal) != {"action"}:
            raise ValueError("no_action takes only action")
        return candidate
    name = proposal.get("name")
    if action == "create":
        if set(proposal) != {"action", "name", "skill_md", "purpose_md"}:
            raise ValueError("create requires name, skill_md and purpose_md")
        validate_skill(name, proposal["skill_md"], proposal["purpose_md"])
        candidate[name] = {"SKILL.md": proposal["skill_md"], "PURPOSE.md": proposal["purpose_md"]}
    elif action == "patch":
        if set(proposal) != {"action", "name", "edits"}:
            raise ValueError("patch requires name and edits")
        if not isinstance(name, str) or name not in candidate:
            raise ValueError("Cannot patch a skill that does not exist")
        candidate[name]["SKILL.md"] = apply_edits(candidate[name]["SKILL.md"], proposal["edits"])
        candidate[name]["PURPOSE.md"] += f"\n- Iteration {iteration}: patched; details in wiki/skill-impact.md.\n"
        validate_skill(name, **dict(skill_md=candidate[name]["SKILL.md"],
                                   purpose_md=candidate[name]["PURPOSE.md"]))
    else:
        raise ValueError(f"Unsupported proposal action: {action!r}")
    return candidate
