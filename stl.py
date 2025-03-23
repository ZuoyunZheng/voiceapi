import streamlit as st
import asyncio
import websockets
import json


async def asr_client(uri, stop_event):
    print("Initiating websocket connection")
    try:
        async with websockets.connect(uri) as websocket:
            st.session_state["connection_status"] = "Connected"
            st.session_state["messages"] = []
            print("Established WebSocket connection")

            # Send initial data to keep the connection alive
            await websocket.send(b"Initial data")
            print("Sent initial data to WebSocket")

            while not stop_event.is_set():
                message = await websocket.recv()
                message_data = json.loads(message)
                st.session_state["messages"].append(message_data)
                st.experimental_rerun()
    except Exception as e:
        st.session_state["connection_status"] = "Disconnected"
        st.session_state["messages"].append(f"Connection error: {e}")
        print(f"Connection error: {e}")


async def toggle_connection(uri, stop_event):
    if st.session_state["connection_status"] == "Disconnected":
        stop_event.clear()
        await asr_client(uri, stop_event)
    else:
        stop_event.set()
        st.session_state["connection_status"] = "Disconnected"
        print("WebSocket connection stopped")


async def main():
    uri = "ws://127.0.0.1:8000/asr"
    stop_event = asyncio.Event()

    if "connection_status" not in st.session_state:
        st.session_state["connection_status"] = "Disconnected"
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    st.title("ASR WebSocket Client")

    if st.button("Toggle WebSocket Connection"):
        asyncio.create_task(toggle_connection(uri, stop_event))

    st.write(f"Connection Status: {st.session_state['connection_status']}")

    st.write("Messages:")
    for msg in st.session_state["messages"]:
        st.json(msg)


if __name__ == "__main__":
    asyncio.run(main())
