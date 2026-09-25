targetScope = 'subscription'

@description('The azd environment name. Keep it stable between deployments.')
@minLength(1)
@maxLength(64)
param environmentName string

@description('Deployment region from a curated shortlist; verify feature availability and live capacity.')
@allowed([
  'swedencentral'
  'eastus'
  'westeurope'
  'northeurope'
])
@metadata({
  azd: {
    type: 'location'
    default: 'swedencentral'
  }
})
param location string

var resourceToken = uniqueString(subscription().id, 'azure-translator-recipes', environmentName, location)
var translatorName = 'translator-recipes-${resourceToken}'
var tags = {
  'azd-env-name': environmentName
  workload: 'azure-translator-recipes'
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-translator-recipes-${resourceToken}'
  location: location
  tags: tags
}

module translator 'br/public:avm/res/cognitive-services/account:0.19.1' = {
  scope: resourceGroup
  params: {
    name: translatorName
    location: location
    kind: 'TextTranslation'
    sku: 'S1'
    customSubDomainName: translatorName
    // The local notebook authenticates with an API key over the public global endpoint.
    disableLocalAuth: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
    enableTelemetry: false
    tags: tags
  }
}

@description('The region containing the Translator account.')
output AZURE_TRANSLATOR_REGION string = location

@description('The resource group managed by this azd environment.')
output AZURE_TRANSLATOR_RESOURCE_GROUP string = resourceGroup.name

@description('The Translator account name, used when retrieving a key separately.')
output AZURE_TRANSLATOR_NAME string = translator.outputs.name

@description('The endpoint reported by the Translator account. The notebook uses the global Text Translation endpoint instead.')
output AZURE_TRANSLATOR_ENDPOINT string = translator.outputs.endpoint

@description('The Translator account resource ID: an RBAC scope, and the client resource_id for Microsoft Entra ID on the global endpoint.')
output AZURE_TRANSLATOR_RESOURCE_ID string = translator.outputs.resourceId
