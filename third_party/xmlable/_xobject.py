"""XObjects
XObjects are an intermediate representation for python types -> xsd/xml
- Produced by @xmlify decorated classes, and by gen_xobject
- Associated xsd, xml and parsing
"""

from humps import pascalize
from dataclasses import dataclass
from types import NoneType, UnionType
from lxml.objectify import ObjectifiedElement
from lxml.etree import Element, Comment, _Element
from abc import ABC, abstractmethod
from typing import Any, Callable, Type, get_args, TypeAlias, cast
from types import GenericAlias

from ._utils import get, typename, firstkey, AnyType
from ._errors import XErrorCtx, ErrorTypes
from ._lxml_helpers import (
    with_text,
    with_child,
    with_children,
    XMLSchema,
    XMLURL,
    children,
)


class XObject(ABC):
    """Any XObject wraps the xsd generation,
    We can map types to XObjects to get the xsd, template xml, etc
    """

    @abstractmethod
    def xsd_out(
        self,
        name: str,
        attribs: dict[str, str] = {},
        add_ns: dict[str, str] = {},
    ) -> _Element:
        """Generate the xsd schema for the object"""
        pass

    @abstractmethod
    def xml_temp(self, name: str) -> _Element:
        """
        Generate commented output for the xml representation
        - Contains no values, only comments
        - Not valid xml (can contain nested comments, comments instead of values)
        """
        pass

    @abstractmethod
    def xml_out(self, name: str, val: Any, ctx: XErrorCtx) -> _Element:
        pass

    @abstractmethod
    def xml_in(self, obj: ObjectifiedElement, ctx: XErrorCtx) -> Any:
        pass


@dataclass
class BasicObj(XObject):
    """
    An xobject for a simple type (e.g string, int)
    """

    type_str: str
    convert_fn: Callable[[Any], str]
    validate_fn: Callable[[Any], bool]
    parse_fn: Callable[[ObjectifiedElement], Any]

    def xsd_out(
        self,
        name: str,
        attribs: dict[str, Any] = {},
        add_ns: dict[str, str] = {},
    ) -> _Element:
        # NOTE: namespace cringe:
        #       - lxml will deal with qualifying namespaces for the name of the
        #         element, but not for attributes
        #       - XMLSchema type attributes must be qualified
        if (prefix := firstkey(add_ns, XMLURL)) is not None:
            return Element(
                f"{XMLSchema}element",
                name=name,
                type=f"{prefix}:{self.type_str}",
                attrib=attribs,
            )
        else:
            # add new namespace, resolve conflicts with extra 's'
            new_ns = "xs"
            while new_ns in add_ns:
                new_ns += "s"
            add_ns[new_ns] = XMLURL
            return Element(
                f"{XMLSchema}element",
                name=name,
                type=f"{new_ns}:{self.type_str}",
                attrib=attribs,
                nsmap={new_ns: XMLURL},
            )

    def xml_temp(self, name: str) -> _Element:
        return with_text(Element(name), f"Fill me with an {self.type_str}")

    def xml_out(self, name: str, val: Any, ctx: XErrorCtx) -> _Element:
        if not self.validate_fn(val):
            raise ErrorTypes.InvalidData(ctx, val, self.type_str)
        return with_text(Element(name), self.convert_fn(val))

    def xml_in(self, obj: ObjectifiedElement, ctx: XErrorCtx) -> Any:
        try:
            return self.parse_fn(obj)
        except Exception as e:
            raise ErrorTypes.ParseFailure(ctx, obj.text, self.type_str, e)


