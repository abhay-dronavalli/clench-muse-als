import json
import re
from pathlib import Path
from typing import get_args

import pytest

from core.contracts import Message, parse_message

DOC = Path(__file__).resolve().parent.parent / "docs" / "contracts.md"
JSON_BLOCKS = re.findall(r"```json\n(.*?)\n```", DOC.read_text(encoding="utf-8"), flags=re.DOTALL)
ALL_TYPES = {cls.model_fields["type"].default for cls in get_args(get_args(Message)[0])}


@pytest.mark.parametrize("block", JSON_BLOCKS)
def test_doc_example_parses(block):
    data = json.loads(block)
    assert parse_message(data).model_dump(mode="json") == data


def test_doc_has_an_example_for_every_type():
    assert {json.loads(b)["type"] for b in JSON_BLOCKS} == ALL_TYPES
