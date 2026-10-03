"""The one part of the live contract run that can be checked without GitHub.

`scripts/contract.py` is unmocked by design, so nothing here runs it. What is
worth asserting offline is that its declaration still covers every type the
tool claims to create: a seventh data type added to `CREATABLE` without a
field in the contract config would otherwise ship untested against GitHub,
and the gap would be invisible — the run would pass, having never tried it.
"""
from __future__ import annotations

import importlib.util
import pathlib
import tomllib

import pytest

from boardkit.config import CREATABLE, Config

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "contract.py"


@pytest.fixture(scope="module")
def contract():
    """The script as a module. It is not importable: `scripts/` is not a
    package, deliberately, so that nothing in `src/` can come to depend on it."""
    spec = importlib.util.spec_from_file_location("contract", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def declared(contract, tmp_path) -> Config:
    source = tmp_path / "boardkit.toml"
    source.write_text(contract.declaration("octo", "octo/widgets", "a board"),
                      encoding="utf-8")
    table = tomllib.loads(source.read_text(encoding="utf-8"))
    return Config(root=tmp_path, source=source, **table)


def test_the_declaration_is_valid_configuration(declared):
    assert declared.project_name == "a board"
    assert declared.status_order == ("Inbox", "Todo", "Done")


def test_every_creatable_data_type_has_a_field_in_the_contract_run(declared):
    """A type the tool can create and the live run never creates is a hole."""
    types = {field.data_type for field in declared.fields}
    types.add("SINGLE_SELECT")  # the status field, built from `status_order`
    assert set(CREATABLE) - types == set()


def test_every_writable_type_is_round_tripped(contract, declared):
    """Each field the run writes exists in the config it builds the board from."""
    names = {field.name for field in declared.fields} | {declared.status_field}
    assert set(contract.ROUND_TRIP) <= names
    assert contract.UNWRITABLE in names


def test_the_unwritable_field_is_left_out_of_the_round_trip(contract):
    """`sync.value_literal` refuses an iteration write, so asserting a
    successful round trip for it would assert the opposite of the truth."""
    assert contract.UNWRITABLE not in contract.ROUND_TRIP


def test_an_option_carrying_a_description_is_accepted(declared):
    """The contract config declares one both ways, because `admin apply` sends
    a description on every option and only one form exercises that."""
    area = declared.field("area")
    assert area is not None
    assert area.options == ("core", "docs")
    assert area.descriptions == {"core": "the library itself"}
