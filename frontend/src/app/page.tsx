"use client";

import { useState, useEffect } from 'react';

const ws = new WebSocket('ws://localhost:8000/asr');

export default function Home() {
  interface Message {
    id: number;
    content: string;
  }

  const [messages, setMessages] = useState<Message[]>([]);
  const [mediaRecorder, setMediaRecorder] = useState<MediaRecorder | null>(null);
  const [audioChunks, setAudioChunks] = useState<Blob[]>([]);

  useEffect(() => {
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setMessages(prev => [...prev, data]);
    };

    return () => {
      ws.close();
    };
  }, []);

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

      recorder.ondataavailable = (event) => {
        setAudioChunks(prev => [...prev, event.data]);
      };

      recorder.onstop = () => {
        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        // Convert audioBlob to PCM and send to WebSocket
        ws.send(audioBlob);
        setAudioChunks([]);
      };

      setMediaRecorder(recorder);
      recorder.start();
    } catch (error) {
      console.error('Error starting recording:', error);
    }
  };

  const stopRecording = () => {
    if (mediaRecorder) {
      mediaRecorder.stop();
    }
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
