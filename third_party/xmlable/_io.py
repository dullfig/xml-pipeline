"""
Easy file IO for users
- Need to make it obvious when an xml has been overwritten
- Easy parsing from a file
"""

from pathlib import Path
from typing import Any, TypeVar
from termcolor import colored
from lxml.objectify import parse as objectify_parse
from lxml.etree import _ElementTree

from ._utils import typename
from ._xobject import is_xmlified
from ._errors import ErrorTypes


def write_file(file_path: str | Path, tree: _ElementTree):
    print(
        colored(f"Overwriting {file_path}", "red", attrs=["blink"]), end="..."
    )
    with open(file=file_path, mode="wb") as f:
        tree.write(f, xml_declaration=True, encoding="utf-8", pretty_print=True)
    print(colored(f"Complete!", "green", attrs=["blink"]))


def parse_file(cls: type, file_path: str | Path) -> Any:
    """
    Parse a file, validate and produce instance of cls
    INV: cls must be an xmlified class
    """
    if not is_xmlified(cls):
        raise ErrorTypes.NotXmlified(cls)
    with open(file=file_path, mode="r") as f:
        return cls.parse(objectify_parse(f).getroot())  # type: ignore[attr-defined]


def write_xsd(
    file_path: str | Path,
    cls: type,
    namespaces: dict[str, str] = {},
    imports: dict[str, str] = {},
):
    if not is_xmlified(cls):
        raise ErrorTypes.NonXMlifiedType(typename(cls))
    else:
        write_file(file_path, cls.xsd(namespaces=namespaces, imports=imports))  # type: ignore[attr-defined]


def write_xml_template(
    file_path: str | Path, cls: type, schema_name: str | None = None
):
    if not is_xmlified(cls):
        raise ErrorTypes.NonXMlifiedType(typename(cls))
    else:
        schema_id: str = (
            schema_name if schema_name is not None else typename(cls)
        )
        write_file(file_path, cls.xml(schema_id))  # type: ignore[attr-defined]


def write_xml_value(file_path: str | Path, val: Any):
    cls = type(val)
    if not is_xmlified(cls):
        raise ErrorTypes.NonXMlifiedType(typename(cls))
    else:
        write_file(file_path, val.xml_value())  # type: ignore[attr-defined]
