# Hosted agent with Foundry IQ and Web IQ

A small technical demo: a **Python LangChain agent** hosted by **Foundry Hosted
Agents**, with document retrieval through **Foundry IQ** and public web retrieval
through **Microsoft Web IQ**. It combines evidence and cites its sources.
There is no business-specific workflow or web UI.

```text
Foundry Hosted Agent -> LangChain -> Foundry Toolbox (MCP)
                                    |-- Foundry IQ -> AI Search -> Blob documents
                                    `-- Web IQ -> public web
```

`src/agent.py` contains the agent. `src/main.py` uses the Microsoft LangGraph
hosting adapter, without a custom HTTP server. `deploy/` contains Terraform and
small setup scripts. Terraform owns Azure infrastructure; the Python setup
script owns Search data-plane objects and retrieval connections; Terraform also
owns the Application Insights project connection. `azd` publishes
the toolbox and hosted agent versions.

**Deployment status:** Terraform infrastructure is provisioned in
`rg-demo-lnca-maia-foundry-cgigax`: Foundry project/models, Search, Blob Storage,
Web IQ, workspace-based Application Insights and RBAC. Hosted agent `iq-agent`
version **8** is active in **tools mode**.
The `iq-tools` toolbox, Blob knowledge source, `documents` knowledge base and
both managed MCP connections are configured. A real hosted invocation retrieved
a synthetic document and live Web IQ results, then cited both source URLs.
The temporary test document and its indexed content were removed afterwards.
A maintenance corpus of 15 PDF/Word files has since been uploaded to the private
Blob container and indexed into 105 chunks. Those documents are not part of this
repository. A document-only hosted query about a site's specialization returned
a grounded answer and a citation to the uploaded PDF. Event Grid is unchanged.
Application Insights is connected and verified: a version 8 Web IQ invocation
produced a successful request with four correlated LangChain spans. The test
prompt and retrieved passage were absent from those spans.

## Prerequisites

Python 3.12 locally, Terraform 1.9+, Azure CLI, and Azure Developer CLI with the
`azure.ai.agents`, `azure.ai.projects`, and `azure.ai.toolboxes` extensions.
Hosted code uses the Python 3.13 runtime. The hosting SDK is a pinned preview;
`src/constraints.txt` keeps its transitive dependencies compatible.

You need resource creation and role-assignment permissions, model quota in
France Central, and a **paid, Web IQ-enabled subscription**. Azure credits and
Visual Studio subscriptions are not supported by Web IQ.

Both CLI logins are needed:

```powershell
az login
azd auth login
```

## 1. Provision infrastructure

Run from this sample folder. The existing RG is referenced, not created or
destroyed by Terraform.

```powershell
Copy-Item deploy\terraform.tfvars.example deploy\terraform.tfvars
# Set your subscription_id in deploy\terraform.tfvars.
az provider register --namespace Microsoft.WebIQ --wait
az provider register --namespace Microsoft.Insights --wait
az provider register --namespace Microsoft.OperationalInsights --wait
terraform -chdir=deploy init
terraform -chdir=deploy plan -out=demo.tfplan
terraform -chdir=deploy apply demo.tfplan
```

This creates a Foundry account/project, chat and embedding deployments, AI
Search S1 (one partition and one replica), private Blob container, managed
identities/RBAC, a global Web IQ billing resource, Application Insights and its
Log Analytics workspace. The project monitoring connection is created automatically.
Terraform inherits existing resource-group tags and serializes Foundry child
updates to avoid Azure operation conflicts.

Defaults are `gpt-4o-mini` with EU Data Zone Standard and
`text-embedding-3-large`, each allocated 50,000 tokens per minute to support
document ingestion and retrieval. These are pay-per-token deployments, not
provisioned throughput commitments. GPT-4o-mini is deprecated: check its lifecycle and
regional quota before deployment or choose a supported model using
`chat_model` in Terraform. Search uses its November 2026 preview contract.

## 2. Deploy

### Agent only (no retrieval tools)

To deploy the LangChain hosted agent without configuring Search, connections or
the toolbox:

```powershell
& deploy\deploy.ps1 -AgentOnly
```

This explicitly selects `AGENT_MODE=chat`. The agent can hold a conversation
using the existing model, but cannot search documents or the web and must say
so. No Web IQ key is needed. Model calls use the project's OpenAI-compatible
endpoint rather than calling the account-level endpoint directly. It does not
modify Event Grid or provision infrastructure.

The default `AGENT_MODE=tools` still requires both MCP tools and fails startup
if they are unavailable; there is no automatic fallback to chat mode.

### Full Foundry IQ and Web IQ integration

Get the Web IQ resource ID with `terraform -chdir=deploy output -json
configuration`. Connect that resource in the
[Web IQ Developer Portal](https://webiq.microsoft.ai/profiles), create its
AppID, and generate an API key with Web Search access.
This activation is not performed by Terraform.

```powershell
$venv = Join-Path $env:LOCALAPPDATA "foundry-iq-venv"
python -m venv $venv
$python = Join-Path $venv "Scripts\python.exe"
& $python -m pip install -r src\requirements.txt -r deploy\requirements.txt
& deploy\deploy.ps1 -Python $python
```

Use a short virtual-environment path on Windows to avoid dependency installation
failures caused by long file paths.

Without `-AgentOnly`, the script prompts for the Web IQ key without echoing it. It creates/updates
the Blob knowledge source, knowledge base and project connections, then deploys
the toolbox and agent. The key goes only into the managed Foundry connection,
not Terraform state, `azure.yaml`, command-line arguments, or Git.

If role assignments are still propagating, configuration can return 403.
Wait for propagation and rerun; errors are not silently ignored.
Configuration uses stable resource names. Republishing creates new immutable
toolbox/agent versions, not duplicate resources.

Terraform grants the project identity **Foundry User** on its parent Foundry
account so the project can proxy model inference. After deployment, the script
grants the agent instance identity **Cognitive Services OpenAI User** on that
account and **Foundry User** on this project for inference and toolbox access.
The instance identity is available only after the agent has been created; these
two assignments are managed by the deployment script, not Terraform.

## 3. Invoke

```powershell
azd ai agent invoke iq-agent "Hello. Which capabilities are enabled in this deployment?"
```

After deploying the full integration:

```powershell
azd ai agent invoke iq-agent "Search the web for current Azure AI Search documentation and cite its sources."
azd ai agent invoke iq-agent "What do the uploaded documents say about deployment?"
```

Without uploaded documents, the agent should explicitly report missing
document evidence. It should not substitute web evidence without saying so.
Once documents are indexed, ask a question combining their content with public
information to exercise both tools.

For local development, copy `src\.env.example` to `src\.env`, fill its
non-secret endpoints from Terraform, and run `src\main.py` with the virtual
environment's Python (`& $python src\main.py`). Only the two intended MCP tools are loaded. Structured
MCP evidence is exposed to the model, rather than hidden in tool artifacts.
Web IQ requests are capped at two passages of 1,200 characters each to fit the
demo model's small token quota; this sample is not a full-page web crawler.
The knowledge base also limits each retrieval to five output documents and a
5,000-token grounding budget. References remain available for citations.
Set `AGENT_MODE=chat` for local development without a toolbox. The model endpoint
is the project endpoint followed by `/openai/v1/`, not the account endpoint used
by the Search ingestion configuration.

## Observability

Terraform connects `appi-iq-a6fb988f` to project `iq-demo` through an `AppInsights`
connection. Foundry injects the connection string into hosted containers; the
SDK can also resolve it through the project. Do not copy it into `azure.yaml`
or Git. `terraform -chdir=deploy output -json observability` returns only
resource identifiers, including the Application Insights application ID.

The SDK exports request and LangChain spans with execution times, model/tool
names and token usage when available. The sample overrides its preview
adapter's content-recording default: prompts, responses and retrieved content
are redacted, and tool arguments are omitted. It reuses the host exporter rather
than configuring a second Azure Monitor exporter or LangSmith integration.
`AGENT_APP_INSIGHTS_ENABLED=false` disables export of SDK console messages,
which can contain document content; console logs remain available through
`azd ai agent monitor` and require appropriate access.

In Azure Portal, open Application Insights, then **Transaction search** or
**Logs**. Correlate `requests` and `dependencies` using `operation_Id`.
The SDK operation ID can differ from the gateway Trace ID printed by `azd`;
find the SDK request by `azure.ai.agentserver.response_id`, then use its
`operation_Id` to retrieve its child spans.
Allow a few minutes for ingestion after an invocation. Readers need monitoring
data access, such as **Log Analytics Reader** on the workspace. No new runtime
role assignment is needed to send telemetry through the managed connection.

The workspace retains data for 30 days and has a 1 GB/day ingestion cap. The cap
is a cost guardrail, not a guaranteed hard spending limit; hitting it can interrupt
telemetry collection. Azure Monitor ingestion and retention can incur charges.

To republish code without reconfiguring retrieval connections or entering the
Web IQ key again:

```powershell
azd env set AGENT_MODE tools
azd deploy iq-agent
azd ai agent show iq-agent
azd ai agent invoke iq-agent --version 8 --new-session "Reply with TELEMETRY_CHECK_OK. Do not use tools."
```

Replace `8` with the version returned by your deployment.

## Upload documents

Use the Storage account and container from Terraform's configuration output:

```powershell
az storage blob upload-batch --account-name YOUR-STORAGE-NAME --destination documents --source C:\demo-documents --auth-mode login
```

The generated Blob indexer runs every five minutes. To trigger it manually:

```powershell
Push-Location deploy
& $python configure_search.py --reindex
Pop-Location
```

Keep customer documents out of Git. Document references may point to private
Blob URLs: readers need their own authorization to open them. Service RBAC
does **not** provide per-user document filtering in this demo.

## Checks and cleanup

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
& $python -m unittest discover -s src\tests
& $python -m unittest discover -s deploy -p "test_*.py"
terraform -chdir=deploy validate
```

Cloud checks covered real Web IQ search, an empty Foundry IQ corpus, synthetic
Blob ingestion/retrieval, and a combined hosted query with document and web
citations. The synthetic data was removed afterwards. Local tests use fake tools
and a fake model; they also cover bounded Web IQ requests, evidence preservation,
tool errors, token refresh, session separation and the Hosted Responses adapter.
Remote multi-turn/session isolation and a formal evaluation suite are not yet
verified.
Conversation checkpoints are in memory and do not survive a runtime restart.

AI Search has ongoing capacity costs; Hosted sessions, model tokens and Web IQ
calls are billed separately, as is Azure Monitor telemetry. Stop unused hosted sessions in Foundry. To remove
the sample, review `terraform -chdir=deploy plan -destroy` before running
`terraform -chdir=deploy destroy`: **this deletes the sample's uploaded
documents**, but leaves the existing RG intact. Keep Terraform state private
and backed up. Do not use `azd down` to manage this Terraform infrastructure.

See [Foundry IQ](https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect),
[Hosted Agents](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents),
and [Web IQ MCP](https://learn.microsoft.com/web-iq/integrations/mcp).
