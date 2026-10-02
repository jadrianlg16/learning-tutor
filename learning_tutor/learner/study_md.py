"""Read study material out of markdown — no model involved.

Four readers, each deliberately literal so that what they extract can be predicted from the
file alone (CONTRACTS.md, *Study tools*):

* :func:`parse_questions` — numbered multiple-choice questions, ``N. stem … [tag]`` with the
  options ``A) …`` on the lines that follow. A block without at least two options is not a
  question (numbered lists in worked examples look the same until the options are missing).
* :func:`parse_key` — answer-key rows ``| N | tag | LETTER | explanation |``.
* :func:`parse_cards` — flashcards from paragraphs that open with a bold term,
  ``**Term.** definition …``; the lines that follow (bullets included) belong to the card
  until the next bold term or heading.
* :func:`parse_tables` — GFM pipe tables, titled by the nearest heading.

Plus :func:`parse_node_titles`, which reads ``## <tag> <title>`` headings so a question
tagged ``[1.2]`` can be filed under the concept its source calls ``1.2``.

Document text is data, never instructions (CONTRACTS.md hard rule 4): nothing here
interprets what the text says, it only cuts it up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: ``N. stem`` or ``**N.** stem`` — the number may be bolded
_QUESTION = re.compile(r"^(?:\*\*)?(\d{1,3})\.(?:\*\*)?\s+(\S.*)$")
_OPTION = re.compile(r"^\s*([A-Ea-e])\)\s+(\S.*)$")
_TAG = re.compile(r"\[(\d+(?:\.\d+)*)\]")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TAGGED_TITLE = re.compile(r"^(\d+(?:\.\d+)+)\s+(.+?)(?:\s*\(\d+[^)]*\))?\s*$")
_BOLD_LEAD = re.compile(r"^\*\*(.+?)\*\*\s*[.:—–-]?\s*(.*)$")
_RULE = re.compile(r"^([-*_])(\s*\1){2,}$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")

#: section headings that hold practice questions / definitions, in Spanish and English
_QUESTION_SECTIONS = re.compile(
    r"reactivos|preguntas|questions|quiz|pr[aá]ctica|practice|ejercicios|exercises", re.I
)
_CONCEPT_SECTIONS = re.compile(
    r"concept|definici|glosario|glossary|key terms|t[eé]rminos|vocabul", re.I
)
_CRITERION_HEADERS = {"criterio", "criterion", "criteria", "aspecto", "aspect", "característica",
                      "caracteristica", "feature", "dimensión", "dimension", ""}


@dataclass
class ParsedQuestion:
    number: int
    stem: str
    options: list[tuple[str, str]]
    tag: str | None
    section: str | None


@dataclass
class KeyRow:
    number: int
    tag: str | None
    letter: str
    explanation: str | None


@dataclass
class ParsedCard:
    front: str
    back: str
    tag: str | None
    section: str | None


@dataclass
class ParsedTable:
    title: str
    columns: list[str]
    rows: list[list[str]]
    tag: str | None
    section: str | None


@dataclass
class _Line:
    text: str
    headings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers
def _lines(markdown: str) -> list[_Line]:
    """Every line with the heading stack above it (outside fenced code blocks)."""

    out: list[_Line] = []
    stack: list[tuple[int, str]] = []
    fenced = False
    for raw in (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.lstrip().startswith("```"):
            fenced = not fenced
        match = None if fenced else _HEADING.match(raw)
        if match:
            level = len(match.group(1))
            stack = [(lvl, text) for lvl, text in stack if lvl < level]
            stack.append((level, match.group(2).strip()))
        out.append(_Line(raw, [text for _, text in stack]))
    return out


def _tag_of(headings: list[str]) -> str | None:
    for heading in reversed(headings):
        match = _TAGGED_TITLE.match(heading)
        if match:
            return match.group(1)
    return None


def _section_of(headings: list[str]) -> str | None:
    """The nearest tagged heading ("1.1 Tipos de requerimientos"), else the nearest one."""

    for heading in reversed(headings):
        match = _TAGGED_TITLE.match(heading)
        if match:
            return f"{match.group(1)} {match.group(2)}"
    return headings[-1] if headings else None


def _in_sections(lines: list[_Line], pattern: re.Pattern[str]) -> list[_Line]:
    """The lines under a heading matching ``pattern`` — or all lines when none matches."""

    if not any(pattern.search(h) for line in lines for h in line.headings):
        return lines
    return [line for line in lines if any(pattern.search(h) for h in line.headings)]


def _clean_cell(text: str) -> str:
    text = text.replace("\\|", "|").strip()
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text).strip()


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [_clean_cell(cell) for cell in re.split(r"(?<!\\)\|", body)]


def _dedent(block: list[str]) -> str:
    """Trim the block and each line; blank lines between paragraphs are kept."""

    stripped = [line.strip() for line in block]
    while stripped and not stripped[-1]:
        stripped.pop()
    while stripped and not stripped[0]:
        stripped.pop(0)
    return "\n".join(stripped)


# --------------------------------------------------------------------------- questions
def parse_questions(markdown: str) -> tuple[list[ParsedQuestion], list[str]]:
    """Numbered multiple-choice questions. Returns ``(questions, problems)``."""

    lines = _in_sections(_lines(markdown), _QUESTION_SECTIONS)
    blocks: list[tuple[int, list[_Line]]] = []
    has_options = False
    for line in lines:
        match = _QUESTION.match(line.text)
        # A numbered line opens a new question only once the open one has its options:
        # an ordering question's steps ("1. … 4.") are part of its stem, indented or not.
        if match and (not blocks or blocks[-1][0] < 0 or has_options):
            blocks.append((int(match.group(1)), [line]))
            has_options = False
        elif blocks:
            if _HEADING.match(line.text):
                blocks.append((-1, []))  # a heading closes the open question
                has_options = False
            else:
                blocks[-1][1].append(line)
                if _OPTION.match(line.text):
                    has_options = sum(1 for ln in blocks[-1][1] if _OPTION.match(ln.text)) >= 2

    questions: list[ParsedQuestion] = []
    problems: list[str] = []
    for number, block in blocks:
        if number < 0 or not block:
            continue
        first = _QUESTION.match(block[0].text)
        stem_lines = [first.group(2) if first else block[0].text]
        options: list[tuple[str, str]] = []
        wrapping = False  # the line above was an option (or its continuation)
        for line in block[1:]:
            opt = _OPTION.match(line.text)
            if opt:
                options.append((opt.group(1).upper(), opt.group(2).strip()))
                wrapping = True
            elif options:
                # a wrapped option line continues the option directly above it; a blank
                # line or a rule (`---`) ends the options, and what follows is not part of them
                text = line.text.strip()
                if not text or _RULE.match(text):
                    wrapping = False
                elif wrapping:
                    letter, previous = options[-1]
                    options[-1] = (letter, f"{previous} {text}")
            else:
                stem_lines.append(line.text)
        stem = "\n".join(part.strip() for part in stem_lines).strip()
        tags = _TAG.findall(stem)
        tag = tags[-1] if tags else _tag_of(block[0].headings)
        stem = _TAG.sub("", stem).strip()
        stem = re.sub(r"[ \t]+\n", "\n", stem)
        if len(options) < 2:
            if tags:
                problems.append(f"question {number} has a tag but fewer than two options")
            continue
        letters = [letter for letter, _ in options]
        if letters != [chr(ord("A") + i) for i in range(len(letters))]:
            problems.append(f"question {number}: options are {''.join(letters)}, expected A, B, C…")
        questions.append(
            ParsedQuestion(
                number=number,
                stem=stem,
                options=options,
                tag=tag,
                section=_section_of(block[0].headings),
            )
        )
    return questions, problems


def parse_key(markdown: str) -> list[KeyRow]:
    """Answer-key table rows: a number, a single letter, an optional tag and explanation."""

    rows: list[KeyRow] = []
    for line in _lines(markdown):
        text = line.text.strip()
        if not text.startswith("|") or _TABLE_SEP.match(text):
            continue
        cells = _split_row(text)
        if not cells or not cells[0].isdigit():
            continue
        letter = next((c.upper() for c in cells[1:] if re.fullmatch(r"[A-Ea-e]", c)), None)
        if not letter:
            continue
        tag = next((c for c in cells[1:] if re.fullmatch(r"\d+(?:\.\d+)+", c)), None)
        rest = [c for c in cells[1:] if c and c.upper() != letter and c != tag]
        rows.append(
            KeyRow(
                number=int(cells[0]),
                tag=tag,
                letter=letter,
                explanation=rest[-1] if rest else None,
            )
        )
    return rows


def match_key(question: ParsedQuestion, key: list[KeyRow]) -> KeyRow | None:
    """The key row for a question: same number, and the same tag when both carry one.

    Matching on the tag too is what lets a combined key (four areas, numbering restarting
    at 1 in each) answer the right area's question 1.
    """

    same_number = [row for row in key if row.number == question.number]
    if question.tag:
        tagged = [row for row in same_number if row.tag == question.tag]
        if tagged:
            return tagged[0]
        if any(row.tag for row in same_number):
            return None
    return same_number[0] if len(same_number) == 1 else None


# --------------------------------------------------------------------------- nodes
def parse_node_titles(markdown: str) -> dict[str, str]:
    """``{tag: "tag title"}`` from headings like ``## 1.1 Tipos de requerimientos (12 …)``."""

    titles: dict[str, str] = {}
    for line in _lines(markdown):
        match = _HEADING.match(line.text)
        if not match:
            continue
        tagged = _TAGGED_TITLE.match(match.group(2).strip())
        if tagged and tagged.group(1) not in titles:
            titles[tagged.group(1)] = f"{tagged.group(1)} {tagged.group(2).strip()}"
    return titles


