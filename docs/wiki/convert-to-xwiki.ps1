# Convert Markdown wiki docs to XWiki format using Pandoc
#
# Prerequisites:
#   - Pandoc installed (https://pandoc.org/installing.html)
#   - Run from docs/wiki directory
#
# Usage:
#   .\convert-to-xwiki.ps1
#
# Output:
#   Creates xwiki/ directory with converted files

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutputDir = Join-Path $ScriptDir "xwiki"

Write-Host "Converting Markdown to XWiki format..."
Write-Host "Output directory: $OutputDir"
Write-Host ""

# Create output directory structure
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OutputDir "architecture") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OutputDir "reference") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OutputDir "tutorials") | Out-Null

# Function to convert a single file
function Convert-File {
    param (
        [string]$Input,
        [string]$Output
    )

    if (Test-Path $Input) {
        Write-Host "Converting: $Input"
        pandoc -f markdown -t xwiki $Input -o $Output
    }
}

# Convert root level docs
Convert-File (Join-Path $ScriptDir "Home.md") (Join-Path $OutputDir "Home.xwiki")
Convert-File (Join-Path $ScriptDir "Installation.md") (Join-Path $OutputDir "Installation.xwiki")
Convert-File (Join-Path $ScriptDir "Quick-Start.md") (Join-Path $OutputDir "Quick-Start.xwiki")
Convert-File (Join-Path $ScriptDir "Writing-Handlers.md") (Join-Path $OutputDir "Writing-Handlers.xwiki")
Convert-File (Join-Path $ScriptDir "LLM-Router.md") (Join-Path $OutputDir "LLM-Router.xwiki")
Convert-File (Join-Path $ScriptDir "Why-XML.md") (Join-Path $OutputDir "Why-XML.xwiki")

# Convert architecture docs
Convert-File (Join-Path $ScriptDir "architecture\Overview.md") (Join-Path $OutputDir "architecture\Overview.xwiki")
Convert-File (Join-Path $ScriptDir "architecture\Message-Pump.md") (Join-Path $OutputDir "architecture\Message-Pump.xwiki")
Convert-File (Join-Path $ScriptDir "architecture\Thread-Registry.md") (Join-Path $OutputDir "architecture\Thread-Registry.xwiki")
Convert-File (Join-Path $ScriptDir "architecture\Shared-Backend.md") (Join-Path $OutputDir "architecture\Shared-Backend.xwiki")

# Convert reference docs
Convert-File (Join-Path $ScriptDir "reference\Configuration.md") (Join-Path $OutputDir "reference\Configuration.xwiki")
Convert-File (Join-Path $ScriptDir "reference\Handler-Contract.md") (Join-Path $OutputDir "reference\Handler-Contract.xwiki")
Convert-File (Join-Path $ScriptDir "reference\CLI.md") (Join-Path $OutputDir "reference\CLI.xwiki")

# Convert tutorials
Convert-File (Join-Path $ScriptDir "tutorials\Hello-World.md") (Join-Path $OutputDir "tutorials\Hello-World.xwiki")

Write-Host ""
Write-Host "Conversion complete!"
Write-Host ""
Write-Host "Files created in: $OutputDir"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Review the converted files"
Write-Host "  2. Upload to XWiki via REST API or import feature"
Write-Host ""
Write-Host "XWiki REST API example:"
Write-Host "  curl -u admin:password -X PUT ``"
Write-Host "    'https://xml-pipeline.org/rest/wikis/xwiki/spaces/Docs/pages/Home' ``"
Write-Host "    -H 'Content-Type: text/plain' ``"
Write-Host "    -d @$OutputDir\Home.xwiki"
