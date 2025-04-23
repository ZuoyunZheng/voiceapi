"use client";

import { useState, useEffect } from 'react';

interface Message {
  id: number;
  content: string;
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [mediaRecorder, setMediaRecorder] = useState<MediaRecorder | null>(null);
  const [audioChunks, setAudioChunks] = useState<Blob[]>([]);
  const [isRecording, setIsRecording] = useState<boolean>(false);
  const [connectionStatus, setConnectionStatus] = useState<'connected' | 'disconnected' | 'error'>('disconnected');
  const [ws, setWs] = useState<WebSocket | null>(null);

  // Initialize WebSocket connection
  useEffect(() => {
    // const getWebSocketUrl = (): string => {
    //   const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    //   const hostname = window.location.hostname;
    //   // Use the actual port where your WebSocket server is exposed
    //   return `${protocol}//${hostname}:8000/asr`;
    // };

    // Create WebSocket connection when component mounts
    // const websocket = new WebSocket(getWebSocketUrl());
    const ws = new WebSocket('ws://localhost:8000/asr')
    
    ws.onopen = () => {
      setConnectionStatus('connected');
      console.log('WebSocket connected');
    };
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setMessages(prev => [...prev, data]);
    };
    
    ws.onclose = () => {
      setConnectionStatus('disconnected');
      console.log('WebSocket disconnected');
    };
    
    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      setConnectionStatus('error');
    };
    
    setWs(ws);
    
    return () => {
      ws.close();
    };
  }, []);

  const startRecording = async () => {
    try {
      // Request audio with specific constraints for better compatibility
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        }
      });
      
      // Try WebM first, fall back to other formats if needed
      let mimeType = 'audio/webm';
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = 'audio/ogg';
        if (!MediaRecorder.isTypeSupported(mimeType)) {
          mimeType = '';  // Let browser choose
        }
      }
      
      const recorder = new MediaRecorder(stream, { 
        mimeType: mimeType || undefined
      });

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          setAudioChunks(prev => [...prev, event.data]);
        }
      };

      recorder.onstop = () => {
        if (audioChunks.length > 0) {
          const audioBlob = new Blob(audioChunks, { type: mimeType });
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(audioBlob);
            console.log('Sent audio data to server');
          } else {
            console.error('WebSocket not connected');
          }
          setAudioChunks([]);
        }
      };

      recorder.start(100); // Collect data every 100ms for more real-time feedback
      setMediaRecorder(recorder);
      setIsRecording(true);
    } catch (error) {
      console.error('Error starting recording:', error);
      alert('Could not access microphone. Please check permissions and try again.');
    }
  };

  const stopRecording = () => {
    if (mediaRecorder && isRecording) {
      mediaRecorder.stop();
      setIsRecording(false);
      
      // Stop all audio tracks
      mediaRecorder.stream.getTracks().forEach(track => track.stop());
    }
  };

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold mb-4">WebSocket ASR Client</h1>
      
      <div className="mb-4">
        <div className="flex items-center mb-2">
          <div className={`w-3 h-3 rounded-full mr-2 ${
            connectionStatus === 'connected' ? 'bg-green-500' : 
            connectionStatus === 'error' ? 'bg-red-500' : 'bg-gray-500'
          }`}></div>
          <span>WebSocket: {connectionStatus}</span>
        </div>
        
        <div className="space-x-2">
          <button 
            onClick={startRecording} 
            disabled={isRecording}
            className={`px-4 py-2 rounded ${isRecording ? 'bg-gray-300' : 'bg-blue-500 text-white'}`}
          >
            Start Recording
          </button>
          <button 
            onClick={stopRecording} 
            disabled={!isRecording}
            className={`px-4 py-2 rounded ${!isRecording ? 'bg-gray-300' : 'bg-red-500 text-white'}`}
          >
            Stop Recording
          </button>
        </div>
      </div>
      
      <div>
        <h2 className="text-xl font-semibold mb-2">Messages:</h2>
        {messages.length === 0 ? (
          <p className="text-gray-500">No messages yet</p>
        ) : (
          <ul className="border rounded-lg divide-y">
            {messages.map((message, index) => (
              <li key={index} className="p-3">
                <strong>ID: {message.id}</strong> - {message.content}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
