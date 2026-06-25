from logging import config
from typing import Any
from strands import Agent, tool
import asyncio
import msal
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model
from mcp_client.client import get_streamable_http_mcp_client

app = BedrockAgentCoreApp()
log = app.logger

# Define a Streamable HTTP MCP Client
mcp_clients = [get_streamable_http_mcp_client()]

DEFAULT_SYSTEM_PROMPT = """
You are a helpful assistant. Use tools when appropriate.

"""


# Define a collection of tools used by the model
tools = []

_INLINE_FUNCTION_NAMES = set()

# Define a simple function tool
@tool
def add_numbers(a: int, b: int) -> int:
    """Return the sum of two numbers"""
    return a+b
tools.append(add_numbers)



# Add MCP client to tools if available
for mcp_client in mcp_clients:
    if mcp_client:
        tools.append(mcp_client)


def _make_conversation_manager():
    return NullConversationManager()

_agent = None

def get_or_create_agent():
    global _agent
    if _agent is None:
        _agent = Agent(
            model=load_model(),
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            tools=tools,
            conversation_manager=_make_conversation_manager(),
            hooks=[
            ],
        )
    return _agent


def _extract_prompt(payload: dict):
    """Accept harness-style messages[], tool_results[], or plain prompt string payloads."""
    if "messages" in payload:
        return payload["messages"]
    if "tool_results" in payload:
        return [{"role": "user", "content": [{"toolResult": {
            "toolUseId": tr["toolUseId"],
            "status": tr.get("status", "success"),
            "content": tr.get("content", []),
        }} for tr in payload["tool_results"]]}]
    return payload.get("prompt", "")


def _has_inline_function_call(messages) -> bool:
    """Return True if messages contains an assistant toolUse for an inline function tool."""
    if not _INLINE_FUNCTION_NAMES or not isinstance(messages, list):
        return False
    for msg in messages:
        if msg.get("role") == "assistant":
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("toolUse", {}).get("name") in _INLINE_FUNCTION_NAMES:
                    return True
    return False


def _is_inline_function_call(event: dict) -> bool:
    """Check if a contentBlockStart event is for an inline function tool."""
    if not _INLINE_FUNCTION_NAMES:
        return False
    cbs = event.get("contentBlockStart", {})
    start = cbs.get("start", {})
    tool_use = start.get("toolUse") if isinstance(start, dict) else None
    return tool_use is not None and tool_use.get("name") in _INLINE_FUNCTION_NAMES



def _extract_inbound_token(context: Any = None) -> str:
    """
    Extract the inbound user JWT from the `Authorization` header AgentCore forwards.

    AgentCore delivers the validated user token in the `Authorization`
    header (the runtime's `RequestHeaderAllowlist` includes `Authorization`).
    The 'Bearer ' prefix is stripped so a raw JWT is ready for MSAL OBO.
    """
    raw = ""
    headers = getattr(context, "request_headers", None) or {}
    
    for key, val in headers.items():
        if isinstance(val, str) and val.strip() and key.lower() == "authorization":
            raw = val.strip()
            break

    # MSAL acquire_token_on_behalf_of expects the raw JWT, not "Bearer {jwt}"
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
        log.info("****** Authorization header contains a Bearer token.")
    else:
        log.warning("****** Bearer token not found in Authorization header.")

    return raw

@app.entrypoint
async def invoke(payload, context):
    log.info("****** Invoking Agent.....")

    """ Get the bearer token from the inbound request."""
    _inbound_user_token = _extract_inbound_token(context)

    # MSAL OBO flow parameters
    # All configuration values are currently hard-coded for demonstration purposes 
    # and should be externalized before production use.

    tenant_id = "61ce3eb4-4692-48a4-9af5-a63f5be45418" # Microsoft Entra tenant ID
    client_id = "12fc29c0-ad16-49b4-be9a-e4a5a91ef628"  # Agent Identity Blueprint ID
    client_secret = ""  # Agent Identity Blueprint secret
    agent_identity_id = "08920e81-610f-4260-b0d7-06595ead6d10"  # Agent identity ID (NOT the blueprint)
    scopes = ["api://80faa936-de73-4923-b2fa-8d38723cc4fc/mymcp.read"]  # Scopes for the downstream API
    scopesForApp = ["api://80faa936-de73-4923-b2fa-8d38723cc4fc/.default"]  # Scopes for the downstream API (for client credentials flow)    

    # Acquire token on behalf of using MSAL
    obo_token = None
    if _inbound_user_token:
        try:

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
            
            if "access_token" in t1_result:
                t1_token = t1_result["access_token"]
                log.info("****** Successfully acquired T1 token.")
            else:
                error_desc = t1_result.get("error_description", "Unknown error")
                log.error(f"****** Failed to acquire T1 token: {error_desc}")


             # Step 2: Initialize the MSAL ConfidentialClientApplication for the agent identity
            _agent_app = msal.ConfidentialClientApplication(
                client_id=agent_identity_id,
                client_credential={"client_assertion": t1_result["access_token"]},
                authority=f"https://login.microsoftonline.com/{tenant_id}"
            )

            # Step 2: Use the inbound token to acquire a token for the downstream API
            token_response = _agent_app.acquire_token_on_behalf_of(
                user_assertion=_inbound_user_token,
                scopes=scopes
            )

            if "access_token" in token_response:
                obo_token = token_response["access_token"]
                log.info("****** Successfully acquired OBO token.")
                log.info(f"****** OBO Token: {obo_token}")
            else:
                error_desc = token_response.get("error_description", "Unknown error")
                log.error(f"****** Failed to acquire OBO token: {error_desc}")

            # Step 2: Token acquisition for the downstream API using client credentials flow (if needed)
            autonomous_token_response = _agent_app.acquire_token_for_client(scopes=scopesForApp)

            if "access_token" in autonomous_token_response:
                autonomous_token = autonomous_token_response["access_token"]
                log.info("****** Successfully acquired app token for downstream API.")
                log.info(f"****** App Token: {autonomous_token}")
            else:
                error_desc = autonomous_token_response.get("error_description", "Unknown error")
                log.error(f"****** Failed to acquire app token for downstream API: {error_desc}")

        except Exception as e:
            log.error(f"****** Exception during token acquisition: {str(e)}")
    else:
        log.warning("****** No inbound token available for flow.")

    agent = get_or_create_agent()

    prompt = _extract_prompt(payload)


    async for event in agent.stream_async(
        prompt,
    ):
        if not isinstance(event, dict) or "event" not in event:
            continue
        cbs = event["event"].get("contentBlockStart")
        if cbs is not None and not cbs.get("start"):
            continue
        yield event


if __name__ == "__main__":
    app.run()
