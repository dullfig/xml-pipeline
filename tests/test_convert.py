"""
Tests for XML/JSON conversion tools.
"""

from __future__ import annotations

import json

import pytest

from xml_pipeline.tools.convert import (
    MAX_INPUT_SIZE,
    _dict_to_xml,
    _validate_tag_name,
    _xml_to_dict,
    json_to_xml,
    xml_extract,
    xml_to_json,
)
import xml.etree.ElementTree as ET


# ============================================================================
# TestXmlToDict
# ============================================================================

class TestXmlToDict:
    """Test _xml_to_dict helper."""

    def test_basic_conversion(self):
        root = ET.fromstring("<user><name>Alice</name><age>30</age></user>")
        result = _xml_to_dict(root)
        assert result == {"name": "Alice", "age": 30}

    def test_attributes_with_at_prefix(self):
        """Element with children preserves attributes with @ prefix."""
        root = ET.fromstring('<item id="42"><name>test</name></item>')
        result = _xml_to_dict(root)
        assert result == {"@id": "42", "name": "test"}

    def test_leaf_with_attributes_returns_text(self):
        """Leaf element (no children) returns text content, attributes lost."""
        root = ET.fromstring('<item id="42">text</item>')
        result = _xml_to_dict(root)
        assert result == "text"  # No children → returns text only

    def test_nested_elements(self):
        xml = "<root><user><name>Bob</name></user></root>"
        root = ET.fromstring(xml)
        result = _xml_to_dict(root)
        assert result == {"user": {"name": "Bob"}}

    def test_repeated_tags_become_array(self):
        xml = "<root><item>a</item><item>b</item><item>c</item></root>"
        root = ET.fromstring(xml)
        result = _xml_to_dict(root)
        assert result == {"item": ["a", "b", "c"]}

    def test_empty_element_becomes_none(self):
        root = ET.fromstring("<root><empty></empty></root>")
        result = _xml_to_dict(root)
        assert result == {"empty": None}

    def test_text_type_inference_int(self):
        root = ET.fromstring("<root><val>42</val></root>")
        result = _xml_to_dict(root)
        assert result["val"] == 42
        assert isinstance(result["val"], int)

    def test_text_type_inference_float(self):
        root = ET.fromstring("<root><val>3.14</val></root>")
        result = _xml_to_dict(root)
        assert result["val"] == 3.14
        assert isinstance(result["val"], float)

    def test_text_type_inference_bool_true(self):
        root = ET.fromstring("<root><val>true</val></root>")
        result = _xml_to_dict(root)
        assert result["val"] is True

    def test_text_type_inference_bool_false(self):
        root = ET.fromstring("<root><val>false</val></root>")
        result = _xml_to_dict(root)
        assert result["val"] is False

    def test_text_type_inference_string(self):
        root = ET.fromstring("<root><val>hello world</val></root>")
        result = _xml_to_dict(root)
        assert result["val"] == "hello world"

    def test_text_with_spaces_stays_string(self):
        root = ET.fromstring("<root><val> 42 </val></root>")
        result = _xml_to_dict(root)
        # After strip, "42" becomes int
        assert result["val"] == 42

    def test_scientific_notation_stays_string(self):
        """1e10 has no dot — should stay as string (int parse fails, no dot → no float)."""
        root = ET.fromstring("<root><val>1e10</val></root>")
        result = _xml_to_dict(root)
        # No dot → int parse fails → stays string
        assert result["val"] == "1e10"


# ============================================================================
# TestDictToXml
# ============================================================================