@dataclass
class ListObj(XObject):
    """
    An ordered list of objects
    """

    item_xobject: XObject
    list_elem_name: str
    struct_name: str = "List"

    def xsd_out(
        self,
        name: str,
        attribs: dict[str, str] = {},
        add_ns: dict[str, str] = {},
    ) -> _Element:
        return with_child(
            Element(f"{XMLSchema}element", name=name, attrib=attribs),
            with_children(
                Element(f"{XMLSchema}complexType"),
                [
                    Comment(f"This is a {self.struct_name}"),
                    with_child(
                        Element(f"{XMLSchema}sequence"),
                        self.item_xobject.xsd_out(
                            self.list_elem_name,
                            {"minOccurs": "0", "maxOccurs": "unbounded"},
                            add_ns,
                        ),
                    ),
                ],
            ),
        )

    def xml_temp(self, name: str) -> _Element:
        return with_children(
            Element(name),
            [
                Comment(f"This is a {self.struct_name}"),
                self.item_xobject.xml_temp(self.list_elem_name),
            ],
        )

    def xml_out(self, name: str, val: Any, ctx: XErrorCtx) -> _Element:
        if len(val) > 0:
            return with_children(
                Element(name),
                [
                    self.item_xobject.xml_out(
                        self.list_elem_name,
                        item_val,
                        ctx.next(f"{self.list_elem_name}[{i}]"),
                    )
                    for i, item_val in enumerate(val)
                ],
            )
        else:
            return with_child(
                Element(name), Comment(f"Empty {self.struct_name}!")
            )

    def xml_in(self, obj: ObjectifiedElement, ctx: XErrorCtx) -> list[Any]:
        parsed = []
        for i, child in enumerate(children(obj)):
            if child.tag != self.list_elem_name:
                raise ErrorTypes.UnexpectedTag(
                    ctx, self.list_elem_name, self.struct_name, child.tag
                )
            else:
                parsed.append(
                    self.item_xobject.xml_in(
                        child, ctx.next(f"{self.list_elem_name}[{i}]")
                    )
                )
        return parsed


@dataclass
class StructObj(XObject):
    """An order list of key-value pairs"""  # TODO: make objects variable length tuple

    objects: list[tuple[str, XObject]]
    struct_name: str = "Struct"

    def xsd_out(
        self,
        name: str,
        attribs: dict[str, str] = {},
        add_ns: dict[str, str] = {},
    ) -> _Element:
        return with_child(
            Element(f"{XMLSchema}element", name=name, attrib=attribs),
            with_child(
                Element(f"{XMLSchema}complexType"),
                with_children(
                    Element(f"{XMLSchema}sequence"),
                    [Comment(f"This is a {self.struct_name}")]
                    + [
                        xobj.xsd_out(member, {}, add_ns)
                        for member, xobj in self.objects
                    ],
                ),
            ),
        )

    def xml_temp(self, name: str) -> _Element:
        return with_children(
            Element(name),
            [Comment(f"This is a {self.struct_name}")]
            + [xobj.xml_temp(member) for member, xobj in self.objects],
        )

    def xml_out(self, name: str, val: Any, ctx: XErrorCtx) -> _Element:
        if len(val) != len(self.objects):
            raise ErrorTypes.IncorrectType(
                ctx, len(self.objects), self.struct_name, val, name
            )

        return with_children(
            Element(name),
            [
                xobj.xml_out(member, v, ctx.next(member))
                for (member, xobj), v in zip(self.objects, val)
            ],
        )

    def xml_in(
        self, obj: ObjectifiedElement, ctx: XErrorCtx
    ) -> list[tuple[str, Any]]:
        parsed = []
        for i, (child, (name, xobj)) in enumerate(
            zip(children(obj), self.objects)
        ):
            if child.tag != name:
                raise ErrorTypes.IncorrectElementTag(
                    ctx, self.struct_name, obj.tag, i, name, child.tag
                )
            parsed.append((name, xobj.xml_in(child, ctx.next(name))))
        return parsed


