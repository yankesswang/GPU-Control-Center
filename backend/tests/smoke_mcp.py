"""Exercise the live MCP protocol, central API and worker together."""
import asyncio
import os
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    url = os.getenv('MCP_URL', 'http://mcp:8001/mcp')
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            assert names == {'list_resources', 'submit_job', 'get_job', 'explain_wait', 'cancel_job'}, names

            async def call(name, args=None):
                result = await session.call_tool(name, args or {})
                assert not result.isError, result
                assert result.structuredContent is not None, result
                return result.structuredContent

            resources = await call('list_resources')
            assert resources['models'][0]['id'] == 'demo.progress'
            job = await call('submit_job', {'name': 'MCP protocol smoke test', 'source': 'mcp-smoke', 'steps': 5})
            explanation = await call('explain_wait', {'job_id': job['id']})
            assert explanation['job_id'] == job['id']
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                saved = await call('get_job', {'job_id': job['id']})
                if saved['status'] == 'completed':
                    assert saved['result']['simulated'] is True
                    break
                assert saved['status'] in ('queued', 'running'), saved
                await asyncio.sleep(0.5)
            else:
                raise AssertionError('Demo worker did not finish the MCP-created job')
            other = await call('submit_job', {'name': 'MCP cancel smoke test', 'source': 'mcp-smoke', 'steps': 120})
            await call('cancel_job', {'job_id': other['id']})
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                saved = await call('get_job', {'job_id': other['id']})
                if saved['status'] == 'cancelled':
                    break
                await asyncio.sleep(0.3)
            else:
                raise AssertionError('Cancellation was not acknowledged')
            invalid = await session.call_tool('get_job', {'job_id': '../nodes'})
            assert invalid.isError
            print('MCP PASS: initialize, 5 tools, submit, explain, complete, cancel, invalid-id rejection')


if __name__ == '__main__':
    asyncio.run(main())
