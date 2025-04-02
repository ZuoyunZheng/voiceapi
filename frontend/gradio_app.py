import gradio as gr
import asyncio
import websockets
import json

# Global variables to manage WebSocket connection
connected = False
stop_event = None


async def asr_client(uri, send_data):
    print("Initiating websocket connection")
    try:
        async with websockets.connect(uri) as websocket:
            global connected
            connected = True
            messages = []
            print("Established WebSocket connection")

            # Send initial data to keep the connection alive
            await websocket.send(send_data)
            print("Sent initial data to WebSocket")

            while True:
                message = await websocket.recv()
                message_data = json.loads(message)
                messages.append(message_data)
                return messages  # Refresh the UI with new messages
    except Exception as e:
        connected = False
        print(f"Connection error: {e}")
        return [f"Connection error: {e}"]


def start_connection(send_data):
    uri = "ws://127.0.0.1:8000/asr"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(asr_client(uri, send_data))


# Gradio Interface
with gr.Blocks() as demo:
    send_data = gr.Textbox(label="Data to Send")
    button = gr.Button("Connect")
    messages = gr.Textbox(label="Messages")

    button.click(start_connection, inputs=send_data, outputs=messages)

demo.launch()
