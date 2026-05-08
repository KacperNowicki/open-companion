#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
SCRATCH_ROOT = ROOT / "app" / "tests" / "integration" / ".tmp"

for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def _fresh_profile() -> Path:
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    profile = SCRATCH_ROOT / f"oc-skills-{uuid.uuid4().hex}"
    (profile / "companion" / "skills" / "shared").mkdir(parents=True, exist_ok=True)
    (profile / "companion" / "skills" / "companion").mkdir(parents=True, exist_ok=True)
    (profile / "companion" / "skills" / "assistant").mkdir(parents=True, exist_ok=True)
    return profile


def _reload_modules(profile: Path, project_root: Path | None = None) -> tuple[object, object]:
    os.environ["OPEN_COMPANION_PROFILE_DIR"] = str(profile)
    os.environ["OPEN_COMPANION_PROJECT_ROOT"] = str(project_root or profile)
    for name in list(sys.modules):
        if (
            name == "app"
            or name.startswith("app.")
            or name == "backend"
            or name.startswith("backend.")
            or name == "runtime_paths"
            or name == "layer_skills"
        ):
            sys.modules.pop(name, None)
    import runtime_paths  # noqa: F401
    import layer_skills
    from backend.tools.builtin import skills as skills_module
    return layer_skills, skills_module


def _cleanup(profile: Path) -> None:
    shutil.rmtree(profile, ignore_errors=True)


PASSED: list[str] = []
FAILED: list[str] = []


def _record(name: str, ok: bool, reason: str = "") -> None:
    if ok:
        PASSED.append(name)
        print(f"PASS: {name}")
    else:
        FAILED.append(name)
        print(f"FAIL: {name}: {reason}")


def test_write_and_list_skill() -> None:
    name = "test_write_and_list_skill"
    profile = _fresh_profile()
    try:
        _layer_skills, skills = _reload_modules(profile)
        content = "---\npurpose: Demo skill description.\ntools: run_terminal\nsyntax: demo command\nargs: input text\n---\n# Demo\n\nBody text."
        result = skills.write_skill("demo_skill", "shared", content)
        if "written to shared" not in result:
            _record(name, False, f"write result unexpected: {result}")
            return
        listing = skills.list_skills("shared")
        if "demo_skill" not in listing or "Demo skill description." not in listing:
            _record(name, False, f"listing missing entry or description: {listing!r}")
            return
        if "(shared)" not in listing:
            _record(name, False, f"listing missing layer label: {listing!r}")
            return
        _record(name, True)
    finally:
        _cleanup(profile)


def test_help_skill_returns_content() -> None:
    name = "test_help_skill_returns_content"
    profile = _fresh_profile()
    try:
        _layer_skills, skills = _reload_modules(profile)
        content = "---\npurpose: Help test.\ntools: help_skill\nsyntax: help_skill name\nargs: name\n---\n# Help\n\nFull body here."
        skills.write_skill("helpme", "shared", content)
        fetched = skills.help_skill("helpme", "shared")
        if fetched != content:
            _record(name, False, f"content mismatch: {fetched!r}")
            return
        _record(name, True)
    finally:
        _cleanup(profile)


def test_help_skill_searches_all_layers() -> None:
    name = "test_help_skill_searches_all_layers"
    profile = _fresh_profile()
    try:
        _layer_skills, skills = _reload_modules(profile)
        content = "---\npurpose: Searchable.\ntools: help_skill\nsyntax: help_skill searchme\nargs: name\n---\n# Search\n\nBody."
        skills.write_skill("searchme", "companion", content)
        fetched = skills.help_skill("searchme")
        if fetched != content:
            _record(name, False, f"expected to find skill across layers, got: {fetched!r}")
            return
        _record(name, True)
    finally:
        _cleanup(profile)


def test_delete_skill() -> None:
    name = "test_delete_skill"
    profile = _fresh_profile()
    try:
        _layer_skills, skills = _reload_modules(profile)
        skills.write_skill("gone", "shared", "---\npurpose: x\ntools: delete_skill\nsyntax: delete_skill gone\nargs: name, layer\n---\ntext")
        deleted = skills.delete_skill("gone", "shared")
        if "deleted from shared" not in deleted:
            _record(name, False, f"delete result unexpected: {deleted}")
            return
        listing = skills.list_skills("shared")
        if "gone" in listing.split():
            _record(name, False, f"skill still listed: {listing!r}")
            return
        _record(name, True)
    finally:
        _cleanup(profile)


def test_name_validation_rejects_traversal() -> None:
    name = "test_name_validation_rejects_traversal"
    profile = _fresh_profile()
    try:
        _layer_skills, skills = _reload_modules(profile)
        result = skills.write_skill("../evil", "shared", "x")
        if not isinstance(result, str):
            _record(name, False, "did not return a string")
            return
        if "Invalid skill name" not in result and "Invalid skill path" not in result:
            _record(name, False, f"did not reject traversal: {result!r}")
            return
        shared_dir = profile / "companion" / "skills" / "shared"
        if any(p.name != "." and p.name != ".." for p in shared_dir.iterdir()):
            _record(name, False, "file was created despite rejection")
            return
        evil = profile / "companion" / "skills" / "evil.md"
        if evil.exists():
            _record(name, False, "traversal wrote outside shared dir")
            return
        _record(name, True)
    finally:
        _cleanup(profile)


