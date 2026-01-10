"""
The IXmlify interface
- Contains the methods needed to make get_xobject work
- Allows type checking of user's implementations
"""

from abc import ABC, abstractmethod
from lxml.etree import _Element
from ._xobject import XObject
from ._utils import AnyType


class IXmlify(ABC):
    """
    A useful interface for ensuring the attributes required for
    @manual_xmlify are present
    """

    @staticmethod
    @abstractmethod
    def get_xobject() -> XObject:
        """
        produces an xobject encapsulates the:
         - xsd usage (e.g <xs:element name="..." type="thisclass!"/>)
         - xml template
         - xml value
         - parsing

        ```
        @manual_xmlify
        class Foo(IXmlify):
            def get_xobject() -> XObject:
                class MyObj(XObject):
                    # ... definitions

                return MyObj
        ```
        """
        pass

    @staticmethod
    @abstractmethod
    def xsd_forward(add_ns: dict[str, str]) -> _Element:
        """
        Produces the forward declaration
        - xsd definition of the class's type
        ```
        @manual_xmlify
        class Foo(IXmlify):
            def xsd_forward(add_ns: dict[str, str]) -> _Element:
                return Element('{XMLSchema}complexType', name="Foo", ...)
        ```
        """
        pass

    @staticmethod
    @abstractmethod
    def xsd_dependencies() -> set[AnyType]:
        """
        The user classes that need to be before this first

        For example:
        ```
        @manual_xmlify
        class A(IXMlify):
            # xobject uses Foo and Bar
            def xsd_depedencies() -> set[type]:
                return {Foo, Bar}
        ```
        """
        pass