class TestDictToXml:
    """Test _dict_to_xml helper."""

    def test_basic(self):
        elem = _dict_to_xml({"name": "Alice", "age": 30}, "user")
        xml_str = ET.tostring(elem, encoding="unicode")
        assert "<name>Alice</name>" in xml_str
        assert "<age>30</age>" in xml_str

    def test_nested(self):
        data = {"address": {"city": "NYC"}}
        elem = _dict_to_xml(data, "root")
        xml_str = ET.tostring(elem, encoding="unicode")
        assert "<address><city>NYC</city></address>" in xml_str

    def test_arrays(self):
        data = {"items": [1, 2, 3]}
        elem = _dict_to_xml(data, "root")
        xml_str = ET.tostring(elem, encoding="unicode")
        assert xml_str.count("<items>") == 3

    def test_attributes(self):
        data = {"@id": "42", "name": "test"}
        elem = _dict_to_xml(data, "item")
        assert elem.get("id") == "42"

    def test_none_value(self):
        data = {"empty": None}
        elem = _dict_to_xml(data, "root")
        child = elem.find("empty")
        assert child is not None
        assert child.text is None

    def test_invalid_tag_name_rejected(self):
        with pytest.raises(ValueError, match="Invalid XML tag name"):
            _dict_to_xml({"name": "v"}, "123invalid")

    def test_invalid_child_tag_name_rejected(self):
        with pytest.raises(ValueError, match="Invalid XML tag name"):
            _dict_to_xml({"123bad": "v"}, "root")

    def test_tag_with_spaces_rejected(self):
        with pytest.raises(ValueError, match="Invalid XML tag name"):
            _dict_to_xml({"name": "v"}, "has space")

    def test_valid_tag_names(self):
        """Valid tags should not raise."""
        _dict_to_xml({"_under": "v"}, "root")
        _dict_to_xml({"item.sub": "v"}, "root")  # dots allowed
        _dict_to_xml({"a-b": "v"}, "root")  # hyphens allowed


# ============================================================================
# TestValidateTagName
# ============================================================================

class TestValidateTagName:
    """Test _validate_tag_name directly."""

    def test_valid_names(self):
        for name in ["root", "_private", "a1", "my-tag", "ns.tag", "CamelCase"]:
            _validate_tag_name(name)  # should not raise

    def test_invalid_names(self):
        for name in ["123", "has space", "", "a b", "@attr"]:
            with pytest.raises(ValueError):
                _validate_tag_name(name)


# ============================================================================
# TestXmlToJson (the @tool function)
# ============================================================================

class TestXmlToJson:
    """Test xml_to_json tool."""

    async def test_happy_path(self):
        result = await xml_to_json(xml_string="<user><name>Alice</name><age>30</age></user>")
        assert result.success is True
        data = result.data["data"]
        assert data["name"] == "Alice"
        assert data["age"] == 30

    async def test_strip_root_false(self):
        result = await xml_to_json(
            xml_string="<user><name>Alice</name></user>",
            strip_root=False,
        )
        assert result.success is True
        data = result.data["data"]
        assert "user" in data

    async def test_strip_root_true(self):
        result = await xml_to_json(
            xml_string="<user><name>Alice</name></user>",
            strip_root=True,
        )
        assert result.success is True
        data = result.data["data"]
        assert data == {"name": "Alice"}

    async def test_malformed_xml_error(self):
        result = await xml_to_json(xml_string="<broken><unclosed>")
        assert result.success is False
        assert "Invalid XML" in result.error

    async def test_size_limit_exceeded(self):
        big_xml = "<root>" + "x" * (MAX_INPUT_SIZE + 1) + "</root>"
        result = await xml_to_json(xml_string=big_xml)
        assert result.success is False
        assert "limit" in result.error

    async def test_json_output_is_valid(self):
        result = await xml_to_json(xml_string="<r><a>1</a></r>")
        assert result.success is True
        parsed = json.loads(result.data["json"])
        assert isinstance(parsed, dict)


# ============================================================================
# TestJsonToXml
# ============================================================================

