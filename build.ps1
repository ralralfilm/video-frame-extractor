$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
python .\packaging\build.py
