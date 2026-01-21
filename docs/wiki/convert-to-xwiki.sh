#!/bin/bash
# Convert Markdown wiki docs to XWiki format using Pandoc
#
# Prerequisites:
#   - Pandoc installed (https://pandoc.org/installing.html)
#   - Run from docs/wiki directory
#
# Usage:
#   ./convert-to-xwiki.sh
#
# Output:
#   Creates xwiki/ directory with converted files

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/xwiki"

echo "Converting Markdown to XWiki format..."
echo "Output directory: $OUTPUT_DIR"
echo ""

# Create output directory structure
mkdir -p "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR/architecture"
mkdir -p "$OUTPUT_DIR/reference"
mkdir -p "$OUTPUT_DIR/tutorials"

# Function to convert a single file
convert_file() {
    local input="$1"
    local output="$2"

    if [ -f "$input" ]; then
        echo "Converting: $input"
        pandoc -f markdown -t xwiki "$input" -o "$output"
    fi
}

# Convert root level docs
convert_file "$SCRIPT_DIR/Home.md" "$OUTPUT_DIR/Home.xwiki"
convert_file "$SCRIPT_DIR/Installation.md" "$OUTPUT_DIR/Installation.xwiki"
convert_file "$SCRIPT_DIR/Quick-Start.md" "$OUTPUT_DIR/Quick-Start.xwiki"
convert_file "$SCRIPT_DIR/Writing-Handlers.md" "$OUTPUT_DIR/Writing-Handlers.xwiki"
convert_file "$SCRIPT_DIR/LLM-Router.md" "$OUTPUT_DIR/LLM-Router.xwiki"
convert_file "$SCRIPT_DIR/Why-XML.md" "$OUTPUT_DIR/Why-XML.xwiki"

# Convert architecture docs
convert_file "$SCRIPT_DIR/architecture/Overview.md" "$OUTPUT_DIR/architecture/Overview.xwiki"
convert_file "$SCRIPT_DIR/architecture/Message-Pump.md" "$OUTPUT_DIR/architecture/Message-Pump.xwiki"
convert_file "$SCRIPT_DIR/architecture/Thread-Registry.md" "$OUTPUT_DIR/architecture/Thread-Registry.xwiki"
convert_file "$SCRIPT_DIR/architecture/Shared-Backend.md" "$OUTPUT_DIR/architecture/Shared-Backend.xwiki"

# Convert reference docs
convert_file "$SCRIPT_DIR/reference/Configuration.md" "$OUTPUT_DIR/reference/Configuration.xwiki"
convert_file "$SCRIPT_DIR/reference/Handler-Contract.md" "$OUTPUT_DIR/reference/Handler-Contract.xwiki"
convert_file "$SCRIPT_DIR/reference/CLI.md" "$OUTPUT_DIR/reference/CLI.xwiki"

# Convert tutorials
convert_file "$SCRIPT_DIR/tutorials/Hello-World.md" "$OUTPUT_DIR/tutorials/Hello-World.xwiki"

echo ""
echo "Conversion complete!"
echo ""
echo "Files created in: $OUTPUT_DIR"
echo ""
echo "Next steps:"
echo "  1. Review the converted files"
echo "  2. Upload to XWiki via REST API or import feature"
echo ""
echo "XWiki REST API example:"
echo "  curl -u admin:password -X PUT \\"
echo "    'https://xml-pipeline.org/rest/wikis/xwiki/spaces/Docs/pages/Home' \\"
echo "    -H 'Content-Type: text/plain' \\"
echo "    -d @$OUTPUT_DIR/Home.xwiki"
