import traceback
import aiohttp
import asyncio
import json

async def log_to_openobserve(job, trace, level="info"):
    url = "https://api.openobserve.ai/api/2uTsBRWQsbOEDrUhez32jn1GHta/gogizmo/_json"
    auth = aiohttp.BasicAuth('jhajharia934@gmail.com', 'iC41n0I8e5XHr2d0')
    headers = {"Content-Type": "application/json"}

    log_entry = [{
        "level": level,
        "job": job,
        "log": trace
    }]

    async with aiohttp.ClientSession() as session:
        async with session.post(url, auth=auth, headers=headers, json=log_entry, ssl=False) as response:
            if response.status == 200:
                print("Log entry successfully sent.")
            else:
                print(f"Failed to send log entry. Status: {response.status}")


