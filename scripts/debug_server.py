import asyncio

import uvicorn


async def main():
    loop = asyncio.get_running_loop()
    print(f"Event loop: {type(loop).__name__}", flush=True)

    config = uvicorn.Config(
        "investigation_engine.infrastructure.api.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
    )

    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(
        main(),
        loop_factory=asyncio.SelectorEventLoop,
    )