"""XMLable
A decorator to allow creation of xml config based on python dataclasses

Given a dataclass:
- Produce an xsd schema based on the class
- Produce an xml template based on the class
- Given any instance of the class, make a best-effort attempt at turning it into
  a filled xml
- Create a parser for parsing the xml
"""

from humps import pascalize
from dataclasses import fields, is_dataclass
from typing import Any, dataclass_transform, cast
from lxml.objectify import ObjectifiedElement
from lxml.etree import Element, _Element

from ._utils import get, typename, AnyType
from ._errors import XError, XErrorCtx, ErrorTypes
from ._manual import manual_xmlify
from ._lxml_helpers import with_children, with_child, XMLSchema
from ._xobject import XObject, gen_xobject


def validate_class(cls: AnyType):
    """
    Validate tha the class can be xmlified
    - Must be a dataclass
    - Cannot have any members called 'comment' (lxml parses comments as this tag)
    - Cannot have
    """
    if not is_dataclass(cls):
        raise ErrorTypes.NotADataclass(cls)

    reserved_attrs = ["get_xobject", "xsd_forward", "xsd_dependencies"]

    # TODO: cleanup repetition
    for f in fields(cls):
        if f.name in reserved_attrs:
            raise ErrorTypes.ReservedAttribute(cls, f.name)
        elif f.name == "comment":
            raise ErrorTypes.CommentAttribute(cls)

    # JUSTIFY: Could potentially have added other attributes (of the class,
    #          rather than a field of an instance as provided by dataclass
    #          fields)
    for reserved in reserved_attrs:
        if hasattr(cls, reserved):
            raise ErrorTypes.ReservedAttribute(cls, reserved)
    if hasattr(cls, "comment"):
        raise ErrorTypes.CommentAttribute(cls)


@dataclass_transform()
def xmlify(cls: type) -> AnyType:
    try:
        validate_class(cls)

        cls_name = typename(cls)
        forward_decs = cast(set[AnyType], {cls})
        meta_xobjects = [
            (
                pascalize(f.name),
                f,
                gen_xobject(cast(AnyType, f.type), forward_decs),
            )
            for f in fields(cls)
        ]

        class UserXObject(XObject):
            def xsd_out(
                self,
                name: str,
                attribs: dict[str, str] = {},
                add_ns: dict[str, str] = {},
            ) -> _Element:
                return Element(
                    f"{XMLSchema}element",
                    name=name,
                    type=cls_name,
                    attrib=attribs,
                )

            def xml_temp(self, name: str) -> _Element:
                return with_children(
                    Element(name),
                    [
                        xobj.xml_temp(pascal_name)
                        for pascal_name, _, xobj in meta_xobjects
                    ],
                )

            def xml_out(self, name: str, val: Any, ctx: XErrorCtx) -> _Element:
                return with_children(
                    Element(name),
                    [
                        xobj.xml_out(
                            pascal_name,
                            get(val, m.name),
                            ctx.next(pascal_name),
                        )
                        for pascal_name, m, xobj in meta_xobjects
                    ],
                )

            def xml_in(self, obj: ObjectifiedElement, ctx: XErrorCtx) -> Any:
                parsed: dict[str, Any] = {}
                for pascal_name, m, xobj in meta_xobjects:
                    if (m_obj := get(obj, pascal_name)) is not None:
                        parsed[m.name] = xobj.xml_in(
                            m_obj, ctx.next(pascal_name)
                        )
                    else:
                        raise ErrorTypes.NonMemberTag(ctx, cls, obj.tag, m.name)
                return cls(**parsed)

        cls_xobject = UserXObject()

        # JUSTIFY: Why are xsd forward & dependencies not part of xobject?
        #          - xobject covers the use (not forward decs)
        #          - we want to present error messages to the user containing
        #            their types, so xsd dependencies are in terms of python
        #            types, rather than xobjects
        #          - forward and dependencies do not apply to the basic types,
        #            only user types

        def xsd_forward(add_ns: dict[str, str]) -> _Element:
            return with_child(
                Element(f"{XMLSchema}complexType", name=cls_name),
                with_children(
                    Element(f"{XMLSchema}sequence"),
                    [
                        xobj.xsd_out(pascal_name, attribs={}, add_ns=add_ns)
                        for pascal_name, m, xobj in meta_xobjects
                    ],
                ),
            )

        def xsd_dependencies() -> set[AnyType]:
            return forward_decs

        def get_xobject():
            return cls_xobject

        # helper methods for gen_xobject, and other dataclasses to generate their
        # x methods
        cls.xsd_forward = xsd_forward  # type: ignore[attr-defined]
        cls.xsd_dependencies = xsd_dependencies  # type: ignore[attr-defined]
        cls.get_xobject = get_xobject  # type: ignore[attr-defined]

        return manual_xmlify(cls)
    except XError as e:
        # NOTE: Trick to remove dirty 'internal' traceback, and raise from
        #       xmlify (makes more sense to user than seeing internals)
        e.__traceback__ = None
        raise e
