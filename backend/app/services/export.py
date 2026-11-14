"""
Shared novel-export rendering logic (Phase 3).

Both the Markdown and PDF export endpoints (``app.api.novels``) build on the
same content structure here — title, chapters in order with headings, and a
character/creature lore appendix — so the two formats never drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2

from app.db.models import Novel

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
)


def has_exportable_content(novel: Novel) -> bool:
    """Return True if the novel has at least one chapter with generated prose."""
    return any((chapter.content or "").strip() for chapter in novel.chapters)


def export_filename_stem(novel: Novel) -> str:
    """Slugify the novel title for use as a download filename."""
    slug = re.sub(r"[^a-z0-9]+", "-", novel.title.lower()).strip("-")
    return slug or f"novel-{novel.id}"


def render_markdown(novel: Novel) -> str:
    """Serialize a novel (title, ordered chapters, lore appendix) to Markdown."""
    lines: list[str] = [f"# {novel.title}", ""]

    subtitle_bits = [bit for bit in (novel.genre, novel.tone) if bit]
    if subtitle_bits:
        lines.append(f"*{' — '.join(subtitle_bits)}*")
        lines.append("")
    if novel.synopsis:
        lines.append(novel.synopsis)
        lines.append("")

    chapters = sorted(novel.chapters, key=lambda chapter: chapter.chapter_number)
    for chapter in chapters:
        heading = f"## Chapter {chapter.chapter_number}"
        if chapter.title:
            heading += f": {chapter.title}"
        lines.append(heading)
        lines.append("")
        lines.append((chapter.content or "").strip())
        lines.append("")

    if novel.characters:
        lines.append("## Characters")
        lines.append("")
        for character in novel.characters:
            header = f"### {character.name}"
            if character.role:
                header += f" — {character.role}"
            lines.append(header)
            if character.backstory:
                lines.append("")
                lines.append(character.backstory.strip())
            lines.append("")

    if novel.creatures:
        lines.append("## Creatures")
        lines.append("")
        for creature in novel.creatures:
            header = f"### {creature.name}"
            if creature.species_type:
                header += f" — {creature.species_type}"
            lines.append(header)
            if creature.lore_description:
                lines.append("")
                lines.append(creature.lore_description.strip())
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_html(novel: Novel) -> str:
    """Render the novel into the HTML template used as PDF input."""
    template = _jinja_env.get_template("novel_export.html")
    chapters = sorted(novel.chapters, key=lambda chapter: chapter.chapter_number)
    return template.render(
        novel=novel,
        chapters=chapters,
        characters=novel.characters,
        creatures=novel.creatures,
    )


def render_pdf(novel: Novel) -> bytes:
    """Render the novel to a PDF document (HTML -> WeasyPrint).

    WeasyPrint is imported lazily so that importing this module (and
    therefore the FastAPI app) never requires its native Pango/Cairo
    libraries to be installed — only the PDF export path does.
    """
    from weasyprint import HTML

    html = render_html(novel)
    return HTML(string=html).write_pdf()
