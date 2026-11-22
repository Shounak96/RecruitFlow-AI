import importlib.util
from pathlib import Path


def migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20261121_0016_simplify_profile_projects.py"
    )
    spec = importlib.util.spec_from_file_location("project_migration_0016", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_project_details_merge_without_overwriting_or_duplication() -> None:
    merge = migration_module().merge_legacy_project_description
    result = merge(
        {
            "description": "Existing user description with Developer details.",
            "what_built": "Built a resilient event pipeline.",
            "role": "Developer",
            "duration": "Six months",
            "start_date": "2025-01-01",
            "end_date": "2025-06-30",
            "responsibilities": "Designed recovery safeguards.",
            "outcomes": "Improved operational readiness.",
            "url": "https://example.test/project",
        }
    )

    assert result.startswith("Existing user description")
    assert "What was built: Built a resilient event pipeline." in result
    assert "Role: Developer" not in result  # Already present in the user's description.
    assert "Duration: Six months" in result
    assert "Dates: 2025-01-01 to 2025-06-30" in result
    assert "Responsibilities: Designed recovery safeguards." in result
    assert "Outcomes: Improved operational readiness." in result
    assert "Repository/demo: https://example.test/project" in result


def test_legacy_what_built_becomes_description_and_repeated_text_is_not_duplicated() -> None:
    merge = migration_module().merge_legacy_project_description
    result = merge(
        {
            "description": None,
            "what_built": "Built an API. Designed its schema.",
            "responsibilities": "Designed its schema.",
        }
    )
    assert result == "What was built: Built an API. Designed its schema."


def test_downgrade_preserves_consolidated_description_source() -> None:
    source = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20261121_0016_simplify_profile_projects.py"
    ).read_text(encoding="utf-8")
    assert "SET what_built = description" in source
    assert "COALESCE(what_built, description" not in source
