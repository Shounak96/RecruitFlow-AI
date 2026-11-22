import hashlib
import json
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ProfessionalProfile, ProfileEducation, ProfileExperience, ProfileProject,
    ProfileSkillGroup,
)

TOKEN = re.compile(r"[a-z][a-z0-9+#.-]{1,}", re.I)
STOP = {
    "and", "the", "with", "for", "from", "that", "this", "role", "team",
    "work", "working", "using", "software", "engineer", "engineering", "developer",
    "development", "application", "applications", "responsible", "responsibilities",
}
RESPONSIBILITY_TERMS = {
    "architect", "automate", "build", "debug", "deploy", "design", "implement",
    "maintain", "monitor", "operate", "optimize", "scale", "secure", "test",
    "troubleshoot",
}
DOMAIN_TERMS = {
    "operational_reliability": {
        "availability", "fault", "fault-tolerant", "incident", "monitoring", "observability",
        "recovery", "reliability", "resilience", "resilient", "safeguard", "safeguards",
    },
    "delivery_tooling": {
        "automation", "ci", "cd", "ci/cd", "deploy", "deployment", "deployments",
        "pipeline", "pipelines", "release", "test", "testing", "tooling",
    },
    "retrieval_evaluation": {
        "embedding", "embeddings", "evaluation", "grounding", "llm", "rag", "retrieval",
        "vector", "vectors",
    },
    "backend_services": {
        "api", "apis", "backend", "database", "databases", "microservice", "microservices",
        "postgresql", "service", "services", "sql",
    },
    "platform_infrastructure": {
        "cloud", "container", "containers", "docker", "infrastructure", "kubernetes",
        "platform", "terraform",
    },
    "distributed_streaming": {
        "distributed", "event", "events", "kafka", "messaging", "redpanda", "streaming",
    },
}
TOKEN_ALIASES = {
    "architected": "architect", "architecting": "architect",
    "automated": "automate", "automating": "automate",
    "built": "build", "building": "build",
    "debugged": "debug", "debugging": "debug",
    "deployed": "deploy", "deploying": "deploy",
    "designed": "design", "designing": "design",
    "implemented": "implement", "implementing": "implement",
    "maintained": "maintain", "maintaining": "maintain",
    "monitored": "monitor", "operated": "operate", "operating": "operate",
    "optimized": "optimize", "optimizing": "optimize",
    "scaled": "scale", "scaling": "scale", "secured": "secure",
    "tested": "test", "testing": "test",
}


def load_profile_rows(db: Session, owner_id: UUID):
    profile = db.scalar(select(ProfessionalProfile).where(ProfessionalProfile.owner_id == owner_id))
    if profile is None:
        return None, [], [], [], []
    def rows(model):
        return list(db.scalars(select(model).where(model.owner_id == owner_id, model.profile_id == profile.id).order_by(model.position)))
    return profile, rows(ProfileEducation), rows(ProfileExperience), rows(ProfileSkillGroup), rows(ProfileProject)


def _tokens(text: str | None) -> set[str]:
    normalized: set[str] = set()
    for raw in TOKEN.findall(text or ""):
        token = raw.lower().strip(".-")
        normalized.add(token)
        normalized.update(part for part in token.split("-") if part)
    return {TOKEN_ALIASES.get(token, token) for token in normalized}


def _score_evidence(text: str, job_description: str | None) -> dict:
    job_tokens = _tokens(job_description) - STOP
    evidence_tokens = _tokens(text) - STOP
    matched_terms = sorted(job_tokens & evidence_tokens)
    matched_responsibilities = sorted(
        (job_tokens & evidence_tokens) & RESPONSIBILITY_TERMS
    )
    matched_domains: list[str] = []
    domain_score = 0
    for domain, vocabulary in DOMAIN_TERMS.items():
        job_hits = job_tokens & vocabulary
        evidence_hits = evidence_tokens & vocabulary
        if job_hits and evidence_hits:
            matched_domains.append(domain)
            domain_score += min(3, len(job_hits)) * min(3, len(evidence_hits))
    score = len(matched_terms) + (2 * len(matched_responsibilities)) + (2 * domain_score)
    return {
        "score": score,
        "matched_terms": matched_terms[:10],
        "matched_responsibilities": matched_responsibilities[:6],
        "matched_domains": matched_domains,
    }