class TupleObj(XObject):
    """An anonymous struct"""

    def __init__(
        self,
        objects: tuple[XObject, ...],
        elem_gen: Callable[[int], str] = lambda i: f"Item-{i+1}",
    ):
        self.elem_gen = elem_gen
        self.struct: StructObj = StructObj(
            [(self.elem_gen(i), xobj) for i, xobj in enumerate(objects)],
            struct_name="Tuple",
        )

    def xsd_out(
        self,
        name: str,
        attribs: dict[str, str] = {},
        add_ns: dict[str, str] = {},
    ) -> _Element:
        return self.struct.xsd_out(name, attribs, add_ns)

    def xml_temp(self, name: str) -> _Element:
        return self.struct.xml_temp(name)

    def xml_out(self, name: str, val: Any, ctx: XErrorCtx) -> _Element:
        return self.struct.xml_out(name, val, ctx)

    def xml_in(
        self, obj: ObjectifiedElement, ctx: XErrorCtx
    ) -> tuple[Any, ...]:
        # Assumes the objects are in the correct order
        return tuple(zip(*self.struct.xml_in(obj, ctx)))[1]  # type: ignore[no-any-return]


class SetOBj(XObject):
    """An unordered collection of unique elements"""

    def __init__(self, inner: XObject, elem_name: str = "setitem"):
        self.list = ListObj(inner, elem_name, struct_name="set")

    def xsd_out(
        self,
        name: str,
        attribs: dict[str, str] = {},
        add_ns: dict[str, str] = {},
    ) -> _Element:
        return self.list.xsd_out(name, attribs, add_ns)

    def xml_temp(self, name: str) -> _Element:
        return self.list.xml_temp(name)

    def xml_out(self, name: str, val: Any, ctx: XErrorCtx) -> _Element:
        return self.list.xml_out(name, list(val), ctx)

    def xml_in(self, obj: ObjectifiedElement, ctx: XErrorCtx) -> set[Any]:
        parsed: set[Any] = set()
        for item in self.list.xml_in(obj, ctx):
            if item in parsed:
                raise ErrorTypes.DuplicateItem(ctx, "set", obj.tag, item)
            parsed.add(item)
        return parsed


@dataclass
class DictObj(XObject):
    """An unordered collection of key-value pair elements"""

    key_xobject: XObject
    val_xobject: XObject
    key_name: str = "Key"
    val_name: str = "Val"
    item_name: str = "Item"

    def xsd_out(
        self,
        name: str,
        attribs: dict[str, str] = {},
        add_ns: dict[str, str] = {},
    ) -> _Element:
        return with_child(
            Element(f"{XMLSchema}element", name=name, attrib=attribs),
            with_children(
                Element(f"{XMLSchema}complexType"),
                [
                    Comment("this is a dictionary!"),
                    with_child(
                        Element(f"{XMLSchema}sequence"),
                        with_child(
                            Element(
                                f"{XMLSchema}element",
                                name=self.item_name,
                                minOccurs="0",
                                maxOccurs="unbounded",
                            ),
                            with_child(
                                Element(f"{XMLSchema}complexType"),
                                with_children(
                                    Element(f"{XMLSchema}sequence"),
                                    [
                                        self.key_xobject.xsd_out(
                                            self.key_name, {}, add_ns
                                        ),
                                        self.val_xobject.xsd_out(
                                            self.val_name, {}, add_ns
                                        ),
                                    ],
                                ),
                            ),
                        ),
                    ),
                ],
            ),
        )

    def xml_temp(self, name: str) -> _Element:
        return with_children(
            Element(name),
            [
                Comment("This is a dictionary"),
                with_children(
                    Element(self.item_name),
                    [
                        self.key_xobject.xml_temp(self.key_name),
                        self.val_xobject.xml_temp(self.val_name),
                    ],
                ),
            ],
        )

    def xml_out(self, name: str, val: Any, ctx: XErrorCtx) -> _Element:
        item_ctx = ctx.next(self.item_name)

        return with_children(
            Element(name),
            [
                with_children(
                    Element(self.item_name),
                    [
                        self.key_xobject.xml_out(
                            self.key_name, k, item_ctx.next(name)
                        ),
                        self.val_xobject.xml_out(
                            self.val_name, v, item_ctx.next(name)
                        ),
                    ],
                )
                for k, v in val.items()
            ],
        )

    def xml_in(self, obj: ObjectifiedElement, ctx: XErrorCtx) -> dict[Any, Any]:
        parsed = {}
        for child in children(obj):
            if child.tag != self.item_name:
                raise ErrorTypes.InvalidDictionaryItem(
                    ctx,
                    self.item_name,
                    self.key_name,
                    self.val_name,
                    child.tag,
                    obj.tag,
                )
            else:
                child_ctx = ctx.next(self.item_name)
                k = self.key_xobject.xml_in(
                    get(child, self.key_name), child_ctx.next(self.key_name)
                )
                v = self.val_xobject.xml_in(
                    get(child, self.val_name), child_ctx.next(self.val_name)
                )

                if k in parsed:
                    raise ErrorTypes.DuplicateItem(
                        ctx, "dictionary", obj.tag, k
                    )

                parsed[k] = v
                # TODO: Check for other tags? Fail better?
        return parsed


