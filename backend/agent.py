import argparse
import asyncio
import logging
import os

import zmq
import zmq.asyncio
from dotenv import load_dotenv
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_tavily import TavilySearch
from langgraph.prebuilt import create_react_agent
from pydantic import SecretStr

load_dotenv()
logger = logging.getLogger(__file__)


class AgentStream:
    def __init__(
        self,
        model: Runnable,
        sample_rate: int,
        push_port: str,
        pull_port: str,
    ) -> None:
        self.model = model
        self.push_port = push_port
        self.pull_port = pull_port
        self.sample_rate = sample_rate
        self.online = False
        # ZeroMQ context
        self.context = zmq.asyncio.Context()
        self.push_socket = self.context.socket(zmq.PUSH)
        self.push_socket.bind(push_port)
        self.pull_socket = self.context.socket(zmq.PULL)
        self.pull_socket.connect(pull_port)

    async def start(self):
        if self.online:
            asyncio.create_task(self.run_online())
        else:
            asyncio.create_task(self.run_offline())

    async def run_online(self):
        raise NotImplementedError("Online Agent not implemented")

    async def run_offline(self):
        while True:
            query = await self.pull_socket.recv_pyobj()
            speaker_id, content = query["id"], query["content"]
            response = ""
            async for s in self.model.astream(
                {"messages": [("user", content)]}, stream_mode="values"
            ):
                message = s["messages"][-1]
                if isinstance(message, tuple):
                    logger.info(message)
                else:
                    logger.info(message.pretty_repr())
                response = message.content
            await self.push_socket.send_pyobj(
                {
                    "speaker_id": 0,
                    "speaker_name": "Assistant",
                    "segment_type": "assistant",
                    "segment_content": response,
                }
            )

    async def close(self):
        self.push_socket.close()
        self.pull_socket.close()


def setup_agent():
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp", api_key=SecretStr(os.getenv("GEMINI_API_KEY"))
    )
    tool = TavilySearch(
        max_results=5,
        topic="general",
        # include_answer=False,
        # include_raw_content=False,
        # include_images=False,
        # include_image_descriptions=False,
        # search_depth="basic",
        # time_range="day",
        # include_domains=None,
        # exclude_domains=None
    )
    tools = [tool]

    return create_react_agent(llm, tools=tools)


async def start_agent_stream(args) -> AgentStream:
    stream = AgentStream(
        setup_agent(),
        args.sample_rate,
        args.push_port,
        args.pull_port,
    )
    await stream.start()
    return stream


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Left parameters for speechllm down the line
    parser.add_argument("--sample_rate", type=int, default=16000, help="Sample rate")
    parser.add_argument(
        "--push_port", type=str, default="tcp://127.0.0.1:8008", help="ZeroMQ push port"
    )
    parser.add_argument(
        "--pull_port", type=str, default="tcp://127.0.0.1:8007", help="ZeroMQ pull port"
    )
    parser.add_argument("--docker", action="store_true", help="Docker serving, use DNS")
    args = parser.parse_args()
    if args.docker:
        args.push_port = os.environ.get("PUSH_PORT", args.push_port)
        args.pull_port = os.environ.get("PULL_PORT", args.pull_port)

    logging.basicConfig(
        format="%(levelname)s: %(asctime)s %(name)s:%(lineno)s %(message)s",
        level=logging.INFO,
    )

    loop = asyncio.get_event_loop()
    agent_stream = loop.run_until_complete(start_agent_stream(args))
    # TODO: logger not printing here for some reason
    logger.info(f"Agent stream started with ports: {args.pull_port}, {args.push_port}")

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(agent_stream.close())
    finally:
        loop.close()
