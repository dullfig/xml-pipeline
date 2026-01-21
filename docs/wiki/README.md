# Wiki Documentation

This directory contains documentation for the xml-pipeline.org XWiki.

## Structure

```
wiki/
├── Home.md                      # Wiki home page
├── Installation.md              # Installation guide
├── Quick-Start.md               # Quick start guide
├── Writing-Handlers.md          # Handler guide
├── LLM-Router.md                # LLM router guide
├── Why-XML.md                   # Rationale for XML
├── architecture/
│   ├── Overview.md              # Architecture overview
│   ├── Message-Pump.md          # Message pump details
│   ├── Thread-Registry.md       # Thread registry details
│   └── Shared-Backend.md        # Shared backend details
├── reference/
│   ├── Configuration.md         # Full configuration reference
│   ├── Handler-Contract.md      # Handler specification
│   └── CLI.md                   # CLI reference
├── tutorials/
│   └── Hello-World.md           # Step-by-step tutorial
├── convert-to-xwiki.sh          # Bash conversion script
├── convert-to-xwiki.ps1         # PowerShell conversion script
└── README.md                    # This file
```

## Converting to XWiki Format

### Prerequisites

Install Pandoc: https://pandoc.org/installing.html

### Convert All Files

**Windows (PowerShell):**
```powershell
cd docs/wiki
.\convert-to-xwiki.ps1
```

**Linux/macOS (Bash):**
```bash
cd docs/wiki
chmod +x convert-to-xwiki.sh
./convert-to-xwiki.sh
```

### Output

Converted files are placed in `xwiki/` directory with `.xwiki` extension.

## Uploading to XWiki

### Option 1: XWiki REST API

```bash
# Upload a single page
curl -u admin:password -X PUT \
  'https://xml-pipeline.org/rest/wikis/xwiki/spaces/Docs/pages/Home' \
  -H 'Content-Type: text/plain' \
  -d @xwiki/Home.xwiki
```

### Option 2: XWiki Import

1. Go to XWiki Administration
2. Content → Import
3. Upload the files

### Option 3: Copy/Paste

1. Create page in XWiki
2. Switch to Wiki editing mode
3. Paste converted content

## Wiki Links

The Markdown files use `[[Page Name]]` wiki-link syntax. Pandoc converts these to XWiki's `[[Page Name]]` format.

If your XWiki uses different space structure, you may need to adjust links:

```
[[Installation]]           → [[Docs.Installation]]
[[architecture/Overview]]  → [[Docs.Architecture.Overview]]
```

## Updating Documentation

1. Edit the Markdown files in this directory
2. Run the conversion script
3. Upload to XWiki

Keep Markdown as the source of truth for version control.