def resolve_type(v: Any) -> AnyType:
    """Determine the type of some value, using primitive types
    - If empty container, only provide top container type
    INV: only generic types for v are {tuple, list, dict, set}
    """
    t = type(v)
    if t in {int, float, str, bool, NoneType}:
        return t
    elif t == dict and len(v) > 0:
        t0, t1 = next(iter(v.items()))
        return dict[resolve_type(t0), resolve_type(t1)]  # type: ignore[misc, index, no-any-return]
    elif t == list and len(v) > 0:
        return list[resolve_type(v[0])]  # type: ignore[misc, index, no-any-return]
    elif t == set and len(v) > 0:
        return set[resolve_type(next(iter(v)))]  # type: ignore[misc, index, no-any-return]
    elif t == tuple and len(v) > 0:
        return tuple[*(resolve_type(vi) for vi in v)]  # type: ignore[misc, no-any-return]
    else:
        # INV: non-generic type
        return t


@dataclass
class UnionObj(XObject):
    """A variant, can be one of several different types"""

    xobjects: dict[AnyType, XObject]
    elem_gen: Callable[[AnyType], str] = lambda t: pascalize(typename(t))

    def xsd_out(
        self,
        name: str,
        attribs: dict[str, str] = {},
        add_ns: dict[str, str] = {},
    ) -> _Element:
        return with_child(
            Element(f"{XMLSchema}element", name=name, attrib=attribs),
            with_children(
                Element(f"{XMLSchema}complexType"),
                [
                    Comment("this is a union!"),
                    with_children(
                        Element(f"{XMLSchema}sequence"),
                        [
                            xobj.xsd_out(
                                self.elem_gen(t), {"minOccurs": "0"}, add_ns
                            )
                            for t, xobj in self.xobjects.items()
                        ],
                    ),
                ],
            ),
        )

    def xml_temp(self, name: str) -> _Element:
        return with_children(
            Element(name),
            [
                Comment(
                    "This is a union, the following variants are possible, only one can be present"
                )
            ]
            + [
                xobj.xml_temp(self.elem_gen(t))
                for t, xobj in self.xobjects.items()
            ],
        )

    def xml_out(self, name: str, val: Any, ctx: XErrorCtx) -> _Element:
        t = resolve_type(val)

        if (val_xobj := self.xobjects.get(t)) is not None:
            variant_name = self.elem_gen(t)
            return with_child(
                Element(name),
                val_xobj.xml_out(variant_name, val, ctx.next(variant_name)),
            )
        else:
            raise ErrorTypes.InvalidVariant(
                ctx, name, list(self.xobjects.keys()), t, val
            )

    def xml_in(self, obj: ObjectifiedElement, ctx: XErrorCtx) -> Any:
        named = {self.elem_gen(t): xobj for t, xobj in self.xobjects.items()}
        variants = list(children(obj))

        if len(variants) != 1:
            raise ErrorTypes.MultipleVariants(ctx, [v.tag for v in variants])

        variant = variants[0]
        if (xobj := named.get(variant.tag)) is not None:
            return xobj.xml_in(variant, ctx.next(variant.tag))
        else:
            raise ErrorTypes.ParseInvalidVariant(
                ctx, str(obj.tag), list(named.keys()), str(variant)
            )


