"""Exercise the live MCP protocol, central API and worker together."""
import asyncio
import json
import os
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

EXPECTED_TOOLS = {
    'list_resources',
    'list_nodes',
    'list_models',
    'run_inference',
    'submit_job',
    'wait_for_job',
    'list_jobs',
    'get_job',
    'explain_wait',
    'cancel_job',
    'retry_job',
    'get_energy_analytics',
    'get_scheduler_state',
    'simulate_dispatch',
    'manage_model',
    'set_default_runtime',
    'set_node_maintenance',
}


async def main():
    url = os.getenv('MCP_URL', 'http://mcp:8001/mcp')
    token = os.getenv('MCP_API_KEY', '').strip()
    headers = {'Authorization': f'Bearer {token}'} if token else None

    health_url = url.rsplit('/mcp', 1)[0] + '/health'
    async with httpx.AsyncClient(timeout=5.0) as client:
        health = await client.get(health_url)
        assert health.status_code == 200, health.text
        assert health.json()['api_reachable'] is True, health.json()

    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Verify full expanded tool surface
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            assert names == EXPECTED_TOOLS, f'Unexpected tools: {names ^ EXPECTED_TOOLS}'

            # Verify resources & prompts
            resources_list = await session.list_resources()
            uris = {str(r.uri) for r in resources_list.resources}
            assert {'gpu://overview', 'gpu://nodes', 'gpu://models'}.issubset(uris), uris
            ov_res = await session.read_resource('gpu://overview')
            assert json.loads(ov_res.contents[0].text)['overview']['online_nodes'] >= 0

            prompts_list = await session.list_prompts()
            prompt_names = {p.name for p in prompts_list.prompts}
            assert {'cluster_health_check', 'run_local_llm'}.issubset(prompt_names), prompt_names

            async def call(name, args=None):
                result = await session.call_tool(name, args or {})
                assert not result.isError, result
                assert result.structuredContent is not None, result
                return result.structuredContent

            resources = await call('list_resources')
            nodes_summary = await call('list_nodes', {'compact': True})
            assert nodes_summary['count'] == len(resources['nodes'])

            models_res = await call('list_models', {'only_available': True})
            model = next(m for m in models_res['models'] if m.get('model_type', 'llm') == 'llm')
            demo = model['id'] == 'demo.progress'

            # 1) Sync one-shot run_inference (or submit+wait for demo)
            if not demo:
                inf = await call('run_inference', {
                    'name': 'MCP sync inference smoke test',
                    'source': 'mcp-smoke',
                    'model': model['id'],
                    'prompt': '請用繁體中文說你好。',
                    'max_tokens': 16,
                    'timeout_seconds': 90,
                })
                assert inf['completed'] is True, inf
                assert inf['text'], inf
                assert inf['result']['simulated'] is False
                assert inf['result']['model_released'] is True
            else:
                job = await call('submit_job', {
                    'name': 'MCP inference smoke test',
                    'source': 'mcp-smoke',
                    'model': model['id'],
                    'steps': 5,
                })
                waited = await call('wait_for_job', {'job_id': job['id'], 'timeout_seconds': 90})
                assert waited['completed'] is True, waited
                assert waited['result']['simulated'] is True

            # 2) Async submit -> explain_wait -> cancel -> retry -> cancel
            other = await call('submit_job', {
                'name': 'MCP cancel smoke test',
                'source': 'mcp-smoke',
                'model': model['id'],
                'prompt': 'Count from 1 to 20.',
                'max_tokens': 32,
                'steps': 120,
            })
            explanation = await call('explain_wait', {'job_id': other['id']})
            assert explanation['job_id'] == other['id']
            await call('cancel_job', {'job_id': other['id']})
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                saved = await call('get_job', {'job_id': other['id']})
                if saved['status'] == 'cancelled':
                    break
                await asyncio.sleep(0.3)
            else:
                raise AssertionError('Cancellation was not acknowledged')

            retried = await call('retry_job', {'job_id': other['id']})
            assert retried['id'] != other['id']
            await call('cancel_job', {'job_id': retried['id']})

            # 3) list_jobs & energy analytics
            recent = await call('list_jobs', {'source': 'mcp-smoke', 'limit': 5})
            assert recent['count'] >= 1
            energy = await call('get_energy_analytics', {'range': '24h'})
            assert energy['range'] == '24h'

            # 4) Invalid UUID rejection
            invalid = await session.call_tool('get_job', {'job_id': '../nodes'})
            assert invalid.isError
            print(
                f"MCP PASS ({model['id']}): health, initialize, 17 tools, 3 resources, 2 prompts, "
                'sync inference, explain, cancel, retry, energy, invalid-id rejection'
            )


if __name__ == '__main__':
    asyncio.run(main())
