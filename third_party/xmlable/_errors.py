"""
Colourful & descriptive errors for xmlable
- Clear messages
- Trace for parsing
"""

from dataclasses import dataclass
from typing import Any, Iterable
from termcolor import colored
from termcolor.termcolor import Color

from ._utils import typename, AnyType


def trace_note(trace: list[str], arrow_c: Color, node_c: Color):
    return colored(" > ", arrow_c).join(
        map(lambda x: colored(x, node_c), trace)
    )


@dataclass
class XErrorCtx:
    trace: list[str]

    def next(self, node: str):
        return XErrorCtx(trace=self.trace + [node])


# TODO: Custom backtrace to point to location in the file
class XError(Exception):
    def __init__(
        self,
        short: str,
        what: str,
        why: str,
        ctx: XErrorCtx | None = None,
        notes: Iterable[str] = [],
    ):
        super().__init__(colored(short, "red", attrs=["blink"]))
        self.add_note(colored("What:  " + what, "blue"))
        self.add_note(colored("Why:   " + why, "yellow"))
        if ctx is not None:
            self.add_note(
                colored("Where: ", "magenta")
                + trace_note(ctx.trace, "light_magenta", "light_cyan")
            )
        for note in notes:
            self.add_note(note)


class ErrorTypes:
    @staticmethod
    def NonXMlifiedType(t_name: str) -> XError:
        return XError(
            short="Non XMlified Type",
            what=f"You attempted to use {t_name} in an xmlified class, but {t_name} is not xmlified",
            why=f"All types used in an xmlified class must be xmlified",
        )

    @staticmethod
    def InvalidData(ctx: XErrorCtx, val: Any, t_name: str) -> XError:
        return XError(
            short="Invalid Data",
            what=f"Could not validate {val} as a valid {t_name}",
            why=f"Produced xml must be valid",
            ctx=ctx,
        )

    @staticmethod
    def ParseFailure(
        ctx: XErrorCtx, text: str | None, t_name: str, caught: Exception
    ) -> XError:
        return XError(
            short="Parse Failure",
            what=f"Failed to parse {text} as a {t_name} with error: \n {caught}",
            why=f"This error implies the xml is not validated against the current xsd, or there is a bug in this type's parser",
            ctx=ctx,
        )

    @staticmethod
    def UnexpectedTag(
        ctx: XErrorCtx, expected_name: str, struct_name: str, tag_found: str
    ) -> XError:
        return XError(
            short="Unexpected Tag",
            what=f"Expected {expected_name} but found {tag_found}",
            why=f"This is a {struct_name} that contains 0..n elements of {expected_name} and no other elements",
            ctx=ctx,
        )

    @staticmethod
    def IncorrectType(
        ctx: XErrorCtx, expected_len: int, struct_name: str, val: Any, name: str
    ) -> XError:
        return XError(
            short="Incorrect Type",
            what=f"You have provided {len(val)} values {val} for {name}, but {name} is a {struct_name} that takes only {expected_len} values",
            why=f"In order to generate xml, the values provided need to be the correct types",
            ctx=ctx,
        )

    @staticmethod
    def IncorrectElementTag(
        ctx: XErrorCtx,
        struct_name: str,
        tag_name: str,
        elem_index: int,
        tag_expected: str,
        tag_found: str,
    ) -> XError:
        return XError(
            short="Incorrect Element Tag",
            what=f"While parsing {struct_name} {tag_name} we expected element {elem_index} to be {tag_expected}, but found {tag_found}",
            why=f"The xml representation for {struct_name} requires the correct names in the correct order",
            ctx=ctx,
        )

    @staticmethod
    def DuplicateItem(
        ctx: XErrorCtx, struct_name: str, tag: str, item: str
    ) -> XError:
        return XError(
            short=f"Duplicate item in {struct_name}",
            what=f"In {tag} the item {item} is present more than once",
            why=f"A set can only contain unique items",
            ctx=ctx,
        )

    @staticmethod
    def InvalidDictionaryItem(
        ctx: XErrorCtx,
        expected_tag: str,
        expected_key: str,
        expected_val: str,
        dict_tag: str,
        item_tag: str,
    ) -> XError:
        return XError(
            short="Invalid item in dictionary",
            what=f"An unexpected item with {dict_tag} is in dictionary {item_tag}",
            why=f"Each item must have tag {expected_tag} with children {expected_key} and {expected_val}",
            ctx=ctx,
        )

    @staticmethod
    def InvalidVariant(
        ctx: XErrorCtx,
        name: str,
        expected_types: list[AnyType],
        found_type: AnyType | None,
        found_value: Any,
    ) -> XError:
        types = " | ".join(map(str, expected_types))
        return XError(
            short=f"Datatype not in Union",
            what=f"{name} is a union of {types}, which does not contain {found_type} (you provided: {found_value})",
            why=f"... uuuh, its a union?",
            ctx=ctx,
        )

    @staticmethod
    def MultipleVariants(ctx: XErrorCtx, variant_names: list[str]) -> XError:
        return XError(
            short="Multiple union variants present",
            what=f"variants {', '.join(variant_names)} are present",
            why=f"A union can only be one variant at a time",
            ctx=ctx,
        )

    @staticmethod
    def ParseInvalidVariant(
        ctx: XErrorCtx, tag: str, named_variants: list[str], found_variant: str
    ) -> XError:
        return XError(
            short="Invalid Variant",
            what=f"The union {tag} can contain variants {', '.join(named_variants)}, but you have used {found_variant}",
            why=f"Only valid variants can be parsed",
            ctx=ctx,
        )

    @staticmethod
    def NoneIsSome(ctx: XErrorCtx, name: str, val: Any) -> XError:
        return XError(
            short="None object is not None",
            what=f"{name} contains value {val} which is not None",
            why="A None type object can only contain none",
            ctx=ctx,
        )

    @staticmethod
    def NotADataclass(cls: AnyType) -> XError:
        cls_name: str = typename(cls)
        return XError(
            short="Non-Dataclass",
            what=f"{cls_name} is not a dataclass",
            why=f"xmlify uses dataclasses to get fields",
            ctx=XErrorCtx([cls_name]),
            notes=[f"\nTry:\n@xmlify\n@dataclass\nclass {cls_name}:"],
        )

    @staticmethod
    def ReservedAttribute(cls: AnyType, attr_name: str) -> XError:
        cls_name: str = typename(cls)
        return XError(
            short=f"Reserved Attribute",
            what=f"{cls_name}.{attr_name} is used by xmlify, so it cannot be a field of the class",
            why=f"xmlify aguments {cls_name} by adding methods it can then use for xsd, xml generation and parsing",
            ctx=XErrorCtx([cls_name]),
        )

    @staticmethod
    def CommentAttribute(cls: AnyType) -> XError:
        cls_name: str = typename(cls)
        return XError(
            short=f"Comment Attribute",
            what=f"xmlifed classes cannot use comment as an attribute",
            why=f"comment is used as a tag name for comments by lxml, so comments inserted on xml generation could conflict",
            ctx=XErrorCtx([cls_name]),
        )

    @staticmethod
    def NonMemberTag(
        ctx: XErrorCtx, cls: AnyType, tag: str, name: str
    ) -> XError:
        cls_name: str = typename(cls)
        return XError(
            short="Non member tag",
            what=f"In {tag} {cls_name}.{name} could not be found.",
            why=f"All members, including {cls_name}.{name} must be present",
            ctx=ctx,
        )

    @staticmethod
    def MissingAttribute(
        cls: AnyType, required_attrs: set[str], missing_attr: str
    ) -> XError:
        cls_name: str = typename(cls)
        return XError(
            short="Missing Attribute",
            what=f"The attribute {missing_attr} is missing from {cls_name}",
            why=f"To be manual_xmlified the attributes: {', '.join(required_attrs)} are required. Try using help(IXmlify)",
            ctx=XErrorCtx([cls_name]),
        )

    @staticmethod
    def DependencyCycle(cycle: list[AnyType]) -> XError:
        return XError(
            short="Dependency Cycle in XSD",
            what=f"There is a cycle: {'<-'.join(map(str, cycle))}",
            why="The XSDs for classes are written to the .xsd file in dependency order",
        )

    @staticmethod
    def NotXmlified(cls: AnyType) -> XError:
        cls_name: str = typename(cls)
        return XError(
            short="Not Xmlified",
            what=f"{cls_name} is not xmlified, and hence cannot have an associated parser",
            why=f"the .xsd(...) method is required to write_xsd",
            notes=[f"To fix, try:\n@xmlify\n@dataclass\nclass {cls_name}: ..."],
        )
