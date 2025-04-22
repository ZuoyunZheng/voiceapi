import asyncio

import websockets


async def test_websocket(uri):
    print("Initiating websocket connection")
    try:
        async with websockets.connect(uri) as websocket:
            print("WebSocket connection established")
            await websocket.send("Test message")
            response = await websocket.recv()
            print(f"Received response: {response}")
    except Exception as e:
        print(f"Connection error: {e}")


if __name__ == "__main__":
    uri = "ws://127.0.0.1:8000/asr"
    asyncio.run(test_websocket(uri))
