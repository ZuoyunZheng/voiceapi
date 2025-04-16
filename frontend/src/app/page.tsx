"use client";

import { useState, useEffect, useRef } from 'react';
import Recorder from 'recorder-js';

const ws = new WebSocket('ws://localhost:8000/asr');

export default function Home() {
  const [messages, setMessages] = useState([]);
  const [recorder, setRecorder] = useState(null);
  const audioContextRef = useRef<AudioContext | null>(null);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)() || null;
    }

    async function initializeRecorder() {
      if (!audioContextRef.current) return;
      const rec = new Recorder(audioContextRef.current, {
        
      });

      setRecorder(rec);
    }

    initializeRecorder();

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setMessages(prev => [...prev, data]);
    };

    return () => {
      ws.close();
    };
  }, []);

  const startRecording = async () => {
    if (!recorder) return;
    try {
      await recorder.init();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioContextRef.current.resume();
      recorder.start(stream);
    } catch (error) {
      console.error('Error starting recording:', error);
    }
  };

  const stopRecording = async () => {
    if (!recorder) return;
    try {
      const { buffer } = await recorder.stop();
      const pcmData = convertToPCM(buffer[0]); // Assuming single channel
      ws.send(pcmData);
      recorder.clear();
    } catch (error) {
      console.error('Error stopping recording:', error);
    }
  };

  const convertToPCM = (audioData) => {
    const floatTo16BitPCM = (output, offset, input) => {
      for (let i = 0; i < input.length; i++, offset += 2) {
        const s = Math.max(-1, Math.min(1, input[i]));
        output.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
      }
    };

    const buffer = new ArrayBuffer(audioData.length * 2);
    const output = new DataView(buffer);
    floatTo16BitPCM(output, 0, audioData);
    return buffer;
  };

  return (
    <div>
      <h1>WebSocket ASR Client</h1>
      <button onClick={startRecording}>Start Recording</button>
      <button onClick={stopRecording}>Stop Recording</button>
      <div>
        <h2>Messages:</h2>
        <ul>
          {messages.map((message, index) => (
            <li key={index}>
              <strong>ID: {message.id}</strong> - {message.content}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
