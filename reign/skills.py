"""Approved REIGN workflows and the bounded, side-effect-free MCP helpers."""

from dataclasses import asdict, dataclass


MAX_CHECKLIST_ITEMS = 20
MAX_CHECKLIST_LABEL = 140


@dataclass(frozen=True)
class Skill:
    id: str
    title: str
    description: str
    providers: tuple[str, ...]
    tool: str | None
    steps: tuple[str, ...]


SKILLS = {
    skill.id: skill for skill in (
        Skill("email_briefing", "Email briefing", "Review unread mail and prioritize replies.",
              ("outlook", "gmail"), None,
              ("Connect and verify each mailbox independently.",
               "Read a bounded sample of unread messages from each connected mailbox.",
               "Group items by urgency and identify messages that need a reply.",
               "Label sampled counts as minimums and identify any skipped mailbox.")),
        Skill("aws_sso", "AWS sign-in", "Guide a human through AWS IAM Identity Center sign-in.",
              ("aws",), None,
              ("Select an approved AWS Identity Center profile.",
               "Open the provider's sign-in page for the account holder.",
               "Verify the account and role before reporting access.")),
        Skill("action_checklist", "Action checklist", "Turn follow-ups into a personal checklist.",
              (), "reign_checklist_prepare",
              ("Draft action labels for review.", "Save the checklist to your workspace.",
               "Mark items complete and copy the current list.")),
    )
}


def catalog():
    return [{**asdict(skill), "availability": "ready" if skill.tool else "needs_setup"}
            for skill in SKILLS.values()]


def workflow(skill_id):
    skill = SKILLS[skill_id]
    return {"skill": skill.id, "title": skill.title,
            "status": "ready" if skill.tool else "needs_setup",
            "providers": list(skill.providers), "steps": list(skill.steps),
            "action": "open_skills" if skill.tool else "open_connections"}


def checklist(items):
    labels = [" ".join(item.split()) for item in items]
    if (not labels or len(labels) > MAX_CHECKLIST_ITEMS or any(
        not label or len(label) > MAX_CHECKLIST_LABEL or any(ord(ch) < 32 for ch in label)
        for label in labels
    )):
        raise ValueError("Invalid checklist labels")
    return [{"label": label, "done": False} for label in labels]
