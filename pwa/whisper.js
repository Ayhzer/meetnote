/**
 * whisper.js — Transcription locale via Transformers.js (WASM)
 */
let _pipeline = null;
let _currentModel = null;

export async function transcribe(audioBlob, { model, language, onProgress } = {}) {
  model = model || 'Xenova/whisper-base';

  if (!_pipeline || _currentModel !== model) {
    const { pipeline } = await import(
      'https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2/dist/transformers.min.js'
    );
    onProgress?.('Chargement du modèle…');
    _pipeline = await pipeline('automatic-speech-recognition', model, {
      progress_callback: (p) => {
        if (p.status === 'downloading') {
          const pct = Math.round((p.loaded / p.total) * 100);
          onProgress?.(`Téléchargement modèle : ${pct}%`);
        }
      },
    });
    _currentModel = model;
  }

  onProgress?.('Transcription en cours…');
  const arrayBuffer = await audioBlob.arrayBuffer();
  const float32 = await _decodeAudio(arrayBuffer);

  const result = await _pipeline(float32, {
    language: language || 'fr',
    task: 'transcribe',
    chunk_length_s: 30,
    stride_length_s: 5,
    return_timestamps: false,
  });

  return Array.isArray(result) ? result.map((r) => r.text).join('\n') : result.text;
}

async function _decodeAudio(arrayBuffer) {
  const audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  const decoded = await audioCtx.decodeAudioData(arrayBuffer);
  audioCtx.close();
  // Mix to mono
  const ch = decoded.numberOfChannels;
  const len = decoded.length;
  const mono = new Float32Array(len);
  for (let c = 0; c < ch; c++) {
    const chanData = decoded.getChannelData(c);
    for (let i = 0; i < len; i++) mono[i] += chanData[i];
  }
  for (let i = 0; i < len; i++) mono[i] /= ch;
  return mono;
}
