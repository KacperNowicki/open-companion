from __future__ import annotations

import re
from pathlib import Path

import context_manager
from runtime_paths import PROFILE_ROOT, PROJECT_ROOT, ensure_runtime_dirs

SHIPPED_SKILLS_ROOT = PROJECT_ROOT / "companion" / "skills"
SKILLS_ROOT = PROFILE_ROOT / "companion" / "skills"

_LAYER_SKILL_DIRS = {
    "shared": ("shared",),
    "companion": ("shared", "companion"),
    "assistant": ("shared", "assistant"),
}
_FRONTMATTER_RE = re.compile(r"^---\n(?P<body>.*?)\n---\n?", re.DOTALL)
_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")
_DESCRIPTION_RE = re.compile(r"^<!--\s*description:\s*(.*?)\s*-->\s*$")
_REQUIRED_FRONTMATTER_KEYS = ("description",)
_STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "from",
    "into",
    "please",
    "that",
    "this",
    "with",
    "your",
}
_DEFAULT_SKILLS_CONFIG = {
    "index_enabled": True,
    "max_full_skills": 0,
    "index_budget_tokens": 700,
    "full_budget_tokens": 0,
}


def ensure_skill_library() -> None:
    ensure_runtime_dirs()
    for directory in ("shared", "companion", "assistant"):
        (SKILLS_ROOT / directory).mkdir(parents=True, exist_ok=True)

    if not SHIPPED_SKILLS_ROOT.exists():
        return
    try:
        if SHIPPED_SKILLS_ROOT.resolve() == SKILLS_ROOT.resolve():
            return
    except OSError:
        return

    for source_path in sorted(SHIPPED_SKILLS_ROOT.rglob("*.md")):
        if not source_path.is_file():
            continue
        relative_path = source_path.relative_to(SHIPPED_SKILLS_ROOT)
        target_path = SKILLS_ROOT / relative_path
        if target_path.exists():
            continue
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            continue


def _iter_skill_files(layer_name: str) -> list[tuple[Path, str]]:
    ensure_skill_library()
    directories = _LAYER_SKILL_DIRS.get(layer_name, ())
    seen: set[Path] = set()
    files: list[tuple[Path, str]] = []
    for directory in directories:
        root = SKILLS_ROOT / directory
        if not root.exists():
            continue
        for pattern in ("**/SKILL.md", "**/*.md"):
            for candidate in sorted(root.glob(pattern)):
                if candidate in seen or not candidate.is_file():
                    continue
                seen.add(candidate)
                files.append((candidate, directory))
    return files


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(content or "")
    if not match:
        return {}, content
    body = match.group("body")
    metadata: dict[str, str] = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip().strip('"').strip("'")
    return metadata, content[match.end():]


def parse_skill_content(content: str) -> tuple[dict[str, str], str, list[str]]:
    metadata, body = _parse_frontmatter(content)
    # Accept 'purpose' as a backward-compatible alias for 'description'
    has_description = bool(str(metadata.get("description") or "").strip())
    has_purpose = bool(str(metadata.get("purpose") or "").strip())
    missing = [] if (has_description or has_purpose) else ["description"]
    return metadata, body, missing


def _first_heading(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _first_paragraph(content: str) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
            continue
        if line.startswith("#") or line.startswith("<!--"):
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current).strip())
    return paragraphs[0] if paragraphs else ""


def _extract_description(body: str) -> str:
    lines = body.splitlines()
    if lines:
        match = _DESCRIPTION_RE.match(lines[0].strip())
        if match:
            return match.group(1).strip()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("<!--"):
            continue
        return stripped[:100]
    return ""


def _normalize_tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(str(text or "").lower())
        if token not in _STOP_WORDS
    }


def _skill_name_from_path(skill_path: Path) -> str:
    if skill_path.stem == "SKILL":
        return skill_path.parent.name
    return skill_path.stem


def _derive_skill_name(skill_path: Path, metadata: dict[str, str], body: str) -> str:
    return _skill_name_from_path(skill_path)


def _derive_skill_summary(metadata: dict[str, str], body: str) -> str:
    summary = str(
        metadata.get("description")
        or metadata.get("purpose")  # backward-compat alias
        or metadata.get("summary")
        or ""
    ).strip()
    if summary:
        return summary
    description = _extract_description(body)
    if description:
        return description
    paragraph = _first_paragraph(body)
    if paragraph:
        return paragraph
    heading = _first_heading(body)
    if heading:
        return heading
    return "Runtime workflow guidance."


def discover_layer_skills(layer_name: str) -> list[dict[str, str]]:
    skills: list[dict[str, str]] = []
    for skill_path, scope in _iter_skill_files(layer_name):
        try:
            raw_content = skill_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw_content:
            continue
        metadata, body, missing = parse_skill_content(raw_content)
        if missing:
            continue
        relative_path = str(skill_path.relative_to(SKILLS_ROOT)).replace("\\", "/")
        skills.append(
            {
                "name": _derive_skill_name(skill_path, metadata, body),
                "path": str(skill_path),
                "relative_path": relative_path,
                "summary": _derive_skill_summary(metadata, body),
                "scope": scope,
                "layer_visibility": "shared" if scope == "shared" else layer_name,
            }
        )
    return skills


def load_skill_body(skill: dict[str, str]) -> str:
    try:
        return Path(skill["path"]).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_layer_skills(layer_name: str) -> list[dict[str, str]]:
    skills: list[dict[str, str]] = []
    for skill in discover_layer_skills(layer_name):
        content = load_skill_body(skill)
        if not content:
            continue
        skills.append({"name": skill["name"], "content": content})
    return skills