def test_delete_missing_is_graceful() -> None:
    name = "test_delete_missing_is_graceful"
    profile = _fresh_profile()
    try:
        _layer_skills, skills = _reload_modules(profile)
        result = skills.delete_skill("nonexistent", "shared")
        if not isinstance(result, str):
            _record(name, False, "did not return a string")
            return
        if "not found" not in result:
            _record(name, False, f"unexpected message: {result!r}")
            return
        _record(name, True)
    finally:
        _cleanup(profile)


def test_build_skill_prompt_section_is_compact() -> None:
    name = "test_build_skill_prompt_section_is_compact"
    profile = _fresh_profile()
    try:
        layer_skills, skills = _reload_modules(profile)
        body = (
            "---\n"
            "purpose: Compact registry test.\n"
            "tools: help_skill\n"
            "syntax: help_skill compact_skill\n"
            "args: name\n"
            "---\n"
            "# Compact\n\n"
            "THIS_IS_THE_BODY_TEXT_SHOULD_NOT_APPEAR\n"
            "More body lines here.\n"
        )
        skills.write_skill("compact_skill", "shared", body)
        section = layer_skills.build_skill_prompt_section("companion")
        if "Compact registry test." not in section:
            _record(name, False, f"section missing description: {section!r}")
            return
        if "THIS_IS_THE_BODY_TEXT_SHOULD_NOT_APPEAR" in section:
            _record(name, False, "section included skill body text (not compact)")
            return
        if "- **compact_skill** (shared):" not in section:
            _record(name, False, f"section missing bullet format: {section!r}")
            return
        _record(name, True)
    finally:
        _cleanup(profile)


def test_prepare_skill_context_keeps_compact_index() -> None:
    name = "test_prepare_skill_context_keeps_compact_index"
    profile = _fresh_profile()
    try:
        layer_skills, skills = _reload_modules(profile)
        body = (
            "---\n"
            "purpose: Compact context test.\n"
            "tools: help_skill\n"
            "syntax: help_skill context_skill\n"
            "args: name\n"
            "---\n"
            "# Compact Context\n\n"
            "THIS_BODY_SHOULD_NOT_BE_IN_THE_INDEX\n"
        )
        skills.write_skill("context_skill", "shared", body)
        context = layer_skills.prepare_skill_context("assistant", "read the skill", config={})
        index_section = str(context.get("index_section") or "")
        selected_section = str(context.get("selected_section") or "")
        if "Compact context test." not in index_section:
            _record(name, False, f"context index missing description: {index_section!r}")
            return
        if "THIS_BODY_SHOULD_NOT_BE_IN_THE_INDEX" in index_section:
            _record(name, False, f"context index leaked full skill body: {index_section!r}")
            return
        if selected_section:
            _record(name, False, f"selected section should stay empty by default: {selected_section!r}")
            return
        _record(name, True)
    finally:
        _cleanup(profile)


def test_clean_profile_seeds_shipped_skills() -> None:
    name = "test_clean_profile_seeds_shipped_skills"
    profile = _fresh_profile()
    try:
        layer_skills, _skills = _reload_modules(profile, project_root=ROOT)
        section = layer_skills.build_skill_prompt_section("companion")
        if "## Skills" not in section:
            _record(name, False, f"shipped skills were not available in a clean profile: {section!r}")
            return
        if "scheduler_workflows" not in section.lower():
            _record(name, False, f"companion shipped skills did not seed into the profile: {section!r}")
            return
        seeded = profile / "companion" / "skills" / "companion" / "scheduler_workflows.md"
        if not seeded.exists():
            _record(name, False, f"expected shipped skill to be copied into profile: {seeded}")
            return
        _record(name, True)
    finally:
        _cleanup(profile)


def test_write_skill_requires_frontmatter() -> None:
    name = "test_write_skill_requires_frontmatter"
    profile = _fresh_profile()
    try:
        _layer_skills, skills = _reload_modules(profile)
        result = skills.write_skill("bad_skill", "shared", "# Missing frontmatter")
        if "Skill frontmatter is required" not in result:
            _record(name, False, f"expected frontmatter validation error, got: {result!r}")
            return
        _record(name, True)
    finally:
        _cleanup(profile)


def main() -> int:
    tests = [
        test_write_and_list_skill,
        test_help_skill_returns_content,
        test_help_skill_searches_all_layers,
        test_delete_skill,
        test_name_validation_rejects_traversal,
        test_delete_missing_is_graceful,
        test_build_skill_prompt_section_is_compact,
        test_prepare_skill_context_keeps_compact_index,
        test_clean_profile_seeds_shipped_skills,
        test_write_skill_requires_frontmatter,
    ]
    for test in tests:
        try:
            test()
        except Exception as exc:
            _record(test.__name__, False, f"raised: {exc!r}")
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
