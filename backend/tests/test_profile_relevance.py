from types import SimpleNamespace

from app.profile_service import _select_relevant


def experience(role, summary, technologies=()):
    return SimpleNamespace(
        role=role, company="Synthetic Company", work_summary=summary,
        technologies=list(technologies),
    )


def project(title, built, responsibilities, technologies=()):
    return SimpleNamespace(
        title=title, description=f"{built} {responsibilities}",
        technologies=list(technologies),
    )


def evidence_set():
    return (
        [experience("Backend Engineer", "Built Python REST APIs and PostgreSQL microservices.", ("Python", "PostgreSQL"))],
        [
            project(
                "Reliability Sandbox", "Built a distributed fault-injection system.",
                "Implemented incident detection, recovery safeguards, and observability.",
                ("FastAPI", "Redpanda"),
            ),
            project(
                "Retrieval Evaluation", "Built retrieval-augmented generation evaluation tooling.",
                "Tested embeddings, grounding, and vector retrieval quality.",
                ("LLM", "Python"),
            ),
            project(
                "Delivery Toolkit", "Built CI/CD developer tooling.",
                "Automated testing, deployment pipelines, and releases.",
                ("Docker",),
            ),
        ],
    )


def selected_names(jd):
    experiences, projects = evidence_set()
    return [
        item.title if kind == "project" else item.role
        for kind, item, _, _ in _select_relevant(experiences, projects, jd)
    ]


def test_platform_jd_favors_distributed_reliability_evidence() -> None:
    selected = _select_relevant(
        *evidence_set(),
        "Design distributed systems with fault tolerance, incident recovery, observability, and platform reliability.",
    )
    assert selected[0][1].title == "Reliability Sandbox"
    assert "operational_reliability" in selected[0][2]["matched_domains"]
    assert {"fault", "incident", "observability", "recovery"} <= set(selected[0][2]["matched_terms"])


def test_rag_jd_favors_retrieval_evaluation_evidence() -> None:
    assert selected_names(
        "Build RAG retrieval evaluation for embeddings, vector search, and grounding."
    )[0] == "Retrieval Evaluation"


def test_delivery_jd_favors_actual_pipeline_and_tooling_evidence() -> None:
    assert selected_names(
        "Build CI/CD developer tooling and automate testing, deployment pipelines, and releases."
    )[0] == "Delivery Toolkit"


def test_backend_jd_can_favor_work_experience_over_projects() -> None:
    selected = _select_relevant(
        *evidence_set(), "Develop backend APIs, microservices, SQL databases, and PostgreSQL services."
    )
    assert selected[0][0] == "experience"
    assert selected[0][1].role == "Backend Engineer"


def test_sparse_jd_does_not_force_project_and_order_only_breaks_ties() -> None:
    assert selected_names("General software engineering opportunity.") == []
    tied = [
        project("First", "Built Kafka event streaming services.", "Designed messaging safeguards."),
        project("Second", "Built Kafka event streaming services.", "Designed messaging safeguards."),
    ]
    selected = _select_relevant([], tied, "Build Kafka event streaming services.")
    assert [item.title for _, item, _, _ in selected] == ["First", "Second"]
    assert len(selected) == 2
