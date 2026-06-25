# Secure AWS Agent Core with Microsoft Entra ID


This repo walks through the two main ways an agent can access data:

1. On-Behalf-Of (OBO). Here the agent works in the context of a signed-in user, so it can only ever touch what that user is already allowed to access and never more. You get the token by calling MSAL's `acquire_token_on_behalf_of`, handing it the inbound user token via `user_assertion` and the `scopes` you need.

1. Application-Only (client credentials). Now the agent acts purely as itself, with no user involved. This is the go-to for autonomous agents that operate on their own, or when the agent needs to access resources the user simply doesn't have. You get the token by calling MSAL's `acquire_token_for_client`, passing `scopes` as `your-resource/.default`. For example, `api://12345678-0000-1111-2222-111112222233/.default`.

## Create agent blueprint and agent identity

To create an agent identity blueprint, follow these steps:

1. Sign in to the [Microsoft Entra admin center](https://entra.microsoft.com) with your admin account.
1. From the menu, select **Agent ID**.
1. Select **Agent blueprints** and then select **Create agent blueprint**. 
   1. Enter a **name** for the agent blueprint and select **Next**.
   1. As you are the one to create the agent identity blueprint, you're the **owner** and **sponsor** of the blueprint. 
   1. Review and **create**. 
1.	Select **done** which will take you back to the list of blueprints.
1. The new blueprint is created with an agent identity. You can find it under **Linked agent identities**. or select Agent identities from the menu and locate the one you just created.

## Add delegated permissions 

For agents operating on behalf of a user, the blueprint requires an identifier URI and at least one scope (delegated permission). The identifier URI is a globally unique URI that identifies the agent identity blueprint and serves as the prefix its scope names. Your agent client application will use this scope later to request access.

1. From the menu, select “manifest”.
1. Locate the **identifierUris** and copy its value. It’s usually starts with `api://` followed by your application ID. For example, `api://4444444-0000-0000-0000-000000444444`.
1.	Next, define a scope which is a delegated permission. 
1.	Locate the **oauth2PermissionScopes** attribute under **api**.
1.	Set its value with a permission `access_agent`, and generate a globally unique ID (GUID). Here is an example (remember to generate a new GUID)

    ```json
    {
        "id": "1111111-0000-0000-0000-000000111111",
        "adminConsentDisplayName": "Allow the application to access the agent on behalf of the singed-in user.",
        "adminConsentDescription": "Access agent",
        "value": "access_agent",
        "type": "User",
        "isEnabled": true
    }
    ```

1. Take note of the scope's fully qualified URI. It is composed of the agent identity blueprint's **identifierUris** value, followed by the scope name. For example, `api://44444444-0000-0000-0000-000000444444/access_agent`. This value is required when configuring the client application that requests the scope.

## Agent blueprint credentials

When an agent requests an access token from Microsoft Entra ID, it must authenticate to prove the request is legitimate. This is done by presenting a client credential, which can be a **client secret**, a **certificate**, or a **federated identity credential (FIC)**. This demo uses a client secret because it's the quickest to set up.

1. **Select Certificates & secrets** > **Client secrets** > **New client secret**.
1. Add a **description** for your client secret.
1. Select an **expiration** for the secret or specify a custom lifetime, thn select **Add**.
1. Record the **client secret Value** for use in your agent. This secret value is never displayed again after you leave this page.

⚠️ A client secret is intended for local development and testing only. Before going to production, switch to a more secure credential like a certificate, or (recommended) a managed identity used as a federated identity credential, which removes stored secrets entirely.

## Register your application

An interactive agent starts with a user interface. The front door through which users interact with it. This can be a mobile, desktop, web, or single-page application. This application must be registered in [Microsoft Entra ID](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app). 

For the agent to access resources on behalf of a signed-in user, you also need granted your application permission to the blueprint. Specifically, the `access_agent` scope you configured in the previous step. After you registered your client application, follow these steps:

1. From the menu, select **API permissions**.
1. Then **Add a permission** and select **APIs my organization uses**. You may also find it under the **My APIs**.
1. Select your agent identity blueprint.
1. From the list of permission, select the `access_agent`.
1. Select **Add permissions** to complete the process.
1. Finally, select the **Grant admin consent for {your tenant}**, and then select **Yes**.

With the agent identity blueprint, the agent identity, and the client application now configured, the next step is to configure authentication within the client application. This procedure is well documented, so it is omitted here. One important note: when the client application requests an access token to call AWS Bedrock AgentCore, the requested scope must be the one you copied earlier. For example, `api://44444444-0000-0000-0000-000000444444/access_agent`.

# AWS Bedrock AgentCore Code-based agent

Follow the [guidance to create Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-get-started-cli.html). In this demo we use direct code deployment (the code-zip option) rather than the Container option, but the configuration steps are largely the same.

## Configure inbound JWT authorizer

The client application passes a bearer token (the one carrying the `api://44444444-0000-0000-0000-000000444444/access_agent` scope). This access token serves two purposes:  it gets the client app past [inbound JWT authorizer](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/inbound-jwt-authorizer.html) on AgentCore, and it can be exchanged (via the On-Behalf-Of flow) for a new token to call your MCP server. 

AgentCore also needs the inbound access token so the agent can exchange it for a new one. By default the runtime doesn't forward the Authorization header to your agent code, you have to [allow list it](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html). To do so, open [agentcore/agentcore.json](./agentcore/agentcore.json) and add `authorizerConfiguration` and the `requestHeaderAllowlist` to the `runtimes` collection.

```json
{
  "runtimes": [
    {
      "name": "MyAgent1",
      ...
      "authorizerConfiguration": {
        "customJwtAuthorizer": {
          "discoveryUrl": "https://login.microsoftonline.com/replace-with-your-tenant-ID/v2.0/.well-known/openid-configuration",
          "allowedAudience": [
            "{replace with your blueprint ID}"
          ],
          "allowedScopes": [
            "access_agent"
          ]
        }
      },
      "requestHeaderAllowlist": [
        "Authorization"
      ]
    }
  ]
}
```

## Test your agent

To test your agent, first obtain an access token with the `api://44444444-0000-0000-0000-000000444444/access_agent` scope (replace the GUID with your blueprint ID). Then pass the token directly on the command line when you invoke the agent.

```cli
agentcore invoke --prompt "What is the capital of Germany" --bearer-token "add an access token here".
```

# The source code

To keep things simple, all parameters in this sample are hardcoded. Replace them with values from your own environment. Note also that production code should cache the acquired tokens to avoid a full token exchange on every request and use more secure credential like a certificate, or (recommended) a managed identity used as a federated identity credential. Please check out [this example](https://github.com/astaykov/agentid-agentcore/blob/021a060084a9f84d12dff95182653de7464237fd/agent/src/agent.py). 

## Get the bearer token

The code starts by getting the bearer token from the inbound request:

```
_inbound_user_token = _extract_inbound_token(context)
```
## MSAL parameters

Configure the MSAL parameters:

- `tenant_id` = "Replace with the your Microsoft Entra tenant ID" 
- `client_id` = "Replace with the Agent Identity Blueprint ID"   
- `client_secret` = "Replace with the Agent Identity Blueprint secret"  
- `agent_identity_id` = "Replace with the Agent identity ID (NOT the blueprint)"  
- `scopes` = ["Replace with the Scopes for the downstream API"]  
- `scopesForApp` = ["Replace with the Scopes for the downstream API/.default"]  # Scopes for the downstream API (for client credentials flow) .

## Bluerint confidential client

The agent identity blueprint authenticates by presenting its client credential, which may be a client secret, a certificate, or a managed identity token (used as a federated identity credential). Microsoft Entra ID validates the credential and returns the exchange token `T1`. The `T1` serves as the client assertion in the subsequent step, where it is exchanged for either a user token (via the On-Behalf-Of flow) or an app-only token (via the client credentials flow).

```python
# Step 1: Initialize the MSAL ConfidentialClientApplication for the blueprint app
_blueprint_app = msal.ConfidentialClientApplication(
    client_id=client_id,
    client_credential=client_secret,
    authority=f"https://login.microsoftonline.com/{tenant_id}"
)

# Step 1: Acquire a token for the blueprint app
t1_result = _blueprint_app.acquire_token_for_client(
    scopes=["api://AzureADTokenExchange/.default"],
    fmi_path=agent_identity_id,
)
```

In the next step, you initialize the MSAL ConfidentialClientApplication representing the agent identity (not the blueprint), supplying `T1` as the `client_assertion`.

```python
# Step 2: Initialize the MSAL ConfidentialClientApplication for the agent identity
_agent_app = msal.ConfidentialClientApplication(
client_id=agent_identity_id,
client_credential={"client_assertion": t1_result["access_token"]},
authority=f"https://login.microsoftonline.com/{tenant_id}"
)
```

Once `_agent_app` is initialized, the agent can acquire a downstream token On-Behalf-Of flow (`acquire_token_on_behalf_of`), passing the inbound user access token as the `user_assertion` together with the target `scopes`

```python
# Step 2: Use the inbound token to acquire a token for the downstream API
token_response = _agent_app.acquire_token_on_behalf_of(
user_assertion=_inbound_user_token,
scopes=scopes
)
```

The agent can also aquire access token using the client credentials flow (`acquire_token_for_client`), obtaining an application-only token with no user context.

```python
# Step 2: Token acquisition for the downstream API using client credentials flow (if needed)
autonomous_token_response = _agent_app.acquire_token_for_client(scopes=scopesForApp)
```



## Project Structure

This project was created with the [AgentCore CLI](https://github.com/aws/agentcore-cli).

```
my-project/
├── AGENTS.md               # AI coding assistant context
├── agentcore/
│   ├── agentcore.json      # Project config (agents, memories, credentials, gateways, evaluators)
│   ├── aws-targets.json    # Deployment targets (account + region)
│   ├── .env.local          # Secrets — API keys (gitignored)
│   ├── .llm-context/       # TypeScript type definitions for AI assistants
│   │   ├── agentcore.ts    # AgentCoreProjectSpec types
│   │   ├── aws-targets.ts  # Deployment target types
│   │   └── mcp.ts          # Gateway and MCP tool types
│   └── cdk/                # CDK infrastructure (@aws/agentcore-cdk)
├── app/                    # Agent application code
└── evaluators/             # Custom evaluator code (if any)
```


## Documentation

- [AgentCore CLI](https://github.com/aws/agentcore-cli)
- [AgentCore CDK Constructs](https://github.com/aws/agentcore-l3-cdk-constructs)
- [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)
