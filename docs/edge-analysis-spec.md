# Edge Analysis API Specification

**Status:** Draft  
**Author:** Donna (with Dan)  
**Date:** 2026-01-26

## Overview

When users connect nodes in the visual flow editor, an AI analyzes the schema compatibility and proposes field mappings. This provides immediate visual feedback (green/yellow/red) and reduces manual configuration for common cases.

## User Experience

### Visual Feedback

When a user draws a connection from Node A to Node B:

```
┌─────────────┐                           ┌─────────────┐
│   Node A    │    🟢 ────────────────▶   │   Node B    │
│             │    High confidence        │             │
└─────────────┘                           └─────────────┘

┌─────────────┐                           ┌─────────────┐
│   Node C    │    🟡 ─ ─ ─ ─ ─ ─ ─ ─▶   │   Node D    │
│             │    Review suggested       │             │
└─────────────┘                           └─────────────┘

┌─────────────┐                           ┌─────────────┐
│   Node E    │    🔴 ─ ─ ─ ✕ ─ ─ ─ ─▶   │   Node F    │
│             │    Manual mapping needed  │             │
└─────────────┘                           └─────────────┘
```

### Confidence Levels

| Level | Color | Threshold | Meaning |
|-------|-------|-----------|---------|
| HIGH | 🟢 Green | ≥ 0.8 | All required inputs mapped, types compatible |
| MEDIUM | 🟡 Yellow | 0.4 - 0.79 | Some mappings uncertain or missing optional fields |
| LOW | 🔴 Red | < 0.4 | Cannot determine mapping, manual intervention required |

### Interaction Flow

1. User drags connection from A output → B input
2. Frontend calls `POST /api/v1/flows/{id}/analyze-edge`
3. Backend analyzes schemas (cached if previously computed)
4. Frontend renders line with confidence color
5. User clicks line → mapping panel opens
6. User can accept, modify, or manually define mappings
7. Changes saved to flow's `canvas_state`

## API Endpoint

### POST /api/v1/flows/{flow_id}/analyze-edge

Analyze compatibility between two nodes and propose field mappings.

#### Request

```json
{
  "from_node": "calculator.add",
  "to_node": "calculator.multiply",
  "from_output_schema": "<optional: override if not using node's default>",
  "to_input_schema": "<optional: override if not using node's default>"
}
```

**Notes:**
- If schemas not provided, fetched from node registry
- Schemas can be XSD strings or references to registered schemas

#### Response

```json
{
  "edge_id": "add_to_multiply",
  "confidence": 0.85,
  "level": "high",
  
  "proposed_mapping": {
    "mappings": [
      {
        "from_field": "output.sum",
        "to_field": "input.value",
        "confidence": 0.95,
        "reason": "Exact name match, compatible types (int → int)"
      },
      {
        "from_field": "output.operands",
        "to_field": "input.factors",
        "confidence": 0.6,
        "reason": "Semantic similarity, both arrays of numbers"
      }
    ],
    "unmapped_required": [
      {
        "field": "input.precision",
        "type": "int",
        "default": 2,
        "suggestion": "Set constant value or map from upstream"
      }
    ],
    "unmapped_optional": [
      {
        "field": "input.label",
        "type": "string"
      }
    ]
  },
  
  "warnings": [
    "input.precision has no source, using default value 2",
    "output.metadata will be discarded (no matching input field)"
  ],
  
  "errors": [],
  
  "analysis_method": "llm",  // or "heuristic" for simple cases
  "cached": false,
  "analysis_time_ms": 245
}
```

#### Error Response

```json
{
  "error": "schema_not_found",
  "message": "Node 'calculator.add' has no registered output schema",
  "details": {
    "node": "calculator.add"
  }
}
```

### GET /api/v1/flows/{flow_id}/edges

List all edges in a flow with their current mapping status.

#### Response

```json
{
  "edges": [
    {
      "id": "edge_1",
      "from_node": "input",
      "to_node": "calculator.add",
      "confidence": 0.92,
      "level": "high",
      "mapping_status": "auto",
      "last_analyzed": "2026-01-26T06:30:00Z"
    },
    {
      "id": "edge_2", 
      "from_node": "calculator.add",
      "to_node": "formatter",
      "confidence": 0.45,
      "level": "medium",
      "mapping_status": "user_modified",
      "last_analyzed": "2026-01-26T06:30:00Z"
    }
  ]
}
```

### PUT /api/v1/flows/{flow_id}/edges/{edge_id}/mapping

Save user-defined or modified mapping for an edge.

#### Request

```json
{
  "mappings": [
    {
      "from_field": "output.sum",
      "to_field": "input.value"
    },
    {
      "to_field": "input.factor",
      "constant": 5
    },
    {
      "to_field": "input.label",
      "expression": "concat('Result: ', output.sum)"
    }
  ],
  "user_confirmed": true
}
```

## Analysis Engine

### Heuristic Analysis (Fast Path)

Used when schemas are simple and mapping is obvious:

1. **Exact name match** — `output.value` → `input.value` (confidence: 0.95)
2. **Case-insensitive match** — `output.Value` → `input.value` (confidence: 0.9)
3. **Common aliases** — `output.result` → `input.value` (confidence: 0.7)
4. **Type compatibility** — int → float OK, string → int NOT OK

### LLM Analysis (Deep Path)

Used when heuristics produce low confidence:

```
System: You are analyzing data flow compatibility between two XML schemas.

Given:
- Source schema (output of previous step): {from_schema}
- Target schema (input of next step): {to_schema}

Propose a field mapping. For each target field, identify:
1. The best source field to map from (if any)
2. Confidence (0-1) in the mapping
3. Brief reason for the mapping

If a required target field cannot be mapped, flag it.
If source fields will be discarded, note them.

Respond in JSON format.
```