def _take_sections_to_budget(sections: list[str], budget_tokens: int) -> list[str]:
    if budget_tokens <= 0:
        return []
    chosen: list[str] = []
    for section in sections:
        candidate = chosen + [section]
        if context_manager.count_tokens("\n\n".join(candidate)) > budget_tokens:
            break
        chosen.append(section)
    return chosen


def get_skills_config(config: dict | None = None) -> dict:
    context_cfg = config.get("context", {}) if isinstance(config, dict) else {}
    skills_cfg = context_cfg.get("skills", {}) if isinstance(context_cfg, dict) else {}
    merged = dict(_DEFAULT_SKILLS_CONFIG)
    if isinstance(skills_cfg, dict):
        merged.update(skills_cfg)
    try:
        merged["max_full_skills"] = max(0, int(merged.get("max_full_skills", _DEFAULT_SKILLS_CONFIG["max_full_skills"])))
    except (TypeError, ValueError):
        merged["max_full_skills"] = _DEFAULT_SKILLS_CONFIG["max_full_skills"]
    for key in ("index_budget_tokens", "full_budget_tokens"):
        try:
            merged[key] = max(0, int(merged.get(key, _DEFAULT_SKILLS_CONFIG[key])))
        except (TypeError, ValueError):
            merged[key] = _DEFAULT_SKILLS_CONFIG[key]
    merged["index_enabled"] = bool(merged.get("index_enabled", True))
    return merged


def build_skill_index_section(skills: list[dict[str, str]] | None = None, budget_tokens: int | None = None) -> str:
    if not skills:
        return ""
    lines = [
        "## Skills",
        "",
        "You have access to the following skills. Use `help_skill` to read the full content of any skill before following its workflow.",
        "",
    ]
    entries = []
    for skill in skills:
        summary = str(skill.get("summary") or "").strip()
        scope = str(skill.get("scope") or "shared").strip() or "shared"
        if summary:
            entries.append(f"- **{skill['name']}** ({scope}): {summary}")
        else:
            entries.append(f"- **{skill['name']}** ({scope}):")
    effective_budget = budget_tokens if budget_tokens is not None else _DEFAULT_SKILLS_CONFIG["index_budget_tokens"]
    remaining = max(0, effective_budget - context_manager.count_tokens("\n".join(lines)))
    chosen = _take_sections_to_budget(entries, remaining)
    if not chosen:
        return ""
    return "\n".join(lines + chosen).strip()


def _score_skill(skill: dict[str, str], query_tokens: set[str], layer_name: str) -> int:
    haystack = " ".join(
        [
            skill.get("name", ""),
            skill.get("summary", ""),
            skill.get("relative_path", ""),
            skill.get("layer_visibility", ""),
        ]
    ).lower()
    haystack_tokens = _normalize_tokens(haystack)
    name_tokens = _normalize_tokens(skill.get("name", ""))
    score = 1 if skill.get("layer_visibility") == layer_name else 0
    for token in query_tokens:
        if token in haystack_tokens:
            score += 4
        if token in name_tokens:
            score += 4
    return score


def select_relevant_skills(
    layer_name: str,
    user_message: str,
    config: dict | None = None,
    skills: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    cfg = get_skills_config(config)
    max_full = cfg["max_full_skills"]
    if max_full <= 0:
        return []
    available = list(skills if skills is not None else discover_layer_skills(layer_name))
    query_tokens = _normalize_tokens(user_message)
    if not query_tokens:
        return []
    scored = [
        (skill, _score_skill(skill, query_tokens, layer_name))
        for skill in available
    ]
    scored = [item for item in scored if item[1] > 1]
    scored.sort(key=lambda item: (-item[1], item[0]["relative_path"]))
    return [item[0] for item in scored[:max_full]]


def build_selected_skill_section(skills: list[dict[str, str]] | None = None, budget_tokens: int | None = None) -> str:
    if not skills:
        return ""
    sections = []
    for skill in skills:
        content = skill.get("content") or load_skill_body(skill)
        if not content:
            continue
        sections.append(f"### {skill['name']}\n{content}")
    if not sections:
        return ""
    header = "## Selected Runtime Skills"
    effective_budget = budget_tokens if budget_tokens is not None else _DEFAULT_SKILLS_CONFIG["full_budget_tokens"]
    remaining = max(0, effective_budget - context_manager.count_tokens(header))
    chosen = _take_sections_to_budget(sections, remaining)
    if not chosen:
        return ""
    return "\n\n".join([header] + chosen).strip()


def prepare_skill_context(layer_name: str, user_input: str = "", config: dict | None = None) -> dict[str, object]:
    cfg = get_skills_config(config)
    all_skills = discover_layer_skills(layer_name)
    index_section = ""
    if cfg["index_enabled"]:
        index_section = build_skill_index_section(all_skills, budget_tokens=cfg["index_budget_tokens"])
    selected = select_relevant_skills(layer_name, user_input, config=config, skills=all_skills)
    selected_section = build_selected_skill_section(selected, budget_tokens=cfg["full_budget_tokens"])
    return {
        "all_skills": all_skills,
        "selected_skills": selected,
        "index_section": index_section,
        "selected_section": selected_section,
    }


def build_skill_prompt_section(layer_name: str, user_message: str = "", config: dict | None = None) -> str:
    context = prepare_skill_context(layer_name, user_message, config=config)
    parts = [part for part in (context["index_section"], context["selected_section"]) if part]
    return "\n\n".join(parts).strip()


def get_skill_content(layer_name: str, skill_name: str) -> str | None:
    clean_name = str(skill_name or "").strip()
    if not clean_name:
        return None
    for candidate, _scope in _iter_skill_files(layer_name):
        if _skill_name_from_path(candidate) != clean_name:
            continue
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            return None
    return None