# --------------------------------------------------------------------------- cards
def parse_cards(markdown: str) -> list[ParsedCard]:
    """Flashcards from bold-term paragraphs (``**Term.** definition``)."""

    lines = _in_sections(_lines(markdown), _CONCEPT_SECTIONS)
    cards: list[ParsedCard] = []
    current: tuple[str, list[str], list[str]] | None = None  # (front, body, headings)

    def close() -> None:
        if current is None:
            return
        front, body, headings = current
        back = _dedent(body)
        if front and back:
            cards.append(
                ParsedCard(
                    front=front,
                    back=back,
                    tag=_tag_of(headings),
                    section=_section_of(headings),
                )
            )

    previous_heading: list[str] | None = None
    for line in lines:
        if previous_heading is not None and line.headings != previous_heading:
            close()
            current = None
        previous_heading = line.headings
        if _HEADING.match(line.text):
            close()
            current = None
            continue
        lead = _BOLD_LEAD.match(line.text)
        if lead and not re.fullmatch(r"[\d.]+", lead.group(1).strip()):
            close()
            front = lead.group(1).strip().rstrip(".:").strip()
            current = (front, [lead.group(2)], line.headings)
        elif current is not None:
            current[1].append(line.text)
    close()
    return cards


# --------------------------------------------------------------------------- tables
def _is_key_table(columns: list[str], rows: list[list[str]]) -> bool:
    numbered = sum(1 for row in rows if row and row[0].isdigit())
    lettered = sum(1 for row in rows if any(re.fullmatch(r"[A-Ea-e]", c) for c in row[1:]))
    return bool(rows) and numbered == len(rows) and lettered == len(rows)