### Caching Strategy

- Cache key: `hash(from_schema) + hash(to_schema)`
- TTL: 24 hours (schemas rarely change)
- Invalidate on: node schema update, user clear cache
- Store: Redis or in-memory LRU

## Database Schema

### Edge Mappings Table

```sql
CREATE TABLE edge_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_id UUID NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    
    -- Edge identification
    from_node VARCHAR(100) NOT NULL,
    to_node VARCHAR(100) NOT NULL,
    
    -- Analysis results
    confidence NUMERIC(3,2),
    level VARCHAR(10),  -- 'high', 'medium', 'low'
    analysis_method VARCHAR(20),  -- 'heuristic', 'llm'
    
    -- The actual mapping (JSON)
    proposed_mapping JSONB,
    user_mapping JSONB,  -- User overrides, if any
    
    -- Status
    user_confirmed BOOLEAN DEFAULT FALSE,
    
    -- Timestamps
    analyzed_at TIMESTAMP WITH TIME ZONE,
    confirmed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(flow_id, from_node, to_node)
);

CREATE INDEX idx_edge_mappings_flow ON edge_mappings(flow_id);
```

## Sequencer Integration

When a sequence is executed, the sequencer factory:

1. Loads all edge mappings for the flow
2. For each edge, generates a transformer function:

```python
def generate_transformer(edge_mapping: EdgeMapping) -> Callable:
    """
    Generate a function that transforms A's output to B's input.
    """
    def transform(source_xml: str) -> str:
        source = parse_xml(source_xml)
        target = {}
        
        for mapping in edge_mapping.effective_mapping:
            if mapping.from_field:
                target[mapping.to_field] = extract(source, mapping.from_field)
            elif mapping.constant is not None:
                target[mapping.to_field] = mapping.constant
            elif mapping.expression:
                target[mapping.to_field] = evaluate(mapping.expression, source)
        
        return serialize_xml(target, edge_mapping.to_schema)
    
    return transform
```

3. Transformer is called between each step in the sequence

## LLM-Created Sequences (Runtime)

Agents can dynamically create sequences at runtime. The sequencer factory runs the same analysis but with stricter rules.

### Runtime Policy

| Confidence | Design-time (Canvas) | Run-time (LLM) |
|------------|---------------------|----------------|
| 🟢 High | Auto-wire, run | Auto-wire, run |
| 🟡 Medium | Show warning, let user decide | **Block**, request clarification |
| 🔴 Low | Show error, require manual | **Block**, request clarification |

**Rationale:** Letting LLMs YOLO on uncertain mappings is risky. Better to ask for explicit instructions.

### LLM Clarification Flow

```
1. LLM requests sequence: A → B → C
2. Factory analyzes:
   - A → B: green ✓
   - B → C: yellow (ambiguous)
3. Factory responds with structured error:
   - Which edge failed
   - What B outputs
   - What C expects  
   - Suggested resolutions
4. LLM provides hints:
   - Explicit mappings
   - Constants for missing fields
   - Fields to drop
5. Factory re-analyzes with hints
6. If green: run. If not: back to step 3.
```

### Edge Hints Payload

LLMs can provide explicit wiring instructions:

```xml
<CreateSequence>
  <steps>transformer, uploader</steps>
  <initial_payload>...</initial_payload>
  
  <edge_hints>
    <edge from="transformer" to="uploader">
      <map from="result" to="payload"/>
      <constant field="destination">/uploads/output.json</constant>
      <drop field="factor"/>
      <drop field="metadata"/>
    </edge>
  </edge_hints>
</CreateSequence>
```

### Hint Types

| Hint | Syntax | Effect |
|------|--------|--------|
| Map field | `<map from="X" to="Y"/>` | Wire source field to target field |
| Set constant | `<constant field="Y">value</constant>` | Set target field to literal value |
| Drop field | `<drop field="X"/>` | Explicitly ignore source field |
| Expression | `<expr field="Y">concat(X.a, X.b)</expr>` | Compute target from expression |

### Error Response to LLM

When wiring fails:

```xml
<SequenceError>
  <code>wiring_failed</code>
  <edge from="transformer" to="uploader"/>
  
  <source_fields>
    <field name="result" type="string"/>
    <field name="factor" type="int"/>
    <field name="metadata" type="object"/>
  </source_fields>
  
  <target_fields>
    <field name="payload" type="string" required="true" mapped="result" confidence="0.85"/>
    <field name="destination" type="string" required="true" mapped="" confidence="0"/>
  </target_fields>
  
  <issues>
    <issue type="unmapped_required">destination has no source</issue>
    <issue type="unmapped_source">factor will be dropped</issue>
    <issue type="unmapped_source">metadata will be dropped</issue>
  </issues>
  
  <suggestion>Provide mapping for 'destination' or set as constant.</suggestion>
</SequenceError>
```

This gives the LLM enough information to either:
- Provide the missing hints
- Try a different sequence
- Ask the user for help

## Future Enhancements

### v1.1 — Type Coercion
- Automatic int → string, date formatting, etc.
- Warnings when lossy conversion occurs

### v1.2 — Expression Builder
- Visual expression editor for complex mappings
- Functions: `concat()`, `format()`, `split()`, `lookup()`

### v1.3 — Learning from Corrections
- Track when users override AI suggestions
- Fine-tune confidence thresholds
- Eventually: personalized mapping suggestions

### v2.0 — Multi-Output Nodes
- Some nodes produce multiple outputs
- UI shows multiple output ports
- User wires specific port to specific input

---

*This spec is a living document. Update as implementation progresses.*
