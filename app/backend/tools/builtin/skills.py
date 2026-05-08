from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

try:
    from app.backend.runtime_paths import PROFILE_ROOT
    from app.backend import layer_skills
except ImportError:  # pragma: no cover - fallback for direct module execution
    from runtime_paths import PROFILE_ROOT  # type: ignore
    import layer_skills  # type: ignore

SKILLS_ROOT = os.path.join(str(PROFILE_ROOT), "companion", "skills")
VALID_LAYERS = ("companion", "assistant", "shared")
MAX_CONTENT_BYTES = 32 * 1024
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

_LAYER_SEARCH_ORDER = ("shared", "companion", "assistant")


def help_skill(name: str, layer: str = "") -> str:
    clean_name = str(name or "").strip()
    clean_layer = str(layer or "").strip()
    if not clean_name:
        return "Skill name is required."

    if clean_layer in VALID_LAYERS:
        try:
            content = layer_skills.get_skill_content(clean_layer, clean_name)
        except Exception:  # noqa: BLE001
            content = None
        if content:
            return content

    for candidate_layer in _LAYER_SEARCH_ORDER:
        if clean_layer in VALID_LAYERS and candidate_layer == clean_layer:
            continue
        try:
            content = layer_skills.get_skill_content(candidate_layer, clean_name)
        except Exception:  # noqa: BLE001
            content = None
        if content:
            return content

    return f"Skill '{clean_name}' not found. Use list_skills to see available skills."


def _extract_description(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return "(no description)"
    except UnicodeDecodeError:
        return "(no description)"
    metadata, _body, missing = layer_skills.parse_skill_content(content)
    if missing:
        return "(invalid frontmatter)"
    return str(metadata.get("description") or metadata.get("purpose") or "").strip() or "(no description)"


def list_skills(layer: str = "all") -> str:
    clean_layer = str(layer or "all").strip() or "all"
    if clean_layer != "all" and clean_layer not in VALID_LAYERS:
        return f"Invalid layer '{clean_layer}'. Must be one of: {', '.join(VALID_LAYERS)}, all."

    target_layers = VALID_LAYERS if clean_layer == "all" else (clean_layer,)
    try:
        layer_skills.ensure_skill_library()
    except Exception:  # noqa: BLE001
        pass

    entries: list[str] = []
    for layer_name in target_layers:
        try:
            layer_dir = os.path.join(SKILLS_ROOT, layer_name)
            if not os.path.isdir(layer_dir):
                continue
            for root, _dirs, filenames in os.walk(layer_dir):
                for filename in sorted(filenames):
                    if not filename.endswith(".md"):
                        continue
                    full_path = os.path.join(root, filename)
                    if not os.path.isfile(full_path):
                        continue
                    skill_name = Path(filename).stem
                    if skill_name == "SKILL":
                        skill_name = Path(root).name
                    description = _extract_description(full_path)
                    if description == "(invalid frontmatter)":
                        continue
                    entries.append(f"{skill_name} ({layer_name}): {description}")
        except OSError:
            continue

    if not entries:
        return f"No skills found for layer '{clean_layer}'."
    return "\n".join(entries)


def _is_within_skills_root(target: str) -> bool:
    try:
        common = os.path.commonpath([os.path.abspath(target), os.path.abspath(SKILLS_ROOT)])
    except ValueError:
        return False
    return common == os.path.abspath(SKILLS_ROOT)


def write_skill(name: str, layer: str, content: str) -> str:
    clean_name = str(name or "").strip()
    clean_layer = str(layer or "").strip()
    body = "" if content is None else str(content)

    if clean_layer not in VALID_LAYERS:
        return f"Invalid layer '{clean_layer}'. Must be one of: {', '.join(VALID_LAYERS)}."
    if not NAME_PATTERN.match(clean_name):
        return f"Invalid skill name '{clean_name}'. Use alphanumeric, hyphens, and underscores only."
    if len(body.encode("utf-8")) > MAX_CONTENT_BYTES:
        return f"Skill content exceeds {MAX_CONTENT_BYTES} byte limit."
    metadata, _content_body, missing = layer_skills.parse_skill_content(body)
    if missing:
        return (
            "Skill frontmatter is required at the top of the file. "
            f"Missing required keys: {', '.join(missing)}."
        )

    target = os.path.join(SKILLS_ROOT, clean_layer, clean_name + ".md")
    if not _is_within_skills_root(target):
        return "Invalid skill path."

    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        backup_note = ""
        if os.path.exists(target):
            backup_dir = os.path.join(SKILLS_ROOT, ".backups", clean_layer)
            os.makedirs(backup_dir, exist_ok=True)
            stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            backup_path = os.path.join(backup_dir, f"{clean_name}.{stamp}.md")
            shutil.copy2(target, backup_path)
            backup_note = f" Backup created: .backups/{clean_layer}/{clean_name}.{stamp}.md."
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(body)
    except OSError as exc:
        return f"Error writing skill: {exc}"

    preview = "\n".join(body.splitlines()[:12])
    return (
        f"Skill '{clean_name}' written to {clean_layer}.{backup_note} "
        "It will appear in the skill registry on the next turn.\n"
        f"[Preview]\n{preview}"
    )


def delete_skill(name: str, layer: str) -> str:
    clean_name = str(name or "").strip()
    clean_layer = str(layer or "").strip()

    if clean_layer not in VALID_LAYERS:
        return f"Invalid layer '{clean_layer}'. Must be one of: {', '.join(VALID_LAYERS)}."
    if not NAME_PATTERN.match(clean_name):
        return f"Invalid skill name '{clean_name}'. Use alphanumeric, hyphens, and underscores only."

    target = os.path.join(SKILLS_ROOT, clean_layer, clean_name + ".md")
    if not _is_within_skills_root(target):
        return "Invalid skill path."

    if not os.path.exists(target):
        return f"Skill '{clean_name}' not found in {clean_layer}."

    try:
        backup_dir = os.path.join(SKILLS_ROOT, ".backups", clean_layer)
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        backup_path = os.path.join(backup_dir, f"{clean_name}.{stamp}.deleted.md")
        shutil.copy2(target, backup_path)
        os.remove(target)
    except OSError as exc:
        return f"Error deleting skill: {exc}"

    return f"Skill '{clean_name}' deleted from {clean_layer}. Backup created: .backups/{clean_layer}/{clean_name}.{stamp}.deleted.md."