def _table_title(section: str | None, columns: list[str]) -> str:
    base = section or "Table"
    if len(columns) >= 3 and columns[0].strip().lower() in _CRITERION_HEADERS:
        return f"{base}: {' vs '.join(columns[1:])}"
    return f"{base}: {' · '.join(c for c in columns if c)}"


def parse_tables(markdown: str) -> list[ParsedTable]:
    """GFM pipe tables with a header row. Answer-key tables are skipped."""

    lines = _lines(markdown)
    tables: list[ParsedTable] = []
    i = 0
    while i < len(lines) - 1:
        head, sep = lines[i].text, lines[i + 1].text
        if head.strip().startswith("|") and _TABLE_SEP.match(sep):
            columns = _split_row(head)
            rows: list[list[str]] = []
            j = i + 2
            while j < len(lines) and lines[j].text.strip().startswith("|"):
                cells = _split_row(lines[j].text)
                cells = (cells + [""] * len(columns))[: len(columns)]
                if any(cells):
                    rows.append(cells)
                j += 1
            if len(columns) >= 2 and rows and not _is_key_table(columns, rows):
                section = _section_of(lines[i].headings)
                tables.append(
                    ParsedTable(
                        title=_table_title(section, columns),
                        columns=columns,
                        rows=rows,
                        tag=_tag_of(lines[i].headings),
                        section=section,
                    )
                )
            i = j
            continue
        i += 1
    return tables
