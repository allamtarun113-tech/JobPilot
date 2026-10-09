"""Tailoring may only manipulate existing elements and every new number needs cited evidence."""

from jobpilot.models.resume_ops import HideBullet, MoveEntry, RewriteBullet
from jobpilot.resume import diff, ops
from jobpilot.resume.tex_import import import_tex
from tests.conftest import SAMPLE_RESUME


def test_checked_ops_apply_and_describe_changes() -> None:
    """Backed rewrite, hide and move apply and are described."""
    base = import_tex(SAMPLE_RESUME.read_text())
    section = next(s for s in base.sections if len(s.entries) >= 2 and s.entries[0].bullets)
    first, second = section.entries[:2]
    bullet = first.bullets[0]
    plan = [
        RewriteBullet(
            op="rewrite_bullet",
            bullet_id=bullet.id,
            text="Improved performance by 40% using Python",
            evidence=[101],
        ),
        HideBullet(op="hide_bullet", bullet_id=first.bullets[-1].id),
        MoveEntry(op="move_entry", entry_id=second.id, before_entry_id=first.id),
    ]
    tailored, applied, rejected = ops.apply(
        base, plan, {101: "Improved performance by forty percent using Python"}
    )
    assert rejected == [] and len(applied) == 3
    assert tailored.sections[base.sections.index(section)].entries[0].id == second.id
    changes = diff.describe(base, tailored)
    assert any("reworded" in change for change in changes)
    assert any("reordered" in change for change in changes)


def test_uncited_or_new_numbers_are_rejected() -> None:
    """A new number or an unknown citation rejects the rewrite."""
    base = import_tex(SAMPLE_RESUME.read_text())
    bullet = next(b for s in base.sections for e in s.entries for b in e.bullets)
    tailored, applied, rejected = ops.apply(
        base,
        [
            RewriteBullet(
                op="rewrite_bullet", bullet_id=bullet.id, text="Raised accuracy by 50%", evidence=[1]
            ),
            RewriteBullet(
                op="rewrite_bullet", bullet_id=bullet.id, text="Raised accuracy by 40%", evidence=[999]
            ),
        ],
        {1: "Raised accuracy by 40 percent"},
    )
    assert applied == [] and len(rejected) == 2
    assert (
        next(b for s in tailored.sections for e in s.entries for b in e.bullets if b.id == bullet.id).text
        == bullet.text
    )
