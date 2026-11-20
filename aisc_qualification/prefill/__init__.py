"""Pre-filling a qualification from a document somebody already wrote.

The form asks for the same things a technical documentation file already
contains. Rather than retyping them, upload the file: this reads what it can,
proposes values, and the person amends them before saving. Nothing here decides
anything on its own, and nothing is stored: the proposals go back to the form.

VENDORED, not written here. Every module in this package is the pilot's own
`services/prefill/prefill` (repo `lux-ai-factory/aisc-qualification`, branch
`pilot`, commit 4311cab33dc2871427d222023cd6fdd139339974), taken unchanged apart
from the import path (`prefill.X` -> `aisc_qualification.prefill.X`). The two data
files are the pilot's too: `prefill_fields.json` and `vair_vocab.json`
(`src/data/`, same commit), and `annexPoints.json` for the 14 Annex IV point ids.
That is the point of the port — the reading rules are the pilot's, not a
re-implementation of them.

The pilot's own tests for these modules travel with them, in
`aisc_qualification/tests/pilot_prefill/`, and run against this copy:

    uv run --no-project --with pytest --with pypdf --with python-docx \
        python -m pytest aisc_qualification/tests/pilot_prefill -q

The app's own side of it (this form's fields, blocks and rows) is
`aisc_qualification/services/qualification_prefill.py`; the endpoint is
`POST /projects/{pid}/qualification/prefill`.
"""
