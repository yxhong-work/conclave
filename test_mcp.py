import asyncio

from backend.app.mcp_tools import apify_threads_post, run_openai_prompt, web_search, maps, realping

# 可以一個list 同時放多個mcp

asyncio.run(
    run_openai_prompt(
        "請搜索關於joeman的最新貼文。",
        [apify_threads_post],
    )
)

asyncio.run(
    run_openai_prompt(
        "Search news about iPhone Duo.",
        [web_search],
    )
)

asyncio.run(
    run_openai_prompt(
        "苗栗最好的西餐廳。",
        [maps],
    )
)

asyncio.run(
    run_openai_prompt(
        "臺北大安區租房15坪價格大概多少",
        [realping],
    )
)