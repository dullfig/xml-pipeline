"""xmlable — vendored and extended for AgentServer v2.1"""

from lxml import etree, objectify
from lxml.etree import _Element
from lxml.objectify import ObjectifiedElement
from typing import Type, TypeVar, Any
from io import BytesIO

from ._xmlify import xmlify

T = TypeVar("T")

def _get_xobject(cls: Type[T]) -> Any:
    if not hasattr(cls, "get_xobject"):
        raise ValueError(f"Class {cls.__name__} is not decorated with @xmlify")
    return cls.get_xobject()

def parse_element(cls: Type[T], element: _Element | ObjectifiedElement) -> T:
    """Direct in-memory deserialization from validated lxml Element."""
    xobject = _get_xobject(cls)
    obj_element = objectify.fromstring(etree.tostring(element))
    return xobject.xml_in(obj_element, ctx=None)

def parse_bytes(cls: Type[T], xml_bytes: bytes) -> T:
    tree = objectify.parse(BytesIO(xml_bytes))
    root = tree.getroot()
    return parse_element(cls, root)

def parse_string(cls: Type[T], xml_str: str) -> T:
    return parse_bytes(cls, xml_str.encode("utf-8"))

__all__ = ["xmlify", "parse_element", "parse_bytes", "parse_string"]
