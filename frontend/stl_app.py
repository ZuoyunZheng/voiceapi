import streamlit as st
import threading
import json
import argparse
import pyaudio
import websockets.sync.client
from websockets.exceptions import ConnectionClosed
from queue import Queue
import logging
from pydub import AudioSegment
import io

import time


# Audio parameters
CHUNK = 1024
FORMAT, WIDTH = pyaudio.paInt16, 2  # 16-bit is 2 bytes
CHANNELS = 1
RATE = 16000  # Common sample rate for ASR
message_queue = Queue()  # thread-safe queue for multithreaded comm.

logger = logging.getLogger(__file__)


def initialize_session_state(docker):
    """Initialize the session state with default values"""
    global message_queue
    if "connection_status" not in st.session_state:
        st.session_state["connection_status"] = "Disconnected"
    if "messages" not in st.session_state:
        st.session_state["message_queue"] = message_queue
        st.session_state["messages"] = []
        message_queue = st.session_state["message_queue"]
        logger.info(f"NEW RUN: {len(st.session_state['messages'])} messages so far...")
    else:
        message_queue = st.session_state["message_queue"]
        logger.info(f"RERUN: {len(st.session_state['messages'])} messages so far...")
    if "websocket_uri" not in st.session_state:
        if docker:
            st.session_state["websocket_uri"] = "ws://app:8000/asr"
        else:
            st.session_state["websocket_uri"] = "ws://127.0.0.1:8000/asr"
    if "thread_started" not in st.session_state:
        st.session_state["thread_started"] = False


def process_audio_and_send(websocket, file):
    """Capture audio from microphone and send over websocket"""
    if file:
        # Load the MP3 file directly from the BytesIO object
        sound = AudioSegment.from_file(file, format="mp3")
        sound = sound.set_frame_rate(RATE)
        sound = sound.set_channels(CHANNELS)
        sound = sound.set_sample_width(WIDTH)
        raw_data = sound.raw_data
        buffer = io.BytesIO(raw_data)

        def stream():
            """Generator function that yields chunks of audio data"""
            chunk_size = CHUNK * WIDTH
            while True:
                chunk = buffer.read(chunk_size)
                if not chunk:
                    break
                yield chunk
                time.sleep(0.016)  # 64ms per chunk at 16kHz ~ real-time

        try:
            for data in stream():
                websocket.send(data)
        except Exception as e:
            logger.error(f"Error streaming file: {str(e)}")
    else:
        p = pyaudio.PyAudio()
        stream = p.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )
        try:
            while True:
                data = stream.read(CHUNK, exception_on_overflow=False)
                websocket.send(data)
        except Exception as e:
            logger.error(f"Error streaming audio: {str(e)}")
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()


def receive_asr_results(websocket):
    """Receive ASR results from websocket"""
    segment_id = 0
    while True:
        try:
            result = websocket.recv()
            asr_result = json.loads(result)
            message_queue.put(asr_result)
            logger.info(f"STL recv thread {segment_id}: {asr_result}")
            segment_id += 1
        except ConnectionClosed:
            break
        except Exception as e:
            logger.error(f"Error receiving result: {str(e)}")
            break


def start_websocket_connection(file):
    """Start a new WebSocket connection if not already connected"""
    logger.info("Clearing messages")
    message_queue = Queue()
    st.session_state["messages"] = []
    if (
        st.session_state["connection_status"] == "Connected"
        and st.session_state["websocket"]
    ):
        # Connection already exists
        return

    try:
        # Create a new connection
        websocket = websockets.sync.client.connect(st.session_state["websocket_uri"])
        st.session_state["websocket"] = websocket
        st.session_state["connection_status"] = "Connected"

        # Start sender thread
        sender_thread = threading.Thread(
            target=process_audio_and_send, args=(websocket, file), daemon=True
        )

        # Start receiver thread
        receiver_thread = threading.Thread(
            target=receive_asr_results, args=(websocket,), daemon=True
        )

        # Store threads in session state
        st.session_state["sender_thread"] = sender_thread
        st.session_state["receiver_thread"] = receiver_thread

        # Start threads
        sender_thread.start()
        receiver_thread.start()
        st.session_state["thread_started"] = True

    except Exception as e:
        st.session_state["connection_status"] = "Disconnected"
        print(f"Connection error: {str(e)}")


def stop_websocket_connection():
    """Stop the WebSocket connection and related threads"""
    if st.session_state["connection_status"] == "Disconnected":
        return

    try:
        # Close the websocket connection
        if "websocket" in st.session_state:
            st.session_state["websocket"].close()
            del st.session_state["websocket"]
    except Exception as e:
        print(f"Error closing websocket: {str(e)}")
    finally:
        st.session_state["connection_status"] = "Disconnected"
        st.session_state["thread_started"] = False


def toggle_connection(file):
    """Toggle connection state"""
    if st.session_state["connection_status"] == "Disconnected":
        start_websocket_connection(file)
    else:
        stop_websocket_connection()


def main(args):
    # Initialize session state
    initialize_session_state(args.docker)

    st.title("ASR WebSocket Client")

    # WebSocket URI input
    websocket_uri = st.text_input(
        "WebSocket URI", value=st.session_state["websocket_uri"], key="uri_input"
    )
    st.session_state["websocket_uri"] = websocket_uri

    # Audio File Upload
    file = st.file_uploader("Upload audio file")

    # Connection control button
    button_label = (
        "Connect"
        if st.session_state["connection_status"] == "Disconnected"
        else "Disconnect"
    )
    if st.button(button_label, key="toggle_button"):
        toggle_connection(file)

    # Display connection status
    status_color = (
        "green" if st.session_state["connection_status"] == "Connected" else "red"
    )
    st.markdown(
        f"**Connection Status:** <span style='color:{status_color}'>{st.session_state['connection_status']}</span>",
        unsafe_allow_html=True,
    )

    # Display ASR messages with refresh button
    st.subheader("ASR Results:")

    # Display message count
    st.text(f"Total messages: {len(st.session_state['messages'])}")

    for message in st.session_state["messages"]:
        # Display ASR messages
        with st.chat_message(message["id"]):
            st.write(message["content"])

    # Poll for new messages
    if not message_queue.empty():
        while not message_queue.empty():
            message = message_queue.get()
            st.session_state["messages"].append(message)
            logger.info(f"STL main thread {len(st.session_state['messages'])-1}: {message}")
    else:
        time.sleep(1)
    st.rerun()



if __name__ == "__main__":
    argparser = argparse.ArgumentParser(description="ASR WebSocket Client")
    argparser.add_argument("--docker", action="store_true")
    args = argparser.parse_args()
    logging.basicConfig(
        format="%(levelname)s: %(asctime)s %(name)s:%(lineno)s %(message)s",
        level=logging.INFO,
    )
    main(args)
