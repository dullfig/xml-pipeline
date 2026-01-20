"""
XML/JSON conversion tools.

Enables agents to interoperate with JSON-based APIs and tools (n8n, webhooks, REST APIs).
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

from .base import tool, ToolResult


def _xml_to_dict(element: ET.Element) -> dict | str | list:
    """Recursively convert XML element to dict."""
    # If element has no children, return text content
    if len(element) == 0:
        text = (element.text or "").strip()
        # Try to parse as number/bool
        if text.lower() == "true":
            return True
        if text.lower() == "false":
            return False
        if text == "":
            return None
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return text
    
    result = {}
    
    # Add attributes with @ prefix
    for key, value in element.attrib.items():
        result[f"@{key}"] = value
    
    # Process children
    for child in element:
        child_data = _xml_to_dict(child)
        tag = child.tag
        
        # Handle multiple children with same tag -> array
        if tag in result:
            if not isinstance(result[tag], list):
                result[tag] = [result[tag]]
            result[tag].append(child_data)
        else:
            result[tag] = child_data
    
    return result


def _dict_to_xml(data: Any, tag: str = "item", parent: ET.Element | None = None) -> ET.Element:
    """Recursively convert dict to XML element."""
    if parent is None:
        elem = ET.Element(tag)
    else:
        elem = ET.SubElement(parent, tag)
    
    if isinstance(data, dict):
        for key, value in data.items():
            if key.startswith("@"):
                # Attribute
                elem.set(key[1:], str(value))
            elif isinstance(value, list):
                # Multiple children
                for item in value:
                    _dict_to_xml(item, key, elem)
            elif isinstance(value, dict):
                # Nested object
                _dict_to_xml(value, key, elem)
            else:
                # Simple value as child element
                child = ET.SubElement(elem, key)
                if value is not None:
                    child.text = str(value)
    elif isinstance(data, list):
        for item in data:
            _dict_to_xml(item, "item", elem)
    else:
        if data is not None:
            elem.text = str(data)
    
    return elem


@tool
async def xml_to_json(
    xml_string: str,
    strip_root: bool = True,
) -> ToolResult:
    """
    Convert XML to JSON.
    
    Use this to prepare data for JSON APIs, webhooks, n8n workflows, etc.
    
    Args:
        xml_string: XML content to convert
        strip_root: If True, unwrap single root element (default: True)
    
    Returns:
        json: The JSON string
        data: The parsed data as dict
    
    Example:
        <user><name>Alice</name><age>30</age></user>
        → {"name": "Alice", "age": 30}
    """
    try:
        # Parse XML
        root = ET.fromstring(xml_string.strip())
        data = _xml_to_dict(root)
        
        # Optionally strip the root element wrapper
        if strip_root and isinstance(data, dict) and len(data) == 1:
            # Check if we should unwrap
            pass  # Keep as-is, root is already stripped by _xml_to_dict
        
        # Wrap with root tag name if it's meaningful
        result = {root.tag: data} if not strip_root else data
        
        return ToolResult(success=True, data={
            "json": json.dumps(result, indent=2),
            "data": result,
        })
    except ET.ParseError as e:
        return ToolResult(success=False, error=f"Invalid XML: {e}")
    except Exception as e:
        return ToolResult(success=False, error=f"Conversion error: {e}")


@tool
async def json_to_xml(
    json_string: str,
    root_tag: str = "data",
    pretty: bool = True,
) -> ToolResult:
    """
    Convert JSON to XML.
    
    Use this to convert responses from JSON APIs back to XML format.
    
    Args:
        json_string: JSON content to convert
        root_tag: Name for the root XML element (default: "data")
        pretty: Pretty-print with indentation (default: True)
    
    Returns:
        xml: The XML string
    
    Example:
        {"name": "Alice", "age": 30}
        → <data><name>Alice</name><age>30</age></data>
    """
    try:
        data = json.loads(json_string)
        root = _dict_to_xml(data, root_tag)
        
        if pretty:
            ET.indent(root)
        
        xml_str = ET.tostring(root, encoding="unicode")
        
        return ToolResult(success=True, data={
            "xml": xml_str,
        })
    except json.JSONDecodeError as e:
        return ToolResult(success=False, error=f"Invalid JSON: {e}")
    except Exception as e:
        return ToolResult(success=False, error=f"Conversion error: {e}")


@tool
async def xml_extract(
    xml_string: str,
    xpath: str,
) -> ToolResult:
    """
    Extract data from XML using XPath.
    
    Args:
        xml_string: XML content
        xpath: XPath expression (e.g., ".//item", "./users/user[@id='1']")
    
    Returns:
        matches: List of matching elements as dicts
        count: Number of matches
    """
    try:
        root = ET.fromstring(xml_string.strip())
        elements = root.findall(xpath)
        
        matches = []
        for elem in elements:
            matches.append({
                "tag": elem.tag,
                "attributes": dict(elem.attrib),
                "data": _xml_to_dict(elem),
            })
        
        return ToolResult(success=True, data={
            "matches": matches,
            "count": len(matches),
        })
    except ET.ParseError as e:
        return ToolResult(success=False, error=f"Invalid XML: {e}")
    except Exception as e:
        return ToolResult(success=False, error=f"XPath error: {e}")
