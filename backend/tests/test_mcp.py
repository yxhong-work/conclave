import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mcp_tools import googleMaps, webSearch, threads, realPing, openaiTools


async def run_openai_prompt(prompt, selected_mcp):
    result = await asyncio.to_thread(openaiTools, prompt, selected_mcp)
    if result is not None:
        print(result)
    return result


if __name__ == "__main__":
    # 可以一個 list 同時放多個 MCP。
    # asyncio.run(
    #     run_openai_prompt(
    #         "請搜索關於Tim Cook的消息。",
    #         [threads],
    #     )
    # )

    # asyncio.run(
    #     run_openai_prompt(
    #         "尋找台灣最接近的三屆大選的日期以及大選時所發生的重大事件。",
    #         [webSearch],
    #     )
    # )

    # asyncio.run(
    #     run_openai_prompt(
    #         "幫我尋找苗栗靠近火車站的西餐廳，價格約一人一千。",
    #         [googleMaps,webSearch],
    #     )
    # )

    asyncio.run(
        run_openai_prompt(
            "臺北市大安區最貴的公寓每坪多少錢，最低的價格又是多少？",
            [realPing],
        )
    )
