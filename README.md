# Azure Translator Recipes

`notebook.ipynb` contains a recipe for every public entry point of
`TextTranslationClient` in the version of `azure-ai-translation-text` pinned in
`requirements.txt`, and its recipes demonstrate every field of the request
models those entry points accept. The project also includes Bicep
infrastructure, Azure Developer CLI configuration, SDK-surface validation, and
automated SDK release monitoring.

## Prerequisites

- Python 3.14
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
- An Azure subscription in which you can create resource groups and Azure AI
  services

## Set up the project

Create and activate a virtual environment, then install the pinned SDK:

```sh
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Sign in and deploy the regional Translator resource:

```sh
az login
azd auth login
azd up
```

Load the deployment outputs and retrieve a key without storing it in the
repository:

```sh
export AZURE_TRANSLATOR_REGION="$(azd env get-value AZURE_TRANSLATOR_REGION)"
export AZURE_TRANSLATOR_RESOURCE_GROUP="$(azd env get-value AZURE_TRANSLATOR_RESOURCE_GROUP)"
export AZURE_TRANSLATOR_NAME="$(azd env get-value AZURE_TRANSLATOR_NAME)"
export AZURE_TRANSLATOR_KEY="$(
  az cognitiveservices account keys list \
    --resource-group "$AZURE_TRANSLATOR_RESOURCE_GROUP" \
    --name "$AZURE_TRANSLATOR_NAME" \
    --query key1 \
    --output tsv
)"
```

Start the notebook from an environment that has those variables. The client
uses the global Text Translation endpoint with the resource's key and region.
The notebook contains live service calls and can incur Azure charges.

## Recipes

`TextTranslationClient` has few methods because Text Translation API version
`2026-06-06` has three operations: languages, translate, and transliterate.
`send_request` and `close` complete the client. Most features are options on
the request models, so the notebook has several `translate` recipes:

| Recipe | Demonstrates | Needs |
| --- | --- | --- |
| Translating text | String inputs and several target languages | The provisioned resource |
| Translating with input and target options | HTML input, source and target scripts, profanity handling, and language detection | The provisioned resource |
| Translating with a custom model | A custom model's category ID and `allow_fallback` | A custom translation model published with the resource |
| Translating with an LLM deployment | `tone` and `gender` | A Microsoft Foundry resource with a model deployment |
| Adapting LLM translations to your domain | `reference_text_pairs` and `adaptive_dataset_id` | The Foundry deployment and an adaptive dataset |

Replace the `<your-...>` placeholders before you run those recipes. For the LLM
recipes, set `AZURE_TRANSLATOR_KEY` and `AZURE_TRANSLATOR_REGION` to the key
and region of the Microsoft Foundry resource. LLM translation isn't available
when the resource uses a private endpoint.

Dictionary lookup, dictionary examples, sentence boundaries, and word alignment
are Text Translation v3.0 features that SDK 2.0 removed. There's no separate
detection operation: `translate` detects the source language of any input that
doesn't specify one.

## Deployment region

The [Bicep template](./infra/main.bicep) provides a static regional shortlist:

| Region | Azure identifier | Suggestion |
| --- | --- | --- |
| Sweden Central | `swedencentral` | Default suggestion for a European deployment. |
| East US | `eastus` | US alternative. |
| West Europe | `westeurope` | European alternative. |
| North Europe | `northeurope` | Another European alternative. |

The template deploys a regional `TextTranslation` resource. The notebook
authenticates with that resource's key and region on the global endpoint, which
processes each request in the closest available location. These are starting
points, not guarantees of capacity, quota, subscription eligibility, or feature
availability. Review Microsoft's
[Translator region guidance](https://learn.microsoft.com/azure/ai-services/translator/region-support)
before deployment.

When `AZURE_LOCATION` is unset, `azd up` offers the shortlist with Sweden
Central as the suggested default. This is azd prompt metadata, not a Bicep
parameter default, so it does not bypass the picker. An existing environment
keeps its saved location.

The `@allowed` decorator **restricts** deployments to these four regions; it is
not merely a hint or a live availability query. Edit that list to permit another
region after checking its suitability.

To select a region explicitly:

```sh
azd env set AZURE_LOCATION eastus
azd up
```

Changing the region changes the deterministic resource names and creates new
resources rather than moving the existing ones.

For live account and SKU availability, sign in with the Azure CLI, select the
intended subscription, and run:

```sh
az cognitiveservices account list-skus \
  --kind TextTranslation \
  --query "[?name=='S1'].{regions:locations,restrictions:restrictions}" \
  --output json
```

This query does not guarantee spare capacity or availability of every
Translator capability.

## Validation

Check that the notebook still covers the installed client surface and request
models:

```sh
python scripts/check_sdk_coverage.py
```

The check is also run by GitHub Actions when the SDK pin, notebook, checker, or
workflow changes.