def _select_relevant(experiences, projects, job_description: str | None, limit: int = 2):
    candidates = []
    for position, experience in enumerate(experiences):
        text = " ".join(
            [experience.role, experience.company, experience.work_summary, *experience.technologies]
        )
        candidates.append(("experience", experience, _score_evidence(text, job_description), position))
    for position, project in enumerate(projects):
        text = " ".join(
            [project.title, project.description, *project.technologies]
        )
        candidates.append(("project", project, _score_evidence(text, job_description), position))
    # Python's stable sort preserves profile order only when relevance scores tie.
    ranked = sorted(candidates, key=lambda item: item[2]["score"], reverse=True)
    # One generic shared word is not enough evidence to place an item in the AI context.
    return [item for item in ranked if item[2]["score"] >= 4][:limit]


def profile_snapshot(db: Session, owner_id: UUID, job_description: str | None = None) -> dict:
    profile, education, experiences, skills, projects = load_profile_rows(db, owner_id)
    if profile is None:
        return {}
    completed = [e for e in education if e.graduation_status == "completed"]
    in_progress = [e for e in education if e.graduation_status == "in_progress"]
    current = [e for e in experiences if e.is_current]
    previous = [e for e in experiences if not e.is_current]
    relevant = _select_relevant(experiences, projects, job_description)
    def exp(e, relevance=None):
        result = {"company": e.company, "role": e.role, "employment_type": e.employment_type,
                "is_current": e.is_current, "start_date": str(e.start_date) if e.start_date else None,
                "end_date": str(e.end_date) if e.end_date else None, "work_summary": e.work_summary,
                "technologies": e.technologies}
        if relevance is not None:
            result["matched_evidence"] = {
                key: value for key, value in relevance.items() if key != "score"
            }
        return result
    def project(p, relevance=None):
        result = {"title": p.title, "technologies": p.technologies,
                  "description": p.description[:5000]}
        if relevance is not None:
            result["matched_evidence"] = {
                key: value for key, value in relevance.items() if key != "score"
            }
        return result
    return {
        "profile_version": profile.version,
        "full_name": profile.full_name,
        "headline": profile.headline,
        "location": profile.location,
        "summary": profile.summary,
        "explicit_experience_duration": profile.experience_duration,
        "completed_education": [
            {"degree": e.degree, "field": e.field, "institution": e.institution,
             "start_date": str(e.start_date) if e.start_date else None,
             "end_date": str(e.end_date) if e.end_date else None}
            for e in completed
        ],
        "in_progress_education": [
            {"degree": e.degree, "field": e.field, "institution": e.institution,
             "start_date": str(e.start_date) if e.start_date else None,
             "end_date": str(e.end_date) if e.end_date else None}
            for e in in_progress
        ],
        "current_experience": [exp(e) for e in current[:2]],
        "previous_experience": [exp(e) for e in previous[:2]],
        "jd_relevant_experiences": [
            exp(item, relevance) for kind, item, relevance, _ in relevant if kind == "experience"
        ],
        "jd_relevant_projects": [
            project(item, relevance) for kind, item, relevance, _ in relevant if kind == "project"
        ],
        "skill_groups": [{"category": s.category, "skills": s.skills} for s in skills],
        "evidence_policy": "All facts are explicit user-entered profile data. Omit absent fields; never infer status or duration from dates.",
    }


def snapshot_fingerprint(snapshot: dict) -> str:
    raw = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def selected_links(profile: ProfessionalProfile, *, github: bool, linkedin: bool, portfolio: bool) -> dict[str, str]:
    links: dict[str, str] = {}
    if github and profile.github_url: links["GitHub"] = profile.github_url
    if linkedin and profile.linkedin_url: links["LinkedIn"] = profile.linkedin_url
    if portfolio and profile.portfolio_url: links["Portfolio"] = profile.portfolio_url
    return links


def append_signature_links(body: str, links: dict[str, str]) -> str:
    if not links:
        return body.strip()
    return body.rstrip() + "\n" + "\n".join(f"{label}: {url}" for label, url in links.items())


def remove_signature_links(body: str, links: dict[str, str]) -> str:
    suffix = "\n" + "\n".join(f"{label}: {url}" for label, url in links.items())
    return body[: -len(suffix)].rstrip() if links and body.endswith(suffix) else body