class NoneObj(XObject):
    """
    An object representing the python 'None' type
    - Unions of form `int | None` are used for optionals
    """

    def xsd_out(
        self,
        name: str,
        attribs: dict[str, str] = {},
        add_ns: dict[str, str] = {},
    ) -> _Element:
        return with_child(
            Element(f"{XMLSchema}element", name=name, attrib=attribs),
            Comment("This is a None type"),
        )

    def xml_temp(self, name: str) -> _Element:
        return with_child(Element(name), Comment("This is None"))

    def xml_out(self, name: str, val: Any, ctx: XErrorCtx) -> _Element:
        if val != None:
            raise ErrorTypes.NoneIsSome(ctx, name, val)

        return with_child(Element(name), Comment("This is None"))

    def xml_in(self, obj: ObjectifiedElement, ctx: XErrorCtx) -> Any:
        return None


def is_xmlified(cls):
    return (
        hasattr(cls, "xsd_forward")
        and hasattr(cls, "xsd_dependencies")
        and hasattr(cls, "get_xobject")
        and hasattr(cls, "xsd")
        and hasattr(cls, "xml")
        and hasattr(cls, "xml_value")
        and hasattr(cls, "parse")
    )


def gen_xobject(data_type: AnyType, forward_dec: set[AnyType]) -> XObject:
    basic_types: dict[
        AnyType, tuple[str, Callable[[Any], str], Callable[[Any], bool]]
    ] = {
        int: ("integer", str, lambda d: type(d) == int),
        str: ("string", str, lambda d: type(d) == str),
        float: ("decimal", str, lambda d: type(d) == float),
        bool: (
            "boolean",
            lambda b: "true" if b else "false",
            lambda d: type(d) == bool,
        ),
    }

    if (basic_entry := basic_types.get(data_type)) is not None:
        type_str, convert_fn, validate_fn = basic_entry
        # NOTE: here was can pass the parse_fn as the data type, as the name is
        #       also a constructor. (e.g. `int` -> `int("23") == 32`)
        parse_fn = cast(Callable[[ObjectifiedElement], Any], data_type)
        return BasicObj(type_str, convert_fn, validate_fn, parse_fn)
    elif isinstance(data_type, NoneType) or data_type == NoneType:
        # NOTE: Python typing cringe: None can be both a type and a value
        #       (even when within a type hint!)
        # a: list[None] -> None is an instance of NoneType
        # a: int | None -> Union of int and NoneType
        return NoneObj()
    elif isinstance(data_type, UnionType):
        return UnionObj(
            {t: gen_xobject(t, forward_dec) for t in get_args(data_type)}
        )
    else:
        t_name = typename(data_type)
        if t_name == "list":
            (item_type,) = get_args(data_type)
            return ListObj(
                gen_xobject(item_type, forward_dec),
                pascalize(typename(item_type)),
            )
        elif t_name == "dict":
            key_type, val_type = get_args(data_type)
            return DictObj(
                gen_xobject(key_type, forward_dec),
                gen_xobject(val_type, forward_dec),
            )
        elif t_name == "tuple":
            return TupleObj(
                tuple(gen_xobject(t, forward_dec) for t in get_args(data_type))
            )
        elif t_name == "set":
            (item_type,) = get_args(data_type)
            return SetOBj(
                gen_xobject(item_type, forward_dec),
                pascalize(typename(item_type)),
            )
        else:
            if is_xmlified(data_type):
                forward_dec.add(data_type)
                return data_type.get_xobject()  # type: ignore[attr-defined, no-any-return]
            else:
                raise ErrorTypes.NonXMlifiedType(t_name)
