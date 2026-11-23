[CmdletBinding()]
param(
    [string]$EnvironmentName = "iq-demo",
    [string]$Python = "python",
    [switch]$AgentOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE"
    }
}

Push-Location $PSScriptRoot
try {
    $output = & terraform output -json configuration
    if ($LASTEXITCODE -ne 0) { throw "Apply Terraform before deploying the agent." }
    $config = $output | ConvertFrom-Json
    $location = & az resource show --ids $config.project_id --api-version 2025-06-01 --query location --output tsv
    if ($LASTEXITCODE -ne 0) { throw "Cannot resolve the existing Foundry project location." }
    if (-not $AgentOnly) {
        Invoke-Checked $Python @("$PSScriptRoot\configure_search.py", "--connections")
    }
}
finally { Pop-Location }

$previousUserAgent = $env:AZURE_DEV_USER_AGENT
Push-Location $root
try {
    # Never persist this development-only setting to the azd environment.
    $env:AZURE_DEV_USER_AGENT = "microsoft_foundry_skill"
    if (Test-Path ".azure\$EnvironmentName\.env") {
        Invoke-Checked azd @("env", "select", $EnvironmentName)
    }
    else {
        Invoke-Checked azd @("env", "new", $EnvironmentName, "--no-prompt")
    }
    $settings = @{
        AZURE_SUBSCRIPTION_ID = $config.subscription_id
        AZURE_TENANT_ID = $config.tenant_id
        AZURE_RESOURCE_GROUP = $config.resource_group
        AZURE_LOCATION = $location
        AZURE_AI_PROJECT_ID = $config.project_id
        AZURE_AI_PROJECT_ENDPOINT = $config.project_endpoint
        FOUNDRY_PROJECT_ENDPOINT = $config.project_endpoint
        AZURE_AI_MODEL_DEPLOYMENT_NAME = $config.chat_deployment
        MODEL_ENDPOINT = "$($config.project_endpoint)/openai/v1/"
        CHAT_DEPLOYMENT = $config.chat_deployment
        AGENT_MODE = $(if ($AgentOnly) { "chat" } else { "tools" })
        TOOLBOX_ENDPOINT = "$($config.project_endpoint)/toolboxes/iq-tools/mcp?api-version=v1"
    }
    foreach ($setting in $settings.GetEnumerator()) {
        Invoke-Checked azd @("env", "set", $setting.Key, $setting.Value)
    }
    if (-not $AgentOnly) {
        Invoke-Checked azd @("deploy", "iq-tools", "--no-prompt")
    }
    Invoke-Checked azd @("deploy", "iq-agent", "--no-prompt")
    $agentOutput = & azd ai agent show iq-agent --output json
    if ($LASTEXITCODE -ne 0) { throw "Cannot resolve the deployed agent identity." }
    $principal = ($agentOutput | ConvertFrom-Json).instance_identity.principal_id
    if (-not $principal) { throw "The deployed agent has no instance identity." }
    $accountId = $config.project_id -replace "/projects/[^/]+$", ""
    # The instance identity exists only after deployment.
    $permissions = @(
        @{ Scope = $accountId; Role = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd" }
        @{ Scope = $config.project_id; Role = "53ca6127-db72-4b80-b1b0-d745d6d5456d" }
    )
    foreach ($permission in $permissions) {
        $scope = $permission.Scope
        $role = $permission.Role
        $existing = & az role assignment list --scope $scope --query "[?principalId=='$principal' && ends_with(roleDefinitionId, '$role')].id" --output tsv
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect the agent permissions on $scope." }
        if (-not $existing) {
            Invoke-Checked az @(
                "role", "assignment", "create",
                "--assignee-object-id", $principal,
                "--assignee-principal-type", "ServicePrincipal",
                "--role", $role, "--scope", $scope, "--output", "none"
            )
            Write-Host "Granted agent access on $scope. Allow RBAC propagation before invoking."
        }
    }
}
finally {
    $env:AZURE_DEV_USER_AGENT = $previousUserAgent
    Pop-Location
}
