"""
The @manual_xmlify decorator used to add the .xsd, .xml, .xml_value and .parse
methods to a class that already has .xsd_dependencies, .xsd_forward and
.get_xobject
"""

from typing import Any
from lxml.etree import _Element, Element, _ElementTree, ElementTree
from lxml.objectify import ObjectifiedElement

from ._utils import typename, AnyType, ordered_iter
from ._lxml_helpers import with_children, XMLSchema
from ._errors import XError, XErrorCtx, ErrorTypes


def validate_manual_class(cls: AnyType):
    attrs = {"get_xobject", "xsd_forward", "xsd_dependencies"}
    for attr in attrs:
        if not hasattr(cls, attr):
            raise ErrorTypes.MissingAttribute(cls, attrs, attr)


def type_cycle(from_type: AnyType) -> list[AnyType]:
    # INV: it is an xmlified type for a user define structure
    cycle: list[AnyType] = []

    def visit_dep(curr: AnyType) -> bool:
        if curr == from_type or any(
            visit_dep(dep) for dep in ordered_iter(curr.xsd_dependencies())  # type: ignore[attr-defined]
        ):
            cycle.append(curr)
            return True
        else:
            return False

    assert visit_dep(from_type)
    cycle.append(from_type)
    return cycle


def manual_xmlify(cls: type) -> type:
    """
    Generate the following methods:
    ```
    def xsd(
            id: str = cls_name,
            namespaces: dict[str, str] = {},
            imports: dict[str, str] = {},
        ) -> _ElementTree:
        # ...

    def xml(schema_name: str = cls_name) -> _ElementTree:
        # ...

    def xml_value(self, id: str = cls_name) -> _ElementTree:
        # ...

    def parse(obj: ObjectifiedElement) -> Any:
        # ...
    ```
    """
    try:
        validate_manual_class(cls)
        cls_name = typename(cls)

        cls_xobject = cls.get_xobject()  # type: ignore[attr-defined]

        def xsd(
            id: str = cls_name,
            namespaces: dict[str, str] = {},
            imports: dict[str, str] = {},
        ) -> _ElementTree:
            # Get dependencies (user classes that need to be declared before)
            visited: set[AnyType] = set()
            dec_order: list[AnyType] = []

            def toposort(
                curr: AnyType, visited: set[AnyType], dec_order: list[AnyType]
            ):
                if curr in visited:
                    raise ErrorTypes.DependencyCycle(type_cycle(curr))
                visited.add(curr)
                deps = curr.xsd_dependencies()  # type: ignore[attr-defined]
                for d in ordered_iter(deps):
                    if d not in visited:
                        toposort(d, visited, dec_order)
                dec_order.append(curr)

            toposort(cls, visited, dec_order)

            # Create forward declarations, potentially adding to namespaces
            decs: list[_Element] = [dec.xsd_forward(namespaces) for dec in dec_order]  # type: ignore[attr-defined]

            # generate main element (can add to namespaces)
            main_element = cls_xobject.xsd_out(id, add_ns=namespaces)

            return ElementTree(
                with_children(
                    Element(
                        f"{XMLSchema}schema",
                        id=id,
                        elementFormDefault="qualified",
                        nsmap=namespaces,
                    ),
                    [
                        Element(
                            f"{XMLSchema}import",
                            namespace=ns,
                            schemaLocation=sloc,
                        )
                        for ns, sloc in imports.items()
                    ]
                    + decs
                    + [main_element],
                )
            )

        def xml(schema_name: str = cls_name) -> _ElementTree:
            return ElementTree(cls_xobject.xml_temp(schema_name))

        def xml_value(self, id: str = cls_name) -> _ElementTree:
            return ElementTree(cls_xobject.xml_out(id, self, XErrorCtx([id])))

        def parse(obj: ObjectifiedElement) -> Any:
            return cls_xobject.xml_in(obj, XErrorCtx([obj.tag]))

        cls.xsd = xsd  # type: ignore[attr-defined]
        cls.xml = xml  # type: ignore[attr-defined]
        setattr(cls, "xml_value", xml_value)  # needs to use self to get values
        cls.parse = parse  # type: ignore[attr-defined]

        return cls
    except XError as e:
        # NOTE: Trick to remove dirty 'internal' traceback, and raise from
        #       xmlify (makes more sense to user than seeing internals)
        e.__traceback__ = None
        raise e
