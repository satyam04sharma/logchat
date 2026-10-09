"""A disposable keyless MCP server with explicitly read-only project knowledge."""
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings

server = MCPServer('logchat-demo', version='0.1.0')

@server.tool(annotations=ToolAnnotations(readOnlyHint=True,openWorldHint=False),structured_output=True)
def describe_demo() -> dict[str, object]:
    """Describe the synthetic project's services and intentional dev/prod differences."""
    return {'project':'checkout demo','services':['api'],'dev':'200ms successful requests',
            'prod':'Every fourth request has an 850ms database timeout',
            'note':'This is synthetic configuration knowledge, not pipeline log evidence.'}

@server.resource('demo://architecture')
def architecture() -> str:
    return 'Synthetic checkout API. Dev uses demo-v1; prod uses demo-v2. Docker prints disposable JSON events every two seconds. No customer logs or secrets.'

if __name__ == '__main__':
    server.run(transport='streamable-http',host='0.0.0.0',port=8090,
               transport_security=TransportSecuritySettings(allowed_hosts=['demo-mcp:8090','localhost:8090','127.0.0.1:8090']))
