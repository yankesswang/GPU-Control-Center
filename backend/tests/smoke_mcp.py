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
            model = next(m for m in resources['models'] if m.get('available'))
            demo = model['id'] == 'demo.progress'
            job = await call('submit_job', {'name': 'MCP inference smoke test', 'source': 'mcp-smoke', 'model': model['id'], 'prompt': '請用繁體中文說你好。', 'max_tokens': 16, 'steps': 5})
            explanation = await call('explain_wait', {'job_id': job['id']})
            assert explanation['job_id'] == job['id']
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                saved = await call('get_job', {'job_id': job['id']})
                if saved['status'] == 'completed':
                    assert saved['result']['simulated'] is demo
                    if not demo:
                        assert saved['result']['text']
                        assert saved['result']['model_released'] is True
                    break
                assert saved['status'] in ('queued', 'running'), saved
                await asyncio.sleep(0.5)
            else:
                raise AssertionError('Worker did not finish the MCP-created job')
            other = await call('submit_job', {'name': 'MCP cancel smoke test', 'source': 'mcp-smoke', 'model': model['id'], 'prompt': 'Count from 1 to 20.', 'max_tokens': 32, 'steps': 120})
            await call('cancel_job', {'job_id': other['id']})
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                saved = await call('get_job', {'job_id': other['id']})
                if saved['status'] == 'cancelled':
                    break
                await asyncio.sleep(0.3)
            else:
                raise AssertionError('Cancellation was not acknowledged')
            invalid = await session.call_tool('get_job', {'job_id': '../nodes'})
            assert invalid.isError
            print(f"MCP PASS ({model['id']}): initialize, 5 tools, real submit, explain, complete, cancel, invalid-id rejection")


if __name__ == '__main__':
    asyncio.run(main())
