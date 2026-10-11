# Link every skill in skills/ into .claude/skills/ so Claude Code discovers it.
# Uses NTFS directory junctions (no admin rights needed). The links are gitignored:
# skills/ is the only copy under version control. Safe to re-run.
$root = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $root ".claude\skills"
New-Item -ItemType Directory -Force $dest | Out-Null
Get-ChildItem (Join-Path $root "skills") -Directory | ForEach-Object {
    $link = Join-Path $dest $_.Name
    if (Test-Path $link) {
        $item = Get-Item $link -Force
        if ($item.LinkType -ne "Junction" -and $item.LinkType -ne "SymbolicLink") {
            Write-Warning "$link exists and is not a link; leaving it alone"
            return
        }
        Write-Output "ok      $link"
    } else {
        New-Item -ItemType Junction -Path $link -Target $_.FullName | Out-Null
        Write-Output "linked  $link -> $($_.FullName)"
    }
}
