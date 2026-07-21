param(
    [Parameter(Mandatory = $true)][string]$Message,
    [string]$Title = "BetStats ingestion FAILED"
)
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show(
    $Message, $Title,
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Error
) | Out-Null