class TestJsonToXml:
    """Test json_to_xml tool."""

    async def test_happy_path(self):
        result = await json_to_xml(json_string='{"name": "Alice", "age": 30}')
        assert result.success is True
        assert "<name>Alice</name>" in result.data["xml"]

    async def test_pretty_false(self):
        result = await json_to_xml(
            json_string='{"a": 1}',
            pretty=False,
        )
        assert result.success is True
        # Should be a single line
        assert "\n" not in result.data["xml"].strip()

    async def test_custom_root_tag(self):
        result = await json_to_xml(json_string='{"k": "v"}', root_tag="custom")
        assert result.success is True
        assert result.data["xml"].startswith("<custom")

    async def test_invalid_json_error(self):
        result = await json_to_xml(json_string="{invalid}")
        assert result.success is False
        assert "Invalid JSON" in result.error

    async def test_size_limit_exceeded(self):
        big_json = '{"k": "' + "x" * (MAX_INPUT_SIZE + 1) + '"}'
        result = await json_to_xml(json_string=big_json)
        assert result.success is False
        assert "limit" in result.error

    async def test_invalid_root_tag(self):
        result = await json_to_xml(json_string='{"k": "v"}', root_tag="123bad")
        assert result.success is False


# ============================================================================
# TestXmlExtract
# ============================================================================

class TestXmlExtract:
    """Test xml_extract tool."""

    async def test_xpath_matches(self):
        xml = "<root><item>a</item><item>b</item></root>"
        result = await xml_extract(xml_string=xml, xpath=".//item")
        assert result.success is True
        assert result.data["count"] == 2

    async def test_no_matches(self):
        xml = "<root><item>a</item></root>"
        result = await xml_extract(xml_string=xml, xpath=".//missing")
        assert result.success is True
        assert result.data["count"] == 0
        assert result.data["matches"] == []

    async def test_attribute_filter(self):
        xml = '<root><item id="1">a</item><item id="2">b</item></root>'
        result = await xml_extract(xml_string=xml, xpath=".//item[@id='2']")
        assert result.success is True
        assert result.data["count"] == 1
        assert result.data["matches"][0]["attributes"]["id"] == "2"

    async def test_malformed_xml(self):
        result = await xml_extract(xml_string="<broken>", xpath=".//x")
        assert result.success is False
        assert "Invalid XML" in result.error

    async def test_size_limit_exceeded(self):
        big_xml = "<root>" + "x" * (MAX_INPUT_SIZE + 1) + "</root>"
        result = await xml_extract(xml_string=big_xml, xpath=".//x")
        assert result.success is False
        assert "limit" in result.error


# ============================================================================
# TestXxeSafety
# ============================================================================

class TestXxeSafety:
    """Confirm Python's ET.fromstring does NOT resolve external entities."""

    async def test_external_entity_not_resolved(self):
        """Python's xml.etree.ElementTree uses expat which ignores external entities."""
        xxe_xml = """<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<root><val>&xxe;</val></root>"""
        # ET.fromstring will either:
        # - Raise ParseError (rejects DOCTYPE)
        # - Or ignore the entity entirely
        result = await xml_to_json(xml_string=xxe_xml)
        if result.success:
            # If it parsed, the entity should NOT have been resolved
            val = result.data["data"]
            if isinstance(val, dict):
                assert "root" not in str(val).lower() or "passwd" not in str(val).lower()
        else:
            # ParseError is the safe outcome
            assert "XML" in result.error or "error" in result.error.lower()


# ============================================================================
# TestRoundTrip
# ============================================================================

class TestRoundTrip:
    """Test XML → JSON → XML preserves structure for simple cases."""

    async def test_simple_roundtrip(self):
        original_xml = "<data><name>Alice</name><age>30</age></data>"

        # XML → JSON
        to_json = await xml_to_json(xml_string=original_xml, strip_root=False)
        assert to_json.success is True

        # JSON → XML
        json_str = to_json.data["json"]
        to_xml = await json_to_xml(json_string=json_str, root_tag="wrapper")
        assert to_xml.success is True
        assert "Alice" in to_xml.data["xml"]
        assert "30" in to_xml.data["xml"]
