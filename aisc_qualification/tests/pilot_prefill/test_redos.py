"""No line of an uploaded file can make the readers backtrack for minutes.

Vendored from the pilot (`aisc-qualification`, `services/prefill/tests/test_redos.py`,
pilot branch), keeping the parts that cover the readers vendored here: the heading
patterns and the four text readers. The parts that drive the pilot's HTTP service and
its form-file reader are not vendored — this app serves prefill from its own router
(see `aisc_backend/routers/qualification.py`), whose upload limits are covered there.

A long run of whitespace used to cost time in the square of its length: "\\s*#*\\s*" is two
adjacent \\s* when there is no '#', and a "\\s*" before a separator is retried at every start
position of a search. One 100,000-space line took about 18 minutes on the event loop, which
stopped the whole service for every project.

Each check runs in a child process that is killed at its budget: re holds the GIL, so a thread
could not be timed out.
"""
from __future__ import annotations

import multiprocessing
import time

import pytest

N = 100_000
SPACES = " " * N

_FORK = multiprocessing.get_context("fork")


def _child(queue, fn, args):
    started = time.perf_counter()
    fn(*args)
    queue.put(time.perf_counter() - started)


def _elapsed(fn, *args, kill_after: float = 5.0) -> float:
    """How long fn(*args) takes, or kill_after when it has not finished by then."""
    queue = _FORK.Queue()
    child = _FORK.Process(target=_child, args=(queue, fn, args))
    child.start()
    child.join(kill_after)
    if child.is_alive():
        child.kill()
        child.join()
        return kill_after
    assert child.exitcode == 0, f"the check raised (exit code {child.exitcode})"
    return queue.get()


# Lines a document can hold: a prefix that gets a pattern started, a long whitespace run, an end
# that makes it fail.
PREFIXES = ("", "a", "#", "## ", "risks", "risk 1", "components", "component 1", "annex iv", "1", "1 a", "a [c")
LINES = [p + ws + end for p in PREFIXES for ws in (SPACES, "\t" * N) for end in ("x", "x [c")]


def _match_all(pattern):
    for line in LINES:
        pattern.match(line)


def _patterns():
    from aisc_qualification.prefill import components, fields, headings, risks

    return {
        "RISKS_HEADING": risks.RISKS_HEADING,
        "risks._ROW_HEADING": risks._ROW_HEADING,
        "COMPONENTS_HEADING": headings.COMPONENTS_HEADING,
        "components._ROW_HEADING": components._ROW_HEADING,
        "components._ANNEX_LINE": components._ANNEX_LINE,
        "fields._ANNEX_HEADING": fields._ANNEX_HEADING,
    }


@pytest.mark.parametrize("name", sorted(_patterns()))
def test_each_heading_pattern_is_linear_on_a_long_line(name):
    pattern = _patterns()[name]
    # 48 lines of 100,000 characters each: a linear pattern needs a few milliseconds per line.
    assert _elapsed(_match_all, pattern) < 0.1 * len(LINES) / 4


# Documents that put such a line where each reader looks.
DOCUMENTS = {
    "a whitespace line": "a\n" + SPACES + "x\nb",
    "a whitespace line between words": "a" + SPACES + "x\nb",
    "a tab line": "a\n" + "\t" * N + "x\nb",
    "in the risk section": "Risks\n" + SPACES + "x\n",
    "a risk row heading": "Risks\nrisk 1" + SPACES + "x\n",
    "risk areas": "Risks\nRisk: biased scoring\nAreas: Right" + SPACES + "x\n",
    "in the components section": "Components\n" + SPACES + "x\n",
    "a component row heading": "Components\ncomponent 1" + SPACES + "x\n",
    "a pick list": "Domain: Finance" + SPACES + "x\n",
    "an Annex heading": "1 a" + SPACES + "x\n",
}

QUESTIONS = [{"field": "q1", "text": "What does the system do?", "citation": "Art. 11", "annexPoint": "1a"}]


def _read_everything(text):
    from aisc_qualification.prefill import components, fields, picks, risks

    fields.proposals_from_text(text)
    fields.metadata_from_text(text)
    fields.proposals_for_questions(text, QUESTIONS)
    risks.risks_from_text(text)
    components.components_from_text(text)
    picks.picks_from_text(text)


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_every_reader_reads_a_long_line_quickly(name):
    assert _elapsed(_read_everything, DOCUMENTS[name]) < 2.0
